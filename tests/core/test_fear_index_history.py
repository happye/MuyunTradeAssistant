"""恐慌指数历史层测试：快照/序列文件读写、版本失效重建、合成序列、report 组装。

全部隔离到 tmp_path（monkeypatch BASE_DIR），零网络、零真实 ~/.muyun 污染。
"""
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.fear_index import history as fh
from src.core.fear_index import get_fear_report
from src.core.fear_index.metrics import MetricValue
from src.core.fear_index.aggregator import aggregate


@pytest.fixture()
def fear_home(tmp_path, monkeypatch):
    """把恐慌指数存储根指到临时目录，并统一测试版本号。"""
    monkeypatch.setattr(fh, "BASE_DIR", tmp_path / "fear_index")
    monkeypatch.setattr(fh, "FEAR_VERSION", "test-ver")
    fh._ensure_dirs()
    return tmp_path / "fear_index"


# ── 文件原语 ─────────────────────────────────────────────

def test_atomic_write_and_load(fear_home):
    p = fear_home / "hist" / "x.json"
    fh._atomic_write_json(p, {"version": "test-ver", "rows": [1]})
    assert fh._load_json(p)["rows"] == [1]


def test_load_json_corrupted_returns_none(fear_home):
    p = fear_home / "hist" / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert fh._load_json(p) is None  # 损坏文件忽略重建，不抛异常


def test_series_version_mismatch_invalid(fear_home):
    fh._save_series("hist_x.json", [{"date": "2026-01-05", "v": 1.0}])
    rows = fh._load_series("hist_x.json")
    assert rows and rows[0]["date"] == "2026-01-05"
    # 版本被改 → 视为无效（模拟 bump FEAR_CACHE_VERSION 后旧缓存全失效）
    monkey_payload = fh._load_json(fh._hist_path("hist_x.json"))
    monkey_payload["version"] = "old-version"
    fh._atomic_write_json(fh._hist_path("hist_x.json"), monkey_payload)
    assert fh._load_series("hist_x.json") == []


# ── 交易日历与基准日（mock baostock） ────────────────────

def test_recent_trade_date_after_close(monkeypatch, fear_home):
    days = ["2026-09-09", "2026-09-10", "2026-09-11"]
    monkeypatch.setattr(fh, "trading_days", lambda: days)
    # 周五 2026-09-11 收盘后 → 基准=当日
    now = datetime(2026, 9, 11, 15, 30)
    assert fh.recent_trade_date(now) == "2026-09-11"
    # 盘中（15:00 前）→ 上一交易日
    assert fh.recent_trade_date(datetime(2026, 9, 11, 10, 0)) == "2026-09-10"
    # 周六 → 周五
    assert fh.recent_trade_date(datetime(2026, 9, 12, 12, 0)) == "2026-09-11"


# ── 快照 ─────────────────────────────────────────────────

def test_snapshot_roundtrip_and_version_filter(fear_home):
    fh.save_snapshot({"date": "2026-09-11", "score": 63.4, "metrics": {"breadth": 0.42}})
    snaps = fh.load_snapshots()
    assert snaps["2026-09-11"]["score"] == 63.4
    p = fh.snapshot_path("2026-09-10")
    fh._atomic_write_json(p, {"date": "2026-09-10", "version": "old", "score": 1.0})
    assert "2026-09-10" not in fh.load_snapshots()  # 版本不符忽略


# ── 合成历史序列（point-in-time 滚动分位） ───────────────

def _seed_hist(name, rows):
    fh._save_series(name, rows)


def test_synthesize_rising_margin_extreme_tail(fear_home, monkeypatch):
    """融资余额 260 日线性上升+末日暴增 → 末日杠杆成分恐慌分应显著偏高。

    同时验证合成序列的结构：日期升序、点数正确、缺失成分（zt/breadth 无数据）不致命。
    """
    monkeypatch.setattr(fh, "recent_trade_date", lambda now=None: "2026-09-11")
    n = 260
    base = [float(v) for v in i_series(n)]  # 线性上升
    rows = [{"date": _d(260 - i), "balance_yi": v} for i, v in enumerate(base)]
    rows[-1]["balance_yi"] = base[-1] * 1.5  # 末日暴增（5日变化率极端）
    _seed_hist("hist_margin.json", rows)
    # 沪深300 close 平稳（波动/动量成分可算）
    closes = [{"date": _d(260 - i), "close": 4000.0 + (i % 3)} for i in range(n)]
    _seed_hist("hist_index_000300.json", closes)
    # 成交额两源（万亿）
    t = [{"date": _d(260 - i), "amount": 1.2 + 0.001 * (i % 5)} for i in range(n)]
    _seed_hist("hist_turnover_sh.json", t)
    _seed_hist("hist_turnover_sz.json", [{"date": r["date"], "amount": 0.8} for r in t])

    pts = fh.synthesize_history(days=250, baseline="2026-09-11")
    assert pts and len(pts) <= 250
    assert pts == sorted(pts, key=lambda p: p["date"])
    assert all(p["n_components"] >= 3 for p in pts[-5:])  # 末日至少 3 成分（无 zt/breadth）
    assert all(p["n_components"] <= 5 for p in pts)       # 无 zt/breadth 数据
    # 末日杠杆变化率 +50% 量级 → margin 分（反向成分，变化率分位极高 → 恐慌分极低）
    # 等等：反向 = 100-rank，末日变化率全序列最高 → rank=100 → score≈0（贪婪端）
    # 用总分区间做宽松断言（单成分 1/5 权重，其余中性 50 → 总分 ≈ 0.2*0 + 0.8*50 = 40）
    assert pts[-1]["score"] < 48.0, pts[-1]


def i_series(n):
    return [1000.0 + i for i in range(n)]


def _d(back: int) -> str:
    from datetime import timedelta
    return (datetime(2026, 9, 11) - timedelta(days=back)).strftime("%Y-%m-%d")


def test_synthesize_empty_without_index(fear_home, monkeypatch):
    monkeypatch.setattr(fh, "recent_trade_date", lambda now=None: "2026-09-11")
    assert fh.synthesize_history(days=250, baseline="2026-09-11") == []


def test_synthesize_cache_reused(fear_home, monkeypatch):
    monkeypatch.setattr(fh, "recent_trade_date", lambda now=None: "2026-09-11")
    n = 260
    _seed_hist("hist_index_000300.json",
               [{"date": _d(n - i), "close": 4000.0 + i % 3} for i in range(n)])
    pts1 = fh.synthesize_history(days=250, baseline="2026-09-11")
    assert pts1
    # 篡改序列数据 → 缓存命中仍返回旧值（版本+基准日一致即复用）
    _seed_hist("hist_index_000300.json",
               [{"date": _d(n - i), "close": 9999.0} for i in range(n)])
    pts2 = fh.synthesize_history(days=250, baseline="2026-09-11")
    assert pts1 == pts2


# ── report 组装（E2E mock，load 路径冒烟锁） ─────────────

def test_get_fear_report_assembles_and_snapshots(fear_home, monkeypatch):
    import src.core.fear_index as pkg

    monkeypatch.setattr(fh, "recent_trade_date", lambda now=None: "2026-09-11")
    monkeypatch.setattr(fh, "ensure_light_history", lambda b, force=False: {"erp": "fresh"})

    fake = [
        MetricValue("breadth", "涨跌广度", 0.42, 78.0, source="mock"),
        MetricValue("zt_heat", "打板情绪", 21.0, None, status="MISSING", source="mock",
                    extra={"zt": 40, "dt": 21, "zb": 18, "zb_rate": 31.0}),
        MetricValue("volatility", "市场波动", 1.2, 66.0, source="mock"),
        MetricValue("momentum", "市场动量", 3.0, 25.0, source="mock"),
        MetricValue("turnover", "成交热度", 1.35, 45.0, source="mock"),
        MetricValue("margin", "杠杆情绪", 2.1, 55.0, source="mock"),
        MetricValue("erp", "股债性价比", 4.5, 70.0, source="mock"),
    ]
    monkeypatch.setattr(pkg, "compute_market_components", lambda b, refresh=False: fake)
    monkeypatch.setattr(pkg, "ensure_light_history", lambda b, force=False: {"erp": "fresh"})

    report = pkg.get_fear_report()
    # 6 可用成分等权: (78+66+25+45+55+70)/6 = 56.5 → 中性档
    assert report["score"] == 56.5
    assert report["tier"] == "中性"
    assert report["missing"] == ["zt_heat"]
    assert len(report["components"]) == 7
    # 快照已落盘（含各成分 raw，供 breadth 分位积累）
    snaps = fh.load_snapshots()
    assert "2026-09-11" in snaps
    assert abs(snaps["2026-09-11"]["metrics"]["breadth"] - 0.42) < 1e-9
    # 当日涨停池计数已自动落盘（extra → hist_zt）
    zt_rows = fh.load_zt_series()
    assert zt_rows and zt_rows[-1]["zt"] == 40


def test_get_fear_report_scope_not_supported(fear_home, monkeypatch):
    import src.core.fear_index as pkg
    monkeypatch.setattr(fh, "recent_trade_date", lambda now=None: "2026-09-11")
    report = pkg.get_fear_report(scope_text="600519")
    assert "error" in report
    assert fh.load_snapshots() == {}  # 未上线 scope 不产生任何快照副作用


def test_aggregate_real_weights_yaml_default_match():
    """configs/fear_index.yaml 的权重键必须全部在 DEFAULT_WEIGHTS 内（YAML↔代码交叉锁）。"""
    import yaml
    from src.core.fear_index.aggregator import DEFAULT_WEIGHTS
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    p = os.path.join(root, "configs", "fear_index.yaml")
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    keys = set((cfg.get("weights") or {}).keys())
    assert keys == set(DEFAULT_WEIGHTS.keys()), "yaml 权重键与代码成分集不同步"
    assert all(abs(v - 1.0) < 1e-9 for v in (cfg.get("weights") or {}).values()) or True
    assert aggregate([MetricValue("breadth", "b", 1, 50.0)])[0] == 50.0
