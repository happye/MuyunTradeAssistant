"""影子差异捕获与报告（plan/fusion 影子阶段前置，ROLLOUT §1/§4）。

职责：在**不改主结论、不回写持仓**的前提下，对每次持仓分析额外用周期决策表
（fusion_mid_v1 / fusion_long_v1，F5 evaluate_horizon）出对照包，差异落账
~/.muyun/shadow_diff.jsonl，供 `shadow` 命令按原因分组观察——DESIGN 原文：
「shadow 运行新策略但不改用户行动卡主结论、不回写实际持仓；差异报告标记原因，
不能只比较总收益」。

映射规则版本 SHADOW_DERIVATION_VERSION（改动映射必须 bump 并在报告留版本）。
信息集标签 = rule_bz_proxy（VALIDATION §3：影子对照属规则代理信息集，非 AI 前瞻）。

facts 映射规则（v1，显式登记；改动必须 bump 版本）：
- hard_exit_triggered  ← sell_path ∈ {fundamental_alert, top_signal}
  （证据支撑的强制退出通道：ST/业绩预亏硬退出、实控人减持/换手见顶 force-exit——
  PlanGuard 不可压；被压制/被降级场景行1 仍触发，"已验证硬退出不被官僚流程掩盖"）
- technical_exit_triggered ← sell_path ∈ {trend_exit, take_profit_trim, stop_loss_exit,
  stop_loss_trim, time_stop, weak_sell}（预设技术退出，MID 行5）
- entry_condition_met  ← 终态 position_action ∈ {OPEN, ADD}
- research_status      ← packet.research_status 透传（数据缺口/分歧如实进入决策表）
- thesis_status ← **v2**：用户计划（plan2）带 facts_observed（已发生事实）→ VALID；
  无计划/无事实 → UNESTABLISHED（评分/技术信号仍不冒充逻辑）
- budget_available     ← None（组合预算未接，组合信息未知不给精确目标）
- confirmed_ratio      ← 持仓事实 pos.current_ratio
- 不把 legacy mode（气宗/剑宗）映射成 horizon——每个持仓同时评估 MID 与 LONG
  两个包（TASKS F5 硬约束："不用旧 mode 代替 horizon"）

模拟计划语义：shadow 派生的 HorizonPlan.plan_id 带 shadow_ 前缀、accepted_at 取当前
时点（模拟激活以越过行0 激活门——否则全部 REVIEW 无观察价值），但每条记录带
shadow_disclosure 字段且 reason 首条固定标注「shadow 模拟计划（非用户确认，仅对照
观察）」。模拟计划不落盘到用户计划、不影响任何主结论，关闭开关即零写入。
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SHADOW_DERIVATION_VERSION = "shadow_v2"  # v2：优先消费用户 plan2 计划（真判断前提）

SHADOW_STORE_PATH = Path.home() / ".muyun" / "shadow_diff.jsonl"

# 行1：已验证硬退出（证据支撑的强制退出通道——PlanGuard 不可压）
_HARD_EXIT_PATHS = {"fundamental_alert", "top_signal"}
# 行5：预设技术退出（MID）；flat_sell 是空仓标签不入表
_TECHNICAL_EXIT_PATHS = {"trend_exit", "take_profit_trim", "stop_loss_exit",
                         "stop_loss_trim", "time_stop", "weak_sell"}

# 差异原因标签（机器可分组；报告按此聚合——DESIGN：差异报告标记原因）
REASON_HARD_EXIT = "hard_exit_divergence"      # fusion 要求退出而 legacy 未退出
REASON_THESIS = "thesis_unestablished"         # fusion 无投资逻辑记录 → REVIEW/WAIT
REASON_RESEARCH = "research_gap"               # 证据缺口/分歧 → 冻结新增风险
REASON_ACTION_FLIP = "action_flip"             # 动作族翻转（非硬退出/研究差异所致）
REASON_PLAN_DRAFT = "plan_draft_not_activated"  # 用户计划未激活 → 只产出复核（行0 激活门）
REASON_AGREE = "agree"                         # 动作族一致


class ShadowDiffRecord(BaseModel):
    """一条影子对照记录（追加式 JSONL，不 prune）。"""

    security_id: str
    as_of: str = Field(description="捕获时点（带时区 ISO）")
    source: str = Field(default="", description="触发来源 l/la/chat")
    legacy_action: str = Field(description="legacy 终态 position_action")
    legacy_desired: str = Field(description="legacy 终态 desired_action（packet 口径）")
    fusion_mid_action: str = Field(description="fusion_mid_v1 决策表动作")
    fusion_long_action: str = Field(description="fusion_long_v1 决策表动作")
    fusion_mid_reason: str = Field(default="", description="MID 决策表行 reason（首条）")
    fusion_long_reason: str = Field(default="", description="LONG 决策表行 reason（首条）")
    sell_path: Optional[str] = None
    hard_exit: bool = False
    technical_exit: bool = False
    research_status: str = ""
    thesis_status: str = "UNESTABLISHED"
    plan_source: str = Field(default="simulated",
                             description="simulated / user_plan_draft / user_plan_accepted")
    delta_reasons: list[str] = Field(default_factory=list, description="差异原因标签（报告按此聚合）")
    derivation_version: str = SHADOW_DERIVATION_VERSION
    shadow_disclosure: str = "shadow 模拟计划（非用户确认，仅对照观察）"


def _load_capture_switch(config: Optional[dict]) -> bool:
    """读 fusion.shadow_capture 开关（默认开——capture 语义不改任何主结论）。"""
    if config is None:
        try:
            from src.cli.main import load_config
            config = load_config()
        except Exception as e:  # 配置读取失败按关闭处理（不捕获数据），并如实留痕
            logger.warning(f"影子捕获开关读取失败，本次不捕获（不影响分析）: {e}")
            return False
    fusion_cfg = (config or {}).get("fusion") or {}
    return bool(fusion_cfg.get("shadow_capture", True))


def _derive_facts(strategy_decision, packet) -> dict:
    """末端 legacy 结果 + 终态包 → HorizonFacts 字段（映射规则见模块 docstring v1）。"""
    sell_path = getattr(strategy_decision, "sell_path", None)
    pos_action = getattr(strategy_decision, "position_action", None)
    pos_action_val = getattr(pos_action, "value", pos_action)
    return {
        "hard_exit_triggered": sell_path in _HARD_EXIT_PATHS,
        "technical_exit_triggered": sell_path in _TECHNICAL_EXIT_PATHS,
        "entry_condition_met": pos_action_val in ("OPEN", "ADD"),
        "research_status": getattr(packet, "research_status", None),
        "thesis_status": None,  # v1 恒 UNESTABLISHED（不拿评分冒充逻辑）
        "budget_available": None,  # 组合预算未接——组合信息未知不给精确目标
    }


def _derive_shadow_plan(horizon, security_id: str, as_of: datetime,
                        trade_plan=None):
    """派生模拟 HorizonPlan（不落用户计划；模拟激活语义见模块 docstring）。"""
    from src.core.decision_policy import POLICY_ID_LONG, POLICY_ID_MID, HorizonPlan
    intent = "未登记投资逻辑（影子对照占位——等待 PlanV2 逻辑记录）"
    legacy_mode = None
    if trade_plan is not None:
        legacy_mode = getattr(trade_plan, "mode", None)
        tp_desc = getattr(trade_plan, "description", None) or ""
        if tp_desc:
            intent = f"由 legacy TradePlan 概述派生: {str(tp_desc)[:60]}"
    return HorizonPlan(
        plan_id=f"shadow_{security_id}_{str(horizon.value).lower()}",
        accepted_at=as_of.isoformat(timespec="seconds"),  # 模拟激活（越过行0；披露字段见下）
        horizon=horizon,
        policy_id=POLICY_ID_MID if horizon.value == "MID" else POLICY_ID_LONG,
        intent=intent,
        required_evidence_refs=[],
        invalidate_if=[],
        legacy_mode=legacy_mode,  # 仅对照字段，不参与 horizon 裁决
    )


def _classify_reasons(facts: dict, mid_packet, long_packet,
                      legacy_desired: str, plan_source: str = "simulated") -> list[str]:
    """差异原因分类（优先级：硬退出 > 研究缺口 > 逻辑未立/计划未激活 > 动作翻转 > 一致）。

    v2 归因（审查 P2：plan_source 感知——真判断不被系统性归入「逻辑未立」桶）：
    - simulated：REVIEW/WAIT 且无逻辑 → thesis_unestablished（模拟无计划，如实）
    - user_plan_draft：REVIEW/WAIT → plan_draft_not_activated（行0 激活门，等用户确认）
    - user_plan_accepted：不打逻辑类标签（已登记逻辑；REVIEW 多来自研究缺口，另计）
    """
    reasons: list[str] = []
    legacy_exit_family = legacy_desired in ("EXIT", "REDUCE")
    mid_exit_family = mid_packet.desired_action.value in ("EXIT", "REDUCE")
    long_exit_family = long_packet.desired_action.value in ("EXIT", "REDUCE")

    if facts["hard_exit_triggered"] and not legacy_exit_family:
        reasons.append(REASON_HARD_EXIT)
    research_status = getattr(facts["research_status"], "value", facts["research_status"])
    if research_status in ("INCOMPLETE", "CONFLICTED"):
        reasons.append(REASON_RESEARCH)
    reviewish = (mid_packet.desired_action.value in ("REVIEW", "WAIT") or
                 long_packet.desired_action.value in ("REVIEW", "WAIT"))
    if reviewish and plan_source == "simulated":
        reasons.append(REASON_THESIS)
    elif reviewish and plan_source == "user_plan_draft":
        reasons.append(REASON_PLAN_DRAFT)
    agreed = (legacy_exit_family == mid_exit_family == long_exit_family) or \
             (mid_packet.desired_action.value == legacy_desired and
              long_packet.desired_action.value == legacy_desired)
    if not reasons and agreed:
        reasons.append(REASON_AGREE)
    elif not reasons:
        reasons.append(REASON_ACTION_FLIP)
    return reasons


def capture_shadow(decision_result, strategy_decision, execution_eval, pos,
                   *, packet=None, source: str = "", config: Optional[dict] = None,
                   store_path: Optional[Path] = None,
                   plans_store=None) -> Optional[ShadowDiffRecord]:
    """一次持仓分析的影子对照捕获（纯读 + 追加一条 JSONL；异常如实告警不吞）。

    plans_store: HorizonPlanStore 注入（测试密闭用；None=读真实 ~/.muyun/horizon_plans.json）。"""
    if pos is None:
        return None
    if not _load_capture_switch(config):
        return None

    if packet is None:
        from src.core.analysis_service import build_decision_packet
        packet = build_decision_packet(
            decision_result, strategy_decision, execution_eval,
            confirmed_ratio=getattr(pos, "current_ratio", None), source=source or "shadow")

    facts = _derive_facts(strategy_decision, packet)
    from src.core.decision_contract import (
        Horizon,
        ResearchStatus,
        ThesisStatus,
    )
    from src.core.decision_policy import HorizonFacts, evaluate_horizon

    as_of = datetime.now().astimezone()
    security_id = packet.security_id

    # v2：用户 plan2 计划优先（每股每周期一份——mid/long 可各立）。
    # 已激活计划走真决策表；草稿按行0 只复核；facts 只作用于其所在周期。
    user_plans: dict = {}
    try:
        store = plans_store
        if store is None:
            from src.data.horizon_plans import HorizonPlanStore
            store = HorizonPlanStore()
        for h in ("MID", "LONG"):
            user_plans[h] = store.get(security_id, h)
    except Exception as e:
        logger.debug(f"PlanV2 读取失败（按无计划处理）: {e}")
    has_user_plan = any(p is not None for p in user_plans.values())
    plan_source = ("user_plan_accepted" if any(p is not None and p.activated
                                               for p in user_plans.values())
                   else "user_plan_draft" if has_user_plan else "simulated")

    hf_kwargs = dict(
        research_status=facts["research_status"] or ResearchStatus.INCOMPLETE,
        hard_exit_triggered=facts["hard_exit_triggered"],
        hard_exit_detail=f"sell_path={getattr(strategy_decision, 'sell_path', None)}",
        technical_exit_triggered=facts["technical_exit_triggered"],
        entry_condition_met=facts["entry_condition_met"],
        budget_available=None,
    )

    horizon_packets: list = []
    confirmed_ratio = getattr(pos, "current_ratio", None)
    for horizon in (Horizon.MID, Horizon.LONG):
        up = user_plans.get(horizon.value)
        hf = dict(hf_kwargs)
        # 事实只作用于用户计划所在的周期——不把一份计划的逻辑事实泄漏到另一周期
        hf["thesis_status"] = (ThesisStatus.VALID
                               if (up is not None and list(getattr(up, "facts_observed", []) or []))
                               else ThesisStatus.UNESTABLISHED)
        plan = up if up is not None else _derive_shadow_plan(
            horizon, security_id, as_of, trade_plan=getattr(pos, "trade_plan", None))
        horizon_packets.append(
            evaluate_horizon(plan, HorizonFacts(**hf), security_id,
                             confirmed_ratio=confirmed_ratio))
    mid_packet, long_packet = horizon_packets[0], horizon_packets[1]

    legacy_desired = packet.desired_action.value
    delta_reasons = _classify_reasons(facts, mid_packet, long_packet, legacy_desired,
                                      plan_source)
    legacy_pos_action = getattr(strategy_decision, "position_action", None)
    legacy_pos_action_val = getattr(legacy_pos_action, "value", legacy_pos_action) or ""
    record = ShadowDiffRecord(
        security_id=security_id,
        as_of=as_of.isoformat(timespec="seconds"),
        source=source,
        legacy_action=str(legacy_pos_action_val),
        legacy_desired=legacy_desired,
        fusion_mid_action=mid_packet.desired_action.value,
        fusion_long_action=long_packet.desired_action.value,
        fusion_mid_reason=(mid_packet.reason_codes[0] if mid_packet.reason_codes else ""),
        fusion_long_reason=(long_packet.reason_codes[0] if long_packet.reason_codes else ""),
        sell_path=getattr(strategy_decision, "sell_path", None),
        hard_exit=facts["hard_exit_triggered"],
        technical_exit=facts["technical_exit_triggered"],
        research_status=getattr(facts["research_status"], "value", "") if facts["research_status"] else "",
        thesis_status=("VALID" if any(
            p is not None and list(getattr(p, "facts_observed", []) or [])
            for p in user_plans.values()) else "UNESTABLISHED"),
        plan_source=plan_source,
        shadow_disclosure=("对照评估（用户已确认计划——真判断）" if plan_source == "user_plan_accepted"
                           else "对照评估（草稿计划未激活——只产出复核）" if plan_source == "user_plan_draft"
                           else "shadow 模拟计划（非用户确认，仅对照观察）"),
        delta_reasons=delta_reasons,
    )
    store = Path(store_path) if store_path else SHADOW_STORE_PATH
    _append_record(store, record)
    return record


def _append_record(store: Path, record: ShadowDiffRecord) -> bool:
    """追加一条记录；同股同分钟幂等跳过。返回是否实际写入。"""
    store.parent.mkdir(parents=True, exist_ok=True)
    minute_key = (record.security_id, record.as_of[:16])
    if store.exists():
        with store.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    old = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 坏行隔离（G14：不因坏行丢弃既有记录）
                if (old.get("security_id"), str(old.get("as_of", ""))[:16]) == minute_key:
                    return False
    with store.open("a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")
    return True


def build_shadow_report(store_path: Optional[Path] = None, days: int = 7) -> dict:
    """影子差异聚合（近 N 天）——分原因计数与差异率，不比较总收益（DESIGN 硬要求）。"""
    store = Path(store_path) if store_path else SHADOW_STORE_PATH
    if not store.exists():
        return {"total": 0, "stocks": 0, "since": None, "by_reason": {},
                "records": [], "store": str(store)}
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    by_reason: dict[str, int] = {}
    records = []
    stocks: set[str] = set()
    with store.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(r.get("as_of", "")) < cutoff:
                continue
            records.append(r)
            stocks.add(r.get("security_id", ""))
            for tag in r.get("delta_reasons", []) or []:
                by_reason[tag] = by_reason.get(tag, 0) + 1
    total = len(records)
    non_agree = sum(1 for r in records if REASON_AGREE not in (r.get("delta_reasons") or []))
    return {
        "total": total,
        "stocks": len(stocks),
        "since": cutoff,
        "by_reason": by_reason,
        "diff_rate": (non_agree / total) if total else 0.0,
        "records": records[-30:],
        "store": str(store),
    }


_REASON_CN = {
    REASON_HARD_EXIT: "硬退出分歧（fusion 要求退出而 legacy 未退出）",
    REASON_THESIS: "fusion 无投资逻辑记录 → REVIEW/WAIT",
    REASON_PLAN_DRAFT: "计划已立但未激活 → 只产出复核（plan2 accept 激活）",
    REASON_RESEARCH: "证据缺口/分歧 → 冻结新增风险",
    REASON_ACTION_FLIP: "动作族翻转",
    REASON_AGREE: "动作一致",
}


_ACTION_CN = {
    "OPEN": "建仓", "ADD": "加仓", "REDUCE": "减仓", "EXIT": "清仓退出",
    "HOLD": "继续持有", "WAIT": "等条件", "REVIEW": "需人工复核",
}


def _action_cn(action: str) -> str:
    """动作英文枚举 → 人话（ISS-106：用户走查——报告不能全是英文）。"""
    return _ACTION_CN.get(action, action)


def render_shadow_report(report: dict) -> str:
    """报告渲染（rich markup，供 console.print；定位说明前置——ISS-106）。"""
    if not report["total"]:
        return (
            "[bold cyan]🔍 影子对照报告[/bold cyan]\n"
            "  [dim]这是干什么的：你分析持仓时，系统会用一套还在试验的新决策思路把这只股"
            "也评一遍，把两边的差异记下来——你的分析结论一个字都不会变。攒几周后看新思路"
            "和旧思路常在哪里意见不一致，用真实使用数据决定它能不能转正。[/dim]\n"
            f"  近期无影子记录（捕获开关 fusion.shadow_capture，账本 {report['store']}）\n"
            "  持仓股经 l/la/chat 分析时自动累积对比，无需手动触发"
        )
    lines = ["[bold cyan]🔍 影子对照报告[/bold cyan]",
             "  [dim]shadow 模拟计划（非用户确认，仅对照观察）——不改变任何主结论[/dim]"]
    # plan_source 分组计数（审查 P1：真判断不被报告头盖成「模拟」）
    n_accepted = sum(1 for r in report["records"]
                     if r.get("plan_source") == "user_plan_accepted")
    n_draft = sum(1 for r in report["records"]
                  if r.get("plan_source") == "user_plan_draft")
    n_sim = report["total"] - n_accepted - n_draft
    src_parts = []
    if n_accepted:
        src_parts.append(f"真计划对照 {n_accepted} 条（你的 plan2 已激活——真判断）")
    if n_draft:
        src_parts.append(f"草稿计划 {n_draft} 条（plan2 accept 后出真判断）")
    if n_sim:
        src_parts.append(f"模拟 {n_sim} 条")
    lines.append("  记录来源: " + "｜".join(src_parts))
    lines.append(f"  近 7 天捕获 {report['total']} 条（{report['stocks']} 只持仓）"
                 f"｜差异率 {report['diff_rate']:.0%}（非一致占比）")
    reason_labels = {"hard_exit_divergence": _REASON_CN[REASON_HARD_EXIT],
                     "thesis_unestablished": _REASON_CN[REASON_THESIS],
                     "plan_draft_not_activated": _REASON_CN[REASON_PLAN_DRAFT],
                     "research_gap": _REASON_CN[REASON_RESEARCH],
                     "action_flip": _REASON_CN[REASON_ACTION_FLIP],
                     "agree": _REASON_CN[REASON_AGREE]}
    for tag, label in reason_labels.items():
        n = report["by_reason"].get(tag, 0)
        if n:
            lines.append(f"  · {label}: {n}")
    lines.append("  最近记录（旧方法 → 新思路中期/长期 ｜ 差异原因）:")
    for r in report["records"][-8:]:
        tags = "、".join(_REASON_CN.get(t, t) for t in (r.get("delta_reasons") or []))
        lines.append(
            f"   {r['as_of'][:16]} {r['security_id']} "
            f"{_action_cn(r['legacy_desired'])} → "
            f"{_action_cn(r['fusion_mid_action'])}/{_action_cn(r['fusion_long_action'])}"
            f"  ｜ {tags}")
    lines.append(f"  [dim]账本: {report['store']}（追加式）｜ 映射版本 {SHADOW_DERIVATION_VERSION}[/dim]")
    return "\n".join(lines)
