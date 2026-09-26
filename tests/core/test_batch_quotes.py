# -*- coding: utf-8 -*-
"""ISS-088 批量行情与 K 线磁盘缓存单测（全 mock，零网络零 AI）。

锁死语义：
- 新浪批量解析（真实响应格式 33 字段；坏行/空价/短行跳过）
- get_realtime_quotes 多源降级链：新浪批量→东财全市场→ETF→逐只兜底，部分失败合并
- 预取映射：get_realtime_quote 命中预取零请求；TTL 过期不命中
- K 线当日磁盘缓存：同日二次调用零请求；17:30 灰区/晚间旧缓存重拉
"""
import contextlib
import io
import os
import sys
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pandas as pd
import pytest

import src.data.akshare_client as akc
from src.data.akshare_client import AKShareClient
from src.data.net_guard import RateLimiter


@contextmanager
def _swap(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


@contextlib.contextmanager
def _quiet():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield


def _sina_line(symbol, name, open_, preclose, price, high, low, volume):
    fields = [name, open_, preclose, price, high, low, "0", "0", str(volume), "0"] + ["0"] * 20 + ["2026-09-07", "15:00:00"]
    return f'var hq_str_{symbol}="' + ",".join(fields) + '";'


# ── 新浪批量解析 ──

def test_parse_sina_batch_real_format():
    text = _sina_line("sh600519", "贵州茅台", "1400.00", "1350.00", "1380.50", "1410.00", "1395.00", 5000000) + "\n" + \
           _sina_line("sz000001", "平安银行", "10.50", "10.30", "10.45", "10.60", "10.40", 2000000)
    q = AKShareClient._parse_sina_batch(text)
    assert set(q) == {"600519", "000001"}
    assert q["600519"]["price"] == 1380.5 and q["600519"]["stock_name"] == "贵州茅台"
    assert q["600519"]["change_pct"] == round((1380.5 - 1350.0) / 1350.0 * 100, 2)
    assert q["000001"]["source"] == "sina_batch"


def test_parse_sina_batch_skips_bad_lines():
    good = _sina_line("sh600519", "贵州茅台", "1400.00", "1350.00", "1380.50", "1410.00", "1395.00", 5000000)
    text = "\n".join([
        "var hq_str_sh600000=\"短行,1,2\";",          # 字段不足
        'var hq_str_sh600001="' + ",".join(["空价"] + ["0"] * 32) + '";',  # 价格无效
        "垃圾行没有等号",
        good,
    ])
    q = AKShareClient._parse_sina_batch(text)
    assert set(q) == {"600519"}


# ── 批量降级链 ──

@pytest.fixture()
def _no_throttle(monkeypatch):
    monkeypatch.setattr(akc, "rate_limiter", RateLimiter(intervals={}, default_interval=0.0))
    yield


def test_quotes_chain_sina_partial_em_fill(monkeypatch, _no_throttle):
    """新浪拿到 1 只，东财全市场补齐另 1 只——部分失败合并结果。"""
    monkeypatch.setattr(AKShareClient, "_fetch_sina_batch",
                        classmethod(lambda cls, codes: {"600519": {"stock_code": "600519", "price": 1.0, "source": "sina_batch"}}))
    df = pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "最新价": 1.0, "涨跌幅": 0.0, "成交量": 1, "今开": 1, "最高": 1, "最低": 1, "昨收": 1},
        {"代码": "000001", "名称": "平安银行", "最新价": 2.0, "涨跌幅": 1.0, "成交量": 2, "今开": 2, "最高": 2, "最低": 2, "昨收": 2},
    ])
    monkeypatch.setattr(AKShareClient, "_retry_with_backoff", classmethod(lambda cls, func, **kw: df))
    with _quiet():
        q = AKShareClient.get_realtime_quotes(["600519", "000001"])
    assert q["600519"]["source"] == "sina_batch"
    assert q["000001"]["source"] == "em_all"


def test_quotes_chain_sina_dead_em_covers(monkeypatch, _no_throttle):
    """新浪熔断/失败 → 东财全市场一层全量补齐（降级适配）。"""
    def _dead(cls, codes):
        cb = akc.circuit_breaker
        cb.record_failure("sina_batch")
        return {}
    monkeypatch.setattr(AKShareClient, "_fetch_sina_batch", classmethod(_dead))
    df = pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "最新价": 1.0, "涨跌幅": 0, "成交量": 1, "今开": 1, "最高": 1, "最低": 1, "昨收": 1},
        {"代码": "000001", "名称": "平安银行", "最新价": 2.0, "涨跌幅": 0, "成交量": 2, "今开": 2, "最高": 2, "最低": 2, "昨收": 2},
    ])
    monkeypatch.setattr(AKShareClient, "_retry_with_backoff", classmethod(lambda cls, func, **kw: df))
    with _quiet():
        q = AKShareClient.get_realtime_quotes(["600519", "000001"])
    assert len(q) == 2 and all(v["source"] == "em_all" for v in q.values())
    # 清理本测试造成的熔断状态，避免影响后续用例
    akc.circuit_breaker.record_success("sina_batch")


def test_quotes_chain_fallback_per_code(monkeypatch, _no_throttle):
    """批量源全挂 → 逐只旧链路兜底（baostock 优先的原 get_realtime_quote）。"""
    monkeypatch.setattr(AKShareClient, "_fetch_sina_batch", classmethod(lambda cls, codes: {}))
    monkeypatch.setattr(AKShareClient, "_retry_with_backoff", classmethod(lambda cls, func, **kw: None))
    monkeypatch.setattr(AKShareClient, "get_realtime_quote", classmethod(lambda cls, code, retry=1: {"stock_code": code, "price": 3.0, "source": "baostock"}))
    with _quiet():
        q = AKShareClient.get_realtime_quotes(["600519", "000001"])
    assert len(q) == 2 and q["600519"]["source"] == "baostock"


# ── 预取映射 ──

def test_prefetch_consumed_by_get_realtime_quote(monkeypatch):
    """预取命中：get_realtime_quote 直接返回预取值，不触发任何数据源调用。"""
    now = __import__("time").time()
    with _swap(AKShareClient, "_QUOTE_PREFETCH", {"600519": (now, {"stock_code": "600519", "price": 9.9})}):

        def _boom(cls, code):
            raise AssertionError("预取命中时不得调用数据源")

        monkeypatch.setattr(AKShareClient, "_fetch_baostock_realtime", classmethod(_boom))
        with _quiet():
            q = AKShareClient.get_realtime_quote("600519")
    assert q["price"] == 9.9


def test_prefetch_expired_not_consumed(monkeypatch):
    old = __import__("time").time() - akc.AKShareClient._QUOTE_PREFETCH_TTL - 1
    with _swap(AKShareClient, "_QUOTE_PREFETCH", {"600519": (old, {"stock_code": "600519", "price": 9.9})}):
        # ISS-089 后链路为 新浪→baostock→EM：全部 mock 失败，过期预取不得命中
        monkeypatch.setattr(AKShareClient, "_fetch_sina_batch", classmethod(lambda cls, codes: {}))
        monkeypatch.setattr(AKShareClient, "_fetch_baostock_realtime", classmethod(lambda cls, code: None))
        monkeypatch.setattr(AKShareClient, "_retry_with_backoff", classmethod(lambda cls, func, **kw: None))
        with _quiet():
            q = AKShareClient.get_realtime_quote("600519")
    assert q is None   # 过期预取不命中，走源链路（全 mock 失败 → None）


# ── K 线当日磁盘缓存 ──

def _canned_kline_df():
    return pd.DataFrame({
        "date": ["2026-09-01", "2026-09-02"],
        "open": [10.0, 10.5],
        "close": [10.2, 10.8],
        "volume": [1000, 2000],
    })


def test_kline_disk_cache_same_day_second_call_no_fetch(monkeypatch, tmp_path):
    # 冻结时钟到白天（17:30 前当日缓存有效）——否则 17:30 后跑本用例必挂
    # （当日缓存规则：白天写的缓存 17:30 后须重拉；时间依赖测试必须定死时间，2026-09-26）
    class FakeDayDT(datetime):
        @classmethod
        def now(cls):
            return datetime(2026, 9, 7, 10, 0, 0)

    monkeypatch.setattr(AKShareClient, "_kline_cache_dir", classmethod(lambda cls: tmp_path))
    calls = []
    monkeypatch.setattr(AKShareClient, "_get_historical_kline_uncached",
                        classmethod(lambda cls, code, period, adjust, s, e, retry: calls.append(1) or _canned_kline_df()))
    with _swap(akc, "datetime", FakeDayDT), _quiet():
        df1 = AKShareClient.get_historical_kline("600519")
        df2 = AKShareClient.get_historical_kline("600519")
    assert calls == [1], "同日第二次调用应命中磁盘缓存，不再请求"
    assert df2 is not None and len(df2) == 2
    assert df2["close"].iloc[1] == 10.8   # dtype 往返正确
    assert pd.api.types.is_float_dtype(df2["close"])


def test_kline_disk_cache_expired_evening(monkeypatch, tmp_path):
    """白天拉的缓存，17:30 后必须重拉（baostock 盘后更新当日bar）。"""
    monkeypatch.setattr(AKShareClient, "_kline_cache_dir", classmethod(lambda cls: tmp_path))
    calls = []

    class FakeDT(datetime):
        @classmethod
        def now(cls):
            return datetime(2026, 9, 7, 17, 45, 0)

    monkeypatch.setattr(AKShareClient, "_get_historical_kline_uncached",
                        classmethod(lambda cls, code, period, adjust, s, e, retry: calls.append(1) or _canned_kline_df()))
    with _swap(akc, "datetime", FakeDT), _quiet():
        AKShareClient.get_historical_kline("600519")   # 写入时 fetched_at 也是 17:45（FakeDT）
        # 18:30 后再读：fetch_min>=18*60 → 有效
        FakeDT2 = type("FakeDT2", (datetime,), {"now": classmethod(lambda cls: datetime(2026, 9, 7, 20, 0, 0))})
        with _swap(akc, "datetime", FakeDT2):
            df2 = AKShareClient.get_historical_kline("600519")
    # 写入 17:45（灰区），20:00 读取时 fetch_min=1065 >= 1080? 否 → 应重拉
    assert len(calls) == 2, "灰区写入的缓存晚间应重拉"
    assert df2 is not None


def test_kline_disk_cache_prefetched_evening_valid(monkeypatch, tmp_path):
    """18:00 后写入的缓存，当晚持续有效（零请求）。"""
    monkeypatch.setattr(AKShareClient, "_kline_cache_dir", classmethod(lambda cls: tmp_path))
    calls = []

    class FakeDTWrite(datetime):
        @classmethod
        def now(cls):
            return datetime(2026, 9, 7, 18, 30, 0)

    class FakeDTRead(datetime):
        @classmethod
        def now(cls):
            return datetime(2026, 9, 7, 21, 0, 0)

    monkeypatch.setattr(AKShareClient, "_get_historical_kline_uncached",
                        classmethod(lambda cls, code, period, adjust, s, e, retry: calls.append(1) or _canned_kline_df()))
    with _swap(akc, "datetime", FakeDTWrite), _quiet():
        AKShareClient.get_historical_kline("600519")
    with _swap(akc, "datetime", FakeDTRead), _quiet():
        AKShareClient.get_historical_kline("600519")
    assert len(calls) == 1, "盘后写入的缓存当晚应零请求命中"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
