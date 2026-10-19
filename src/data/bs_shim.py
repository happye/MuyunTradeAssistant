"""Baostock 兼容垫片（本地原型，仅在 MUYUN_DATA_SHIM=tencent 时启用）。

背景：部分网络环境（如海外云主机）无法连通 Baostock TCP 10030 与东方财富 push2his，
但腾讯 web.ifzq.gtimg.cn K线与新浪 hq.sinajs.cn 可达。本模块在**不改动调用方**的前提下，
把 baostock 模块的常用查询函数替换为基于腾讯/新浪的等价实现，返回同构的 ResultData
（error_code / error_msg / fields / next() / get_row_data() / get_data()）。

已知差异（务必知悉）：
- volume：腾讯股票单位为“手”，此处 ×100 换算为“股”；amount 用 close×volume 近似（非真实成交额）。
- turn/peTTM/isST 等字段无来源，返回空串；tradestatus 恒为 "1"（停牌日腾讯不出bar，等价于跳过）。
- query_stock_industry / query_forecast_report / 财务季频接口 返回空结果（error_code="0", 0 行）。
- 前复权口径为腾讯 qfq，与 Baostock 前复权在除权日附近可能存在细微差异。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from functools import lru_cache

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_TX_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_SINA_HQ = "https://hq.sinajs.cn/list="
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
_TIMEOUT = float(os.environ.get("MUYUN_SHIM_TIMEOUT", "8"))
_MIN_INTERVAL = float(os.environ.get("MUYUN_SHIM_MIN_INTERVAL", "0.15"))
_lock = threading.Lock()
_last_call = [0.0]
_installed = False


class ShimResult:
    def __init__(self, fields, rows, error_code="0", error_msg="success"):
        self.fields = list(fields)
        self._rows = rows
        self._i = -1
        self.error_code = error_code
        self.error_msg = error_msg

    def next(self):
        self._i += 1
        return self._i < len(self._rows)

    def get_row_data(self):
        return self._rows[self._i]

    def get_data(self):
        return pd.DataFrame(self._rows, columns=self.fields)


class _LoginResult:
    error_code = "0"
    error_msg = "success"


def _throttle():
    with _lock:
        wait = _MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


def _get(url, params=None):
    last = None
    for attempt in range(3):
        _throttle()
        try:
            r = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT,
                             proxies={"http": None, "https": None})
            if r.status_code == 200 and "waf.tencent.com" not in r.text[:400]:
                return r
            last = f"HTTP {r.status_code} / WAF"
        except requests.RequestException as e:
            last = e
        time.sleep(0.8 * (attempt + 1))
    raise ConnectionError(f"shim fetch failed: {url} ({last})")


def _tx_code(bs_code: str) -> str:
    return bs_code.replace(".", "").lower()


@lru_cache(maxsize=512)
def _tx_kline(tx_code: str, freq: str, adj: str, start: str, end: str) -> tuple:
    """返回 ((date, open, close, high, low, volume_raw), ...)，按日期升序，含 [start, end]。"""
    period = {"d": "day", "w": "week", "m": "month"}.get(freq, "day")
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    out = {}
    # 腾讯单次最多约 640 根：日线按 1 年分段，周/月线一次取
    step = timedelta(days=365) if period == "day" else (e - s + timedelta(days=1))
    cur = s
    while cur <= e:
        seg_end = min(cur + step - timedelta(days=1), e)
        count = 640 if period == "day" else 2000
        param = f"{tx_code},{period},{cur:%Y-%m-%d},{seg_end:%Y-%m-%d},{count},{adj}"
        data = _get(_TX_URL, {"param": param}).json().get("data") or {}
        node = data.get(tx_code) if isinstance(data, dict) else None
        if isinstance(node, dict):
            key = f"{adj}{period}" if adj else period
            rows = node.get(key) or node.get(period) or []
            for row in rows:
                if len(row) >= 6:
                    out[row[0]] = tuple(row[:6])
        cur = seg_end + timedelta(days=1)
    return tuple(out[k] for k in sorted(out))


def _fmt(x):
    if x is None or x == "":
        return ""
    return f"{float(x):.4f}".rstrip("0").rstrip(".")


def query_history_k_data_plus(code, fields, start_date=None, end_date=None,
                              frequency="d", adjustflag="3", **_kw):
    field_list = [f.strip() for f in fields.split(",") if f.strip()]
    try:
        end = end_date or datetime.now().strftime("%Y-%m-%d")
        start = start_date or "2015-01-01"
        adj = {"1": "hfq", "2": "qfq"}.get(str(adjustflag), "")
        tx = _tx_code(code)
        is_index = tx[2:5] in ("000", "399") and (tx.startswith("sh000") or tx.startswith("sz399"))
        if is_index:
            adj = ""
        # 多取 15 天以计算首行 preclose
        pre_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=15)).strftime("%Y-%m-%d")
        raw = _tx_kline(tx, frequency, adj, pre_start, end)
    except Exception as e:  # 网络失败 → 与 baostock 一致：非 "0" 错误码
        logger.warning(f"[bs_shim] K线获取失败 {code}: {e}")
        return ShimResult(field_list, [], error_code="10002007", error_msg=str(e))

    rows = []
    prev_close = None
    for d, o, c, h, l, v in raw:
        vol = float(v) * (1 if is_index else 100)
        rec = {
            "date": d, "code": code, "open": _fmt(o), "high": _fmt(h), "low": _fmt(l),
            "close": _fmt(c), "preclose": _fmt(prev_close), "volume": str(int(vol)),
            "amount": _fmt(float(c) * vol), "adjustflag": str(adjustflag), "turn": "",
            "tradestatus": "1", "pctChg": _fmt((float(c) / float(prev_close) - 1) * 100) if prev_close else "",
            "isST": "0", "peTTM": "", "pbMRQ": "", "psTTM": "", "pcfNcfTTM": "",
        }
        prev_close = c
        if d >= start:
            rows.append([rec.get(f, "") for f in field_list])
    return ShimResult(field_list, rows)


@lru_cache(maxsize=2048)
def _sina_name(sina_code: str) -> str:
    try:
        r = _get(_SINA_HQ + sina_code)
        txt = r.content.decode("gbk", errors="ignore")
        body = txt.split('"')[1] if '"' in txt else ""
        return body.split(",")[0] if body else ""
    except Exception:
        return ""


def query_stock_basic(code=None, code_name=None, **_kw):
    fields = ["code", "code_name", "ipoDate", "outDate", "type", "status"]
    if not code:
        return ShimResult(fields, [])
    name = _sina_name(_tx_code(code))
    if not name:
        return ShimResult(fields, [])
    tx = _tx_code(code)
    typ = "2" if (tx.startswith("sh000") or tx.startswith("sz399")) else "1"
    return ShimResult(fields, [[code, name, "", "", typ, "1"]])


def query_trade_dates(start_date=None, end_date=None, **_kw):
    fields = ["calendar_date", "is_trading_day"]
    end = end_date or datetime.now().strftime("%Y-%m-%d")
    start = start_date or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    try:
        trading = {r[0] for r in _tx_kline("sh000001", "d", "", start, end)}
    except Exception as e:
        return ShimResult(fields, [], error_code="10002007", error_msg=str(e))
    rows, cur = [], datetime.strptime(start, "%Y-%m-%d")
    while cur <= datetime.strptime(end, "%Y-%m-%d"):
        ds = cur.strftime("%Y-%m-%d")
        rows.append([ds, "1" if ds in trading else "0"])
        cur += timedelta(days=1)
    return ShimResult(fields, rows)


def _empty(*_a, **_kw):
    return ShimResult([], [])


_EMPTY_FIELDS = {
    "query_forecast_report": ["code", "profitForcastExpPubDate", "profitForcastExpStatDate",
                              "profitForcastType", "profitForcastAbstract",
                              "profitForcastChgPctUp", "profitForcastChgPctDwn"],
    "query_stock_industry": ["updateDate", "code", "code_name", "industry", "industryClassification"],
}


def _empty_with(name):
    fields = _EMPTY_FIELDS.get(name, [])
    return lambda *_a, **_kw: ShimResult(fields, [])


def install(force: bool = False) -> bool:
    """把垫片挂到 baostock 模块上。幂等。"""
    global _installed
    if _installed and not force:
        return True
    import baostock as bs
    bs.login = lambda *a, **k: _LoginResult()
    bs.logout = lambda *a, **k: _LoginResult()
    bs.query_history_k_data_plus = query_history_k_data_plus
    bs.query_stock_basic = query_stock_basic
    bs.query_trade_dates = query_trade_dates
    for name in ("query_stock_industry", "query_forecast_report", "query_performance_express_report",
                 "query_profit_data", "query_operation_data", "query_growth_data",
                 "query_balance_data", "query_cash_flow_data", "query_dupont_data",
                 "query_hs300_stocks", "query_sz50_stocks", "query_zz500_stocks",
                 "query_all_stock", "query_dividend_data", "query_adjust_factor"):
        setattr(bs, name, _empty_with(name))
    _installed = True
    logger.warning("[bs_shim] 已启用 Baostock→腾讯/新浪 兼容垫片（MUYUN_DATA_SHIM）。"
                   "行业/财务/业绩预告等 Baostock 专有数据将为空。")
    return True


def maybe_install_from_env() -> bool:
    mode = os.environ.get("MUYUN_DATA_SHIM", "").strip().lower()
    if mode in ("1", "tencent", "true", "on"):
        return install()
    return False
