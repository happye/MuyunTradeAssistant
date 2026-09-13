"""恐慌指数纯计算层测试：归一化口径 / 聚合缺失重归一 / scope / 多周期摘要。

口径锁死纪律（对齐 DataFeeder 等价性测试）：normalizer 的分位/阈值插值、
aggregator 的缺失剔除重归一、趋势判断阈值（±8）一旦变动即红灯，
提示改动者 bump FEAR_CACHE_VERSION。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.fear_index import _window_stat
from src.core.fear_index.aggregator import aggregate, tier_of, TIERS
from src.core.fear_index.metrics import MetricValue
from src.core.fear_index.normalizer import (
    MIN_PCT_SAMPLES, clamp_score, pct_rank, percentile_panic, threshold_panic,
)
from src.core.fear_index.scope import parse_scope


# ── 归一化：pct_rank 分位（250 窗口） ─────────────────────

def test_pct_rank_full_window():
    s = list(range(1, 301))
    # 窗口取末 250 个（51..300）：275 的分位 = (275-50)/250 = 90%
    assert abs(pct_rank(275, s) - 90.0) < 1e-9
    assert abs(pct_rank(150, s) - 40.0) < 1e-9


def test_pct_rank_direction():
    s = list(range(1, 301))
    assert percentile_panic(280, s, higher_is_panic=True) > 85
    # 反向成分：值越大恐慌分越低
    assert percentile_panic(280, s, higher_is_panic=False) < 15


def test_pct_rank_insufficient_samples_returns_none():
    # 29 个样本 < MIN_PCT_SAMPLES(60) → None，调用方显式降级，绝不硬算
    assert percentile_panic(5, list(range(1, 30))) is None
    assert MIN_PCT_SAMPLES == 60


def test_pct_rank_ignores_none_and_nonfinite():
    s = [float("nan"), None, *list(range(1, 301))]
    assert pct_rank(275, s) is not None  # 脏值被剔除不炸


# ── 归一化：threshold 分段线性插值 ────────────────────────

def test_threshold_interpolation():
    stops = [(0.2, 95.0), (0.3, 75.0), (0.5, 50.0)]
    assert threshold_panic(0.25, stops) == 85.0   # 95 与 75 的中点
    assert threshold_panic(0.5, stops) == 50.0


def test_threshold_endpoint_extrapolation():
    stops = [(0.2, 95.0), (0.8, 5.0)]
    assert threshold_panic(0.1, stops) == 95.0    # 下端外推
    assert threshold_panic(0.95, stops) == 5.0    # 上端外推


def test_threshold_degenerate_inputs():
    assert threshold_panic(0.5, []) == 50.0                       # 空锚点中性
    assert threshold_panic(0.5, [("x", "y")]) == 50.0             # 非法锚点全忽略


# ── 聚合：缺失剔除重归一 ─────────────────────────────────

def test_aggregate_missing_component_renormalized():
    cs = [MetricValue("a", "A", 1, 80.0),
          MetricValue("b", "B", 1, 40.0),
          MetricValue("c", "C", 1, None)]
    score, used, missing = aggregate(cs)
    assert score == 60.0          # (80+40)/2 —— 缺失的 c 剔除权重，不补 50
    assert used == ["a", "b"]
    assert missing == ["c"]


def test_aggregate_all_missing_returns_none():
    score, used, missing = aggregate([MetricValue("a", "A", 1, None)])
    assert score is None and not used and missing == ["a"]


def test_aggregate_display_component_excluded():
    # in_aggregate=False（如涨停家数展示行）不进总分也不算缺失
    cs = [MetricValue("a", "A", 1, 60.0),
          MetricValue("show", "展示", 1, 99.0, in_aggregate=False)]
    score, used, missing = aggregate(cs)
    assert score == 60.0 and used == ["a"] and missing == []


def test_aggregate_weight_override():
    cs = [MetricValue("breadth", "A", 1, 80.0), MetricValue("zt_heat", "B", 1, 40.0)]
    score, _, _ = aggregate(cs, weights={"breadth": 3.0})
    assert abs(score - (80 * 3 + 40) / 4) < 1e-9


def test_aggregate_unknown_weight_key_ignored():
    # 未知键（拼错的成分名）被安全过滤，不得悄悄影响聚合
    cs = [MetricValue("breadth", "A", 1, 80.0), MetricValue("zt_heat", "B", 1, 40.0)]
    score, _, _ = aggregate(cs, weights={"breadth_typo": 100.0})
    assert score == 60.0


def test_clamp_score_bounds():
    assert clamp_score(-5) == 0.0 and clamp_score(120) == 100.0


# ── 档位映射 ─────────────────────────────────────────────

def test_tier_boundaries():
    assert tier_of(0) == "极度贪婪"
    assert tier_of(19.9) == "极度贪婪"
    assert tier_of(20) == "贪婪"
    assert tier_of(39.9) == "贪婪"
    assert tier_of(40) == "中性"
    assert tier_of(60) == "恐慌"     # 60-80 恐慌
    assert tier_of(80) == "极度恐慌"
    assert tier_of(None) == "无法评估"
    assert len(TIERS) == 5


# ── scope 解析（P1 仅 market 支持） ──────────────────────

def test_scope_market_default():
    for t in ("", "  ", "market"):
        sc = parse_scope(t)
        assert sc.kind == "market" and sc.supported


def test_scope_stock_not_supported_but_parsed():
    for t in ("600519", "000001.SZ"):
        sc = parse_scope(t)
        assert sc.kind == "stock" and not sc.supported and sc.message


def test_scope_sector_not_supported_but_parsed():
    sc = parse_scope("氮化镓")
    assert sc.kind == "sector" and not sc.supported and sc.message


# ── 多周期摘要：统计与趋势阈值（±8 锁死） ────────────────

def _pts(scores, start_day=1):
    return [{"date": f"2026-01-{start_day + i:02d}", "score": s, "n_components": 7}
            for i, s in enumerate(scores)]


def test_window_stat_flat_neutral():
    r = _window_stat(_pts([50.0] * 66), 5)
    assert r["mean"] == 50.0 and r["min"] == 50.0 and r["max"] == 50.0
    assert r["trend"] == "震荡" and r["full"] and r["samples"] == 5
    assert r["cur_rank"] == 100.0  # 并列时 <= 计数语义


def test_window_stat_deepening_threshold():
    r = _window_stat(_pts([50.0, 51.0, 52.0, 53.0, 59.0]), 5)
    assert r["trend"] == "恐慌加深"      # cur-首值=+9 >= 8
    r2 = _window_stat(_pts([50.0, 51.0, 52.0, 53.0, 57.0]), 5)
    assert r2["trend"] == "震荡"         # +7 < 8


def test_window_stat_easing_threshold():
    r = _window_stat(_pts([70.0, 68.0, 66.0, 64.0, 60.0]), 5)
    assert r["trend"] == "恐慌缓和"      # -10 <= -8


def test_window_stat_extremes_and_rank():
    r = _window_stat(_pts([30.0, 20.0, 90.0, 40.0, 50.0]), 5)
    assert r["min"] == 20.0 and r["min_date"] == "2026-01-02"
    assert r["max"] == 90.0 and r["max_date"] == "2026-01-03"
    assert r["cur_rank"] == 80.0         # (30,20,40,50 <= 50) = 4/5


def test_window_stat_insufficient_samples():
    r = _window_stat(_pts([50.0, 52.0]), 66)
    assert r["full"] is False and r["samples"] == 2  # 样本不足如实标注
    assert _window_stat([{"date": "2026-01-01", "score": 50.0, "n_components": 1}], 5) is None
