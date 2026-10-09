"""板块层渗透率 30+ 降级为研究提醒（v0.8.28.1，架构师裁决 2026-10-08）

裁决（docs/2026-10-08_决策简报_渗透率30+_误触发强制清仓.md）：
- A+ B：penetration_stage=="30+" 不再单独触发 P1 强制清仓（该标注是建仓时 AI 对
  四选一枚举的猜测，无数据核实、无「不适用」出口，银行/基建被标 30+ 导致新开仓
  当天即被反复 CLOSE_ALL），降级为研究提醒；旗手滞涨（有 20 日涨幅数据核实）保持不变
- 标注 prompt 加「不适用/证据不足」出口，禁止把「行业成熟」换算成 30+
- 旧标签立即受新规则约束（不做数据迁移，保留原值可追溯）
- 证据记录补 top_signal 专门触发字段（此前 reason 只进 INFO 日志，事后无法定位）

跑法：pytest tests/core/test_sector_penetration_demote.py
"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.exit_signals.sector import (
    check_sector_top_signal,
    penetration_research_reminder,
)


def _mk_plan(**kw):
    from src.data.models import TradePlan
    base = dict(plan_id="x", opened_at="2026-01-01", why_buy="t", when_buy="t",
                stock_code="000001", stock_name="平安银行", entry_price=11.7,
                ratio=0.1, how_much=0.1, locked_initial_stop=10, current_stop=10)
    base.update(kw)
    return TradePlan(**base)


def _mk_sd(**kw):
    from src.data.models import StockData
    base = dict(stock_code="000001", stock_name="平安银行", price=11.78, volume=1000)
    base.update(kw)
    return StockData(**base)


# ── 降级：30+ 不再单独触发强制清仓 ────────────────────────

def test_penetration_30_plus_no_longer_force_exit():
    """裁决核心：新旧计划的 30+ 标签均不能单独触发清仓信号。"""
    p = _mk_plan(penetration_stage="30+", flagbearer_code=None)
    assert check_sector_top_signal(trade_plan=p, stock_data=None, code="000001") == [], \
        "30+ 已降级为研究提醒，不得再作为强制清仓信号返回"


def test_penetration_research_reminder_copy():
    """30+ → 研究提醒（措辞按架构师裁决）；其他档位/无 plan → None。"""
    p = _mk_plan(penetration_stage="30+")
    r = penetration_research_reminder(p)
    assert r is not None and "缺少可核实依据" in r and "不据此触发清仓" in r
    assert penetration_research_reminder(_mk_plan(penetration_stage="1-10")) is None
    assert penetration_research_reminder(_mk_plan(penetration_stage=None)) is None
    assert penetration_research_reminder(None) is None


def test_flagbearer_lag_still_triggers(monkeypatch):
    """降级不得误伤：旗手滞涨（有数据核实）仍是板块层强制信号。"""
    import src.core.exit_signals.sector as sector
    p = _mk_plan(penetration_stage="30+", flagbearer_code="600170")
    sd = _mk_sd(change_20d=20.0)   # 标的 20 日涨 20%（>10% 门槛）
    monkeypatch.setattr(sector, "_fetch_20d_change_pct", lambda code: 2.0)  # 旗手仅涨 2%
    fs = check_sector_top_signal(trade_plan=p, stock_data=sd, code="000498")
    assert fs and fs[0].signal_id == "research.sector.flagbearer_lag"         and fs[0].action_scope == "research" and "600170" in fs[0].detail


def test_below_30_still_no_trigger():
    """其余档位行为不变。"""
    for stage in ("0-1", "1-10", "10-30", None):
        p = _mk_plan(penetration_stage=stage, flagbearer_code=None)
        assert check_sector_top_signal(trade_plan=p, stock_data=None, code="x") == []


# ── 标注层：prompt 提供「不适用/证据不足」出口 ─────────────

class _FakeAIClient:
    """返回固定 JSON 的伪 OpenAI client，并捕获 create kwargs（含 prompt）。"""

    def __init__(self, payload):
        self.payload = payload
        self.kwargs = None

        class _Fn:
            def __init__(outer, holder):
                outer.holder = holder

            def create(inner, **kwargs):
                inner.holder.kwargs = kwargs
                msg = SimpleNamespace(content=json.dumps(inner.holder.payload), reasoning_content=None)
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

        self.chat = SimpleNamespace(completions=_Fn(self))


def _annotate(client):
    from src.core.benzong.auto_scorer import _annotate_sector_meta
    return _annotate_sector_meta(
        client, "deepseek-flash", "000001", "平安银行",
        {"industry": {"industry_name": "银行"}})


def test_annotate_accepts_na_and_insufficient():
    """AI 明确回答「不适用/证据不足」→ 原样保留（可追溯），不再被迫四选一。"""
    for answer in ("不适用", "证据不足"):
        client = _FakeAIClient({"flagbearer_code": None, "penetration_stage": answer})
        fb, ps = _annotate(client)
        assert ps == answer, f"{answer} 应被接受并原样保存"
    # 非法值仍然拒绝（None 出口不变）


def test_annotate_rejects_off_enum_values():
    client = _FakeAIClient({"flagbearer_code": None, "penetration_stage": "成熟行业"})
    _, ps = _annotate(client)
    assert ps is None


def test_annotate_prompt_offers_na_exit():
    """prompt 必须显式提供「不适用/证据不足」出口，并禁止把行业成熟换算成 30+。"""
    client = _FakeAIClient({"flagbearer_code": None, "penetration_stage": "不适用"})
    _annotate(client)
    prompt = (client.kwargs.get("messages") or [{}])[0].get("content", "")
    assert "不适用" in prompt and "证据不足" in prompt
    assert "不适用" in prompt and ("不要" in prompt or "严禁" in prompt or "不能" in prompt)


# ── 证据记录补 top_signal 专门触发字段（E）────────────────

def _mk_evidence_stubs(top_signal):
    from src.data.models import StockData, SignalType, PositionAction
    stock = StockData(stock_code="000001.SH", stock_name="平安银行", price=11.78, volume=1000)
    decision_result = SimpleNamespace(
        stock=stock, decision=SignalType.SELL, score=0.85, position_ratio=0.0,
        signals=[], warnings=["触发止盈条件，分批止盈落袋为安"],
        reason=["[TopSignal] 高位止盈: 板块:渗透率突破30%魔咒"])
    strategy_decision = SimpleNamespace(
        position_action=PositionAction.CLOSE_ALL, sell_path="top_signal",
        top_signal=top_signal)
    return decision_result, strategy_decision


def test_evidence_records_top_signal_trigger_field(monkeypatch, tmp_path):
    """E：证据记录带 top_signal 专门字段（此前只有 INFO 日志，事后无法定位）。"""
    import src.cli.evidence as evidence
    monkeypatch.setattr(evidence, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(evidence, "_EVIDENCE_FILE", tmp_path / "analysis_evidence.jsonl")
    monkeypatch.setattr(evidence, "_REPORT_DIR", tmp_path / "cards")
    reason = "板块:渗透率突破30%魔咒(增速放缓，成长->价值切换见顶)"
    dr, sd = _mk_evidence_stubs(top_signal=reason)
    rec = evidence.record_evidence(dr, sd, source="test")
    assert rec is not None
    assert rec.get("top_signal") == reason, "证据记录缺少 top_signal 专门触发字段"
    card = list((tmp_path / "cards").glob("*.md"))[0].read_text(encoding="utf-8")
    assert "top_signal" in card or "TopSignal" in card or reason[:12] in card


# ── P0 回归贯通：标注 → generator → TradePlan 构造（code-quality-guard 实锤的崩点）──

def test_annotate_na_flows_through_plan_generation():
    """「不适用/证据不足」必须能贯通 TradePlan 构造——Literal 扩枚举前此处直接
    ValidationError（崩在持仓已落库之后 → 无计划裸仓）。"""
    from src.core.benzong.auto_scorer import _annotate_sector_meta
    from src.core.trade_plan.generator import generate_plan_draft
    from src.data.models import StockData

    client = _FakeAIClient({"flagbearer_code": None, "penetration_stage": "不适用"})
    fb, ps = _annotate(client)
    assert ps == "不适用"

    sd = StockData(stock_code="000001.SH", stock_name="平安银行", price=11.78, volume=1000)
    plan = generate_plan_draft(
        stock_code="000001", stock_name="平安银行", entry_price=11.78, ratio=0.1,
        stock_data=sd, benzong_grade="B", penetration_stage=ps,
    )
    assert plan is not None and plan.penetration_stage == "不适用"

    client2 = _FakeAIClient({"flagbearer_code": None, "penetration_stage": "证据不足"})
    _, ps2 = _annotate(client2)
    plan2 = generate_plan_draft(
        stock_code="000498", stock_name="山东路桥", entry_price=6.24, ratio=0.1,
        stock_data=sd, benzong_grade="B", penetration_stage=ps2,
    )
    assert plan2 is not None and plan2.penetration_stage == "证据不足"
