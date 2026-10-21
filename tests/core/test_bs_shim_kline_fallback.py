"""bs_shim K线多源降级（无网络：全部 monkeypatch requests.get）。"""
import json

import pytest

from src.data import bs_shim


class _Resp:
    def __init__(self, status=200, text="", obj=None):
        self.status_code = status
        self.text = text if obj is None else json.dumps(obj)
        self._obj = obj

    def json(self):
        return self._obj if self._obj is not None else json.loads(self.text)


WAF = '<html><script>window.location.href="https://waf.tencent.com/501page.html"</script>'


@pytest.fixture(autouse=True)
def _iso(monkeypatch):
    monkeypatch.setenv("MUYUN_SHIM_DISK_CACHE", "0")
    monkeypatch.setattr(bs_shim, "_MIN_INTERVAL", 0)
    monkeypatch.setattr(bs_shim.time, "sleep", lambda *_: None)
    bs_shim.reset_breakers()
    bs_shim._tx_kline.cache_clear()
    bs_shim.LAST_SOURCE.clear()
    yield
    bs_shim.reset_breakers()
    bs_shim._tx_kline.cache_clear()


def _tx_payload(code, key, rows):
    return {"code": 0, "data": {code: {key: rows}}}


def _sina_rows():
    return [{"day": d, "open": "10", "high": "11", "low": "9", "close": c, "volume": "100000"}
            for d, c in (("2026-10-14", "10"), ("2026-10-15", "10.5"), ("2026-10-16", "11"))]


def _router(calls, tx_proxy=None, tx_web=None, sina=None):
    def get(url, params=None, **_kw):
        calls.append(url)
        if "proxy.finance.qq.com" in url:
            return tx_proxy() if tx_proxy else _Resp(501, WAF)
        if "gtimg" in url:
            return tx_web() if tx_web else _Resp(501, WAF)
        if "sina" in url:
            return sina() if sina else _Resp(500, "")
        raise AssertionError(url)
    return get


def test_tencent_proxy_preferred_qfq(monkeypatch):
    calls = []
    rows = [["2026-10-15", "1", "2", "3", "0.5", "10"], ["2026-10-16", "2", "3", "4", "1", "20"]]
    monkeypatch.setattr(bs_shim.requests, "get", _router(
        calls, tx_proxy=lambda: _Resp(obj=_tx_payload("sz002594", "qfqday", rows))))
    rs = bs_shim.query_history_k_data_plus("sz.002594", "date,close,volume,adjustflag",
                                           "2026-10-16", "2026-10-16", adjustflag="2")
    assert rs.error_code == "0"
    assert rs.get_data().values.tolist() == [["2026-10-16", "3", "2000", "2"]]
    assert all("proxy.finance.qq.com" in u for u in calls)


def test_waf_trips_breaker_and_falls_back_to_sina(monkeypatch):
    calls = []
    monkeypatch.setattr(bs_shim.requests, "get", _router(calls, sina=lambda: _Resp(obj=_sina_rows())))
    rs = bs_shim.query_history_k_data_plus("sh.600276", "date,close,preclose,volume,adjustflag",
                                           "2026-10-15", "2026-10-16", adjustflag="2")
    df = rs.get_data()
    assert rs.error_code == "0"
    assert df["date"].tolist() == ["2026-10-15", "2026-10-16"]
    assert df["preclose"].tolist() == ["10", "10.5"]
    assert df["volume"].tolist() == ["100000", "100000"]  # 股
    assert set(df["adjustflag"]) == {"3"}  # 如实标注：不复权
    n_tx = sum("qq.com" in u or "gtimg" in u for u in calls)
    assert n_tx == 2  # 每个腾讯源 WAF 一次即熔断，不重试
    calls.clear()
    bs_shim.query_history_k_data_plus("sh.600989", "date,close", "2026-10-15", "2026-10-16")
    assert not any("qq.com" in u or "gtimg" in u for u in calls)  # 熔断期直接走新浪


def test_index_from_sina_keeps_volume_and_flag(monkeypatch):
    monkeypatch.setattr(bs_shim.requests, "get", _router([], sina=lambda: _Resp(obj=_sina_rows())))
    df = bs_shim.query_history_k_data_plus("sh.000300", "date,volume,adjustflag",
                                           "2026-10-16", "2026-10-16", adjustflag="3").get_data()
    assert df.values.tolist() == [["2026-10-16", "100000", "3"]]


def test_all_sources_fail_returns_error_code(monkeypatch):
    monkeypatch.setattr(bs_shim.requests, "get", _router([]))
    rs = bs_shim.query_history_k_data_plus("sz.002594", "date,close", "2026-10-15", "2026-10-16")
    assert rs.error_code != "0" and rs.get_data().empty


def test_get_backoff_retries_transient(monkeypatch):
    monkeypatch.delenv("MUYUN_FAST_FAIL", raising=False)
    seq = iter([bs_shim.requests.ConnectionError("x"), _Resp(502, ""), _Resp(obj={"ok": 1})])

    def get(*_a, **_k):
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(bs_shim.requests, "get", get)
    assert bs_shim._get("https://x").json() == {"ok": 1}


def test_weekly_resample_from_sina(monkeypatch):
    monkeypatch.setattr(bs_shim.requests, "get", _router([], sina=lambda: _Resp(obj=_sina_rows())))
    rows = bs_shim._tx_kline("sh600276", "w", "qfq", "2026-10-12", "2026-10-16")
    assert len(rows) == 1 and rows[0][0] == "2026-10-16" and float(rows[0][2]) == 11.0
    assert bs_shim.LAST_SOURCE[("sh600276", "w", "qfq", "2026-10-12", "2026-10-16")] == ("sina", "")
