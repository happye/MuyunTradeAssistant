"""L1 数量账户读取资格回归测试（plan/fusion iteration5，DELIVERY_PLAN L1；R11 V3 反例）

锁死语义（数量决定有无仓，估值与 NAV 决定权重资格；T01 账户事实统一读出口）：
1. V3 场景1：部分卖出后 ratio_stale=true —— 策略状态不得再拿过期比例冒充权重
   （current_position_ratio=None=有仓但权重未知），组合合计如实未知
2. V3 场景2：旧比例 0、数量买入重建仓 —— 有仓（不得按空仓分析/漏建议）
3. 合格估值（价格与 NAV 同日窗）→ 权重=数量×价格/NAV 用已知数字锁定；时点不齐拒精确量
4. RATIO_ONLY 旧账户语义零变化（float 比例直通）
5. 未知权重：精确新增被阻（不吞持仓事实），硬风险/退出意图保留（不按 flat_sell 吞）
6. record_proposal 不因旧比例 0 漏掉重建仓建议；REDUCE 权重未知 → 建议不带伪造目标
7. today 对权重待重估持仓如实显示，不用过期比例做超限误报
8. 纵向：真实 REPL（pos confirm --qty）→ 重启 → 读取 → today 贯穿

隔离纪律同 K0a e2e：三条持久化路径显式重定向并断言在临时根内；零网络零 AI。
跑法：pytest tests/core/test_l1_account_reading.py -q
"""
import io
import os
import sys
from datetime import datetime
from unittest.mock import patch

import pytest
from rich.console import Console

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 纪律（LRN-20260925-015）：patch 前先预导入被 patch 的真实模块
import src.cli.main as cli_main
import src.core.shadow_diff as shadow_module
import src.data.account_service as account_module
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
import start as start_module
from src.cli.today_service import build_today_view
from src.core.decision_policy import HorizonPlan
from src.data.account_service import AccountService
from src.data.models import (
    DecisionResult, DecisionTrace, MarketState, SignalType, StockData,
    StrategyState, TradeLifecycle,
)
from src.data.portfolio import PortfolioManager


PRICE_DAY = "2026-09-30"


def _isolate_paths(tmp_path, monkeypatch):
    """隔离重定向（必须先于一切构造）——返回 (portfolio_path, ledger_path)。"""
    portfolio_path = tmp_path / "portfolio.yaml"
    proposals_path = tmp_path / "proposals.json"
    ledger_path = tmp_path / "account_events.jsonl"
    plans_path = tmp_path / "horizon_plans.json"
    monkeypatch.setattr(portfolio_module, "DEFAULT_PORTFOLIO_PATH", str(portfolio_path))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH", str(proposals_path))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", ledger_path)
    monkeypatch.setattr(shadow_module, "SHADOW_STORE_PATH", tmp_path / "shadow_diff.jsonl")
    import src.data.horizon_plans as horizon_plans_module
    monkeypatch.setattr(horizon_plans_module, "PLANS_FILE", plans_path)
    root = str(tmp_path.resolve())
    for p in (str(portfolio_path), str(proposals_path), str(ledger_path), str(plans_path)):
        assert os.path.abspath(p).startswith(root), f"持久化路径越出临时根: {p}"
    return portfolio_path, ledger_path


def _seed_with_quantity(pm, *, ratio, ledger_path, quantity=1000, price=10.0,
                        trade_date="2026-09-20"):
    """比例视图 + 期初数量账本（数量账户底座）。"""
    pm.add_position("600519", stock_name="测试股", entry_price=price, ratio=ratio)
    svc = AccountService(ledger_path)
    svc.opening_import(opening_cash=100000.0, trade_date=trade_date,
                       lots=[{"security_id": "600519", "quantity": quantity,
                              "cost_price": price, "acquired_at": trade_date}])
    return svc


# ── V3 场景（探针同形）──────────────────────────────────────────

def test_v3_partial_sell_weight_unknown_not_stale(tmp_path, monkeypatch):
    """场景1：10% 旧比例、部分卖后剩 900 股 —— 策略状态权重必须未知（不拿 0.1 冒充），
    组合合计不得用过期比例报数。"""
    _isolate_paths(tmp_path, monkeypatch)
    pm = PortfolioManager()
    svc = _seed_with_quantity(pm, ratio=0.1, ledger_path=account_module.DEFAULT_LEDGER_PATH)
    out = pm.apply_quantity_fill("600519", "SELL", quantity=100, price=10,
                                 trade_date="2026-09-30", fill_id="l1partial",
                                 quantity_before=1000, quantity_after=900, avg_cost=10)
    assert out.ok
    p = pm.get_position("600519")
    state = pm.to_strategy_state("600519")
    assert p.ratio_stale is True
    assert p.quantity_held == 900
    assert state.current_position_ratio is None, \
        f"过期比例不得冒充策略权重（L1 合同 None=未知）: {state.current_position_ratio}"
    assert pm.get_total_position_ratio() is None, \
        "含权重未知持仓时组合合计不得假装是已知数字"
    # 有仓语义：生命周期不得是 FLAT（数量>0 决定有仓；部分卖出不改既有生命周期）
    assert state.lifecycle != TradeLifecycle.FLAT
    # 注：本用例直调投影（不经 CLI 账本事务）——账本事实由竖向用例覆盖


def test_v3_rebuilt_holding_has_position_not_flat(tmp_path, monkeypatch):
    """场景2：旧比例 0、数量买入 100 股 —— 有仓（不得按空仓分析）。"""
    _isolate_paths(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_with_quantity(pm, ratio=0.0, ledger_path=account_module.DEFAULT_LEDGER_PATH)
    out = pm.apply_quantity_fill("600519", "BUY", quantity=100, price=10,
                                 trade_date="2026-09-30", fill_id="l1rebuild",
                                 quantity_before=0, quantity_after=100, avg_cost=10)
    assert out.ok
    p = pm.get_position("600519")
    state = pm.to_strategy_state("600519")
    assert p.ratio_stale is True and p.quantity_held == 100
    assert state.current_position_ratio is None, \
        "重建仓不得因旧比例 0 被当成空仓（0 不是未知）"
    assert state.lifecycle == TradeLifecycle.OPEN
    assert pm.has_position("600519")


# ── 合格估值锁定 / RATIO_ONLY 兼容 ──────────────────────────────

def test_qualified_valuation_locks_weight(tmp_path, monkeypatch):
    """价格与 NAV 同日窗 → 权重=数量×价格/NAV（900×10/90000=0.1，已知数字锁定）。"""
    _isolate_paths(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_with_quantity(pm, ratio=0.1, ledger_path=account_module.DEFAULT_LEDGER_PATH)
    pm.apply_quantity_fill("600519", "SELL", quantity=100, price=10,
                           trade_date="2026-09-30", fill_id="l1q1",
                           quantity_before=1000, quantity_after=900, avg_cost=10)
    state = pm.to_strategy_state("600519", price=10.0, price_as_of=PRICE_DAY,
                                 nav=90000.0, nav_as_of=PRICE_DAY)
    assert state.current_position_ratio == pytest.approx(900 * 10.0 / 90000.0), \
        "合格估值必须用已知数字锁定权重"


def test_pricing_window_mismatch_rejects_precise_weight(tmp_path, monkeypatch):
    """时点不齐（价格日≠NAV 日 / 缺 NAV / 缺价格时点）→ 权重未知——拒精确量。"""
    _isolate_paths(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_with_quantity(pm, ratio=0.1, ledger_path=account_module.DEFAULT_LEDGER_PATH)
    pm.apply_quantity_fill("600519", "SELL", quantity=100, price=10,
                           trade_date="2026-09-30", fill_id="l1q2",
                           quantity_before=1000, quantity_after=900, avg_cost=10)
    cases = [
        dict(price=10.0, price_as_of="2026-09-29", nav=90000.0, nav_as_of=PRICE_DAY),
        dict(price=10.0, price_as_of=PRICE_DAY, nav=None, nav_as_of=None),
        dict(price=10.0, price_as_of=None, nav=90000.0, nav_as_of=PRICE_DAY),
    ]
    for kw in cases:
        state = pm.to_strategy_state("600519", **kw)
        assert state.current_position_ratio is None, \
            f"时点不齐/缺失必须拒精确权重: {kw} -> {state.current_position_ratio}"


def test_ratio_only_account_unchanged(tmp_path, monkeypatch):
    """RATIO_ONLY 旧账户（无数量事实）语义零变化：float 比例直通、合计为数字。"""
    _isolate_paths(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.1)
    pm.add_position("000001", stock_name="平安", entry_price=5, ratio=0.2)
    state = pm.to_strategy_state("600519")
    assert state.current_position_ratio == pytest.approx(0.1), \
        "RATIO_ONLY 账户必须保持既有 float 比例语义"
    assert pm.get_total_position_ratio() == pytest.approx(0.3)
    # 旧 fixture 兼容：ratio_stale 缺省 False、quantity_fact 缺省 None
    p = pm.get_position("600519")
    assert p.ratio_stale is False and p.quantity_fact is None


def test_total_ratio_unknown_when_stale_present(tmp_path, monkeypatch):
    """混合账户：RATIO_ONLY 0.2 + 数量账户权重未知 → 合计未知（不拿部分和冒充总仓位）。"""
    _isolate_paths(tmp_path, monkeypatch)
    pm = PortfolioManager()
    pm.add_position("000001", stock_name="平安", entry_price=5, ratio=0.2)
    _seed_with_quantity(pm, ratio=0.1, ledger_path=account_module.DEFAULT_LEDGER_PATH)
    pm.apply_quantity_fill("600519", "SELL", quantity=100, price=10,
                           trade_date="2026-09-30", fill_id="l1q3",
                           quantity_before=1000, quantity_after=900, avg_cost=10)
    assert pm.get_total_position_ratio() is None


# ── 策略层合同：未知权重阻精确新增、保留退出意图（直调 _calculate_position——
#    被测对象=策略层 None 合同；全 orchestrator 含外源数据副作用，不在单测范围）──

_BULL_TRACE = [DecisionTrace(step="市场状态影响", description="test",
                             data={"final_scores": {"BUY": 0.9, "SELL": 0.0}})]
_BEAR_TRACE = [DecisionTrace(step="市场状态影响", description="test",
                             data={"final_scores": {"BUY": 0.0, "SELL": 0.9}})]


def _stock():
    return StockData(stock_code="600519", stock_name="测试股", price=10.0,
                     volume=100000, change_pct=1.0)


def _decision(signal: SignalType, trace) -> DecisionResult:
    from src.data.models import PositionAction
    action = PositionAction.ADD if signal is SignalType.BUY else (
        PositionAction.REDUCE if signal is SignalType.SELL else PositionAction.HOLD_POSITION)
    return DecisionResult(stock=_stock(), state=MarketState.RISK_ON, decision=signal,
                          score=0.9, signals=[], trace=trace,
                          position_action=action, position_ratio=0.3)


def _calc(signal: SignalType, trace, ratio):
    """直调真实 _calculate_position（策略层仓位合同）。"""
    from src.core.strategy_layer import StrategyLayer
    return StrategyLayer()._calculate_position(
        signal, MarketState.RISK_ON, _decision(signal, trace),
        StrategyState(lifecycle=TradeLifecycle.HOLD, entry_price=10.0,
                      current_position_ratio=ratio))


def test_unknown_weight_blocks_precise_add(tmp_path, monkeypatch):
    """未知权重持仓 + BUY：不得给精确加仓（ADD）——有仓事实保留（HOLD）。"""
    _isolate_paths(tmp_path, monkeypatch)
    action, target = _calc(SignalType.BUY, _BULL_TRACE, None)
    assert action.value != "ADD", f"未知权重不得给精确加仓: {action}"
    assert action.value == "HOLD_POSITION", \
        f"阻精确新增必须保留持仓事实（HOLD 而非清仓/观望）: {action}"
    # 对照1：已知权重（RATIO_ONLY）→ 既有 ADD 语义
    action_k, target_k = _calc(SignalType.BUY, _BULL_TRACE, 0.1)
    assert action_k.value == "ADD", f"已知权重加仓语义不得被 L1 改动: {action_k}"
    # 对照2（守卫 P1-1 回归）：真无持仓（0.0+FLAT）→ 建仓语义 OPEN 不被阻——
    # None 只允许来自「有仓未知」，无持仓必须保持 0.0（防 None 语义误伤建仓）
    from src.core.strategy_layer import StrategyLayer
    action_flat, target_flat = StrategyLayer()._calculate_position(
        SignalType.BUY, MarketState.RISK_ON, _decision(SignalType.BUY, _BULL_TRACE),
        StrategyState())  # 默认 FLAT + 0.0
    assert action_flat.value == "OPEN", \
        f"无持仓（0.0）建仓语义不得被 None 合同误伤: {action_flat}"


def test_exit_intent_preserved_unknown_weight(tmp_path, monkeypatch):
    """未知权重持仓 + 强 SELL：退出意图保留（不按空仓吞掉），减仓目标不伪造。"""
    _isolate_paths(tmp_path, monkeypatch)
    action, target = _calc(SignalType.SELL, _BEAR_TRACE, None)
    assert action.value in ("REDUCE", "CLOSE_ALL"), \
        f"强卖出必须保留退出意图: {action}"
    if action.value == "REDUCE":
        assert target is None, \
            f"未知权重的减仓目标不得用过期比例伪造: {target}"
    # 对照：空仓（已知 0）→ STAY_OUT 既有语义不变
    action_flat, _ = _calc(SignalType.SELL, _BEAR_TRACE, 0.0)
    assert action_flat.value == "STAY_OUT"
    # 退出路径推断：None 不得推断 flat_sell（吞掉退出意图）
    from src.core.strategy_layer import StrategyLayer
    sell_path = StrategyLayer()._infer_sell_path(
        SignalType.SELL, action, _decision(SignalType.SELL, _BEAR_TRACE),
        StrategyState(lifecycle=TradeLifecycle.HOLD, entry_price=10.0,
                      current_position_ratio=None))
    assert sell_path != "flat_sell", f"有仓（数量事实）不得推断 flat_sell: {sell_path}"


# ── 建议与 today 消费者 ─────────────────────────────────────────

def test_record_proposal_rebuilt_not_swallowed(tmp_path, monkeypatch):
    """record_proposal 守卫：非持仓判定数量感知（旧比例 0+数量>0 不再判非持仓）。
    注：本用例手工构造 ADD 决策以直接测守卫本身——真实链路中未知权重会被策略层
    阻 ADD（见 test_unknown_weight_blocks_precise_add），重建仓出 ADD 建议的可达
    场景是合格估值锁定权重之后。"""
    _isolate_paths(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_with_quantity(pm, ratio=0.0, ledger_path=account_module.DEFAULT_LEDGER_PATH)
    pm.apply_quantity_fill("600519", "BUY", quantity=100, price=10,
                           trade_date="2026-09-30", fill_id="l1p1",
                           quantity_before=0, quantity_after=100, avg_cost=10)
    from src.data.models import PositionAction, StrategyDecision
    sd = StrategyDecision(decision=SignalType.BUY, position_action=PositionAction.ADD,
                          position_ratio=0.3, lifecycle_before=TradeLifecycle.HOLD,
                          lifecycle_after=TradeLifecycle.HOLD,
                          strategy_reasons=["理由"], new_state=StrategyState())
    prop = pm.record_proposal("600519", "测试股", sd, source="test")
    assert prop is not None, "重建仓已持股——ADD 建议不得因旧比例 0 被吞"
    # REDUCE + 权重未知 → 建议保留但不伪造目标数字
    # （真实链路里 strategy 层 REDUCE-未知 返回 new_state.current_position_ratio=None）
    sd_red = StrategyDecision(decision=SignalType.SELL, position_action=PositionAction.REDUCE,
                              position_ratio=None, sell_path="weak_sell",
                              lifecycle_before=TradeLifecycle.HOLD,
                              lifecycle_after=TradeLifecycle.HOLD,
                              strategy_reasons=["弱卖出"],
                              new_state=StrategyState(current_position_ratio=None))
    prop2 = pm.record_proposal("600519", "测试股", sd_red, source="test")
    assert prop2 is not None, "未知权重的减仓意图不得被吞"
    assert prop2.target_ratio is None, "REDUCE 权重未知 → 不得伪造目标仓位"


def test_today_unknown_weight_no_false_overcap(tmp_path, monkeypatch):
    """today：权重待重估持仓如实显示（股数+待重估），不用过期比例做超限误报。"""
    _isolate_paths(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_with_quantity(pm, ratio=0.1, ledger_path=account_module.DEFAULT_LEDGER_PATH)
    pm.apply_quantity_fill("600519", "SELL", quantity=100, price=10,
                           trade_date="2026-09-30", fill_id="l1t1",
                           quantity_before=1000, quantity_after=900, avg_cost=10)
    view = build_today_view(pm, risk_profile={"per_stock_max": 0.05})
    holding = next(c for c in view.holding if c.stock_code == "600519")
    text = "\n".join(holding.lines)
    assert "待重估" in text, f"today 必须显示权重待重估: {text}"
    assert "900" in text, f"today 应显示数量事实（900 股）: {text}"
    assert not view.notices or not any("600519" in n and "超限" in n for n in view.notices), \
        "过期比例不得触发超限误报（权重未知不给精确额度判断）"


# ── 纵向：真实 REPL → 重启 → 读取 → today ──────────────────────

def test_v3_vertical_repl_confirm_restart_today(tmp_path, monkeypatch):
    """真实 REPL：pos confirm --qty 部分卖出 → 重启（新实例读盘）→ 读取权重未知 →
    today 如实显示。"""
    _isolate_paths(tmp_path, monkeypatch)
    pm = PortfolioManager()
    _seed_with_quantity(pm, ratio=0.1, ledger_path=account_module.DEFAULT_LEDGER_PATH)
    # pos confirm 需依据待确认建议（K0a e2e 同款前置）
    from src.data.proposals import Proposal
    pm._proposals.add_proposal(Proposal(stock_code="600519", stock_name="测试股",
                                        position_action="REDUCE", target_ratio=0.05,
                                        current_ratio=0.1))
    assert pm._proposals._save()
    parsed = start_module.parse_input(
        "pos confirm 600519 --qty 100 --price 10 --date 2026-09-30")
    assert parsed is not None and parsed[0] == "pos_confirm", f"REPL 解析失败: {parsed}"
    output = io.StringIO()
    with patch.object(cli_main, "console", Console(file=output, width=200)):
        start_module.run_cli(parsed[0], parsed[1])

    # 重启：新 PortfolioManager 实例读盘 —— 读取资格贯穿
    pm2 = PortfolioManager()
    state = pm2.to_strategy_state("600519")
    assert state.current_position_ratio is None, \
        "重启后读取必须反映数量事实（权重未知），不得回退到过期比例"
    assert pm2.get_total_position_ratio() is None
    svc = AccountService(account_module.DEFAULT_LEDGER_PATH)
    assert sum(h.quantity for h in svc.snapshot().holdings) == 900
    view = build_today_view(pm2, risk_profile=None)
    # 部分卖出后建议为 PARTIAL（待确认）——持仓卡在 needs_action 桶；两桶都找
    cards = [c for c in (view.holding + view.needs_action) if c.stock_code == "600519"]
    assert cards, "today 必须显示该持仓"
    assert any("待重估" in "\n".join(c.lines) for c in cards), \
        f"today 必须显示权重待重估: {[c.lines for c in cards]}"
