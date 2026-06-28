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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.core.benzong import cache, data_provider
from src.core.benzong.scorer import BenzhongScore, score_one
from src.core.benzong.dimensions import _lazy_load_dimensions

logger = logging.getLogger(__name__)


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

        if "OPENAI_API_KEY" not in os.environ:
            os.environ["OPENAI_API_KEY"] = api_key

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
        data_summary = data_provider.get_data_summary(code)

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
    if market_turnover is None:
        market_turnover = 1.0  # 中性默认
        logger.debug("市场成交额未获取，流动性系数用 1.0")

    # Step 5: 组装 BenzhongScore（复用 v0.8.6.1 dataclass）
    bs = score_one(
        industry_prosperity=dim_results["industry_prosperity"]["score"],
        business_purity=dim_results["business_purity"]["score"],
        valuation_position=dim_results["valuation_position"]["score"],
        industry_leader=dim_results["industry_leader"]["score"],
        market_recognition=dim_results["market_recognition"]["score"],
        risk_deduction=dim_results["risk_deduction"]["score"],
        market_turnover_trillion=market_turnover,
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

    # 大前提警告（行业景气度=0）
    pre = bs.precondition_warning()
    if pre:
        all_warnings.append(pre)

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
    )
