"""E0 T+1 批次份额模式回归测试（plan/fusion，backtest_engine.t_plus_1_lot_mode）

锁死语义（G11：当天加仓后卖出旧份额——可卖量按交易批次）：
1. 默认关（t_plus_1_lot_mode=False）：走既有单标量 buy_date 整仓判定——现行为
   字节级不变（ISS-065 基线安全）
2. lot 模式开：可卖 = 持仓 − 当日新买；旧份额当日可卖、当日新买不可卖
3. 整仓均为当日新买 → 拦截并计入 _t1_lot_blocked_count
4. min_hold_days 仍仅 legacy_compatible 生效（与 _can_sell 同口径）
5. SimulatedAccount.sell(shares=部分) 支持末日强平只清可卖批次

离线测试（引擎构造不触网）。跑法：pytest tests/core/test_backtest_t1_lot_mode.py -q
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from src.core.backtest_engine import BacktestEngine, SimulatedAccount


@pytest.fixture(scope="module")
def eng_legacy():
    return BacktestEngine(stock_code="600519", start_date="2024-01-01",
                          end_date="2024-03-01", skills_dir="./src/skills")


@pytest.fixture(scope="module")
def eng_lot():
    return BacktestEngine(stock_code="600519", start_date="2024-01-01",
                          end_date="2024-03-01", skills_dir="./src/skills",
                          t_plus_1_lot_mode=True)


def _acct(position, buy_date="2024-01-02"):
    a = SimulatedAccount(initial_capital=100000.0)
    a.position = position
    a.buy_date = buy_date
    return a


# ── 1. 默认路径不变 ─────────────────────────────────────────

def test_legacy_mode_delegates_to_can_sell(eng_legacy):
    """默认关：当日建仓 → 整仓不可卖（单标量口径），可卖股数随 ok 返回。"""
    acct = _acct(1000, buy_date="2024-03-01")  # buy_date == 当日
    ok, sellable = eng_legacy._can_sell_today("2024-03-01", acct, bought_today=1000)
    assert ok is False and sellable == 0
    # 次日整仓可卖
    ok2, sellable2 = eng_legacy._can_sell_today("2024-03-02", acct, bought_today=0)
    assert ok2 is True and sellable2 == 1000


def test_engine_default_flag_off(eng_legacy):
    assert eng_legacy.t_plus_1_lot_mode is False


# ── 2. lot 模式批次份额 ─────────────────────────────────────

def test_lot_mode_old_shares_sellable_same_day(eng_lot):
    """G11 正确答案：当天加仓后，旧份额当日可卖、新份额锁到次日。"""
    acct = _acct(2000, buy_date="2024-01-02")  # 建仓于此前
    ok, sellable = eng_lot._can_sell_today("2024-03-01", acct, bought_today=1000)
    assert ok is True and sellable == 1000  # 持仓 2000 − 当日新买 1000


def test_lot_mode_all_bought_today_blocked(eng_lot):
    acct = _acct(1000, buy_date="2024-03-01")
    before = eng_lot._t1_lot_blocked_count
    ok, sellable = eng_lot._can_sell_today("2024-03-01", acct, bought_today=1000)
    assert ok is False and sellable == 0
    assert eng_lot._t1_lot_blocked_count == before + 1


def test_lot_mode_no_position_no_counter(eng_lot):
    before = eng_lot._t1_lot_blocked_count
    ok, sellable = eng_lot._can_sell_today("2024-03-01", _acct(0), bought_today=0)
    assert ok is False and sellable == 0
    assert eng_lot._t1_lot_blocked_count == before  # 空仓不计拦截


def test_lot_mode_framework_strict_ignores_min_hold(eng_lot):
    """framework_strict 下 min_hold_days 不生效（策略层状态机接管）；批次判定不受影响。"""
    assert eng_lot.execution_mode == BacktestEngine.MODE_FRAMEWORK_STRICT
    acct = _acct(1000, buy_date="2024-02-28")  # 两天前建仓
    ok, sellable = eng_lot._can_sell_today("2024-03-01", acct, bought_today=0)
    assert ok is True and sellable == 1000


def test_lot_mode_legacy_compatible_keeps_min_hold():
    eng = BacktestEngine(stock_code="600519", start_date="2024-01-01",
                         end_date="2024-03-01", skills_dir="./src/skills",
                         execution_mode=BacktestEngine.MODE_LEGACY_COMPATIBLE,
                         min_hold_days=5, t_plus_1_lot_mode=True)
    acct = _acct(1000, buy_date="2024-02-28")  # 持有 2 天 < min_hold 5
    ok, sellable = eng._can_sell_today("2024-03-01", acct, bought_today=0)
    assert ok is False and sellable == 0


# ── 3. 账户部分卖出（末日强平路径）───────────────────────────

def test_account_partial_sell_by_shares():
    a = SimulatedAccount(initial_capital=100000.0)
    a.position = 2000
    a.avg_cost = 10.0
    t = a.sell(10.0, shares=1000)
    assert t is not None and t.shares == 1000
    assert a.position == 1000 and a.has_position


def test_account_sell_more_than_position_capped():
    a = SimulatedAccount(initial_capital=100000.0)
    a.position = 500
    t = a.sell(10.0, shares=1000)
    assert t.shares == 500  # 超卖按持仓封顶（既有语义）
    assert a.position == 0


# ── 4. 参数在构造期如实存储（Monte Carlo 同口径依赖）─────────

def test_lot_mode_flag_stored_both_variants(eng_legacy, eng_lot):
    assert eng_legacy.t_plus_1_lot_mode is False
    assert eng_lot.t_plus_1_lot_mode is True
