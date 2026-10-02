"""O0 异常账户判定与批量持仓集合回归（plan/fusion iteration8；R14 Y1/Y2 反例）

锁死语义（Y1/Y2 转绿合同）：
1. Y1-病根1：账本读取失败 + 旧数量投影为 0 → 不得 NONE/0（异常源不能用旧零
   投影证明空仓）——UNKNOWN / 权重 None / 数量 None（不反推丢失事件的数量）。
2. Y1-病根2：账本 PARTIAL（隔离事件/截断）且无幸存 lot → 不得 NONE"无持仓记录"
   ——UNKNOWN / 权重 None / 数量 None。
3. 正例保留（R14 已接收范围勿伤）：有效空账本明确 NONE；RATIO_ONLY 无账本 HELD；
   PARTIAL 仍幸存正数量 lot → HELD + 权重冻结；持仓文件损坏 → UNKNOWN。
4. 坏行隔离不自动改原账本（文件字节不变）。
5. Y2：真 REPL `la` 与 cli analyze_portfolio 的批量清单先经同一请求快照形成
   （账本确定有仓 ∪ 兼容投影代码，规范化去重）——账本独有持仓到达同次调用的
   分析/终态/影子记录（捕获 ctx 产物与账户版本，不是输出含代码即过）；
   读取异常时报告不完整/待对账，不输出"当前无持仓"；同批 snapshot 恰读 1 次、
   下一请求读到新版本。

隔离纪律：持久化路径全量重定向临时根；零网络零 AI（行情替身 + 规则 fallback）。
跑法：pytest tests/core/test_o0_exceptional_account.py -q
"""
import io
import json
import os
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

import src.cli.main as cli_main
import src.core.analysis_service as analysis
import src.core.shadow_diff as shadow_module
import src.data.account_service as account_module
import src.data.akshare_client as akshare_module
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
import start as start_module
from src.data.account_service import AccountService
from src.data.models import (
    PositionAction, SignalType, StockData, StrategyDecision,
    StrategyState, TradeLifecycle,
)
from src.data.portfolio import PortfolioManager, RequestAccountFacts

PRICE_DAY = "2026-09-30"
CODE = "600519"


# ── 隔离与构造辅助（与 test_m0_account_terminal 同口径，自包含）────────

def _isolate(tmp_path, monkeypatch):
    """持久化路径全量重定向（先于一切构造）。"""
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


def _seed_ledger_only(*, quantity=100, cash=10000.0, trade_date="2026-09-20"):
    """账本独有持仓（无投影）：期初导入 quantity 股。"""
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    svc.opening_import(opening_cash=cash, trade_date=trade_date,
                       lots=[{"security_id": CODE, "quantity": quantity,
                              "cost_price": 10.0, "acquired_at": trade_date}])
    return svc


def _stub_market(code, *, price=10.0, quote_day=PRICE_DAY):
    return StockData(stock_code=code, stock_name="测试股", price=price, volume=100000,
                     change_pct=1.0, avg_volume_20=100000.0,
                     ma5=9.8, ma20=9.5, ma60=9.0, quote_as_of=quote_day)


def _sd(pos_action="HOLD_POSITION", ratio=0.0, sell_path=None, decision=None):
    decision = decision or ("SELL" if pos_action in ("CLOSE_ALL", "REDUCE") else
                            ("BUY" if pos_action in ("OPEN", "ADD") else "HOLD"))
    return StrategyDecision(
        decision=SignalType(decision), position_action=PositionAction(pos_action),
        position_ratio=ratio, sell_path=sell_path,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        strategy_reasons=["策略理由"], new_state=StrategyState())


def _dr(decision="HOLD", score=0.5):
    return SimpleNamespace(
        decision=SimpleNamespace(value=decision), score=score,
        stock=SimpleNamespace(stock_code=CODE), warnings=[])


def _eval() -> SimpleNamespace:
    from src.core.execution_layer import ExecutionEvaluation
    return ExecutionEvaluation(
        original_action=PositionAction.HOLD_POSITION,
        effective_action=PositionAction.HOLD_POSITION,
        blocked=False, block_reason="",
        slippage_pct=0.0, impact_cost_pct=0.0, total_cost_pct=0.0)


def _repl(cmd: str) -> str:
    parsed = start_module.parse_input(cmd)
    assert parsed is not None, f"REPL 解析失败: {cmd}"
    buf = io.StringIO()
    with patch.object(cli_main, "console", Console(file=buf, width=200)):
        with redirect_stdout(buf):
            start_module.run_cli(parsed[0], parsed[1])
    return buf.getvalue()


def _shadow_records():
    if not shadow_module.SHADOW_STORE_PATH.exists():
        return []
    return [json.loads(line) for line in
            shadow_module.SHADOW_STORE_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _append_event(event):
    """经事件账本追加一条事件（AccountService 无直接 append——账本层写入）。"""
    from src.data.account_snapshot import AccountEventLog
    return AccountEventLog(account_module.DEFAULT_LEDGER_PATH).append(event)


# ── 1. Y1：异常源不得证明空仓 ────────────────────────────────────────

def test_y1_read_failure_zero_projection_cannot_prove_flat(tmp_path, monkeypatch):
    """R14 Y1-1：账本有 100 股、旧数量投影为 0、快照读取失败 → 不得 NONE/0。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position(CODE, stock_name="测试股", entry_price=10.0, ratio=0.0)
    pm._data["positions"][CODE]["quantity_fact"] = {
        "quantity": 0, "as_of": "2026-09-19", "avg_cost": 0.0}
    _seed_ledger_only(quantity=100)  # 账本真实存在且有 100 股（读取失败前入账）
    with patch.object(AccountService, "snapshot", side_effect=OSError("fixture read failure")):
        ctx = pm.request_account_facts().context_for(CODE)
    assert ctx.position_state == "UNKNOWN", \
        f"读取失败+零投影不得证明空仓: {ctx.position_state}（{ctx.weight_reason}）"
    assert ctx.confirmed_weight is None, f"权重必须 None（不给 0 冒充）: {ctx.confirmed_weight}"
    assert ctx.quantity is None, "读取失败时不得反推数量（不编造账本内容）"


def test_y1_partial_ledger_without_lot_cannot_prove_flat(tmp_path, monkeypatch):
    """R14 Y1-2：真实截断账本重放为 PARTIAL 且无幸存 lot → 不得 NONE"无持仓记录"。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()  # 无投影
    account_module.DEFAULT_LEDGER_PATH.write_text('{"truncated_opening_event"\n',
                                                  encoding="utf-8")
    raw_before = account_module.DEFAULT_LEDGER_PATH.read_bytes()
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    snap = svc.snapshot()
    assert snap.isolated_events and snap.data_completeness == "PARTIAL", "构造前提：PARTIAL"
    ctx = pm.request_account_facts().context_for(CODE)
    assert ctx.position_state == "UNKNOWN", \
        f"PARTIAL 无幸存 lot 不得证明空仓: {ctx.position_state}（{ctx.weight_reason}）"
    assert ctx.confirmed_weight is None
    assert ctx.quantity is None, "无剩余 lot 时不得编造 100 股恢复（不反推丢失事件数量）"
    assert account_module.DEFAULT_LEDGER_PATH.read_bytes() == raw_before, \
        "坏行隔离不得自动改写原账本（原件保留供人工恢复）"


# ── 2. 正例保留（R14 已接收范围，回归勿伤）────────────────────────────

def test_valid_empty_ledger_is_explicit_none(tmp_path, monkeypatch):
    """有效空账本（健康读取、无持仓、无投影）→ 明确 NONE（既有语义）。"""
    _isolate(tmp_path, monkeypatch)
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    svc.opening_import(opening_cash=10000.0, trade_date="2026-09-20")  # 只导现金
    pm = PortfolioManager()
    ctx = pm.request_account_facts().context_for(CODE)
    assert ctx.position_state == "NONE", f"有效空账本必须明确 NONE: {ctx.position_state}"
    assert ctx.quantity == 0 and ctx.confirmed_weight == 0.0


def test_ratio_only_without_ledger_still_held(tmp_path, monkeypatch):
    """RATIO_ONLY 无账本 → HELD/比例直通（R14 已接收正例，勿伤）。"""
    _isolate(tmp_path, monkeypatch)
    assert not account_module.DEFAULT_LEDGER_PATH.exists()
    pm = PortfolioManager()
    pm.add_position(CODE, entry_price=10, ratio=.1)
    ctx = pm.request_account_facts().context_for(CODE)
    assert ctx.position_state == "HELD" and ctx.confirmed_weight == .1


def test_partial_with_surviving_lot_holds_and_freezes_weight(tmp_path, monkeypatch):
    """PARTIAL 但幸存正数量 lot → HELD/数量事实 + 权重冻结（R14 已接收，回归）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()  # 无投影
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    svc.opening_import(opening_cash=10000.0, trade_date="2026-09-20",
                       lots=[{"security_id": CODE, "quantity": 100,
                              "cost_price": 10.0, "acquired_at": "2026-09-20"}])
    # 幽灵卖出（无持仓的 SELL）→ 整事件隔离 → PARTIAL；期初 100 股幸存
    from src.data.account_snapshot import AccountEvent, EventType
    _append_event(AccountEvent(event_id="ghost_1", event_type=EventType.SELL,
                               security_id="000001", trade_date="2026-09-21",
                               quantity=50, cash_delta=500.0))
    snap = svc.snapshot()
    assert snap.data_completeness == "PARTIAL" and snap.isolated_events
    assert any(h.security_id == CODE and h.quantity == 100 for h in snap.holdings)
    ctx = pm.request_account_facts().context_for(CODE)
    assert ctx.position_state == "HELD", f"幸存 lot 持仓必须保留: {ctx.position_state}"
    assert ctx.quantity == 100, "独立成立的正数量证据必须如实给出"
    assert ctx.confirmed_weight is None, "PARTIAL 冻结精确权重（待对账）"
    assert "待对账" in ctx.weight_reason


def test_corrupted_portfolio_yaml_is_unknown(tmp_path, monkeypatch):
    """持仓文件损坏 → UNKNOWN（G01 损坏保护既有语义，回归）。"""
    _isolate(tmp_path, monkeypatch)
    from pathlib import Path
    Path(portfolio_module.DEFAULT_PORTFOLIO_PATH).write_text("{broken yaml", encoding="utf-8")
    pm = PortfolioManager()
    assert pm._corrupted
    ctx = pm.request_account_facts().context_for(CODE)
    assert ctx.position_state == "UNKNOWN"


# ── 3. Y2：批量清单先经同一请求快照形成 ───────────────────────────────

def test_y2_repl_la_reaches_ledger_only_holding(tmp_path, monkeypatch):
    """真 REPL `la`：账本独有 100 股到达同次调用的分析/影子记录/输出（R14 Y2）。"""
    _isolate(tmp_path, monkeypatch)
    svc = _seed_ledger_only(quantity=100)
    version_before = svc.account_version()
    reads = {"n": 0}
    real_snapshot = AccountService.snapshot

    def counting_snapshot(self):
        reads["n"] += 1
        return real_snapshot(self)

    with patch.object(AccountService, "snapshot", counting_snapshot), \
            patch.object(akshare_module, "get_stock_data", lambda c: _stub_market(c)):
        out = _repl("la")
    assert "当前无持仓记录" not in out, f"账本有仓不得输出无持仓: {out[-400:]}"
    assert CODE in out, "输出必须含账本独有代码"
    records = _shadow_records()
    recs = [r for r in records if r["security_id"] == CODE]
    assert recs, f"同次调用必须产出该持仓的影子记录（终态捕获，非输出含代码即过）: {records}"
    versions = {r["account_version"] for r in recs}
    assert versions == {version_before}, f"影子记录必须带同一账户版本: {versions}"
    assert reads["n"] == 1, f"同批 snapshot 必须恰读 1 次（先快照后清单）: {reads['n']}"


def test_y2_repl_la_read_failure_reports_incomplete(tmp_path, monkeypatch):
    """账本/持仓库读取异常时 `la` 报告不完整/待对账——不得输出"当前无持仓"。"""
    _isolate(tmp_path, monkeypatch)
    from pathlib import Path
    Path(portfolio_module.DEFAULT_PORTFOLIO_PATH).write_text("{broken yaml", encoding="utf-8")
    out = _repl("la")
    assert "当前无持仓记录" not in out, f"异常不得当空仓输出: {out[-400:]}"
    assert ("待对账" in out) or ("不完整" in out), f"必须报告不完整/待对账: {out[-400:]}"


def test_y2_repl_la_mixed_sources_no_leak_no_dup(tmp_path, monkeypatch):
    """投影独有、账本投影重叠、账本独有混合——三只都进同次批量分析，不漏不重。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("000001", stock_name="投影独有", entry_price=5, ratio=0.2)   # A：仅投影
    pm.add_position(CODE, stock_name="重叠持仓", entry_price=10, ratio=0.1)      # B：两边都有
    svc = _seed_ledger_only(quantity=100)                                        # B 账本侧
    from src.data.account_snapshot import AccountEvent, EventType
    _append_event(AccountEvent(event_id="open_ledger_only", event_type=EventType.BUY,
                               security_id="000688", trade_date="2026-09-22",
                               quantity=200, price=8.0, cash_delta=-1600.0,
                               lot_cost_price=8.0))                              # C：仅账本

    def _stub_three(code):
        return _stub_market(code, price={"000001": 5.0, "000688": 8.0}.get(code, 10.0))

    with patch.object(cli_main, "_cli_rag", lambda: None), \
            patch.object(akshare_module, "get_stock_data", _stub_three):
        out = _repl("la")
    records = _shadow_records()
    by_code = {r["security_id"]: r for r in records}
    assert {"000001", "000688", CODE} <= set(by_code), \
        f"三只都须有同次调用的终态影子记录: {by_code.keys()}（输出: {out[-300:]}）"
    versions = {r["account_version"] for r in by_code.values()}
    assert len(versions) == 1 and all(versions), f"同批次共享同一账户版本: {versions}"
    for code in ("000001", "000688", CODE):
        assert code in out, f"输出必须含 {code}（批量逐只卡）"


def test_y2_cli_analyze_portfolio_reaches_ledger_only(tmp_path, monkeypatch):
    """cli analyze_portfolio 与 la 同步：账本独有持仓进入同次分析终态包。"""
    _isolate(tmp_path, monkeypatch)
    _seed_ledger_only(quantity=100)  # 账本独有，无投影
    packets = []
    real_build = analysis.build_decision_packet

    def build(*args, **kwargs):
        pkt = real_build(*args, **kwargs)
        packets.append((kwargs.get("position_state"), kwargs.get("account_version"), pkt))
        return pkt

    with patch.object(cli_main, "_cli_rag", lambda: None), \
            patch.object(akshare_module, "get_stock_data", lambda c: _stub_market(c)), \
            patch.object(analysis, "build_decision_packet", build):
        from rich.console import Console as _C
        with patch.object(cli_main, "console", _C(file=io.StringIO(), width=200)):
            cli_main.analyze_portfolio()
    assert packets, "终态包必须构造（捕获同调用 ctx 产物）"
    states = {c for c, _, _ in packets}
    assert "HELD" in states, f"账本独有持仓必须按 ctx HELD 进终态包: {packets[:2]}"
    version, pkt = packets[-1][1], packets[-1][2]
    assert version, f"终态包必须带账户版本: {version}"
    assert pkt.desired_action.value in ("EXIT", "REDUCE", "HOLD", "WAIT", "REVIEW",
                                        "OPEN", "ADD")


def test_y2_chat_get_portfolio_reports_ledger_only(tmp_path, monkeypatch):
    """同类接线回归：chat 查仓在投影空+账本有仓时如实提示（不谎报无持仓）。"""
    _isolate(tmp_path, monkeypatch)
    import src.chat.tools as chat_tools
    _seed_ledger_only(quantity=100)
    # 显式注入隔离实例——chat_tools._portfolio_manager 是模块级单例，
    # 其他测试可能已设置过（全量顺序依赖防呆）
    monkeypatch.setattr(chat_tools, "_portfolio_manager", PortfolioManager(),
                        raising=False)
    out = chat_tools.get_portfolio()
    assert "当前无持仓记录" not in out, f"账本有仓不得谎报无持仓: {out}"
    assert CODE in out, f"提示必须含账本独有代码: {out}"


def test_y2_repl_la_partial_ledger_reports_incomplete(tmp_path, monkeypatch):
    """guard O批 P1 回归：账本 PARTIAL 时 holding_entries 必须 incomplete=True——
    la 不得经新清单路径输出"当前无持仓"（逐股 ctx UNKNOWN 已由 Y1-2 锁死）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()  # 无投影
    account_module.DEFAULT_LEDGER_PATH.write_text('{"truncated_opening_event"\n',
                                                  encoding="utf-8")
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    snap = svc.snapshot()
    assert snap.data_completeness == "PARTIAL", "构造前提：PARTIAL"
    facts = pm.request_account_facts()
    entries, incomplete = facts.holding_entries()
    assert incomplete is True, f"PARTIAL 清单必须标记 incomplete: {incomplete}"
    out = _repl("la")
    assert "当前无持仓记录" not in out, f"PARTIAL 不得当空仓输出: {out[-400:]}"
    assert ("待对账" in out) or ("不完整" in out), f"必须报告不完整/待对账: {out[-400:]}"


def test_y2_la_single_failure_does_not_abort_batch(tmp_path, monkeypatch):
    """guard O批 P0 回归：单只分析抛非 SystemExit 异常不中止整批——
    handler 不得与循环变量撞名（as e 会把 e 重绑为异常对象，取 .stock_code
    即 AttributeError 且从 handler 传播，剩余持仓全部不分析）。"""
    _isolate(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("000001", stock_name="投影独有", entry_price=5, ratio=0.2)
    _seed_ledger_only(quantity=100)

    real_analyze = cli_main.analyze_live

    def _flaky(code, **kw):
        if code == "000001":  # 第一只（投影优先序）失败
            raise ValueError("fixture analyze failure")
        return real_analyze(code, **kw)

    monkeypatch.setattr(cli_main, "analyze_live", _flaky)
    with patch.object(cli_main, "_cli_rag", lambda: None), \
            patch.object(akshare_module, "get_stock_data", lambda c: _stub_market(c, price=5.0)):
        out = _repl("la")
    assert "分析失败" in out and "fixture analyze failure" in out, \
        f"失败股必须有回执: {out[-300:]}"
    # 失败股之后的其他持仓仍被分析（600519 账本独有排其后——P0 场景必达）
    records = _shadow_records()
    assert any(r["security_id"] == CODE for r in records), \
        f"单只失败不得中止整批（600519 须仍被分析）: {[r['security_id'] for r in records]}"


def test_y2_next_request_reads_new_version(tmp_path, monkeypatch):
    """同批 1 次、下一请求新版本：账本追加事件后新建 RequestAccountFacts 读到新版本。"""
    _isolate(tmp_path, monkeypatch)
    svc = _seed_ledger_only(quantity=100)
    pm = PortfolioManager()
    facts1 = pm.request_account_facts()
    v1 = facts1.account_version
    assert v1, "首次请求必须读到账户版本"
    # 同请求内重复取清单不再读快照（holding_entries 消费构造时缓存）
    with patch.object(AccountService, "snapshot",
                      side_effect=AssertionError("同请求不得重复读快照")):
        entries, incomplete = facts1.holding_entries()
    assert incomplete is False and [e.stock_code for e in entries] == [CODE]
    # 账本追加事件（新成交入账）→ 下一请求读到新版本与新持仓事实
    from src.data.account_snapshot import AccountEvent, EventType
    _append_event(AccountEvent(event_id="dep_next", event_type=EventType.DEPOSIT,
                               trade_date="2026-09-22", cash_delta=1000.0))
    facts2 = pm.request_account_facts()
    assert facts2.account_version != v1, \
        f"下一请求必须读到新账户版本: {facts2.account_version} vs {v1}"
    assert facts2._quantities.get(CODE) == 100
