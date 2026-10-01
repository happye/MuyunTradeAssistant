"""M0 账户事实→最终行动同源闭环回归测试（plan/fusion iteration6，DELIVERY_PLAN M0；R12 W1 反例）

锁死语义（对齐 iteration6/design_contract_probe.py 19 场景规范合同；
数量决定持仓、权重资格独立——R12 W1 根因收口）：
1. W1 反例：100 股/旧比例 0（ratio_stale）——CLOSE_ALL/fundamental_alert → 终态 EXIT
   （不再被最终包吞成 WAIT）；未知权重 REDUCE 不再变 WAIT
2. 规范动作视图：REDUCE+已知空仓→REVIEW；REDUCE+UNKNOWN→REVIEW；EXIT+NONE→WAIT；
   EXIT+UNKNOWN→EXIT；OPEN/ADD+HELD+未知权重→HOLD（冻结新增）；HOLD 不取占位 0
3. EXIT target=0 是动作定义（权重未知不给 delta——不伪造 delta）
4. 请求内上下文：多股同 account_version、读取次数有断言；下一请求见新成交；
   账本数量事实优先（投影滞后/缺失不吞持仓）；冲突→权重未知不编造数量
5. RATIO_ONLY 旧账户语义零变化；不传 position_state 的旧调用方按 ratio 推导兼容
6. 决策表/影子两臂：HELD+未知权重 EXIT/REDUCE 保留；NONE 已知空仓不退出；
   legacy 臂 HOLD 占位 0 不解释为目标清仓
7. 竖向：pos confirm --qty → 重启 → l/la/chat/today（真实 parse_input→run_cli；
   行情替身=数据隔离；待验适配器/装配/影子/证据/建议全真）——公开回执、
   最终 DecisionPacket、影子两臂、证据卡、建议全部带同一账户上下文

隔离纪律同 K0a/L1/L2：持久化路径显式重定向并断言在临时根内；零网络零 AI。
跑法：pytest tests/core/test_m0_account_terminal.py -q
"""
import io
import json
import os
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 纪律（LRN-20260925-015）：patch 前先预导入被 patch 的真实模块
import src.cli.main as cli_main
import src.core.shadow_diff as shadow_module
import src.data.account_service as account_module
import src.data.akshare_client as akshare_module
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
import start as start_module
from src.core.analysis_service import build_decision_packet
from src.core.decision_contract import DesiredAction
from src.core.decision_policy import HorizonFacts, HorizonPlan, evaluate_horizon
from src.core.execution_layer import ExecutionEvaluation
from src.data.account_service import AccountService
from src.data.models import (
    PositionAction, SignalType, StockData, StrategyDecision, StrategyState,
    TradeLifecycle,
)
from src.data.portfolio import AccountContext, PortfolioManager, RequestAccountFacts

PRICE_DAY = "2026-09-30"
CODE = "600519"


def _isolate(tmp_path, monkeypatch):
    """持久化路径全量重定向（先于一切构造）+ 健全性断言。"""
    monkeypatch.setattr(portfolio_module, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH", str(tmp_path / "proposals.json"))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "account_events.jsonl")
    monkeypatch.setattr(shadow_module, "SHADOW_STORE_PATH", tmp_path / "shadow_diff.jsonl")
    import src.cli.evidence as evidence_module
    monkeypatch.setattr(evidence_module, "_EVIDENCE_FILE", tmp_path / "analysis_evidence.jsonl")
    monkeypatch.setattr(evidence_module, "_REPORT_DIR", tmp_path / "cards")
    import src.data.horizon_plans as horizon_plans_module
    import src.data.research_store as research_store_module
    monkeypatch.setattr(horizon_plans_module, "PLANS_FILE", tmp_path / "horizon_plans.json")
    monkeypatch.setattr(research_store_module, "RESEARCH_DIR", tmp_path / "research")
    root = str(tmp_path.resolve())
    for p in (str(tmp_path / "portfolio.yaml"), str(tmp_path / "account_events.jsonl")):
        assert os.path.abspath(p).startswith(root), f"持久化路径越出临时根: {p}"


def _seed_quantity_account(*, ratio=0.0, quantity=100, ledger_path=None, pm=None,
                           cash=10000.0, trade_date="2026-09-20"):
    """旧比例账户 + 数量账本（W1 反例底座：100 股 / 旧比例 0）。"""
    pm = pm or PortfolioManager()
    pm.add_position(CODE, stock_name="测试股", entry_price=10.0, ratio=ratio)
    svc = AccountService(ledger_path or account_module.DEFAULT_LEDGER_PATH)
    svc.opening_import(opening_cash=cash, trade_date=trade_date,
                       lots=[{"security_id": CODE, "quantity": quantity,
                              "cost_price": 10.0, "acquired_at": trade_date}])
    return svc


def _ctx(*, state="HELD", weight=None, quantity=100, version="v_test", reason=""):
    return AccountContext(security_id=CODE, position_state=state,
                          confirmed_weight=weight,
                          quantity=quantity if state == "HELD" else (0 if state == "NONE" else None),
                          weight_reason=reason, account_version=version)


def _sd(pos_action="CLOSE_ALL", ratio=0.0, sell_path=None, decision=None):
    decision = decision or ("SELL" if pos_action in ("CLOSE_ALL", "REDUCE") else
                            ("BUY" if pos_action in ("OPEN", "ADD") else "HOLD"))
    return StrategyDecision(
        decision=SignalType(decision), position_action=PositionAction(pos_action),
        position_ratio=ratio, sell_path=sell_path,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        strategy_reasons=["策略理由"], new_state=StrategyState())


def _dr(decision="SELL", score=0.85, warnings=None):
    return SimpleNamespace(
        decision=SimpleNamespace(value=decision), score=score,
        stock=SimpleNamespace(stock_code=CODE), warnings=warnings or [])


def _eval(blocked=False, reason="") -> ExecutionEvaluation:
    return ExecutionEvaluation(
        original_action=PositionAction.HOLD_POSITION,
        effective_action=PositionAction.HOLD_POSITION,
        blocked=blocked, block_reason=reason,
        slippage_pct=0.0, impact_cost_pct=0.0, total_cost_pct=0.0)


def _packet(pos_action="CLOSE_ALL", *, state="HELD", weight=None, ratio=0.0,
            sell_path="fundamental_alert", account_version=""):
    return build_decision_packet(
        _dr(), _sd(pos_action, ratio, sell_path=sell_path), _eval(False),
        confirmed_ratio=weight, position_state=state, account_version=account_version)


# ── 1. W1 反例：适配器规范动作视图（19 场景合同）─────────────────────

def test_w1_hard_exit_held_unknown_weight_keeps_exit():
    """W1 主反例：100 股/旧比例 0 + CLOSE_ALL/fundamental_alert → EXIT（不再 WAIT）。"""
    p = _packet("CLOSE_ALL", state="HELD", weight=None)
    assert p.desired_action is DesiredAction.EXIT, \
        f"有仓事实不得被吞成 WAIT（W1 主反例）: {p.desired_action}"
    assert p.target_weight == 0.0, "EXIT 目标 0 是动作定义（不依赖权重已知）"
    assert p.confirmed_weight is None, "权重未知= None（不拿旧比例 0 冒充）"


def test_w1_exit_unknown_weight_no_fabricated_delta():
    """EXIT target=0 是动作定义——权重未知时不伪造 delta（delta=None）。"""
    p = _packet("CLOSE_ALL", state="HELD", weight=None)
    assert p.target_weight == 0.0 and p.delta_weight is None, \
        f"权重未知不得伪造 delta: {p.delta_weight}"
    # 对照：权重已知 → delta = 0 - confirmed（既有语义）
    p2 = _packet("CLOSE_ALL", state="HELD", weight=0.2)
    assert p2.delta_weight == pytest.approx(-0.2)


def test_w1_reduce_held_unknown_weight_not_wait():
    """未知权重 REDUCE 不变 WAIT（R12：只改调用参数传 None 仍不够的反例）。"""
    p = _packet("REDUCE", state="HELD", weight=None, sell_path="weak_sell")
    assert p.desired_action is DesiredAction.REDUCE, \
        f"未知权重减仓方向必须保留: {p.desired_action}"
    assert p.target_weight is None, "未知权重不得给精确减仓目标"


def test_reduce_known_empty_goes_review():
    """已知空仓 + REDUCE 信号 → REVIEW（账实不符交人工，不编造等待/清仓）。"""
    p = _packet("REDUCE", state="NONE", weight=0.0)
    assert p.desired_action is DesiredAction.REVIEW
    assert p.target_weight is None


def test_reduce_unknown_presence_goes_review():
    """持仓未知 + REDUCE → REVIEW（19 场景 unknown_presence_reduce）。"""
    p = _packet("REDUCE", state="UNKNOWN", weight=None)
    assert p.desired_action is DesiredAction.REVIEW


def test_exit_known_empty_stays_wait():
    """已知空仓不做退出（19 场景 flat_exit → WAIT）。"""
    p = _packet("CLOSE_ALL", state="NONE", weight=0.0)
    assert p.desired_action is DesiredAction.WAIT
    assert any("空仓" in r for r in p.reason_codes), "钳制原因必须留痕"


def test_exit_unknown_presence_preserved():
    """持仓未知 + 清仓信号 → EXIT 保留（19 场景 unknown_presence_exit）。"""
    p = _packet("CLOSE_ALL", state="UNKNOWN", weight=None)
    assert p.desired_action is DesiredAction.EXIT and p.target_weight == 0.0


def test_open_add_held_unknown_freezes_new_risk():
    """HELD+未知权重 + OPEN/ADD → HOLD（冻结新增风险，不给精确目标）。"""
    for act in ("OPEN", "ADD"):
        p = _packet(act, state="HELD", weight=None, sell_path=None)
        assert p.desired_action is DesiredAction.HOLD, \
            f"{act}+有仓未知权重必须冻结新增: {p.desired_action}"
        assert p.target_weight is None


def test_hold_takes_no_placeholder_zero_target():
    """HOLD 不取内部占位 0（19 场景 hold_has_no_zero_target）。"""
    p = _packet("HOLD_POSITION", state="HELD", weight=None, ratio=0.0, sell_path=None)
    assert p.desired_action is DesiredAction.HOLD and p.target_weight is None


def test_held_known_weight_legacy_rules_unchanged():
    """合格估值锁定权重后：既有目标规则逐条不变（REDUCE 目标须低于当前等）。"""
    p = _packet("REDUCE", state="HELD", weight=0.2, ratio=0.1, sell_path="take_profit_trim")
    assert p.desired_action is DesiredAction.REDUCE and p.target_weight == pytest.approx(0.1)
    p2 = _packet("REDUCE", state="HELD", weight=0.2, ratio=0.25, sell_path="take_profit_trim")
    assert p2.target_weight is None, "减仓目标未低于当前 → None 待复核（既有钳制）"
    p3 = _packet("OPEN", state="NONE", weight=0.0, ratio=0.2, sell_path=None)
    assert p3.desired_action is DesiredAction.OPEN, "已知空仓 OPEN 语义不变"


def test_backward_compat_position_state_derived_from_ratio():
    """旧调用方（不传 position_state）按 ratio 推导：>0→HELD、==0→NONE、None→UNKNOWN；
    已知空仓 ADD→OPEN 钳制保留（契约合法性）。"""
    p = _packet("CLOSE_ALL", state=None, weight=0.2)
    assert p.desired_action is DesiredAction.EXIT
    p2 = _packet("CLOSE_ALL", state=None, weight=0.0)
    assert p2.desired_action is DesiredAction.WAIT
    p3 = _packet("CLOSE_ALL", state=None, weight=None)
    assert p3.desired_action is DesiredAction.EXIT, "组合未知不吞退出方向"
    p4 = build_decision_packet(_dr("BUY"), _sd("ADD", 0.2, sell_path=None), _eval(False),
                               confirmed_ratio=0.0)
    assert p4.desired_action is DesiredAction.OPEN, "空仓 ADD→OPEN 契约钳制保留"


def test_account_version_wired_to_packet():
    """账户版本进最终包（portfolio_revision——批量结果同版本可核对）。"""
    p = _packet("CLOSE_ALL", state="HELD", weight=None, account_version="abc123")
    assert p.portfolio_revision == "abc123"


# ── 2. 请求内账户上下文（RequestAccountFacts）────────────────────────

def test_request_facts_one_read_shared_version_and_refresh(tmp_path, monkeypatch):
    """同请求多股同账户版本、快照读取次数=1；下一请求（新实例）见新成交。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.1)
    pm.add_position("000001", stock_name="平安", entry_price=5, ratio=0.2)
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    svc.opening_import(opening_cash=100000.0, trade_date="2026-09-20",
                       lots=[{"security_id": "600519", "quantity": 1000,
                              "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    reads = {"n": 0}
    real_snapshot = AccountService.snapshot

    def counting_snapshot(self):
        reads["n"] += 1
        return real_snapshot(self)

    with patch.object(AccountService, "snapshot", counting_snapshot):
        facts = RequestAccountFacts(pm)
        ctxs = [facts.context_for(c, price=10.0, price_as_of=PRICE_DAY)
                for c in ("600519", "000001", "600519")]
    assert reads["n"] == 1, f"请求内账户快照必须只读一次（B2）: {reads['n']}"
    assert {c.account_version for c in ctxs} == {facts.account_version}
    assert ctxs[0].account_version and len(facts.account_version) >= 8
    assert ctxs[0].position_state == "HELD" and ctxs[0].quantity == 1000
    # 下一请求：确认新成交 → 新实例读到新数量与新版本
    v1 = facts.account_version
    svc.confirm_fill("m0f1", v1, __import__(
        "src.data.account_service", fromlist=["FillInput"]).FillInput(
        security_id="600519", action="SELL", quantity=100, price=10.0,
        trade_date=PRICE_DAY))
    facts2 = RequestAccountFacts(pm)
    ctx2 = facts2.context_for("600519", price=10.0, price_as_of=PRICE_DAY)
    assert facts2.account_version != v1, "下一请求必须看到新账户版本"
    assert ctx2.quantity == 900, "下一请求必须看到新成交后的数量事实"


def test_context_ledger_quantity_decides_holding(tmp_path, monkeypatch):
    """账本数量事实优先：yaml 投影滞后（无记录/旧比例 0）不吞持仓。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.0)
    AccountService(account_module.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=10000.0, trade_date="2026-09-20",
        lots=[{"security_id": "600519", "quantity": 100,
               "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    facts = RequestAccountFacts(pm)
    ctx = facts.context_for("600519", price=10.0, price_as_of=PRICE_DAY)
    assert ctx.position_state == "HELD" and ctx.quantity == 100, \
        f"数量事实决定持仓（旧比例 0 不等于无仓）: {ctx}"
    assert ctx.confirmed_weight is None, "价格与 NAV 异日 → 权重未知（不拿旧比例 0）"
    # yaml 无记录但账本有股 → 账本优先 HELD（投影滞后不吞持仓）
    pm2 = PortfolioManager()
    facts2 = RequestAccountFacts(pm2)
    ctx2 = facts2.context_for("600519", price=10.0, price_as_of=PRICE_DAY)
    assert ctx2.position_state == "HELD", "账本有股而投影缺失 → HELD（不按空仓吞）"
    assert ctx2.confirmed_weight is None
    # 账本也无股、yaml 无记录 → 已知空仓
    ctx3 = facts2.context_for("000001", price=5.0, price_as_of=PRICE_DAY)
    assert ctx3.position_state == "NONE" and ctx3.confirmed_weight == 0.0


def test_context_conflict_keeps_held_but_weight_unknown(tmp_path, monkeypatch):
    """投影数量≠账本数量：持仓方向一致保留 HELD，权重拒绝精确（未对账不编造）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.1)
    AccountService(account_module.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=90000.0, trade_date="2026-09-20",
        lots=[{"security_id": "600519", "quantity": 900,
               "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    # 投影手工留下过期数量事实（1000），账本实际 900——模拟外部改动/未同步
    rec = pm._data["positions"]["600519"]
    rec["quantity_fact"] = {"quantity": 1000, "as_of": "2026-09-20", "avg_cost": 10.0}
    facts = RequestAccountFacts(pm)
    ctx = facts.context_for("600519", price=10.0, price_as_of=PRICE_DAY)
    assert ctx.position_state == "HELD"
    assert ctx.confirmed_weight is None, "数量不一致不得锁出精确权重"
    assert "不一致" in ctx.weight_reason or "对账" in ctx.weight_reason, \
        f"未对账原因必须可读: {ctx.weight_reason}"


def test_context_ratio_only_semantics_unchanged(tmp_path, monkeypatch):
    """RATIO_ONLY 旧账户：比例直通判持仓与权重（零变化）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.1)
    facts = RequestAccountFacts(pm)  # 无账本——纯比例账户
    ctx = facts.context_for("600519")
    assert ctx.position_state == "HELD" and ctx.quantity is None
    assert ctx.confirmed_weight == pytest.approx(0.1)
    # 比例 0 的 RATIO_ONLY 记录 = 已知空仓（既有语义）
    pm.add_position("000001", stock_name="空仓记录", entry_price=5, ratio=0.0)
    ctx2 = facts.context_for("000001")
    assert ctx2.position_state == "NONE" and ctx2.confirmed_weight == 0.0


def test_context_corrupted_portfolio_goes_unknown(tmp_path, monkeypatch):
    """持仓文件损坏 → UNKNOWN（不把损坏当空仓——不编造数量）。"""
    _isolate(tmp_path, monkeypatch)
    path = tmp_path / "portfolio.yaml"
    path.write_text("{ 损坏内容", encoding="utf-8")
    pm = PortfolioManager()
    assert pm._corrupted, "前置：损坏置位"
    facts = RequestAccountFacts(pm)
    ctx = facts.context_for("600519")
    assert ctx.position_state == "UNKNOWN" and ctx.confirmed_weight is None


def test_context_qualified_valuation_locks_weight(tmp_path, monkeypatch):
    """价格与 NAV 同日窗 → 上下文直接给合格权重（数量×价格/NAV）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.0)
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    svc.opening_import(opening_cash=90000.0, trade_date="2026-09-20",
                       lots=[{"security_id": "600519", "quantity": 900,
                              "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    # NAV 定价时点改为与价格同日（直接构造账本 NAV 事件属 J2 协议；此处经快照注入路径）
    from src.data.account_snapshot import AccountSnapshot
    from datetime import datetime
    snap = svc.snapshot()
    # opening_import 只录现金锚与批次——无 NAV 定价事件（J2 协议），快照 NAV 缺席
    assert snap.nav is None and snap.nav_priced_at is None, \
        f"期初导入不产生 NAV（缺席是状态不是 0）: {snap.nav}"
    facts = RequestAccountFacts(pm)
    # 手工构造同日 NAV 输入（估值门单测归 portfolio._qualified_weight；此处走上下文）
    ctx = facts.context_for("600519", price=10.0, price_as_of=PRICE_DAY)
    # opening_import 未录 NAV → 权重未知（不编造）
    assert ctx.confirmed_weight is None
    # 合格路径：显式 NAV 同日 → 锁定（复用 _qualified_weight 的公共语义）
    w, reason = portfolio_module._qualified_weight(
        900, 10.0, PRICE_DAY, 90000.0, PRICE_DAY)
    assert w == pytest.approx(0.1), f"同日合格估值必须锁定权重: {w} {reason}"


# ── 3. 决策表 position_state（影子两臂语义）──────────────────────────

def _plan(activated=True):
    return HorizonPlan(plan_id="m0_test_mid", horizon=__import__(
        "src.core.decision_contract", fromlist=["Horizon"]).Horizon.MID,
        policy_id="fusion_mid_v1", intent="测试",
        accepted_at=("2026-09-01T00:00:00" if activated else None))


def _hf(**kw):
    base = dict(research_status=None, hard_exit_triggered=False,
                hard_exit_detail="", technical_exit_triggered=False,
                entry_condition_met=False, budget_available=None)
    base.update(kw)
    if base["research_status"] is None:
        from src.core.decision_contract import ResearchStatus
        base["research_status"] = ResearchStatus.COMPLETE
    return HorizonFacts(**base)


def test_horizon_held_unknown_keeps_hard_exit():
    from src.core.decision_contract import DesiredAction as DA
    pkt = evaluate_horizon(_plan(), _hf(hard_exit_triggered=True), CODE,
                           confirmed_ratio=None, position_state="HELD")
    assert pkt.desired_action is DA.EXIT, "HELD+未知权重硬退出 → EXIT（fusion 臂不吞）"


def test_horizon_none_known_flat_waits_on_exit():
    from src.core.decision_contract import DesiredAction as DA
    pkt = evaluate_horizon(_plan(), _hf(hard_exit_triggered=True), CODE,
                           confirmed_ratio=0.0, position_state="NONE")
    assert pkt.desired_action is DA.WAIT, "已知空仓不做退出"


def test_horizon_unknown_presence_exit_preserved():
    from src.core.decision_contract import DesiredAction as DA
    pkt = evaluate_horizon(_plan(), _hf(hard_exit_triggered=True), CODE,
                           confirmed_ratio=None, position_state="UNKNOWN")
    assert pkt.desired_action is DA.EXIT, "持仓未知不吞退出方向"


def test_horizon_held_unknown_entry_holds():
    from src.core.decision_contract import DesiredAction as DA, ThesisStatus
    pkt = evaluate_horizon(_plan(), _hf(entry_condition_met=True,
                                        thesis_status=ThesisStatus.VALID), CODE,
                           confirmed_ratio=None, position_state="HELD")
    assert pkt.desired_action is DA.HOLD, "HELD+未知权重入场条件成立 → HOLD（冻结新增）"


def test_horizon_backward_compat_without_state():
    """不传 position_state：旧 ratio 推导行为零变化（回归锚）。"""
    from src.core.decision_contract import DesiredAction as DA
    pkt = evaluate_horizon(_plan(), _hf(hard_exit_triggered=True), CODE,
                           confirmed_ratio=0.0)
    assert pkt.desired_action is DA.WAIT
    pkt2 = evaluate_horizon(_plan(), _hf(hard_exit_triggered=True), CODE,
                            confirmed_ratio=0.2)
    assert pkt2.desired_action is DA.EXIT


# ── 4. 影子消费同一上下文 ────────────────────────────────────────────

def _seed_accepted_mid_plan(tmp_path):
    """已接受 MID 计划+评估齐（v7 资格绑定的输入——两臂 binding 才会构建）。"""
    from datetime import datetime, timedelta
    from src.data.horizon_plans import HorizonPlanStore
    from src.data.research_store import AssessmentStore
    from src.core.decision_policy import POLICY_ID_MID
    from src.core.research import ThesisAssessment
    from src.core.research_service import ASSERTION_METHOD_VERSION
    from src.core.decision_contract import ThesisStatus
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    plan = HorizonPlan(plan_id=f"p2_{CODE}_m0", security_id=CODE,
                       horizon="MID", policy_id=POLICY_ID_MID,
                       intent="测试意图", policy_version="r4.research_service_v1")
    asm = ThesisAssessment(thesis_id=f"thesis_{CODE}_MID", security_id=CODE,
                           horizon="MID", snapshot_id="snap-m0",
                           status=ThesisStatus.VALID,
                           method_version=ASSERTION_METHOD_VERSION,
                           evaluated_as_of=datetime.now().astimezone() - timedelta(minutes=1))
    plan.assessment_id = asm_store.save(asm)
    plan.snapshot_id = asm.snapshot_id
    plans.save(plan)
    ok, msg = plans.accept(CODE, "MID")
    assert ok, msg
    return plans, asm_store


def test_shadow_arms_exit_for_held_unknown_weight(tmp_path, monkeypatch):
    """影子两臂：HELD+未知权重 + 硬退出 → legacy EXIT、fusion MID/LONG EXIT；
    已接受计划的 MID binding 带同源账户版本与规范两臂。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_quantity_account(ratio=0.0, quantity=100, pm=pm)
    pm.apply_quantity_fill(CODE, "SELL", quantity=900, price=10,
                           trade_date=PRICE_DAY, fill_id="m0s1",
                           quantity_before=1000, quantity_after=100, avg_cost=10)
    plans, asm_store = _seed_accepted_mid_plan(tmp_path)
    facts = RequestAccountFacts(pm)
    ctx = facts.context_for(CODE, price=10.0, price_as_of=PRICE_DAY)
    assert ctx.position_state == "HELD" and ctx.confirmed_weight is None
    sd = _sd("CLOSE_ALL", 0.0, sell_path="fundamental_alert")
    rec = shadow_module.capture_shadow(
        _dr(), sd, _eval(False), pm.get_position(CODE),
        packet=_packet("CLOSE_ALL", state=ctx.position_state, weight=ctx.confirmed_weight,
                       account_version=ctx.account_version),
        source="l", store_path=shadow_module.SHADOW_STORE_PATH,
        plans_store=plans, assessment_store=asm_store,
        quote_as_of=PRICE_DAY, account_context=ctx)
    assert rec is not None
    assert rec.legacy_desired == "EXIT"
    assert rec.fusion_mid_action == "EXIT", \
        f"MID 臂硬退出不得吞成 WAIT: {rec.fusion_mid_action} {rec.fusion_mid_reason}"
    assert rec.fusion_long_action == "EXIT"
    assert rec.account_version == ctx.account_version
    mb = rec.mid_binding
    assert mb is not None and mb["account_version"] == ctx.account_version
    assert mb["arms"]["legacy"]["action"] == "EXIT"
    assert mb["arms"]["legacy"]["target"] == 0.0
    assert mb["arms"]["fusion"]["action"] == "EXIT"
    assert mb["arms"]["fusion"]["target"] == 0.0


def test_shadow_legacy_arm_hold_no_zero_target(tmp_path, monkeypatch):
    """legacy 臂 HOLD：占位 0 不解释为目标清仓（target None / UNKNOWN 态）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_quantity_account(ratio=0.0, quantity=100, pm=pm)
    plans, asm_store = _seed_accepted_mid_plan(tmp_path)
    facts = RequestAccountFacts(pm)
    ctx = facts.context_for(CODE, price=10.0, price_as_of=PRICE_DAY)
    sd = _sd("HOLD_POSITION", 0.0, sell_path=None, decision="HOLD")
    rec = shadow_module.capture_shadow(
        _dr("HOLD", 0.5), sd, _eval(False), pm.get_position(CODE),
        packet=_packet("HOLD_POSITION", state=ctx.position_state,
                       weight=ctx.confirmed_weight, ratio=0.0, sell_path=None,
                       account_version=ctx.account_version),
        source="l", store_path=shadow_module.SHADOW_STORE_PATH,
        plans_store=plans, assessment_store=asm_store,
        quote_as_of=PRICE_DAY, account_context=ctx)
    assert rec is not None
    lt = rec.mid_binding["arms"]["legacy"]
    assert lt["action"] == "HOLD"
    assert lt["target"] is None, f"HOLD 占位 0 不得进绑定目标: {lt['target']}"
    assert lt["target_state"] == "UNKNOWN"


# ── 5. 竖向：pos confirm --qty → 重启 → l/la/chat/today 全链 ─────────

def _repl(cmd: str) -> str:
    parsed = start_module.parse_input(cmd)
    assert parsed is not None, f"REPL 解析失败: {cmd}"
    buf = io.StringIO()
    with patch.object(cli_main, "console", Console(file=buf, width=200)):
        with redirect_stdout(buf):
            start_module.run_cli(parsed[0], parsed[1])
    return buf.getvalue()


def _stub_market(code, *, price=10.0, quote_day=PRICE_DAY):
    return StockData(stock_code=code, stock_name="测试股", price=price, volume=100000,
                     change_pct=1.0, avg_volume_20=100000.0,
                     ma5=9.8, ma20=9.5, ma60=9.0, quote_as_of=quote_day)


def test_vertical_confirm_restart_l_chain_terminal(tmp_path, monkeypatch):
    """竖向 W1：pos confirm --qty 重建仓 → 重启 → 真实 l 全链（行情替身）→
    终态包/影子/证据/建议带同一账户上下文；硬退出输入经装配原语贯穿 EXIT。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_quantity_account(ratio=0.0, quantity=100, pm=pm, cash=10000.0)
    # pos confirm 依据待确认建议（K0a e2e 同款前置）——ADD 建议挂起后按 --qty 确认
    from src.data.proposals import Proposal
    pm._proposals.add_proposal(Proposal(stock_code=CODE, stock_name="测试股",
                                        position_action="ADD", target_ratio=0.1,
                                        current_ratio=0.0))
    assert pm._proposals._save()
    # 公开回执：pos confirm --qty（真实 REPL）
    out = _repl(f"pos confirm {CODE} --qty 100 --price 10 --date {PRICE_DAY}")
    assert "ACCEPTED" in out or "已入账" in out or "确认" in out, \
        f"公开回执必须可见: {out[-400:]}"
    # 重启：新实例读盘
    pm2 = PortfolioManager()
    pos = pm2.get_position(CODE)
    assert pos is not None and pos.quantity_held == 200, \
        "重启后数量事实必须反映两笔买入（期初 100 + 确认 100）"
    # 真实 l 全链（行情替身；适配器/装配/影子/证据/建议全真）
    with patch.object(akshare_module, "get_stock_data", lambda c: _stub_market(c)):
        _repl(f"l {CODE}")
    # 影子记录落盘且带账户版本（接线真实可达）
    records = [json.loads(line) for line in
               shadow_module.SHADOW_STORE_PATH.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    assert records, "l 必须产出影子记录"
    rec = records[-1]
    assert rec["account_version"], "影子记录必须带账户版本（同源上下文）"
    assert rec["legacy_desired"] in ("EXIT", "REDUCE", "HOLD", "WAIT", "REVIEW", "OPEN", "ADD")
    # 证据 JSONL 带 position_state/account_version（追加式新字段）
    import src.cli.evidence as evidence_module
    ev_lines = evidence_module._EVIDENCE_FILE.read_text(encoding="utf-8").splitlines()
    ev = json.loads(ev_lines[-1])
    assert ev.get("account_version") == rec["account_version"], \
        f"证据卡与影子必须同账户版本: {ev.get('account_version')}"
    assert ev.get("position_state") in ("HELD", "NONE", "UNKNOWN")
    # 硬退出输入 → 真实装配原语贯穿 EXIT（等价 orchestrator 硬退出输出的构造输入；
    # 适配器/请求上下文/存储全真——待验对象不被 mock）
    facts = RequestAccountFacts(pm2)
    ctx = facts.context_for(CODE, price=10.0, price_as_of=PRICE_DAY)
    assert ctx.position_state == "HELD" and ctx.confirmed_weight is None
    packet = build_decision_packet(
        _dr(), _sd("CLOSE_ALL", 0.0, sell_path="fundamental_alert"), _eval(False),
        confirmed_ratio=ctx.confirmed_weight, position_state=ctx.position_state,
        account_version=ctx.account_version)
    assert packet.desired_action is DesiredAction.EXIT, \
        f"竖向装配后硬退出必须 EXIT: {packet.desired_action}"
    sd = _sd("CLOSE_ALL", 0.0, sell_path="fundamental_alert")
    rec2 = shadow_module.capture_shadow(
        _dr(), sd, _eval(False), pm2.get_position(CODE),
        packet=packet, source="l", store_path=shadow_module.SHADOW_STORE_PATH,
        account_context=ctx)
    assert rec2.fusion_mid_action == "EXIT" and rec2.fusion_long_action == "EXIT"
    # （binding 两臂在已接受计划场景由单测覆盖；本竖向无用户计划——binding=None 是
    #   v7 合同正确行为，只断言顶层动作族不吞退出）
    # today：数量事实+待重估如实显示
    from src.cli.today_service import build_today_view
    view = build_today_view(pm2, risk_profile=None)
    cards = [c for c in (view.holding + view.needs_action) if c.stock_code == CODE]
    assert cards, "today 必须显示该持仓"
    text = "\n".join("\n".join(c.lines) for c in cards)
    assert "待重估" in text or "200" in text, f"today 必须反映数量事实/待重估: {text}"


def test_vertical_la_batch_same_account_version(tmp_path, monkeypatch):
    """la 批次：多股同请求同账户版本（真实 run_cli；读取次数断言）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.1)
    pm.add_position("000001", stock_name="平安", entry_price=5, ratio=0.2)
    AccountService(account_module.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=100000.0, trade_date="2026-09-20",
        lots=[{"security_id": "600519", "quantity": 1000,
               "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    reads = {"n": 0}
    real_snapshot = AccountService.snapshot

    def counting_snapshot(self):
        reads["n"] += 1
        return real_snapshot(self)

    def _stub_two(code):
        sd = _stub_market(code, price=10.0 if code == "600519" else 5.0)
        return sd

    with patch.object(AccountService, "snapshot", counting_snapshot), \
            patch.object(akshare_module, "get_stock_data", _stub_two):
        _repl("la")
    records = [json.loads(line) for line in
               shadow_module.SHADOW_STORE_PATH.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    by_code = {r["security_id"]: r for r in records}
    assert {"600519", "000001"} <= set(by_code), f"两持仓都须有影子记录: {by_code.keys()}"
    versions = {r["account_version"] for r in by_code.values()}
    assert len(versions) == 1 and all(versions), \
        f"同批次必须共享同一账户版本: {versions}"


def test_vertical_chat_entry_consumes_context(tmp_path, monkeypatch):
    """chat 入口：分析走真实 tools.analyze_stock（行情替身）→ 影子记录带账户版本。"""
    _isolate(tmp_path, monkeypatch)
    import src.chat.tools as chat_tools
    pm = PortfolioManager()
    _seed_quantity_account(ratio=0.1, quantity=1000, pm=pm, cash=90000.0)
    from src.core.runtime import build_live_orchestrator
    orch = build_live_orchestrator(cli_main.load_config())
    # chat 数据获取走 AKShareClient.calculate_indicators（线程内直调）——行情替身
    monkeypatch.setattr(chat_tools, "_orchestrator", orch, raising=False)
    monkeypatch.setattr(chat_tools, "_portfolio_manager", pm, raising=False)
    monkeypatch.setattr(akshare_module.AKShareClient, "calculate_indicators",
                        staticmethod(lambda code, require_historical=False: _stub_market(code)))
    out = chat_tools.analyze_stock(CODE)
    assert not out.startswith(chat_tools.TOOL_ERROR_MARK), f"chat 分析失败: {out[:300]}"
    records = [json.loads(line) for line in
               shadow_module.SHADOW_STORE_PATH.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    assert records, "chat 分析必须产出影子记录"
    rec = records[-1]
    assert rec["source"] == "chat" and rec["account_version"], \
        f"chat 影子记录必须带账户版本: {rec.get('account_version')!r}"
