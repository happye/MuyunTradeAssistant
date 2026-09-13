# -*- coding: utf-8 -*-
"""第四轮对抗审查 D 区块回归测试（ISS-072）

与 `verify_indicator_math.py`（公式正确性）互补：本文件锁的是
「指标窗口对齐」与「决策引擎分数语义」两类行为。

每个测试对应一条审查发现，改代码前先跑本文件确认绿灯。
"""
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.decision_engine import DecisionEngine
from src.core.strategy_layer import StrategyLayer
from src.data.data_feeder import DataFeeder
from src.data.models import (
    SignalType, SkillSignal, StockData, StrategyState, TradeLifecycle,
)


# ==========================================================================
# D01  _safe_rolling 部分窗口降级（回测出值 / live 出 None → 回测信号无法迁移实盘）
# ==========================================================================

def test_safe_rolling_requires_full_window():
    """字段名叫 ma60/ma250，就必须是完整 60/250 根窗口的均值。

    live 侧 akshare_client 用 `rolling(w).mean()`（min_periods 默认 = w），
    不足 w 根直接 NaN→None。回测侧此前在 len>=w//2 时用「现有数据均值」兜底，
    导致同一只股票回测有 ma60 而实盘为 None。
    """
    close60 = pd.Series(np.arange(1, 61, dtype=float))      # 正好 60 根
    close59 = pd.Series(np.arange(1, 60, dtype=float))      # 59 根
    close30 = pd.Series(np.arange(1, 31, dtype=float))      # 30 根（旧降级门槛 w//2）

    assert DataFeeder._safe_rolling(close60, 60) == pytest.approx(30.5), "满窗口应出真值"
    assert DataFeeder._safe_rolling(close59, 60) is None, "59 根 < 60 根，必须为 None（对齐 live）"
    assert DataFeeder._safe_rolling(close30, 60) is None, "30 根曾触发旧降级分支，必须已移除"


def test_safe_rolling_ma250_boundary():
    """ma250 是年线，直接决定 RISK_ON/RISK_OFF 判定，边界必须锁死。"""
    c250 = pd.Series(np.arange(1, 251, dtype=float))
    c249 = pd.Series(np.arange(1, 250, dtype=float))
    c125 = pd.Series(np.arange(1, 126, dtype=float))   # 旧门槛 250//2=125

    assert DataFeeder._safe_rolling(c250, 250) == pytest.approx(125.5)
    assert DataFeeder._safe_rolling(c249, 250) is None
    assert DataFeeder._safe_rolling(c125, 250) is None, "125 根曾给出「伪 250 日均线」"


def test_safe_rolling_short_windows_still_work():
    """小窗口（ma5/avg_volume_20）不应被误伤。"""
    c20 = pd.Series(np.arange(1, 21, dtype=float))
    c19 = pd.Series(np.arange(1, 20, dtype=float))
    assert DataFeeder._safe_rolling(c20, 20) == pytest.approx(10.5)
    assert DataFeeder._safe_rolling(c19, 20) is None


# ==========================================================================
# D02  high_250d 守卫 60 → 250（字段名与守卫不一致，R3 修 high_60d/120d 时漏网）
# ==========================================================================

def test_high_250d_guard_matches_field_name():
    """源码守卫必须写成 250，不能是 60。

    影响链：index_high_250d → decision_engine.py:55-56 drawdown → PANIC 判定。
    用 60 日高点冒充 250 日高点会系统性低估回撤 → 少判 PANIC。

    v0.8.9.5（P2-1 向量化）：回测侧守卫由 `len(recent_250) >= 250` 改为
    `rolling(250).max()`（窗口不足 250 根时为 NaN→None，语义等价），
    断言同步改为锁定 250 窗口存在性。
    """
    root = Path(__file__).resolve().parents[2]

    feeder_src = (root / "src/data/data_feeder.py").read_text(encoding="utf-8")
    rolling_250 = [l.strip() for l in feeder_src.splitlines()
                   if "IDX_HIGH250" in l and "rolling(250)" in l]
    legacy_guard = [l.strip() for l in feeder_src.splitlines()
                    if "recent_250" in l and ">= 250" in l]
    assert rolling_250 or legacy_guard, (
        "未找到 high_250d 的 250 窗口守卫（rolling(250) 或 len>=250），"
        "代码可能已重构且守卫语义丢失"
    )
    assert all("rolling(250)" in g for g in rolling_250), (
        f"data_feeder high_250d 应为 rolling(250) 窗口，实际: {rolling_250}"
    )

    # 行为等价锁：不足 250 根必须为 None（等价于旧 len>=250 守卫），
    # 恰好 250 根给出真值——防止未来把窗口改小测试发现不了
    feeder = DataFeeder.__new__(DataFeeder)
    feeder.stock_code = "600519"
    feeder.start_date = "2024-01-01"
    feeder.end_date = "2024-12-31"
    n = 300
    idx_df = pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n).strftime("%Y-%m-%d"),
        "close": np.arange(1, n + 1, dtype=float),
        "high": np.arange(1, n + 1, dtype=float),
        "preclose": np.arange(1, n + 1, dtype=float),
    })
    feeder._index_df = idx_df
    trend, ma20, ma60, ma250, close, chg, high_250d = feeder._get_index_at_date(str(idx_df['date'].iloc[299]))
    assert high_250d == pytest.approx(300.0), "满 250 窗口应给出真 250 日高点"
    feeder._index_df = idx_df.iloc[:249].copy()
    feeder._precomputed = False
    vals = feeder._get_index_at_date(str(idx_df['date'].iloc[248]))
    assert vals[6] is None, "不足 250 根时 high_250d 必须为 None（防 60 日高点冒充年线高点）"

    # live 侧（未改动）：赋值行 `high_250d = float(df['high'].tail(250).max())` 的守卫在其上方 1-3 行
    aks_lines = (root / "src/data/akshare_client.py").read_text(encoding="utf-8").splitlines()
    live_guards = []
    for i, line in enumerate(aks_lines):
        if "high_250d = float(df['high']" in line:
            window = aks_lines[max(0, i - 3):i]
            live_guards += [w.strip() for w in window if "len(df) >=" in w]
    assert live_guards, "未找到 live 侧 high_250d 守卫行（代码可能已重构）"
    assert all(">= 250" in g for g in live_guards), (
        f"akshare_client high_250d 守卫应为 >=250，实际: {live_guards}"
    )


# ==========================================================================
# D03  动作信号（止损/止盈）一票否决出的 SELL 必须携带置信度
# ==========================================================================

def _mk(name, alias, sig, conf, stype):
    return SkillSignal(skill_name=name, skill_alias=alias, signal=sig,
                       confidence=conf, reason=["测试"], skill_type=stype)


def _stock(price=10.0, change_pct=-3.5, ma20=11.2, ma60=12.0):
    return StockData(
        stock_code="sh600000", stock_name="测试", price=price,
        open=10.6, high=10.7, low=9.9, change_pct=change_pct,
        ma5=10.8, ma10=11.0, ma20=ma20, ma60=ma60, ma120=12.5,
        volume=1e6, avg_volume_20=1e6,
        index_trend="bull", index_ma20=4000, index_ma60=3900,
        index_close=4100, index_ma250=3800, index_high_250d=4200,
    )


def test_action_override_sell_carries_action_confidence():
    """止损 conf=0.85 一票否决出的 SELL，score 不能是 0.0。

    修复前 score 恒取 base 票的 SELL 桶；没有任何 base 技能投 SELL 时该桶为 0.0，
    策略层 `_apply_reverse_cost` 判 score(0.0) < cost(0.003) → SELL 降级为 HOLD，
    止损一票否决形同虚设。
    """
    de = DecisionEngine()
    sigs = [
        _mk("ma_trend", "均线趋势", SignalType.BUY, 0.90, "base"),
        _mk("stop_loss", "止损管理", SignalType.SELL, 0.85, "action"),
    ]
    r = de.make_decision(_stock(), sigs, current_position_ratio=0.20)

    assert r.decision == SignalType.SELL, "止损一票否决应生效"
    assert r.score >= 0.85 - 1e-9, (
        f"止损触发的 SELL 应携带止损置信度(0.85)，实际 score={r.score}"
    )


def test_action_override_sell_score_is_max_of_bucket_and_confidence():
    """base 票也投了 SELL 且分数更高时，不应被动作置信度拉低。"""
    de = DecisionEngine()
    sigs = [
        _mk("ma_trend", "均线趋势", SignalType.SELL, 0.95, "base"),
        _mk("stop_loss", "止损管理", SignalType.SELL, 0.85, "action"),
    ]
    r = de.make_decision(_stock(), sigs, current_position_ratio=0.20)
    assert r.decision == SignalType.SELL
    assert r.score >= 0.95 - 1e-9, f"应取 base 桶与动作置信度的较大者，实际 {r.score}"


def test_stop_loss_veto_not_downgraded_by_reverse_cost():
    """端到端：止损触发时，策略层不得因 score=0 把 SELL 降级成 HOLD。

    降级后 HOLD 分支会走 `buy_score > sell_score*1.5` → ADD，
    即在止损触发当日反向加仓。
    """
    de, sl = DecisionEngine(), StrategyLayer()
    sigs = [
        _mk("ma_trend", "均线趋势", SignalType.BUY, 0.90, "base"),
        _mk("stop_loss", "止损管理", SignalType.SELL, 0.85, "action"),
    ]
    data = _stock()
    state = StrategyState(
        lifecycle=TradeLifecycle.HOLD,
        entry_date="2026-07-01", entry_price=11.0,   # 成本 11.0 / 现价 10.0 → 浮亏 -9.1%
        current_position_ratio=0.20,
        recent_signals=["BUY"] * 5,                   # 历史稳定，排除稳定性降级
        last_decision=SignalType.BUY,
        inertia_counter=5,
        signal_stability_score=1.0,
    )
    r = de.make_decision(data, sigs, current_position_ratio=0.20)
    out = sl.process(r, state, data, current_date="2026-08-28")

    assert out.position_action.value != "ADD", (
        "止损触发当日不得反手加仓（该分支曾因 score=0 被反转成本闸门放行）"
    )


def test_entryexit_force_exit_sets_score_floor():
    """EntryExit 的 Chandelier/趋势破坏清仓必须同时抬 score。

    该分支是安全网，但历史上只改 decision/position_action 不改 score，
    于是策略层把它当零置信度信号降级为 HOLD（进而反手 ADD）。
    结构性断言：score 兜底语句必须位于 CLOSE_ALL 分支内部。
    """
    from src.core.orchestrator import FORCE_EXIT_SCORE

    assert FORCE_EXIT_SCORE > 0.003, (
        "兜底置信度必须高于反转成本，否则仍会被 _apply_reverse_cost 降级"
    )

    root = Path(__file__).resolve().parents[2]
    lines = (root / "src/core/orchestrator.py").read_text(encoding="utf-8").splitlines()

    set_idx = [i for i, l in enumerate(lines)
               if "position_action = PositionAction.CLOSE_ALL if action in" in l]
    assert set_idx, "未找到 EntryExit 的 position_action 赋值"

    # 兜底必须以 `force_exit` 为条件，而不是 `position_action == CLOSE_ALL`。
    # 2026-08-30 实测修正：趋势破坏短期信号走 TRIM/REDUCE，按 CLOSE_ALL 兜底会漏，
    # 而 decision 已被强设为 SELL、score 仍 0 → 被降级成 HOLD 后反手 ADD。
    guard_idx = [i for i, l in enumerate(lines)
                 if "if force_exit and decision_result.score < FORCE_EXIT_SCORE" in l]
    assert guard_idx, (
        "EntryExit 的 score 兜底必须以 force_exit 为条件（不能挂在 CLOSE_ALL 上，"
        "否则 TRIM 减仓通道漏兜底）"
    )
    assert lines[guard_idx[0] + 1].strip() == "decision_result.score = FORCE_EXIT_SCORE", (
        "force_exit 兜底下一条语句应为 score 赋值"
    )

    # 兜底必须位于 SELL 强设分支之后
    assert guard_idx[0] > set_idx[0], "score 兜底位置异常：应在 decision/position_action 强设之后"


def test_trend_break_trim_also_raises_score():
    """趋势破坏的 TRIM（MA5<MA20）通道同样必须抬 score（D03 回归）。

    `check_trend_break` 的短期分支产出 exit_action="TRIM" → position_action=REDUCE，
    但 orchestrator 仍把 decision 强设为 SELL。若 score 兜底只挂在 CLOSE_ALL 分支，
    该通道会带着 score=0 进策略层 → 降级 HOLD → 反手 ADD（减仓信号变成加仓）。
    结构性断言：不得存在 `CLOSE_ALL` 条件包裹 score 兜底的写法。
    """
    root = Path(__file__).resolve().parents[2]
    src = (root / "src/core/orchestrator.py").read_text(encoding="utf-8")
    assert "if decision_result.position_action == PositionAction.CLOSE_ALL:\n" \
           "                            decision_result.position_ratio = 0.0\n" \
           "                            if decision_result.score < FORCE_EXIT_SCORE" not in src, (
        "score 兜底仍嵌在 CLOSE_ALL 分支内 —— TRIM 减仓通道会漏兜底"
    )
    # 且 position_ratio 清零仍应只在 CLOSE_ALL 时做（TRIM 不清零，否则减仓变清仓）
    src_no_comment = "\n".join(
        l for l in src.splitlines() if not l.strip().startswith("#")
    )
    assert "position_action == PositionAction.CLOSE_ALL" in src_no_comment, (
        "CLOSE_ALL 的仓位清零守卫丢失（A06 防幽灵仓位回归）"
    )


def test_all_three_force_exit_channels_raise_score():
    """三条强制离场通道都必须抬 score（D03 同类点 ①②③）。

    orchestrator 里「只改 decision/position_action、不动 score」的强制离场点共三处：
      ① EntryExit Chandelier / 趋势破坏
      ② FundamentalAlert（被ST / 业绩预亏）
      ③ TopSignal（高位止盈大顶）
    任何一处漏改，策略层都会把硬退出降级成 HOLD，HOLD 分支再反手 ADD。
    结构性断言：每个 CLOSE_ALL 通道的标记注释后必须紧跟 score 兜底。
    """
    root = Path(__file__).resolve().parents[2]
    src = (root / "src/core/orchestrator.py").read_text(encoding="utf-8")
    lines = src.splitlines()

    # 三条通道各自的锚点注释（改文案会导致此测试失败，属有意为之：提醒同步更新）
    anchors = {
        "EntryExit/Chandelier": "[EntryExit] Force exit",
        "FundamentalAlert": "[FundamentalAlert] 强制离场",
        "TopSignal": "[TopSignal] 大顶信号强制离场",
    }
    for name, anchor in anchors.items():
        idx = [i for i, l in enumerate(lines) if anchor in l]
        assert idx, f"未找到 {name} 通道（锚点 {anchor!r}），orchestrator 结构已变"
        a = idx[0]
        # 向上 12 行内必须出现 score 兜底
        window = "\n".join(lines[max(0, a - 12):a])
        assert "decision_result.score = FORCE_EXIT_SCORE" in window, (
            f"{name} 强制离场未给 score 兜底 —— 该通道会被策略层两道闸门降级为 HOLD/ADD"
        )

    # 兜底必须只作用于强制离场，不得误伤 TRIM（减仓）
    assert src.count("decision_result.score = FORCE_EXIT_SCORE") == 3, (
        "score 兜底出现次数应为 3（三条通道各一处），多了可能误伤减仓/普通卖出"
    )


# ==========================================================================
# D04  WATCH 决策不应静默上报 HOLD 桶分数
# ==========================================================================

def test_watch_score_not_silently_hold_bucket():
    """final_signal=WATCH 时 score 应取 WATCH 桶，而不是恒为 0 的 HOLD 桶。"""
    de = DecisionEngine()
    sigs = [
        _mk("ma_trend", "均线趋势", SignalType.WATCH, 0.60, "base"),
    ]
    r = de.make_decision(_stock(), sigs, current_position_ratio=0.0)
    if r.decision == SignalType.WATCH:
        assert r.score > 0.0, (
            f"WATCH 决策的 score 取了 HOLD 桶（恒 0），实际 {r.score}"
        )


# ==========================================================================
# D05  DEFAULT_WEIGHTS 类级可变字典被所有实例共享
# ==========================================================================

def test_default_weights_not_shared_between_instances():
    """改一个实例的权重不得污染另一个实例（类级 dict 共享）。"""
    e1, e2 = DecisionEngine(), DecisionEngine()
    key = "ma_trend"
    if key not in e1.weights:
        key = next(iter(e1.weights))
    original = e2.weights[key]
    e1.weights[key] = original + 99.0
    try:
        assert e2.weights[key] == original, (
            f"e1 改权重后 e2 也被污染：{key} {original} → {e2.weights[key]}"
        )
    finally:
        e1.weights[key] = original


def test_empty_weights_dict_not_silently_replaced():
    """传空 dict（falsy）应得到空权重，而不是静默回落默认权重。"""
    e = DecisionEngine(weights={})
    assert e.weights == {}, f"传 {{}} 应得到空权重，实际 {e.weights}"


# ==========================================================================
# D06  tech_context 量能趋势除零
# ==========================================================================

def test_vol_trend_no_zero_division():
    """avg_volume_20=0（长期停牌后 int() 截断）时不得 ZeroDivisionError。"""
    from src.core.tech_context import TechContextBuilder

    data = _stock()
    data.avg_volume_20 = 0.0
    try:
        result = TechContextBuilder._judge_vol_trend(data)
    except ZeroDivisionError:
        pytest.fail("avg_volume_20=0 触发 ZeroDivisionError（守卫只检查了 volume<=0）")
    assert result in ("数据不足", "放量", "缩量", "正常")


def test_rsi_zone_rejects_nan():
    """nan 会通过 `is not None` 守卫并落进 else 分支，静默判成「偏弱」。"""
    from src.core.tech_context import TechContextBuilder

    data = _stock()
    data.rsi_12 = float("nan")
    ctx = TechContextBuilder().build(data)
    rsi = ctx.get("rsi")
    if rsi is not None:
        assert math.isfinite(float(rsi)), "nan 透传进了 tech_context['rsi']"
        assert ctx.get("rsi_zone") != "偏弱", "nan 被静默判成「偏弱」"
    else:
        assert ctx.get("rsi_zone") != "偏弱", "nan 被静默判成「偏弱」"
