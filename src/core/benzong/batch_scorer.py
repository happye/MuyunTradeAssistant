"""笨总批量评分封装（ISS-041 方向 A：选股初筛）

对一批候选股串行跑笨总 6 维 AI 评分，按总分排序返回 TopN。

设计：
- **build client 1 次**：复用 `_build_ai_client`（auto_scorer.py:42）构造一次 AI client+model，
  循环注入 `auto_score(ai_client=, ai_model=)`，避免每只股票重建 client
- **串行执行**：Baostock 全局登录态（akshare_client.py:22 `_bs_login_status`）非线程安全，
  并发会撞登录态。强制串行 + 进度日志
- **网络降级**：auto_score 已有 conf=0+warning 兜底，单只失败不阻塞批次（记入 failures）
- **缓存友好**：auto_score 内部查 cache，同股同日不重算。批量跑完结果可被后续 `bz <code>` 复用

返回结构：
    {
      "ranked": [   # 按 total_score 降序
        {"code","name","total_score","grade","confidence","dim_scores": {...}, "warnings": [...]},
        ...
      ],
      "top_n": [...],      # ranked 的前 top_n 个
      "failures": [{"code","error"}],
      "scanned": N, "scored": M, "failed": K
    }
"""

import logging
from typing import Optional

from src.core.benzong import auto_score
from src.core.benzong.auto_scorer import _build_ai_client
from src.core.benzong.data_provider import get_market_turnover

logger = logging.getLogger(__name__)


def auto_score_batch(
    codes: list[str],
    top_n: int = 10,
    force_refresh: bool = False,
    config: Optional[dict] = None,
    progress_cb=None,
) -> dict:
    """批量笨总 6 维 AI 评分 + 排序。

    Args:
        codes: 候选股票代码列表
        top_n: 返回前 N 名（默认 10）
        force_refresh: 是否强制刷新缓存（跳过 cache）
        config: settings.yaml；None 时 auto_score 内部自动加载
        progress_cb: 可选回调 cb(index, total, code, status)，用于展示进度

    Returns:
        {ranked, top_n, failures, scanned, scored, failed}
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
            ranked.append({
                "code": code,
                "name": bs.stock_name or code,
                "total_score": bs.total_score,
                "normalized_score": bs.normalized_score(),
                "grade": bs.grade(),
                "effective_grade": bs.effective_grade(),
                "confidence": result.overall_confidence,
                "dim_scores": dim_scores,
                "warnings": result.warnings,
                "invalidate": result.invalidate,
            })
            if progress_cb:
                progress_cb(i, total, code, f"{bs.normalized_score():.0f}/{bs.effective_grade()}")
        except Exception as e:
            logger.error(f"[auto_score_batch] {code} 评分失败: {e}")
            failures.append({"code": code, "error": f"{type(e).__name__}: {str(e)[:80]}"})
            if progress_cb:
                progress_cb(i, total, code, "失败")

    # 排序：一票否决排最后 → 实际等级（含景气度闸门）降序 → 归一化分降序
    # 用 effective_grade 排序，让"景气30的A级"被景气度闸门压到 C 后自然下沉
    _grade_rank = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
    ranked.sort(
        key=lambda x: (
            not x.get("invalidate", False),
            _grade_rank.get(x.get("effective_grade", x["grade"]), 0),
            x.get("normalized_score", x["total_score"]),
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
    }
