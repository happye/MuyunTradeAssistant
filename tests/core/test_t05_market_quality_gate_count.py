"""T05 read-only market-snapshot quality-gate count.

The sources are local stand-ins. With a price column, more than 10
positive prices pass and exactly 10 do not. Without that column, 100
rows pass and 101 do not. A rejected non-empty frame is not retried and
is not written. The 1.5s gap is not used. Production code is not changed.
"""
import sys
import threading
import time
import tracemalloc
from datetime import datetime

import pandas as pd

import src.scanner.market_cache as market_cache
from src.scanner.market_cache import MarketCache

BASE = 1_700_000_000.0
COOLDOWN_CALLS = 5


class _Wall(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 16, 10, 0, 0)


class _Clock:
    now = BASE

    def time(self):
        return self.now


def _priced(positive, rows=None):
    rows = positive if rows is None else rows
    prices = [10.0 if i < positive else 0.0 for i in range(rows)]
    return pd.DataFrame({
        "代码": [f"{600000 + i:06d}" for i in range(rows)],
        "最新价": prices,
    })


def _codes_only(rows):
    return pd.DataFrame({
        "代码": [f"{i:06d}" for i in range(rows)],
    })


def _clear_memory():
    MarketCache._shared_stock_df = None
    MarketCache._shared_stock_ts = 0.0
    MarketCache._shared_stock_count = 0


def test_t05_market_quality_gate_count(tmp_path, monkeypatch, capsys):
    assert datetime(2026, 10, 16).weekday() == 4
    cache_dir = tmp_path / "market_cache"
    cache_dir.mkdir()
    saved = (
        MarketCache._shared_stock_df,
        MarketCache._shared_stock_ts,
        MarketCache._shared_stock_count,
        MarketCache._shared_fail_ts,
    )
    clock = _Clock()
    fetches = {"sina": 0, "backup": 0}
    slept = {"n": 0}
    made = {}
    mode = {"sina": "pass11", "backup": "none"}

    def sina(self):
        fetches["sina"] += 1
        key = mode["sina"]
        frame = {
            "pass11": _priced(11),
            "fail10": _priced(10),
            "rows100": _codes_only(100),
            "rows101": _codes_only(101),
        }[key]
        made["sina"] = frame
        return frame

    def backup(self):
        fetches["backup"] += 1
        key = mode["backup"]
        frame = _priced(10) if key == "fail10" else _codes_only(101)
        made["backup"] = frame
        return frame

    def no_sleep(seconds):
        slept["n"] += 1

    monkeypatch.setattr(market_cache, "datetime", _Wall)
    monkeypatch.setattr(market_cache, "time", clock)
    monkeypatch.setattr(market_cache, "_DISK_CACHE_DIR", cache_dir)
    monkeypatch.setattr(market_cache, "_disk_enabled", lambda: True)
    monkeypatch.setattr(MarketCache, "_fetch_sina_market", sina)
    monkeypatch.setattr(MarketCache, "_fetch_efinance_market", backup)
    monkeypatch.setattr(time, "sleep", no_sleep)
    _clear_memory()
    MarketCache._shared_fail_ts = 0.0
    threads_before = threading.active_count()
    tracemalloc.start()
    try:
        cache = MarketCache()
        assert cache.is_trading_hours()
        assert cache._snapshot_quality_ok(_priced(11))
        assert not cache._snapshot_quality_ok(_priced(10))
        assert not cache._snapshot_quality_ok(_priced(10, rows=11))
        assert cache._snapshot_quality_ok(_codes_only(100))
        assert not cache._snapshot_quality_ok(_codes_only(101))

        passed11 = cache.get_all_stocks()
        frame11 = made["sina"]
        after11_sina = fetches["sina"]
        after11_backup = fetches["backup"]
        disk11 = pd.read_pickle(cache_dir / "stocks_df.pkl")
        _clear_memory()
        (cache_dir / "stocks_df.pkl").unlink()
        mode["sina"] = "fail10"
        mode["backup"] = "fail10"
        rejected10 = cache.get_all_stocks()
        frame10 = made["sina"]
        after10_sina = fetches["sina"]
        after10_backup = fetches["backup"]
        fail_ts_after10 = MarketCache._shared_fail_ts
        cooled = [cache.get_all_stocks() for _ in range(COOLDOWN_CALLS)]
        after_cool_sina = fetches["sina"]
        after_cool_backup = fetches["backup"]
        MarketCache._shared_fail_ts = 0.0
        mode["sina"] = "rows100"
        mode["backup"] = "none"
        passed100 = cache.get_all_stocks()
        frame100 = made["sina"]
        after100_sina = fetches["sina"]
        after100_backup = fetches["backup"]
        disk100 = pd.read_pickle(cache_dir / "stocks_df.pkl")
        _clear_memory()
        (cache_dir / "stocks_df.pkl").unlink()
        mode["sina"] = "rows101"
        mode["backup"] = "rows101"
        rejected101 = cache.get_all_stocks()
        frame101 = made["sina"]
        after101_sina = fetches["sina"]
        after101_backup = fetches["backup"]
        fail_ts_after101 = MarketCache._shared_fail_ts
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        (
            MarketCache._shared_stock_df,
            MarketCache._shared_stock_ts,
            MarketCache._shared_stock_count,
            MarketCache._shared_fail_ts,
        ) = saved
    files = list(cache_dir.glob("*.pkl"))
    threads_after = threading.active_count()
    sina10 = after10_sina - after11_sina
    backup10 = after10_backup - after11_backup
    cool_fetches = (after_cool_sina - after10_sina) + (after_cool_backup - after10_backup)
    sina100 = after100_sina - after_cool_sina
    backup100 = after100_backup - after_cool_backup
    sina101 = after101_sina - after100_sina
    backup101 = after101_backup - after100_backup
    assert passed11 is frame11
    assert list(passed11["代码"]) == [f"{600000 + i:06d}" for i in range(11)]
    assert int((passed11["最新价"] > 0).sum()) == 11
    assert len(disk11["data"]) == 11
    assert int((disk11["data"]["最新价"] > 0).sum()) == 11
    assert rejected10.empty and rejected10 is not frame10
    assert all(frame.empty for frame in cooled)
    assert passed100 is frame100
    assert len(passed100) == 100 and "最新价" not in passed100.columns
    assert passed100 is not None and len(disk100["data"]) == 100
    assert "最新价" not in disk100["data"].columns
    assert rejected101.empty and rejected101 is not frame101
    assert after11_sina == 1 and after11_backup == 0
    assert sina10 == 1 and backup10 == 1
    assert cool_fetches == 0
    assert fail_ts_after10 == BASE
    assert sina100 == 1 and backup100 == 0
    assert sina101 == 1 and backup101 == 1
    assert fail_ts_after101 == BASE
    assert slept["n"] == 0
    assert files == []
    assert threads_after <= threads_before + 1
    print(
        f"T05_MARKET_QUALITY_GATE positive_cmp=gt10 rows_cmp=le100 "
        f"pass11_sina={after11_sina} pass11_backup={after11_backup} "
        f"fail10_sina={sina10} fail10_backup={backup10} "
        f"cooldown_calls={COOLDOWN_CALLS} cooldown_fetches={cool_fetches} "
        f"pass100_sina={sina100} pass100_backup={backup100} "
        f"fail101_sina={sina101} fail101_backup={backup101} "
        f"retry_gaps_skipped={slept['n']} files_left={len(files)} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    captured = capsys.readouterr()
    assert "T05_MARKET_QUALITY_GATE" in captured.out
    sys.stderr.write(captured.out)
