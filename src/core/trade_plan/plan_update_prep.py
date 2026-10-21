"""T04-D：pos plan 生成/更新编排服务（无打印、无 input、不写盘）。

从 CLI `_generate_or_update_plan` 抽出：拉行情 →（更新则沿用旧 mode/旗手，
新生成则笨总评分）→ TradePlanGenerator.generate → 更新预览合并。
调用方负责 rich 展示、Y/n 与 attach_plan。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional, Tuple

from src.core.trade_plan.draft_prep import (
    TP_DATA_UNAVAILABLE,
    TP_GENERATE_FAILED,
    TP_READY,
    prepare_trade_plan_draft,
)
from src.data.models import TradePlan

logger = logging.getLogger(__name__)

PU_NO_POSITION = "NO_POSITION"
PU_DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
PU_GENERATE_FAILED = "GENERATE_FAILED"
PU_READY_NEW = "READY_NEW"
PU_READY_UPDATE = "READY_UPDATE"

_MODE_TO_GRADE = {"qizong": "A", "jianzong": "B"}


@dataclass(frozen=True)
class PlanGenUpdateOutcome:
    """pos plan 生成/更新的结构化结果（调用方按 status 渲染/确认/落盘）。

    - NO_POSITION：无持仓
    - DATA_UNAVAILABLE：无技术面，未生成
    - GENERATE_FAILED：generator 抛错
    - READY_NEW：新计划可直接 attach（无 Y/n）
    - READY_UPDATE：已合并预览止损的提案；需调用方 Y/n 后
      accept_plan_update 再 attach；不写盘、不改原 plan
    """
    status: str
    stock_code: str
    action_label: str = ""
    stock_name: str = ""
    plan: Optional[TradePlan] = None
    meta: dict = field(default_factory=dict)
    old_plan: Optional[TradePlan] = None
    old_stop: Optional[float] = None
    proposed_stop: Optional[float] = None
    atr_str: str = "?"
    price: Optional[float] = None
    benzong_grade: Optional[str] = None
    industry_prosperity: Optional[float] = None
    score_normalized: Optional[float] = None
    overall_confidence: Optional[float] = None
    industry_sources: Tuple[str, ...] = ()
    score_failed: bool = False
    reused_mode: Optional[str] = None
    prosperity_from_cache: bool = False
    is_update_path: bool = False
    prosperity_note: str = ""  # cache | unknown | bare | ""
    data_error: str = ""
    generate_error: str = ""


def accept_plan_update(
    plan: TradePlan,
    old_stop: float,
    proposed_stop: float,
    *,
    now: Optional[datetime] = None,
) -> TradePlan:
    """用户明确接受更新预览后追加 adjustments（不写盘）。"""
    ts = (now or datetime.now()).strftime("%Y-%m-%d")
    plan.adjustments.append({
        "date": ts,
        "field": "current_stop",
        "old": old_stop,
        "new": proposed_stop,
        "source": "user",
        "reason": "pos plan 更新预览明确接受；原模式/期限/初始止损保持",
    })
    return plan


def _cached_industry_prosperity(
    stock_code: str,
    *,
    bz_cache_get: Optional[Callable[..., Any]] = None,
    today: Optional[str] = None,
) -> Tuple[Optional[float], str]:
    """更新路径取今日缓存景气度（不重跑评分）。

    返回 (score|None, note)：note 为 cache / unknown / bare，对齐旧 CLI 三条文案。
    """
    if bz_cache_get is None:
        try:
            from src.core.benzong import cache as bz_cache
            bz_cache_get = bz_cache.get
        except Exception:
            return None, "bare"
    day = today or datetime.now().strftime("%Y-%m-%d")
    try:
        cached_ip = bz_cache_get(stock_code, day, "industry_prosperity")
        if cached_ip and "score" in cached_ip:
            return cached_ip["score"], "cache"
        return None, "unknown"
    except Exception as e:
        logger.debug(f"更新路径读取景气度缓存失败({stock_code}): {e}")
        return None, "bare"


def prepare_generate_or_update_plan(
    pm: Any,
    stock_code: str,
    update: bool = False,
    *,
    get_stock_data: Optional[Callable[[str], Any]] = None,
    auto_score_fn: Optional[Callable[..., Any]] = None,
    get_rag_service: Optional[Callable[[], Any]] = None,
    generator_factory: Optional[Callable[..., Any]] = None,
    bz_cache_get: Optional[Callable[..., Any]] = None,
    today: Optional[str] = None,
) -> PlanGenUpdateOutcome:
    """编排生成或更新 TradePlan 提案。默认走真实依赖；测试可注入桩。

    无打印、无 input、不写盘。更新且有旧计划时合并预览止损（单向上移），
    返回 READY_UPDATE；是否接受由调用方决定。
    """
    pos = pm.get_position(stock_code)
    if pos is None:
        return PlanGenUpdateOutcome(status=PU_NO_POSITION, stock_code=stock_code)

    old_plan = pos.trade_plan
    is_update = bool(update and old_plan)
    action_label = "更新" if is_update else "生成"
    display = pos.stock_name or stock_code

    if get_stock_data is None:
        from src.data.akshare_client import get_stock_data as _gsd
        get_stock_data = _gsd

    stock_data = None
    data_error = ""
    atr_str = "?"
    try:
        stock_data = get_stock_data(stock_code)
        if stock_data is not None:
            atr_str = f"{stock_data.atr_14:.2f}" if getattr(stock_data, "atr_14", None) else "?"
    except Exception as e:
        data_error = f"{type(e).__name__}: {str(e)[:100]}"
        logger.warning(f"TradePlan {action_label} 数据拉取失败: {e}")

    if stock_data is None:
        return PlanGenUpdateOutcome(
            status=PU_DATA_UNAVAILABLE,
            stock_code=stock_code,
            action_label=action_label,
            stock_name=display,
            data_error=data_error,
            atr_str=atr_str,
            old_plan=old_plan,
        )

    entry_price = pos.entry_price if pos.entry_price else stock_data.price
    ratio = pos.current_ratio if pos.current_ratio else 0.20
    price = getattr(stock_data, "price", None)

    # 同一份 stock_data，避免 draft 二次拉取
    def _cached_gsd(_code: str):
        return stock_data

    skip_benzong = False
    benzong_grade = None
    industry_prosperity = None
    is_self_reliance = False
    flagbearer_code = None
    penetration_stage = None
    reused_mode = None
    prosperity_from_cache = False

    prosperity_note = ""
    if is_update:
        skip_benzong = True
        reused_mode = old_plan.mode
        benzong_grade = _MODE_TO_GRADE.get(old_plan.mode)
        flagbearer_code = old_plan.flagbearer_code
        penetration_stage = old_plan.penetration_stage
        industry_prosperity, prosperity_note = _cached_industry_prosperity(
            stock_code, bz_cache_get=bz_cache_get, today=today)
        prosperity_from_cache = prosperity_note == "cache"

    draft = prepare_trade_plan_draft(
        stock_code,
        display,
        entry_price,
        ratio,
        get_stock_data=_cached_gsd,
        auto_score_fn=auto_score_fn,
        get_rag_service=get_rag_service,
        generator_factory=generator_factory,
        skip_benzong=skip_benzong,
        benzong_grade=benzong_grade,
        industry_prosperity=industry_prosperity,
        is_self_reliance=is_self_reliance,
        flagbearer_code=flagbearer_code,
        penetration_stage=penetration_stage,
    )

    base_kw = dict(
        stock_code=stock_code,
        action_label=action_label,
        stock_name=display,
        atr_str=draft.atr_str or atr_str,
        price=price,
        benzong_grade=draft.benzong_grade,
        industry_prosperity=draft.industry_prosperity,
        score_normalized=draft.score_normalized,
        overall_confidence=draft.overall_confidence,
        industry_sources=draft.industry_sources,
        score_failed=draft.score_failed,
        reused_mode=reused_mode,
        prosperity_from_cache=prosperity_from_cache,
        is_update_path=is_update,
        prosperity_note=prosperity_note,
        old_plan=old_plan,
        data_error=draft.data_error or data_error,
        generate_error=draft.generate_error,
    )

    if draft.status == TP_DATA_UNAVAILABLE:
        return PlanGenUpdateOutcome(status=PU_DATA_UNAVAILABLE, **base_kw)
    if draft.status == TP_GENERATE_FAILED:
        return PlanGenUpdateOutcome(status=PU_GENERATE_FAILED, **base_kw)
    if draft.status != TP_READY or draft.plan is None:
        return PlanGenUpdateOutcome(
            status=PU_GENERATE_FAILED,
            generate_error=draft.generate_error or "unknown draft status",
            **{k: v for k, v in base_kw.items() if k != "generate_error"},
        )

    if is_update:
        old_stop = old_plan.current_stop
        proposed_stop = max(old_plan.current_stop, draft.plan.current_stop)
        plan = old_plan.model_copy(deep=True)
        plan.current_stop = proposed_stop
        return PlanGenUpdateOutcome(
            status=PU_READY_UPDATE,
            plan=plan,
            meta=draft.meta or {},
            old_stop=old_stop,
            proposed_stop=proposed_stop,
            **base_kw,
        )

    return PlanGenUpdateOutcome(
        status=PU_READY_NEW,
        plan=draft.plan,
        meta=draft.meta or {},
        **base_kw,
    )
