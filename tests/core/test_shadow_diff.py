"""影子差异捕获回归测试（plan/fusion 影子阶段前置，src/core/shadow_diff.py）

锁死语义：
1. facts 映射 v1：hard_exit（fundamental_alert/top_signal）/技术退出/入场条件/
   research_status 透传；thesis 恒 UNESTABLISHED（不拿评分冒充逻辑）
2. 行1 硬退出先于激活门：legacy 被 PlanGuard 压制成 HOLD 时 fusion 双周期仍 EXIT
   ——EXIT 意图不被影子流程吞掉（G02 对照观察的本体）
3. 不把 legacy mode 映射成 horizon：每个持仓同时评估 MID 与 LONG（policy_id 断言）
4. 记录 schema：derivation_version/shadow_disclosure/分周期 reason 全落
5. 存储：追加式 JSONL，同股同分钟幂等；坏行隔离不崩
6. 开关：fusion.shadow_capture=false / 读取失败 / 无持仓 → 零写入
7. 报告：分原因聚合 + 差异率，无总收益比较（DESIGN 硬要求）

纯内存+临时文件测试，无网络。跑法：pytest tests/core/test_shadow_diff.py -q
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.analysis_service import build_decision_packet
from src.core.execution_layer import ExecutionEvaluation
from src.core.shadow_diff import (
    REASON_AGREE,
    REASON_HARD_EXIT,
    REASON_RESEARCH,
    REASON_THESIS,
    SHADOW_DERIVATION_VERSION,
    ShadowDiffRecord,
    _append_record,
    build_shadow_report,
    capture_shadow,
    render_shadow_report,
)
from src.data.models import (
    PositionAction, SignalType, StrategyDecision, StrategyState, TradeLifecycle,
)

_ON = {"fusion": {"shadow_capture": True}}
_OFF = {"fusion": {"shadow_capture": False}}


def _dr(decision="HOLD", score=0.5, warnings=None):
    return SimpleNamespace(
        decision=SimpleNamespace(value=decision), score=score,
        stock=SimpleNamespace(stock_code="601318"),
        warnings=warnings or [],
    )


def _sd(decision="HOLD", pos_action="HOLD_POSITION", ratio=0.0, sell_path=None):
    return StrategyDecision(
        decision=SignalType(decision),
        position_action=PositionAction(pos_action),
        position_ratio=ratio,
        sell_path=sell_path,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        strategy_reasons=["策略理由"],
        divergence=None,
        new_state=StrategyState(),
    )


def _eval(blocked=False) -> ExecutionEvaluation:
    return ExecutionEvaluation(
        original_action=PositionAction.HOLD_POSITION,
        effective_action=PositionAction.HOLD_POSITION,
        blocked=blocked, block_reason="跌停" if blocked else "",
        slippage_pct=0.0, impact_cost_pct=0.0, total_cost_pct=0.0,
    )


def _pos(code="601318", ratio=0.2):
    return SimpleNamespace(stock_code=code, current_ratio=ratio,
                           trade_plan=None, stock_name="测试股")


def _packet(dr=None, sd=None, ev=None, confirmed=0.2):
    return build_decision_packet(
        dr or _dr(), sd or _sd(), ev or _eval(),
        confirmed_ratio=confirmed, source="test")


_UNSET = object()


def _capture(tmp_path, sd=None, dr=None, pos=_UNSET, config=_ON, packet=None):
    return capture_shadow(
        dr or _dr(), sd or _sd(), _eval(),
        _pos() if pos is _UNSET else pos,
        packet=packet or _packet(dr=dr, sd=sd),
        source="test", config=config,
        store_path=tmp_path / "shadow.jsonl")


# ── 1. facts 映射 ───────────────────────────────────────────

def test_hard_exit_maps_exit_in_both_horizons(tmp_path):
    """G02 对照本体：legacy 被压制说 HOLD，fusion 双周期仍 EXIT（行1 先于激活门）。"""
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="fundamental_alert"))
    assert rec.fusion_mid_action == "EXIT" and rec.fusion_long_action == "EXIT"
    assert rec.hard_exit is True
    assert REASON_HARD_EXIT in rec.delta_reasons
    assert "决策表行1" in rec.fusion_mid_reason


def test_top_signal_is_hard_exit(tmp_path):
    rec = _capture(tmp_path, sd=_sd("SELL", "CLOSE_ALL", sell_path="top_signal"))
    assert rec.hard_exit is True
    # legacy 已是退出族 → 无硬退出分歧；动作一致
    assert rec.fusion_mid_action == "EXIT"
    assert rec.delta_reasons == [REASON_AGREE]


def test_technical_exit_with_unestablished_thesis_gives_review(tmp_path):
    """trend_exit 属技术退出，但 v1 无投资逻辑 → 决策表落行10 REVIEW（不冒充 VALID）。"""
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="trend_exit"))
    assert rec.technical_exit is True
    assert rec.fusion_mid_action == "REVIEW"
    assert REASON_THESIS in rec.delta_reasons


def test_weak_sell_is_technical_exit_but_suppressed_legacy_holds(tmp_path):
    """weak_sell 被 PlanGuard 压制成 HOLD 的场景：影子记录 legacy HOLD vs fusion REVIEW
    （v1 无逻辑，不升级为 REDUCE——真 thesis 接入后此差异点变化即观察目标）。"""
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="weak_sell"))
    assert rec.technical_exit is True
    assert rec.legacy_desired == "HOLD"
    assert rec.fusion_mid_action == "REVIEW"


def test_flat_sell_not_technical_exit(tmp_path):
    rec = _capture(tmp_path, sd=_sd("HOLD", "HOLD_POSITION", sell_path="flat_sell"))
    assert rec.technical_exit is False


def test_entry_condition_from_open_add(tmp_path):
    rec = _capture(tmp_path, dr=_dr("BUY"), sd=_sd("BUY", "ADD", 0.3))
    # 行6 需 thesis VALID——v1 逻辑未立 → 落行10 REVIEW（不加仓）
    assert rec.fusion_mid_action == "REVIEW"
    assert REASON_THESIS in rec.delta_reasons
    assert rec.fusion_mid_reason  # reason 非空


def test_research_gap_reason(tmp_path):
    dr = _dr(warnings=["新闻获取失败"])
    rec = _capture(tmp_path, dr=dr)
    assert rec.research_status == "INCOMPLETE"
    assert REASON_RESEARCH in rec.delta_reasons


# ── 2. 记录 schema 与 horizon 正交 ──────────────────────────

def test_record_schema_and_version(tmp_path):
    rec = _capture(tmp_path)
    assert rec.derivation_version == SHADOW_DERIVATION_VERSION
    assert rec.shadow_disclosure == "shadow 模拟计划（非用户确认，仅对照观察）"
    assert rec.thesis_status == "UNESTABLISHED"
    assert rec.legacy_action == "HOLD_POSITION"
    assert rec.legacy_desired == "HOLD"
    assert rec.fusion_mid_reason and rec.fusion_long_reason


def test_both_horizons_evaluated_policy_ids(tmp_path):
    """不把 mode 映射成 horizon：MID 与 LONG 各出一包（policy_id 可分）。"""
    pos = _pos()
    pos.trade_plan = SimpleNamespace(mode="qizong", description="气宗持有计划")
    rec = _capture(tmp_path, pos=pos)
    assert rec.fusion_mid_action and rec.fusion_long_action
    from src.core.decision_policy import POLICY_ID_LONG, POLICY_ID_MID, HorizonFacts, evaluate_horizon
    from src.core.decision_contract import Horizon
    from src.core.shadow_diff import _derive_shadow_plan
    from datetime import datetime
    plan = _derive_shadow_plan(Horizon.LONG, "601318", datetime.now().astimezone(), trade_plan=pos.trade_plan)
    assert plan.policy_id == POLICY_ID_LONG
    assert plan.legacy_mode == "qizong"  # 仅对照字段
    assert plan.plan_id.startswith("shadow_601318_long")
    assert plan.activated  # 模拟激活（披露字段锁在 schema）
    mid_plan = _derive_shadow_plan(Horizon.MID, "601318", datetime.now().astimezone())
    assert mid_plan.policy_id == POLICY_ID_MID


# ── 3. 存储与幂等 ───────────────────────────────────────────

def test_jsonl_append_and_idempotent_same_minute(tmp_path):
    """同股同分钟幂等在 _append_record 层锁死（不依赖时钟巧合）。"""
    store = tmp_path / "shadow.jsonl"
    rec = _capture(tmp_path)
    lines = store.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["security_id"] == "601318"
    assert saved["derivation_version"] == SHADOW_DERIVATION_VERSION
    # 同股同分钟重复写入：返回 False 不追加
    dup = ShadowDiffRecord(security_id="601318", as_of=rec.as_of,
                           legacy_action="HOLD_POSITION", legacy_desired="HOLD",
                           fusion_mid_action="REVIEW", fusion_long_action="REVIEW")
    assert _append_record(store, dup) is False
    assert len(store.read_text(encoding="utf-8").strip().splitlines()) == 1
    # 异分钟照写
    dup2 = ShadowDiffRecord(security_id="601318", as_of="2026-01-01T10:00:00+08:00",
                            legacy_action="HOLD_POSITION", legacy_desired="HOLD",
                            fusion_mid_action="REVIEW", fusion_long_action="REVIEW")
    assert _append_record(store, dup2) is True
    assert len(store.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_bad_jsonl_line_isolated(tmp_path):
    store = tmp_path / "shadow.jsonl"
    store.write_text("{broken json\n", encoding="utf-8")
    _capture(tmp_path)
    lines = store.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # 坏行保留（G14：不丢弃），新记录照写


# ── 4. 开关与边界 ───────────────────────────────────────────

def test_switch_off_no_write(tmp_path):
    rec = _capture(tmp_path, config=_OFF)
    assert rec is None
    assert not (tmp_path / "shadow.jsonl").exists()


def test_non_holding_skipped(tmp_path):
    rec = _capture(tmp_path, pos=None)
    assert rec is None
    assert not (tmp_path / "shadow.jsonl").exists()


def test_switch_read_failure_disables_capture(tmp_path, monkeypatch):
    import src.cli.main as cli_main
    def _boom():
        raise RuntimeError("config broken")
    monkeypatch.setattr(cli_main, "load_config", _boom)
    rec = capture_shadow(_dr(), _sd(), _eval(), _pos(), packet=_packet(),
                         source="test", config=None,
                         store_path=tmp_path / "shadow.jsonl")
    assert rec is None
    assert not (tmp_path / "shadow.jsonl").exists()


def test_capture_returns_typed_record(tmp_path):
    rec = _capture(tmp_path)
    assert isinstance(rec, ShadowDiffRecord)
    assert rec.source == "test"


# ── 5. 报告聚合 ────────────────────────────────────────────

def test_report_aggregates_reasons_no_returns(tmp_path):
    store = tmp_path / "shadow.jsonl"
    rec = _capture(tmp_path)
    report = build_shadow_report(store_path=store)
    assert report["total"] == 1 and report["stocks"] == 1
    assert report["diff_rate"] == 1.0  # thesis 未立 → 非 agree
    assert REASON_THESIS in report["by_reason"]
    text = render_shadow_report(report)
    assert "影子对照报告" in text
    assert "模拟计划" in text  # 披露前置
    assert "收益" not in text  # 报告不比较总收益（DESIGN 硬要求）


def test_report_empty_store(tmp_path):
    report = build_shadow_report(store_path=tmp_path / "none.jsonl")
    assert report["total"] == 0
    text = render_shadow_report(report)
    assert "无影子记录" in text


def test_report_skips_old_records(tmp_path):
    """超出 days 窗口的记录不进聚合（窗口由查询侧控制）。"""
    store = tmp_path / "shadow.jsonl"
    from datetime import datetime, timedelta
    old = ShadowDiffRecord(
        security_id="000001", as_of=(datetime.now() - timedelta(days=30)).isoformat(),
        legacy_action="HOLD_POSITION", legacy_desired="HOLD",
        fusion_mid_action="REVIEW", fusion_long_action="REVIEW",
        delta_reasons=[REASON_THESIS])
    _append_record(store, old)
    _capture(tmp_path)
    report = build_shadow_report(store_path=store, days=7)
    assert report["total"] == 1
