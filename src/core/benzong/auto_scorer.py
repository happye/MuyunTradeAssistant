"""笨总 6 维 AI 自动评分调度器（v0.8.6.2）

这是上层封装：用户跑 `bz 600519` 时调用 `auto_score("600519")` 一行就出结果。

调度逻辑：
1. 拉一次 data_summary（业务介绍/公告/行业/K线/全市场成交额）
2. 对每个维度：先查 cache（同股同日同维），命中则跳过 AI；未命中则调维度评分器
3. 把 6 维结果组装为 BenzhongScore（已有 dataclass），保留 grade + 大前提警告
4. 同时保留每维 metadata（confidence/sources/reasoning），用于 CLI 展示

依赖（按需加载）：
- AI client：用现有 ai_modifier 风格（OpenAI 兼容 SDK，DeepSeek/Kimi）
- RAG service：可选
- data_provider + cache + scorer：本模块内同级
"""

import logging
import os
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.core.benzong import cache, data_provider
from src.core.benzong.scorer import BenzhongScore, score_one
from src.core.benzong.dimensions import _lazy_load_dimensions

logger = logging.getLogger(__name__)

# 景气度判定低置信度阈值（ISS-055）：景气度闸门降级 + 景气判定置信度低于此值时
# 额外警告"降级依赖最不可靠维度"。沿用 start.py overall_confidence<0.5 的既有约定，
# 非笨总评分公式阈值，仅用于透明度提示。
_PROSPERITY_LOW_CONF_THRESHOLD = 0.5


@dataclass
class AutoScoredResult:
    """自动评分完整返回（包装 BenzhongScore + 元数据）"""

    score: BenzhongScore                            # 最终 6 维总分（复用 v0.8.6.1）
    dimensions_meta: dict = field(default_factory=dict)   # 每维 {confidence, sources, reasoning, ...}
    overall_confidence: float = 0.0                  # 6 维 confidence 的最低值
    warnings: list = field(default_factory=list)
    cache_hits: list = field(default_factory=list)
    fetch_status: dict = field(default_factory=dict)
    invalidate: bool = False                         # 风险维度触发的一票否决
    is_self_reliance: bool = False                    # 自主可控概念（教学九，建仓强制剑宗的依据）
    market_turnover: Optional[float] = None          # 全市场成交额(万亿)，供流动性状态展示
    # 报告2.1 笨总教学八板块层信号建仓标注（一次轻量 AI 标注）
    flagbearer_code: Optional[str] = None              # 板块旗手代码（持有期检查旗手滞涨）
    penetration_stage: Optional[str] = None            # 渗透率阶段 0-1/1-10/10-30/30+（30+触发板块见顶）


def _build_ai_client(config: Optional[dict] = None):
    """根据 settings.yaml 的 ai 段构造 OpenAI 兼容客户端。

    复用 ai_modifier.py 的初始化逻辑（默认 provider/model/base_url）。
    失败返回 None（评分器会降级到 conf=0）。
    """
    try:
        if config is None:
            from src.cli.main import load_config
            config = load_config()
        ai_cfg = config.get("ai", {})
        provider = ai_cfg.get("provider", "deepseek")
        provider_cfg = ai_cfg.get(provider, {})
        api_key = provider_cfg.get("api_key") or os.environ.get(f"{provider.upper()}_API_KEY", "")
        base_url = provider_cfg.get("base_url")
        if not api_key:
            logger.warning("AI api_key 未配置，AI 维度将走降级")
            return None, None

        # ISS-078：删除 os.environ["OPENAI_API_KEY"] 写入——client 已显式传 api_key，
        # 把密钥写进进程级 env 只会扩大泄漏面（子进程可继承），且全库无读取方

        from openai import OpenAI
        # 显式 timeout + 重试：防 bz scan 长批量时连接挂起到 WinError 10060/10054
        # （OpenAI SDK 默认 timeout 偏长，单只卡住会拖垮整批）
        ai_timeout = float(ai_cfg.get("request_timeout", 60))
        ai_retries = int(ai_cfg.get("max_retries", 2))
        client_kwargs = {"api_key": api_key, "timeout": ai_timeout, "max_retries": ai_retries}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)
        model = provider_cfg.get("model", "deepseek-chat")
        return client, model
    except Exception as e:
        logger.warning(f"AI client 构造失败: {e}")
        return None, None


def _annotate_sector_meta(ai_client, ai_model, code, name, data_summary):
    """报告2.1: 一次轻量 AI 标注板块旗手+渗透率阶段(笨总教学八, 建仓时一次定)。

    用于板块层高位止盈信号(旗手滞涨/渗透率30%魔咒)。建仓时标注, 持有期检查。
    AI 失败返回 (None, None), 不阻塞评分主流程。
    """
    if not ai_client or not ai_model:
        return None, None
    industry_info = (data_summary or {}).get("industry") or {}
    industry_name = industry_info.get("industry_name", "") if isinstance(industry_info, dict) else ""
    if not industry_name and not name:
        return None, None
    try:
        prompt = (
            f"判断股票 {name or code} (行业: {industry_name or '未知'}) 的板块属性:\n"
            "1. flagbearer_code: 该行业最核心的旗手股票代码(6位数字, 不带前缀), "
            "即板块脸面代表大资金态度的龙头. 不确定填 null.\n"
            "2. penetration_stage: 渗透率阶段, 从 [0-1, 1-10, 10-30, 30+] 选一. "
            "0-1=技术突破初期, 1-10=产业化起步, 10-30=快速增长, 30+=成熟增速放缓.\n"
            '只输出 JSON: {"flagbearer_code":"600519" 或 null, "penetration_stage":"1-10"}'
        )
        extra = {"extra_body": {"thinking": {"type": "disabled"}}} if str(ai_model).startswith("deepseek-v4") else {}
        resp = ai_client.chat.completions.create(
            model=ai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_completion_tokens=120, timeout=20,
            **extra,
        )
        content = (resp.choices[0].message.content or "").strip()
        js = content
        if "```json" in content:
            js = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            js = content.split("```")[1].split("```")[0].strip()
        d = json.loads(js)
        fb = d.get("flagbearer_code")
        ps = d.get("penetration_stage")
        if fb and not (isinstance(fb, str) and fb.isdigit() and len(fb) == 6):
            fb = None
        if ps not in ("0-1", "1-10", "10-30", "30+"):
            ps = None
        return fb, ps
    except Exception as e:
        logger.debug(f"板块元标注失败({code}): {e}")
        return None, None


def _build_rag_service(config: Optional[dict] = None):
    """加载 RAG（可选，失败返回 None）。

    v0.8.6.3：bz 默认不再自动调用本函数（用户决定 bz 不依赖 RAG）。
    保留供需要 RAG 的外部调用方使用。auto_score 默认 rag_service=None。
    """
    try:
        from src.rag.service import get_rag_service
        rag = get_rag_service()
        return rag
    except Exception as e:
        logger.warning(f"RAG service 加载失败: {e}")
        return None


def auto_score(
    code: str,
    name: str = "",
    *,
    force_refresh: bool = False,
    config: Optional[dict] = None,
    ai_client=None,
    ai_model: Optional[str] = None,
    rag_service=None,
    data_summary: Optional[dict] = None,
    market_turnover: Optional[float] = None,
    today: Optional[str] = None,
) -> AutoScoredResult:
    """全自动笨总打分入口。

    Args:
        code: 股票代码
        name: 股票名称（可空，会从 data_summary.industry 拉）
        force_refresh: True 时跳过缓存
        config: 复用现有 settings.yaml；None 时自动加载
        ai_client / ai_model / rag_service: 可注入（mock 测试用）
        data_summary: 可注入（mock 测试用）
        market_turnover: 可注入的全市场成交额（万亿）。批量场景由 auto_score_batch
            开头拉一次后注入，避免每只股票重打 sina。None 时由 get_data_summary 内部自拉。
        today: YYYY-MM-DD，缓存键和缓存过期判定用

    Returns:
        AutoScoredResult
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    logger.info(f"[auto_score] {code} 开始评分（force_refresh={force_refresh}）")

    # Step 1: 准备依赖
    if ai_client is None:
        ai_client, ai_model = _build_ai_client(config)
    # v0.8.6.3: bz 默认不依赖 RAG（rag_service 保持 None，除非外部显式注入）
    # 维度评分器对 rag_service=None 走无 RAG 路径，不影响评分

    # Step 2: 拉数据（如果未注入）
    if data_summary is None:
        data_summary = data_provider.get_data_summary(code, market_turnover=market_turnover)

    if not name and data_summary.get("industry"):
        name = data_summary["industry"].get("stock_name", code)

    # Step 3: 各维度评分（先查缓存）
    dims = _lazy_load_dimensions()
    dim_results = {}
    cache_hits = []
    invalidate_flag = False

    for dim_name, scorer_fn in dims.items():
        if not force_refresh:
            cached = cache.get(code, today, dim_name)
            if cached is not None:
                dim_results[dim_name] = cached
                cache_hits.append(dim_name)
                logger.debug(f"  {dim_name}: 缓存命中")
                if cached.get("invalidate"):
                    invalidate_flag = True
                continue

        try:
            r = scorer_fn(code, name, data_summary=data_summary,
                          ai_client=ai_client, ai_model=ai_model,
                          rag_service=rag_service)
        except Exception as e:
            logger.error(f"  {dim_name} 调用异常: {e}", exc_info=True)
            r = {
                "score": 50, "confidence": 0.0,
                "sources": [], "reasoning": f"维度评分器异常: {e}",
                "data_freshness": "N/A",
                "warnings": [f"{dim_name} 异常"],
            }

        dim_results[dim_name] = r
        if r.get("invalidate"):
            invalidate_flag = True

        # 写缓存（仅在 confidence > 0 时缓存，避免缓存"失败结果"）
        if r.get("confidence", 0) > 0:
            cache.set(code, today, dim_name, r)

    # Step 4: 流动性系数
    market_turnover = data_summary.get("market_turnover")
    turnover_missing = market_turnover is None
    if turnover_missing:
        market_turnover = 1.0  # 中性默认
        logger.debug("市场成交额未获取，流动性系数用 1.0")

    # 自主可控概念检测（笨总教学九 / 视频理念优化报告 1.3）--建仓 mode 判定时强制剑宗的依据
    # 仅计算，警告在 Step 6 all_warnings 汇总处追加
    from src.core.benzong.self_reliance import detect_self_reliance_from_summary
    is_self_reliance = detect_self_reliance_from_summary(data_summary)

    # 报告2.1 笨总教学八板块层标注：一次轻量 AI 标注旗手+渗透率阶段（建仓时定，持有期检查）
    flagbearer_code, penetration_stage = _annotate_sector_meta(
        ai_client, ai_model, code, name, data_summary
    )

    # Step 5: 组装 BenzhongScore（复用 v0.8.6.1 dataclass）
    bs = score_one(
        industry_prosperity=dim_results["industry_prosperity"]["score"],
        business_purity=dim_results["business_purity"]["score"],
        valuation_position=dim_results["valuation_position"]["score"],
        industry_leader=dim_results["industry_leader"]["score"],
        market_recognition=dim_results["market_recognition"]["score"],
        risk_deduction=dim_results["risk_deduction"]["score"],
        market_turnover_trillion=market_turnover,
        invalidate=invalidate_flag,  # B03 修复：红线透传，effective_grade 强制 F
        stock_code=code,
        stock_name=name,
    )

    # Step 6: 整体置信度（最低维度的 conf）+ 警告汇总
    overall_conf = min((r.get("confidence", 0.0) for r in dim_results.values()), default=0.0)
    all_warnings = []
    for dn, r in dim_results.items():
        for w in r.get("warnings", []):
            all_warnings.append(f"[{dn}] {w}")

    # 一票否决警告
    if invalidate_flag:
        all_warnings.insert(0, "🚨 一票否决：风险维度发现红线（叛国/违禁/造假），强烈建议放弃本标的")

    # 自主可控概念警告（笨总教学九 / 报告 1.3）--建仓 mode 强制剑宗，不长持
    if is_self_reliance:
        all_warnings.append(
            "⚠ 自主可控概念（教学九）：笨总提示此类标的波动大、炒短期情绪，"
            "不宜长期持有，建仓 mode 强制剑宗（不长持）。如不认可可手动改 portfolio.yaml 的 mode"
        )

    # 大前提警告（行业景气度=0）
    pre = bs.precondition_warning()
    if pre:
        all_warnings.append(pre)

    # ISS-055: 景气度闸门透明度警告（不动 effective_grade 公式，笨总域）
    # ip==0 已由 precondition_warning 覆盖，此处只管 ip>0 区。
    # H1 审查扩展：pros_conf==0（景气数据缺失，ip=50 兜底）时**无条件**警告--兜底值被当有效
    # "稳定无拐点"违背体系第一前提，即使 effective==grade（其他维也低）也该提示，不依赖降级触发。
    pros_conf = dim_results.get("industry_prosperity", {}).get("confidence", 0.0)
    if bs.industry_prosperity > 0 and pros_conf < _PROSPERITY_LOW_CONF_THRESHOLD:
        if pros_conf == 0.0:
            all_warnings.append(
                "⚠ 景气度数据缺失：评分为兜底值50（非真实判断），effective_grade 闸门立在最不可靠维度（无数据）。"
                "强烈建议人工核实景气或等数据恢复，勿仅凭此分级买入"
            )
        elif bs.effective_grade() != bs.grade():
            all_warnings.append(
                f"⚠ 评级受景气度闸门降级（原始{bs.grade()}->{bs.effective_grade()}），"
                f"但景气度判定置信度仅{pros_conf:.0%}--降级依据最不可靠维度（AI难判真/撞数据天花板），建议人工复核景气判断"
            )

    # v0.8.7.6 审计修复 B15：纯度/龙头/辨识度等 AI 维 conf=0 兜底 50 时评级仍可能到 A
    # （H1 只堵了景气维）。对齐 ISS-055 拍板（透明度警告、不动 effective_grade 公式），
    # 列出具体兜底维度；是否给"兜底维度超过N个压级"留笨总拍板（见 ISS-069）。
    fallback_dims = [
        dn for dn, r in dim_results.items()
        if r.get("confidence", 0) == 0 and dn != "industry_prosperity"
    ]
    if fallback_dims:
        all_warnings.append(
            f"⚠ {len(fallback_dims)} 维为兜底值50（数据缺失，非真实判断）：{', '.join(fallback_dims)}"
            f"——评级可靠性存疑，建议人工复核或等数据恢复"
        )

    # 数据获取整体不全 → 警告
    fetch_status = data_summary.get("fetch_status", {})
    failed_sources = [k for k, v in fetch_status.items() if not v]
    if failed_sources:
        all_warnings.append(f"⚠ 数据源失败: {', '.join(failed_sources)}（评分置信度受限）")

    return AutoScoredResult(
        score=bs,
        dimensions_meta=dim_results,
        overall_confidence=overall_conf,
        warnings=all_warnings,
        cache_hits=cache_hits,
        fetch_status=fetch_status,
        invalidate=invalidate_flag,
        is_self_reliance=is_self_reliance,
        market_turnover=None if turnover_missing else market_turnover,  # B27 修复：不再用 !=1.0 判空（真实1.0万亿被误吞）
        flagbearer_code=flagbearer_code,
        penetration_stage=penetration_stage,
    )
