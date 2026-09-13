"""恐慌指数对抗性场景测试（v0.8.10 对抗审查批）：故障注入/边界/降级路径。

每个用例对应对抗审查发现的一个攻击面，全部 mock 注入、零网络、隔离 tmp_path：
- 单边成交额缺失静默当双边（口径污染）
- 收盘后-17:30 窗口 baseline 不在序列（回退 STALE vs 序列过期 MISSING）
- backfill 保留边界探测 / 未来日期过滤 / 熔断拒绝
- 轻量回填全源爆炸 / 全成分 MISSING 不落快照 / 渲染层空值防御 / 图表短序列
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.fear_index import history as fh
from src.core.fear_index.metrics import market_metrics as mm_mod
from src.core.fear_index.metrics import MetricValue
from src.core.fear_index import display
from src.core.fear_index import charts
from src.data.net_guard import circuit_breaker


@pytest.fixture()
def fear_home(tmp_path, monkeypatch):
    monkeypatch.setattr(fh, "BASE_DIR", tmp_path / "fear_index")
    monkeypatch.setattr(fh, "FEAR_VERSION", "test-ver")
    fh._ensure_dirs()
    return tmp_path / "fear_index"


def _d(back: int, anchor: str = "2026-09-11") -> str:
    from datetime import datetime, timedelta
    base = datetime.strptime(anchor, "%Y-%m-%d")
    # 仅工作日序列即可（合成不依赖交易日历）
    d = base - timedelta(days=back)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _seed_closes(n: int = 300, anchor: str = "2026-09-11", end_offset: int = 0):
    """升序写入（模拟 _refresh_index_series 的 sorted 输出）。"""
    rows = sorted(({"date": _d(i + end_offset, anchor), "close": 4000.0 + (i % 3)}
                   for i in range(n)), key=lambda r: r["date"])
    fh._save_series("hist_index_000300.json", rows)
    return rows


def test_vol_mom_survives_unsorted_series_file(fear_home, monkeypatch):
    """防御：序列文件意外乱序时 index[-1] 仍取最新交易日（sort_index 锁）。"""
    monkeypatch.setattr(fh, "recent_trade_date", lambda now=None: "2026-09-11")
    rows = [{"date": _d(i), "close": 4000.0 + (i % 3)} for i in range(300)]
    rows.reverse()  # 故意乱序（新→旧）
    fh._save_series("hist_index_000300.json", rows)
    fh._save_series("hist_turnover_sh.json", [{"date": r["date"], "amount": 1.2} for r in rows])
    fh._save_series("hist_turnover_sz.json", [{"date": r["date"], "amount": 0.8} for r in rows])
    fh._save_series("hist_margin.json", [{"date": r["date"], "balance_yi": 10000.0} for r in rows])
    fh._save_series("hist_erp.json", [{"date": r["date"], "erp_pct": 4.0} for r in rows])
    comps = mm_mod.compute_market_components("2026-09-11")
    vol = next(c for c in comps if c.name == "volatility")
    assert vol.status == "OK" and vol.score is not None  # 乱序不致取错末日


# ── Bug 1 回归锁：单边成交额绝不静默当双边 ───────────────

def test_turnover_single_side_missing_is_excluded(fear_home, monkeypatch):
    monkeypatch.setattr(fh, "recent_trade_date", lambda now=None: "2026-09-11")
    _seed_closes(300)
    n = 300
    sh = [{"date": _d(i), "amount": 1.2} for i in range(n)]
    sz = [{"date": _d(i), "amount": 0.8} for i in range(n)]
    fh._save_series("hist_turnover_sh.json", sh)
    # 深市缺「回退锚定日」那一天的（单边缺失）
    hole = _d(0)
    fh._save_series("hist_turnover_sz.json", [r for r in sz if r["date"] != hole])
    fh._save_series("hist_margin.json",
                    [{"date": _d(i), "balance_yi": 10000.0 + i} for i in range(n)])
    fh._save_series("hist_erp.json",
                    [{"date": _d(i), "erp_pct": 4.0 + 0.001 * (i % 7)} for i in range(n)])

    pts = fh.synthesize_history(days=250, baseline="2026-09-11")
    target = [p for p in pts if p["date"] == hole]
    others = [p for p in pts if p["date"] != hole][-5:]
    assert target, "锚定日应出现在序列中"
    assert all(p["n_components"] >= 5 for p in others)
    assert target[0]["n_components"] < others[-1]["n_components"], \
        "单边缺失日的可用成分数必须比完整日少（turnover 被剔除而非按单边计算）"


# ── Bug 3 回归锁：收盘后窗口回退 STALE / 序列过期 MISSING ─

def test_vol_mom_falls_back_when_baseline_missing(fear_home, monkeypatch):
    """baseline(当日) 日线未出 → 用序列末日计算并标 STALE，不整成分 MISSING。"""
    monkeypatch.setattr(fh, "recent_trade_date", lambda now=None: "2026-09-11")
    _seed_closes(300, end_offset=1)  # 序列止于 2026-09-10
    comps = mm_mod._metric_vol_mom("2026-09-11")
    vol = next(c for c in comps if c.name == "volatility")
    mom = next(c for c in comps if c.name == "momentum")
    assert vol.status == "STALE" and vol.score is not None
    assert vol.data_ts == "2026-09-10"
    assert "未出" in vol.note
    assert mom.status == "STALE" and mom.score is not None


def test_vol_mom_missing_when_series_stale(fear_home, monkeypatch):
    """序列末日距今 >7 天 → 不拿旧数据充数，MISSING。"""
    monkeypatch.setattr(fh, "recent_trade_date", lambda now=None: "2026-09-11")
    _seed_closes(300, anchor="2026-07-01")  # 序列止于 7 月初
    comps = mm_mod._metric_vol_mom("2026-09-11")
    vol = next(c for c in comps if c.name == "volatility")
    assert vol.status == "MISSING" and vol.score is None


# ── Bug 5 / 脏数据 回归锁：backfill 边界与未来日期 ────────

def test_backfill_stops_at_boundary_after_15_consecutive_empty(fear_home, monkeypatch):
    monkeypatch.setattr(fh, "recent_trade_date", lambda now=None: "2026-09-11")
    days = [_d(i, "2026-09-11") for i in range(0, 60) if
            (__import__("datetime").datetime.strptime("2026-09-11", "%Y-%m-%d")
             - __import__("datetime").timedelta(days=i)).weekday() < 5]
    days = sorted(set(days))
    monkeypatch.setattr(fh, "trading_days", lambda: days)
    calls = []

    def _fake_fetch(ymd):
        calls.append(ymd)
        return None
    monkeypatch.setattr(fh, "fetch_zt_counts", _fake_fetch)
    stats = fh.backfill_zt_pools(days=len(days))
    assert len(calls) == 15, f"连续15空必须停（实际请求{len(calls)}次）"
    assert stats["failed"] == 15 and stats["filled"] == 0


def test_backfill_never_requests_future_dates(fear_home, monkeypatch):
    monkeypatch.setattr(fh, "recent_trade_date", lambda now=None: "2026-09-11")
    days = ["2026-09-01", "2026-09-10", "2026-09-11",
            "2026-09-14", "2026-09-15", "2026-10-09"]  # 末 3 个是未来交易日
    monkeypatch.setattr(fh, "trading_days", lambda: days)

    def _ok(ymd):
        return {"date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}",
                "zt": 40, "dt": 21, "zb": 18, "zb_rate": 31.03}
    monkeypatch.setattr(fh, "fetch_zt_counts", _ok)
    stats = fh.backfill_zt_pools(days=10)
    assert stats["filled"] == 3  # 只回填 <= 2026-09-11 的 3 个交易日
    assert not any(p.name >= "20260914.json" for p in (fear_home / "hist" / "hist_zt").glob("*.json"))


def test_zt_breaker_rejected_returns_none_without_request(fear_home, monkeypatch):
    monkeypatch.setattr(circuit_breaker, "allow", lambda key: False)
    assert fh.fetch_zt_counts("20260911") is None  # 熔断中直接放弃，不假数据


# ── 全链故障：回填爆炸 / 全成分缺失 / 渲染与图表防御 ──────

def test_light_history_all_sources_fail_no_crash(fear_home, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("网络挂了")
    monkeypatch.setattr(fh, "_refresh_index_series", _boom)
    monkeypatch.setattr(fh, "_fetch_margin_rows", _boom)
    monkeypatch.setattr(fh, "_fetch_erp_rows", _boom)
    status = fh.ensure_light_history("2026-09-11", force=True)
    assert all(v == "failed" for v in status.values())
    assert len(status) == 5  # index + turnover_sh + turnover_sz + margin + erp


def test_report_all_components_missing_no_snapshot(fear_home, monkeypatch):
    import src.core.fear_index as pkg
    monkeypatch.setattr(fh, "recent_trade_date", lambda now=None: "2026-09-11")
    fake = [MetricValue(n, n, None, None, status="MISSING")
            for n in ("breadth", "zt_heat", "volatility", "momentum",
                      "turnover", "margin", "erp")]
    monkeypatch.setattr(pkg, "compute_market_components", lambda b, refresh=False: fake)
    monkeypatch.setattr(pkg, "ensure_light_history", lambda b, force=False: {})
    report = pkg.get_fear_report()
    assert report["score"] is None and report["tier"] == "无法评估"
    assert fh.load_snapshots() == {}  # 算不出的日子绝不落快照污染历史


def test_display_survives_none_score_and_missing(fear_home):
    report = {"scope": "market", "as_of": "2026-09-11", "score": None,
              "tier": "无法评估", "used": [], "missing": ["breadth"],
              "components": [{"name": "breadth", "label": "涨跌广度", "raw": None,
                              "score": None, "status": "MISSING", "source": "t",
                              "data_ts": "", "note": "挂了", "in_aggregate": True,
                              "extra": {}}],
              "quality": {"erp": "failed"},
              "generated_at": "2026-09-13T12:00:00"}
    display.show_overview(report)  # 不抛即过
    summary = {"as_of": "2026-09-11", "windows": [], "headline": None,
               "chart_path": None, "chart_error": "走势图生成失败"}
    display.show_history(summary)


def test_chart_rejects_short_series(fear_home, tmp_path):
    out = tmp_path / "c.png"
    assert charts.render_market_chart([], out) is None
    two = [{"date": "2026-09-10", "score": 50.0}, {"date": "2026-09-11", "score": 60.0}]
    assert charts.render_market_chart(two, out) is None
    assert not out.exists()


def test_chart_works_without_cjk_font(fear_home, tmp_path, monkeypatch):
    monkeypatch.setattr(charts, "_setup_cjk_font", lambda: (False, ""))
    pts = [{"date": _d(i), "score": 40.0 + i % 20} for i in range(66, 0, -1)]
    out = tmp_path / "no_cjk.png"
    path = charts.render_market_chart(pts, out)
    assert path and out.exists()  # 英文标签兜底路径完整出图
