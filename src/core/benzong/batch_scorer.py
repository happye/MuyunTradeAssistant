"""笨总批量评分封装（ISS-041 方向 A：选股初筛）

对一批候选股串行跑笨总 6 维 AI 评分，按总分排序返回 TopN。

设计：
- **build client 1 次**：复用 `_build_ai_client`（auto_scorer.py:42）构造一次 AI client+model，
  循环注入 `auto_score(ai_client=, ai_model=)`，避免每只股票重建 client
- **串行执行**：Baostock 全局登录态（akshare_client.py:22 `_bs_login_status`）非线程安全，
  并发会撞登录态。强制串行 + 进度日志
- **网络降级**：auto_score 已有 conf=0+warning 兜底，单只失败不阻塞批次（记入 failures）
- **缓存友好**：auto_score 内部查 cache，同股同日不重算。批量跑完结果可被后续 `bz <code>` 复用
- **逐项结果回调（T05-H）**：`result_cb(record)` 只接收**实际已返回**的结构化结果，
  供批量入口（ba）记账；`progress_cb` 仍是纯展示。二者异常都被隔离——已接受的评分
  不会因回调异常改判失败，也不会造成 ranked + failures 双计。
  record 结构：`{"code", "ok": bool, "status": "complete"|"degraded"|"failed",
  "degraded_dims", "total_score", "normalized_score", "grade", "effective_grade",
  "confidence", "invalidate"}`；失败时含 "error"。
  注意：这些记录只描述运行可见性，不授予投资信号权限，也不改变 cache 消费资格。

返回结构：
    {
      "ranked": [   # 按 total_score 降序
        {"code","name","total_score","grade","confidence","dim_scores": {...},
         "scoring_status": "complete"|"degraded", "degraded_dims": [...], "warnings": [...]},
        ...
      ],
      "top_n": [...],      # ranked 的前 top_n 个
      "failures": [{"code","error"}],
      "scanned": N, "scored": M, "failed": K,
      "result_cb_errors": N,   # 记账回调异常次数（>0 → 任务记录可能不完整）
    }
"""

import logging
import math
from typing import Optional

from src.core.benzong import auto_score
from src.core.benzong.auto_scorer import _build_ai_client
from src.core.benzong.data_provider import get_market_turnover

logger = logging.getLogger(__name__)

# 笨总 6 维（与 dimensions._lazy_load_dimensions 同源）：用于判定“部分维度未评出”
_EXPECTED_DIMS = (
    "industry_prosperity", "business_purity", "valuation_position",
    "industry_leader", "market_recognition", "risk_deduction",
)


def _unrated_dimensions(dimensions_meta) -> list:
    """列出置信度缺失/0/非法（或整维缺失）的维度——这类结果只能算降级，不算完整评分。"""
    meta = dimensions_meta if isinstance(dimensions_meta, dict) else {}
    unrated = []
    for dim in _EXPECTED_DIMS:
        item = meta.get(dim)
        if not isinstance(item, dict):
            unrated.append(dim)
            continue
        conf = item.get("confidence")
        if isinstance(conf, bool) or not isinstance(conf, (int, float)):
            unrated.append(dim)
            continue
        if not math.isfinite(conf) or conf <= 0 or conf > 1:
            unrated.append(dim)
    return unrated


def _notify_result(result_cb, record: dict) -> bool:
    """调用结构化逐项结果回调；异常隔离，返回是否发生回调异常（记录不可靠）。"""
    if result_cb is None:
        return False
    try:
        result_cb(record)
        return False
    except Exception as e:
        logger.warning(
            f"[auto_score_batch] {record.get('code')} 逐项结果回调异常"
            f"（本次任务记录可能不完整）: {e}")
        return True


def _notify_progress(progress_cb, index, total, code, status) -> None:
    """调用展示进度回调；异常只影响显示，不得把已接受的结果算失败。"""
    if progress_cb is None:
        return
    try:
        progress_cb(index, total, code, status)
    except Exception as e:
        logger.debug(f"[auto_score_batch] {code} 进度展示回调异常（仅影响显示）: {e}")


def auto_score_batch(
    codes: list[str],
    top_n: int = 10,
    force_refresh: bool = False,
    config: Optional[dict] = None,
    progress_cb=None,
    result_cb=None,
) -> dict:
    """批量笨总 6 维 AI 评分 + 排序。

    Args:
        codes: 候选股票代码列表
        top_n: 返回前 N 名（默认 10）
        force_refresh: 是否强制刷新缓存（跳过 cache）
        config: settings.yaml；None 时 auto_score 内部自动加载
        progress_cb: 可选回调 cb(index, total, code, status)，仅用于展示进度
        result_cb: 可选结构化逐项结果回调 cb(record)，仅接收实际已返回的结果
            （成功含 complete/degraded 状态，失败含 error）；回调异常被隔离，
            不影响 ranked/failures 业务结果

    Returns:
        {ranked, top_n, failures, scanned, scored, failed, result_cb_errors}
    """
    codes = [c for c in codes if c]
    total = len(codes)
    logger.info(f"[auto_score_batch] 开始批量评分 {total} 只，top_n={top_n}")

    # build AI client 一次（批量复用，避免每只重建）
    # v0.8.6.3: bz 不依赖 RAG，rag_service 保持 None
    ai_client, ai_model = _build_ai_client(config)
    rag_service = None
    if ai_client is None:
        logger.warning("[auto_score_batch] AI client 构建失败，全部维度将走降级")

    # 全市场成交额整批只拉一次，循环注入（per-batch fresh：局部变量，函数返回即销毁，不跨批缓存）
    # 否则每只 auto_score 各自新建 MarketCache → 实例缓存失效 → 20只×73页×3重试=4380 次 sina 自残
    market_turnover = get_market_turnover()
    if market_turnover is None:
        logger.warning("[auto_score_batch] 全市场成交额获取失败，本批流动性系数降级到 1.0")

    ranked = []
    failures = []
    result_cb_errors = 0

    for i, code in enumerate(codes, start=1):
        try:
            result = auto_score(
                code,
                force_refresh=force_refresh,
                config=config,
                ai_client=ai_client,
                ai_model=ai_model,
                rag_service=rag_service,
                market_turnover=market_turnover,
            )
            bs = result.score
            dim_scores = {k: v["score"] for k, v in result.dimensions_meta.items()}
            # v0.8.8.6（ISS-082）：带上各维置信度——ba 白话点评据此把 conf=0
            # （降级未评出）的维度显示为"未评出"，不再拿中性 50 冒充真实打分
            dim_confidences = {k: v.get("confidence", 0) for k, v in result.dimensions_meta.items()}
            # T05-H：部分维度未评出（confidence 缺失/0/非法/整维缺失）→ 降级，不冒充完整评分
            unrated_dims = _unrated_dimensions(result.dimensions_meta)
            status = "degraded" if unrated_dims else "complete"
            ranked.append({
                "code": code,
                "name": bs.stock_name or code,
                "total_score": bs.total_score,
                "normalized_score": bs.normalized_score(),
                "grade": bs.grade(),
                "effective_grade": bs.effective_grade(),
                "confidence": result.overall_confidence,
                "dim_scores": dim_scores,
                "dim_confidences": dim_confidences,
                "warnings": result.warnings,
                "invalidate": result.invalidate,
                "scoring_status": status,
                "degraded_dims": unrated_dims,
            })
            _notify_progress(progress_cb, i, total, code,
                             f"{bs.normalized_score():.0f}/{bs.effective_grade()}")
            if _notify_result(result_cb, {
                "code": code,
                "ok": True,
                "status": status,
                "degraded_dims": unrated_dims,
                "total_score": bs.total_score,
                "normalized_score": bs.normalized_score(),
                "grade": bs.grade(),
                "effective_grade": bs.effective_grade(),
                "confidence": result.overall_confidence,
                "invalidate": bool(result.invalidate),
            }):
                result_cb_errors += 1
        except Exception as e:
            logger.error(f"[auto_score_batch] {code} 评分失败: {e}")
            error = f"{type(e).__name__}: {str(e)[:80]}"
            failures.append({"code": code, "error": error})
            _notify_progress(progress_cb, i, total, code, "失败")
            if _notify_result(result_cb, {
                "code": code,
                "ok": False,
                "status": "failed",
                "error": error,
            }):
                result_cb_errors += 1

    # 排序：一票否决排最后 → 实际等级（含景气度闸门）降序 → 归一化分降序
    # 用 effective_grade 排序，让"景气30的A级"被景气度闸门压到 C 后自然下沉
    _grade_rank = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
    ranked.sort(
        key=lambda x: (
            not x.get("invalidate", False),
            # A37 修复：`x.get("effective_grade", x["grade"])` 只防键缺失不防值为 None
            # （键存在但值为 None 时返回 None → .get(None,0) 恒 0 当 F 级沉底），用 or 兜底
            _grade_rank.get(x.get("effective_grade") or x.get("grade") or "F", 0),
            x.get("normalized_score") or x.get("total_score") or 0,
        ),
        reverse=True,
    )

    top = ranked[:top_n] if top_n > 0 else ranked

    return {
        "ranked": ranked,
        "top_n": top,
        "failures": failures,
        "scanned": total,
        "scored": len(ranked),
        "failed": len(failures),
        "result_cb_errors": result_cb_errors,
    }
