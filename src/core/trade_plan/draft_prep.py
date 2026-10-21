"""T04-C：TradePlan 草稿编排服务（无打印、无 input、不写盘）。

从 CLI `_try_attach_trade_plan` / `_generate_or_update_plan` 抽出：
拉行情 → 笨总评分 → TradePlanGenerator.generate。
调用方负责展示与 Y/n / attach_plan。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

from src.data.models import TradePlan

logger = logging.getLogger(__name__)

TP_DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
TP_GENERATE_FAILED = "GENERATE_FAILED"
TP_READY = "READY"


@dataclass(frozen=True)
class TradePlanDraftOutcome:
    """准备 TradePlan 草稿的结构化结果（调用方按 status 渲染/确认/落盘）。

    - DATA_UNAVAILABLE：无 ATR/MA 等技术面，未生成草稿（与 CLI 跳过语义一致）
    - GENERATE_FAILED：generator 抛错，未生成
    - READY：plan/meta 可用；是否 attach 由调用方 Y/n 或 chat confirm 决定
    """
    status: str
    stock_code: str
    stock_name: str = ""
    entry_price: float = 0.0
    ratio: float = 0.0
    plan: Optional[TradePlan] = None
    meta: dict = field(default_factory=dict)
    benzong_grade: Optional[str] = None
    atr_str: str = "?"
    ma20_str: str = "?"
    ma60_str: str = "?"
    data_error: str = ""
    score_failed: bool = False
    score_normalized: Optional[float] = None
    industry_prosperity: Optional[float] = None
    overall_confidence: Optional[float] = None
    industry_sources: Tuple[str, ...] = ()
    generate_error: str = ""


def industry_sources_from_score(bz_score: Any) -> Tuple[str, ...]:
    """从笨总评分对象提取行业景气来源列表（供 CLI 组 rich 行 / chat 组纯文本）。"""
    try:
        meta = (getattr(bz_score, "dimensions_meta", None) or {}).get("industry_prosperity") or {}
        srcs = meta.get("sources") or []
        return tuple(str(s) for s in srcs[:4])
    except Exception as e:
        logger.debug(f"行业景气来源摘要生成失败(不影响输出): {e}")
        return ()


def prepare_trade_plan_draft(
    stock_code: str,
    stock_name: str,
    entry_price: float,
    ratio: float,
    *,
    get_stock_data: Optional[Callable[[str], Any]] = None,
    auto_score_fn: Optional[Callable[..., Any]] = None,
    get_rag_service: Optional[Callable[[], Any]] = None,
    generator_factory: Optional[Callable[..., Any]] = None,
    skip_benzong: bool = False,
    benzong_grade: Optional[str] = None,
    industry_prosperity: Optional[float] = None,
    is_self_reliance: bool = False,
    flagbearer_code: Optional[str] = None,
    penetration_stage: Optional[str] = None,
) -> TradePlanDraftOutcome:
    """编排生成 TradePlan 草稿。默认走真实依赖；测试可注入桩。

    skip_benzong=True 时不跑 auto_score，使用传入的 grade/prosperity/旗手等
    （供 pos plan --update 沿用旧 mode 的路径）。
    """
    display = stock_name or stock_code

    def _base(**kw):
        return TradePlanDraftOutcome(
            stock_code=stock_code, stock_name=display,
            entry_price=entry_price, ratio=ratio, **kw)

    if get_stock_data is None:
        from src.data.akshare_client import get_stock_data as _gsd
        get_stock_data = _gsd

    stock_data = None
    data_error = ""
    atr_str = ma20_str = ma60_str = "?"
    try:
        stock_data = get_stock_data(stock_code)
        if stock_data:
            atr_str = f"{stock_data.atr_14:.2f}" if stock_data.atr_14 else "?"
            ma20_str = f"{stock_data.ma20:.2f}" if stock_data.ma20 else "?"
            ma60_str = f"{stock_data.ma60:.2f}" if stock_data.ma60 else "?"
    except Exception as e:
        data_error = f"{type(e).__name__}: {str(e)[:100]}"
        logger.warning(f"TradePlan 数据拉取失败: {e}")

    if stock_data is None:
        return _base(status=TP_DATA_UNAVAILABLE, data_error=data_error)

    score_failed = False
    score_normalized = None
    overall_confidence = None
    industry_sources: Tuple[str, ...] = ()
    if not skip_benzong:
        if auto_score_fn is None:
            from src.core.benzong.auto_scorer import auto_score as auto_score_fn
        try:
            bz = auto_score_fn(stock_code, name=display)
            benzong_grade = bz.score.effective_grade()
            industry_prosperity = bz.score.industry_prosperity
            is_self_reliance = bz.is_self_reliance
            flagbearer_code = bz.flagbearer_code
            penetration_stage = bz.penetration_stage
            score_normalized = bz.score.normalized_score()
            overall_confidence = bz.overall_confidence
            industry_sources = industry_sources_from_score(bz.score)
        except Exception as e:
            score_failed = True
            logger.warning(f"建仓笨总评分失败: {e}")
            benzong_grade = None
            industry_prosperity = None

    rag_service = None
    if get_rag_service is None:
        def _default_rag():
            from src.rag.service import get_rag_service as _gr
            return _gr()
        get_rag_service = _default_rag
    try:
        rag_service = get_rag_service()
    except Exception:
        rag_service = None

    if generator_factory is None:
        from src.core.trade_plan import TradePlanGenerator

        def generator_factory(rag):
            return TradePlanGenerator(rag_service=rag, ai_modifier=None)

    try:
        gen = generator_factory(rag_service)
        plan, meta = gen.generate(
            stock_code=stock_code, stock_name=display,
            entry_price=entry_price, ratio=ratio, stock_data=stock_data,
            benzong_grade=benzong_grade, industry_prosperity=industry_prosperity,
            is_self_reliance=is_self_reliance,
            flagbearer_code=flagbearer_code, penetration_stage=penetration_stage,
        )
    except Exception as e:
        logger.error(f"TradePlan 生成失败: {e}")
        return _base(
            status=TP_GENERATE_FAILED,
            atr_str=atr_str, ma20_str=ma20_str, ma60_str=ma60_str,
            score_failed=score_failed, score_normalized=score_normalized,
            industry_prosperity=industry_prosperity,
            overall_confidence=overall_confidence,
            industry_sources=industry_sources,
            benzong_grade=benzong_grade,
            generate_error=str(e),
        )

    return _base(
        status=TP_READY,
        plan=plan,
        meta=meta or {},
        benzong_grade=benzong_grade,
        atr_str=atr_str, ma20_str=ma20_str, ma60_str=ma60_str,
        score_failed=score_failed, score_normalized=score_normalized,
        industry_prosperity=industry_prosperity,
        overall_confidence=overall_confidence,
        industry_sources=industry_sources,
    )
