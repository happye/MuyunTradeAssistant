"""N0 账户事实成为所有消费者的事实来源回归测试（plan/fusion iteration7，DELIVERY_PLAN N0；R13 X1 反例）

锁死语义（R13 X1 四条转绿 + 消费面收口；已装配 ctx 是唯一事实源——不再从
pos.current_ratio/to_strategy_state 第二套装配二次判仓）：
1. X1-strategy：账本 100 股 + 投影旧比例 .1 → 策略权重=ctx 合格权重（None，不是 .1）；
   账本 100 股 + 投影缺失 → 策略不生成 FLAT/0（重建仓 OPEN、成本未知不伪造）
2. X1-chat：真实 chat 入口 + 行情/上游策略替身 + 账本有仓投影缺失 + 硬退出 →
   同一调用内最终包 EXIT + 账户版本随包（不再 WAIT/None）
3. X1-read-failure：账本快照读取失败 → UNKNOWN 待对账（不当作明确空仓 NONE/0）
4. 隔离事件（PARTIAL）：权重冻结 None+待对账原因；数量投影冲突/损坏持仓回归
5. 无投影有仓：影子仍捕获（退出方向保留）、建议不伪造可确认项（明确条件/缺口）、
   摘要/面板以 ctx 为判据（不再以 pos 存在为前置）
6. la 批次快照读取次数=1 的真断言；下一请求见新成交；旧比例无账本仍兼容

隔离纪律同 M0/M1：持久化路径显式重定向并断言在临时根内；零付费 AI。
如实披露：l/chat 竖向用例跑真实公开入口全链，其中笨总数据提供方存在既有外联
尝试（margin 表拉取，失败即降级——与 R12 guarded runner 观察一致，非本批引入）；
AI 依赖环境 config（无 key 时降级为纯技术面），socket 级阻断属架构师 runner 职责。
跑法：pytest tests/core/test_n0_ctx_source_of_truth.py -q
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
import src.chat.tools as chat_tools
import src.cli.main as cli_main
import src.core.shadow_diff as shadow_module
import src.core.strategy_layer as strategy_layer_module
import src.data.account_service as account_module
import src.data.akshare_client as akshare_module
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
import start as start_module
from src.core.analysis_service import build_decision_packet
from src.core.decision_contract import DesiredAction
from src.data.account_service import AccountService
from src.data.models import (
    PositionAction, SignalType, StockData, StrategyDecision, StrategyState,
    TradeLifecycle,
)
from src.data.portfolio import PortfolioManager, RequestAccountFacts

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


def _seed_ledger_only(*, quantity=100, cash=10000.0, code=CODE):
    """账本数量事实（无任何投影写入）。"""
    AccountService(account_module.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=cash, trade_date="2026-09-20",
        lots=[{"security_id": code, "quantity": quantity,
               "cost_price": 10.0, "acquired_at": "2026-09-20"}])


def _stub_market(code, *, price=10.0):
    return StockData(stock_code=code, stock_name="测试股", price=price, volume=100000,
                     change_pct=1.0, avg_volume_20=100000.0,
                     ma5=9.8, ma20=9.5, ma60=9.0, quote_as_of=PRICE_DAY)


# ── X1-1：ctx 贯穿策略状态（不再第二套装配）──────────────────────────

def test_strategy_state_consumes_ctx_weight_not_projection_ratio(tmp_path, monkeypatch):
    """账本 100 股 + 投影旧比例 .1 → 策略权重来自 ctx（None），不得拿 .1。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(CODE, stock_name="测试股", entry_price=10.0, ratio=0.1)
    _seed_ledger_only(quantity=100)
    facts = RequestAccountFacts(pm)
    ctx = facts.context_for(CODE, price=10.0, price_as_of=PRICE_DAY)
    assert ctx.position_state == "HELD" and ctx.confirmed_weight is None
    state = pm.to_strategy_state(CODE, price=10.0, price_as_of=PRICE_DAY,
                                 nav=facts.nav, nav_as_of=facts.nav_day,
                                 account_context=ctx)
    assert state.current_position_ratio is None, \
        f"策略权重必须来自 ctx（未知），不得从投影旧比例 .1 二次判读: {state.current_position_ratio}"
    assert state.lifecycle != TradeLifecycle.FLAT


def test_strategy_state_held_no_projection_not_flat_zero(tmp_path, monkeypatch):
    """账本 100 股 + 投影缺失 → 策略不生成 FLAT/0（重建仓 OPEN；成本未知不伪造）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_ledger_only(quantity=100)
    facts = RequestAccountFacts(pm)
    ctx = facts.context_for(CODE, price=10.0, price_as_of=PRICE_DAY)
    assert ctx.position_state == "HELD" and ctx.quantity == 100
    state = pm.to_strategy_state(CODE, price=10.0, price_as_of=PRICE_DAY,
                                 nav=facts.nav, nav_as_of=facts.nav_day,
                                 account_context=ctx)
    assert state.current_position_ratio is None
    assert state.lifecycle != TradeLifecycle.FLAT, \
        f"账本有仓不得生成 FLAT（X1 主反例）: {state.lifecycle}"
    assert state.entry_price is None, "投影缺失时成本未知——不从价格伪造"
    # 已知空仓照旧 FLAT/0.0
    ctx_none = facts.context_for("000001")
    state2 = pm.to_strategy_state("000001", account_context=ctx_none)
    assert state2.lifecycle == TradeLifecycle.FLAT and state2.current_position_ratio == 0.0


# ── X1-2：真实 chat 入口 + 上游策略替身 + 硬退出 → 同调用内 EXIT ─────

class _HardExitStrategyFixture:
    """上游策略替身（R13 允许：行情/AI/上游策略可替身；装配/最终适配/存储全真）。"""

    def __init__(self, state):
        self._state = state

    def process(self, decision_result, strategy_state, data, current_date=None):
        return StrategyDecision(
            decision=SignalType.SELL, position_action=PositionAction.CLOSE_ALL,
            position_ratio=None, sell_path="fundamental_alert",
            lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
            strategy_reasons=["硬退出替身"], new_state=deepcopy_state(strategy_state))


def deepcopy_state(state):
    from copy import deepcopy
    return deepcopy(state)


def _repl(cmd: str) -> str:
    parsed = start_module.parse_input(cmd)
    assert parsed is not None, f"REPL 解析失败: {cmd}"
    buf = io.StringIO()
    with patch.object(cli_main, "console", Console(file=buf, width=200)):
        with redirect_stdout(buf):
            start_module.run_cli(parsed[0], parsed[1])
    return buf.getvalue()


def test_chat_no_projection_hard_exit_same_call_exit(tmp_path, monkeypatch):
    """X1-chat 主反例：账本 100 股、投影缺失、真实 chat 入口 + 行情/上游替身 +
    硬退出 → 同一调用内最终包 EXIT、账户版本随包、影子/证据带同源上下文。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_ledger_only(quantity=100)
    from src.core.runtime import build_live_orchestrator
    orch = build_live_orchestrator(cli_main.load_config())
    monkeypatch.setattr(chat_tools, "_orchestrator", orch, raising=False)
    monkeypatch.setattr(chat_tools, "_portfolio_manager", pm, raising=False)
    monkeypatch.setattr(akshare_module.AKShareClient, "calculate_indicators",
                        staticmethod(lambda code, require_historical=False: _stub_market(code)))
    # 上游策略替身：固定 CLOSE_ALL/fundamental_alert（PlanGuard/执行层/最终适配全真）
    monkeypatch.setattr(strategy_layer_module.StrategyLayer, "process",
                        _HardExitStrategyFixture(None).process)
    out = chat_tools.analyze_stock(CODE)
    assert not out.startswith(chat_tools.TOOL_ERROR_MARK), f"chat 分析失败: {out[:300]}"
    # 最终适配器在同调用内收到硬退出 + ctx → EXIT（不再 WAIT）
    records = [json.loads(line) for line in
               shadow_module.SHADOW_STORE_PATH.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    assert records, "影子必须捕获（无投影有仓不缺位）"
    rec = records[-1]
    assert rec["legacy_desired"] == "EXIT", \
        f"chat 硬退出必须贯穿最终包: {rec['legacy_desired']}"
    assert rec["account_version"], "账户版本必须随影子记录"
    # 证据卡同源
    import src.cli.evidence as evidence_module
    ev = json.loads(evidence_module._EVIDENCE_FILE.read_text(encoding="utf-8").splitlines()[-1])
    assert ev.get("desired_action") == "EXIT" and ev.get("account_version") == rec["account_version"]


def test_l_no_projection_hard_exit_same_call_exit(tmp_path, monkeypatch):
    """l 公开入口同款：账本有仓无投影 + 上游硬退出替身 → 同调用内最终包 EXIT。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_ledger_only(quantity=100)
    monkeypatch.setattr(akshare_module, "get_stock_data", lambda c: _stub_market(c))
    monkeypatch.setattr(strategy_layer_module.StrategyLayer, "process",
                        _HardExitStrategyFixture(None).process)
    _repl(f"l {CODE}")
    records = [json.loads(line) for line in
               shadow_module.SHADOW_STORE_PATH.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    assert records, "l 必须产出影子记录"
    rec = records[-1]
    assert rec["legacy_desired"] == "EXIT", \
        f"l 硬退出必须贯穿最终包（无投影不得按空仓吞）: {rec['legacy_desired']}"
    assert rec["account_version"]


# ── X1-3：账本读取失败 ≠ 明确空仓 ────────────────────────────────────

def test_ledger_read_failure_is_unknown_not_none(tmp_path, monkeypatch):
    """快照读取失败：无投影 → UNKNOWN 待对账（不当作 NONE/0）；数量投影在账本
    读不到时同样保守未对账。RATIO_ONLY 投影比例独立成立（不依赖账本——回归锚）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(CODE, stock_name="测试股", entry_price=10, ratio=0.1)
    (tmp_path / "account_events.jsonl").write_text('{"corrupt"\n', encoding="utf-8")
    with patch.object(AccountService, "snapshot", side_effect=OSError("disk error")):
        facts = RequestAccountFacts(pm)
        ctx2 = facts.context_for("000001")
        assert ctx2.position_state == "UNKNOWN", \
            f"无投影+读取失败必须 UNKNOWN（X1——不得当作空仓）: {ctx2.position_state}"
        assert ctx2.confirmed_weight is None
        assert "失败" in ctx2.weight_reason or "对账" in ctx2.weight_reason
        # 数量投影在、账本读不到 → 未对账（不冒充已核对）
        pm.add_position("000002", stock_name="有数量投影", entry_price=5, ratio=0.0)
        pm._data["positions"]["000002"]["quantity_fact"] = {
            "quantity": 50, "as_of": "2026-09-20", "avg_cost": 5.0}
        ctx3 = facts.context_for("000002")
        assert ctx3.position_state == "UNKNOWN" and ctx3.confirmed_weight is None
    # RATIO_ONLY 投影比例语义独立成立（账本缺席/失败不影响比例直通——既有语义）
    facts2 = RequestAccountFacts(pm)
    ctx1 = facts2.context_for(CODE)
    assert ctx1.position_state == "HELD" and ctx1.confirmed_weight == pytest.approx(0.1)


# ── X1-4：隔离事件 / 冲突 / 损坏分别验证 ─────────────────────────────

def test_isolated_events_freeze_weight_with_reason(tmp_path, monkeypatch):
    """账本含隔离事件（PARTIAL）→ 持仓保留、权重冻结 None + 待对账原因。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(CODE, stock_name="测试股", entry_price=10, ratio=0.1)
    # 有效事件 + 损坏行（重放隔离留痕——D2 恢复形态）
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    svc.opening_import(opening_cash=10000.0, trade_date="2026-09-20",
                       lots=[{"security_id": CODE, "quantity": 100,
                              "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    with open(account_module.DEFAULT_LEDGER_PATH, "a", encoding="utf-8") as f:
        f.write('{"broken json line"\n')
    snap = svc.snapshot()
    assert snap.isolated_events or snap.data_completeness == "PARTIAL", \
        "前置：损坏行必须进入隔离留痕"
    facts = RequestAccountFacts(pm)
    ctx = facts.context_for(CODE, price=10.0, price_as_of=PRICE_DAY)
    assert ctx.position_state == "HELD", "隔离事件不吞持仓事实"
    assert ctx.confirmed_weight is None, "隔离事件冻结精确权重"
    assert "隔离" in ctx.weight_reason or "对账" in ctx.weight_reason, \
        f"待对账原因必须可读: {ctx.weight_reason}"
    assert ctx.account_version, "版本仍随快照（同请求一致）"


def test_projection_conflict_and_corrupted_portfolio_regression(tmp_path, monkeypatch):
    """数量投影冲突→HELD+权重None；损坏持仓→UNKNOWN（M0 回归锚定不回退）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(CODE, stock_name="测试股", entry_price=10, ratio=0.1)
    AccountService(account_module.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=90000.0, trade_date="2026-09-20",
        lots=[{"security_id": CODE, "quantity": 900,
               "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    pm._data["positions"][CODE]["quantity_fact"] = {
        "quantity": 1000, "as_of": "2026-09-20", "avg_cost": 10.0}
    facts = RequestAccountFacts(pm)
    ctx = facts.context_for(CODE)
    assert ctx.position_state == "HELD" and ctx.confirmed_weight is None
    assert "不一致" in ctx.weight_reason or "对账" in ctx.weight_reason
    # 损坏持仓文件 → UNKNOWN
    (tmp_path / "portfolio.yaml").write_text("{ 损坏", encoding="utf-8")
    pm2 = PortfolioManager()
    facts2 = RequestAccountFacts(pm2)
    assert facts2.context_for(CODE).position_state == "UNKNOWN"


# ── 消费面：影子/建议/摘要以 ctx 为判据 ──────────────────────────────

def test_shadow_captures_held_without_projection(tmp_path, monkeypatch):
    """无投影但 ctx 有仓 → 影子仍捕获（退出方向保留——不再以 pos 存在为前置）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_ledger_only(quantity=100)
    facts = RequestAccountFacts(pm)
    ctx = facts.context_for(CODE, price=10.0, price_as_of=PRICE_DAY)
    from src.data.models import StrategyDecision as SD
    sd = SD(decision=SignalType.SELL, position_action=PositionAction.CLOSE_ALL,
            position_ratio=None, sell_path="fundamental_alert",
            lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
            strategy_reasons=["硬退出"], new_state=StrategyState())
    dr = SimpleNamespace(decision=SimpleNamespace(value="SELL"), score=0.9,
                         stock=SimpleNamespace(stock_code=CODE), warnings=[])
    packet = build_decision_packet(dr, sd, SimpleNamespace(
        effective_action=SimpleNamespace(value="HOLD"), blocked=False),
        confirmed_ratio=ctx.confirmed_weight, position_state=ctx.position_state,
        account_version=ctx.account_version, source="l")
    rec = shadow_module.capture_shadow(
        dr, sd, SimpleNamespace(effective_action=SimpleNamespace(value="HOLD"), blocked=False),
        None,  # 无投影——不再前置 pos 存在
        packet=packet, source="l", store_path=shadow_module.SHADOW_STORE_PATH,
        quote_as_of=PRICE_DAY, account_context=ctx)
    assert rec is not None, "ctx 有仓时影子不得因 pos 缺失缺位"
    assert rec.legacy_desired == "EXIT"
    assert rec.account_version == ctx.account_version


def test_summary_diagnoses_no_projection_holding(tmp_path, monkeypatch):
    """摘要面板以 ctx 为判据：账本有仓无投影显示数量+待对账+退出方向保留；
    合格估值显示派生权重及来源。"""
    from src.cli.main import _print_plain_summary
    pm = PortfolioManager()
    facts = RequestAccountFacts(pm)  # 无账本无投影
    ctx = portfolio_module.AccountContext(
        security_id=CODE, position_state="HELD", quantity=100,
        confirmed_weight=None, weight_reason="缺价格或NAV（账本数量事实；比例视图待重估）",
        account_version="vtest")
    sd = StrategyDecision(decision=SignalType.SELL, position_action=PositionAction.CLOSE_ALL,
                          position_ratio=None, sell_path="fundamental_alert",
                          lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
                          strategy_reasons=["硬退出"], new_state=StrategyState())
    dr = SimpleNamespace(decision=SimpleNamespace(value="SELL"), score=0.9,
                         stock=SimpleNamespace(stock_code=CODE), warnings=[])
    buf = io.StringIO()
    with patch.object(cli_main, "console", Console(file=buf, width=200)):
        _print_plain_summary(dr, sd, _stub_market(CODE), None,
                             account_context=ctx, execution_eval=None)
    text = buf.getvalue()
    assert "100" in text and ("待对账" in text or "投影缺失" in text), \
        f"摘要必须诊断无投影有仓: {text}"
    assert "退出" in text, "退出方向保留必须可见"
    # 合格估值：派生权重+来源显示
    ctx_q = portfolio_module.AccountContext(
        security_id=CODE, position_state="HELD", quantity=900,
        confirmed_weight=0.1, weight_reason="合格估值锁定", account_version="vtest")
    pos_like = SimpleNamespace(entry_price=10.0, current_ratio=0.0, weight_unknown=True,
                               quantity_held=900, trade_plan=None,
                               strategy_state={}, lifecycle="HOLD")
    buf2 = io.StringIO()
    with patch.object(cli_main, "console", Console(file=buf2, width=200)):
        _print_plain_summary(dr, sd, _stub_market(CODE), pos_like,
                             account_context=ctx_q, execution_eval=None)
    text2 = buf2.getvalue()
    assert "10%" in text2 and ("估值" in text2 or "派生" in text2), \
        f"合格估值必须显示派生权重及来源（不打旧比例 0%）: {text2}"


def test_proposal_no_projection_not_fabricated(tmp_path, monkeypatch):
    """无投影有仓：不伪造可确认建议（明确缺口——pos confirm 依赖投影会死路）；
    退出方向由终态包/摘要/影子承载。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_ledger_only(quantity=100)
    sd = StrategyDecision(decision=SignalType.SELL, position_action=PositionAction.CLOSE_ALL,
                          position_ratio=None, sell_path="fundamental_alert",
                          lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
                          strategy_reasons=["硬退出"], new_state=StrategyState())
    prop = pm.record_proposal(CODE, "测试股", sd, source="test")
    assert prop is None, "无投影不伪造可确认建议（待对账缺口由摘要/诊断承载）"
    # 有投影重建仓（旧比例 0+数量事实）建议照常（M0 回归锚定）
    pm2 = PortfolioManager()
    AccountService(account_module.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=10000.0, trade_date="2026-09-20",
        lots=[{"security_id": "000001", "quantity": 100,
               "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    pm2.add_position("000001", stock_name="平安", entry_price=10, ratio=0.0)
    AccountService(account_module.DEFAULT_LEDGER_PATH).confirm_fill(
        "n0p1", "", __import__("src.data.account_service", fromlist=["FillInput"]).FillInput(
            security_id="000001", action="BUY", quantity=100, price=10.0,
            trade_date=PRICE_DAY))
    pm2.apply_quantity_fill("000001", "BUY", quantity=100, price=10.0,
                            trade_date=PRICE_DAY, fill_id="n0p1",
                            quantity_before=0, quantity_after=200, avg_cost=10.0)
    sd_add = StrategyDecision(decision=SignalType.SELL, position_action=PositionAction.REDUCE,
                              position_ratio=None, sell_path="weak_sell",
                              lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
                              strategy_reasons=["弱卖出"], new_state=StrategyState(
                                  current_position_ratio=None))
    prop2 = pm2.record_proposal("000001", "平安", sd_add, source="test")
    assert prop2 is not None and prop2.target_ratio is None, \
        "有投影数量仓 REDUCE 建议保留且不伪造目标（M0 语义不变）"


# ── la 批次：读取次数真断言 ──────────────────────────────────────────

def test_la_batch_snapshot_read_count_is_one(tmp_path, monkeypatch):
    """la 多股：快照读取次数=1（真断言）；下一请求见新成交与新版账户版本。"""
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

    def _stub_two(code):
        return _stub_market(code, price=10.0 if code == "600519" else 5.0)

    with patch.object(AccountService, "snapshot", counting_snapshot), \
            patch.object(akshare_module, "get_stock_data", _stub_two):
        _repl("la")
    assert reads["n"] == 1, f"la 批次快照必须只读一次（N0 真断言）: {reads['n']}"
    records = [json.loads(line) for line in
               shadow_module.SHADOW_STORE_PATH.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    by_code = {r["security_id"]: r for r in records}
    assert {"600519", "000001"} <= set(by_code)
    versions = {r["account_version"] for r in by_code.values()}
    assert len(versions) == 1 and all(versions)
    # 下一请求：确认新成交 → 新实例新数量
    v1 = next(iter(versions))
    svc.confirm_fill("n0f1", v1, __import__(
        "src.data.account_service", fromlist=["FillInput"]).FillInput(
        security_id="600519", action="SELL", quantity=100, price=10.0,
        trade_date=PRICE_DAY))
    facts2 = RequestAccountFacts(pm)
    assert facts2.account_version != v1
    assert facts2.context_for("600519").quantity == 900


def test_ratio_only_no_ledger_still_compat(tmp_path, monkeypatch):
    """旧比例账户无账本：语义零变化（回归锚——N0 不伤 RATIO_ONLY）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(CODE, stock_name="测试股", entry_price=10, ratio=0.1)
    facts = RequestAccountFacts(pm)
    ctx = facts.context_for(CODE)
    assert ctx.position_state == "HELD" and ctx.confirmed_weight == pytest.approx(0.1)
    state = pm.to_strategy_state(CODE, account_context=ctx)
    assert state.current_position_ratio == pytest.approx(0.1)
    assert state.lifecycle != TradeLifecycle.FLAT
