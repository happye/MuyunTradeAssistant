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
- thesis_status        ← 恒 UNESTABLISHED（v1 无投资逻辑记录层——不拿评分/技术信号
  冒充逻辑；真 thesis 待 PlanV2 逻辑记录流程，F7 TODO）
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

SHADOW_DERIVATION_VERSION = "shadow_v1"

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
                      legacy_desired: str) -> list[str]:
    """差异原因分类（优先级：硬退出 > 研究缺口 > 逻辑未立 > 动作翻转 > 一致）。"""
    reasons: list[str] = []
    legacy_exit_family = legacy_desired in ("EXIT", "REDUCE")
    mid_exit_family = mid_packet.desired_action.value in ("EXIT", "REDUCE")
    long_exit_family = long_packet.desired_action.value in ("EXIT", "REDUCE")

    if facts["hard_exit_triggered"] and not legacy_exit_family:
        reasons.append(REASON_HARD_EXIT)
    research_status = getattr(facts["research_status"], "value", facts["research_status"])
    if research_status in ("INCOMPLETE", "CONFLICTED"):
        reasons.append(REASON_RESEARCH)
    if facts["thesis_status"] is None:  # v1 恒 UNESTABLISHED
        if mid_packet.desired_action.value in ("REVIEW", "WAIT") or \
                long_packet.desired_action.value in ("REVIEW", "WAIT"):
            reasons.append(REASON_THESIS)
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
                   store_path: Optional[Path] = None) -> Optional[ShadowDiffRecord]:
    """一次持仓分析的影子对照捕获（纯读 + 追加一条 JSONL；异常如实告警不吞）。"""
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
    hf_kwargs = dict(
        research_status=facts["research_status"] or ResearchStatus.INCOMPLETE,
        thesis_status=ThesisStatus.UNESTABLISHED,
        hard_exit_triggered=facts["hard_exit_triggered"],
        hard_exit_detail=f"sell_path={getattr(strategy_decision, 'sell_path', None)}",
        technical_exit_triggered=facts["technical_exit_triggered"],
        entry_condition_met=facts["entry_condition_met"],
        budget_available=None,
    )

    horizon_packets: list = []
    confirmed_ratio = getattr(pos, "current_ratio", None)
    for horizon in (Horizon.MID, Horizon.LONG):
        plan = _derive_shadow_plan(horizon, security_id, as_of,
                                   trade_plan=getattr(pos, "trade_plan", None))
        horizon_packets.append(
            evaluate_horizon(plan, HorizonFacts(**hf_kwargs), security_id,
                             confirmed_ratio=confirmed_ratio))
    mid_packet, long_packet = horizon_packets[0], horizon_packets[1]

    legacy_desired = packet.desired_action.value
    delta_reasons = _classify_reasons(facts, mid_packet, long_packet, legacy_desired)
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
        thesis_status="UNESTABLISHED",
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
    REASON_RESEARCH: "证据缺口/分歧 → 冻结新增风险",
    REASON_ACTION_FLIP: "动作族翻转",
    REASON_AGREE: "动作一致",
}


def render_shadow_report(report: dict) -> str:
    """报告渲染（rich markup，供 console.print；仅观察用途声明前置）。"""
    if not report["total"]:
        return (
            "[bold cyan]🔍 影子对照报告[/bold cyan]\n"
            f"  近期无影子记录（捕获开关 fusion.shadow_capture，账本 {report['store']}）\n"
            "  持仓股经 l/la/chat 分析时自动累积对比：legacy 终态 vs fusion_mid/long 决策表"
        )
    lines = ["[bold cyan]🔍 影子对照报告[/bold cyan]",
             "  [dim]shadow 模拟计划（非用户确认，仅对照观察）——不改变任何主结论[/dim]"]
    lines.append(f"  近 7 天捕获 {report['total']} 条（{report['stocks']} 只持仓）"
                 f"｜差异率 {report['diff_rate']:.0%}（非 agree 占比）")
    reason_labels = {"hard_exit_divergence": _REASON_CN[REASON_HARD_EXIT],
                     "thesis_unestablished": _REASON_CN[REASON_THESIS],
                     "research_gap": _REASON_CN[REASON_RESEARCH],
                     "action_flip": _REASON_CN[REASON_ACTION_FLIP],
                     "agree": _REASON_CN[REASON_AGREE]}
    for tag, label in reason_labels.items():
        n = report["by_reason"].get(tag, 0)
        if n:
            lines.append(f"  · {label}: {n}")
    lines.append("  最近记录（legacy → MID/LONG ｜ 原因）:")
    for r in report["records"][-8:]:
        lines.append(
            f"   {r['as_of'][:16]} {r['security_id']} {r['legacy_desired']} → "
            f"{r['fusion_mid_action']}/{r['fusion_long_action']}"
            f"  ｜ {'; '.join(r.get('delta_reasons', []))}")
    lines.append(f"  [dim]账本: {report['store']}（追加式）｜ 映射版本 {SHADOW_DERIVATION_VERSION}[/dim]")
    return "\n".join(lines)
