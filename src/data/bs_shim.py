"""Baostock 兼容垫片（本地原型，仅在 MUYUN_DATA_SHIM=tencent 时启用）。

背景：部分网络环境（如海外云主机）无法连通 Baostock TCP 10030 与东方财富 push2his，
但腾讯 web.ifzq.gtimg.cn K线与新浪 hq.sinajs.cn 可达。本模块在**不改动调用方**的前提下，
把 baostock 模块的常用查询函数替换为基于腾讯/新浪的等价实现，返回同构的 ResultData
（error_code / error_msg / fields / next() / get_row_data() / get_data()）。

已知差异（务必知悉）：
- volume：腾讯股票单位为“手”，此处 ×100 换算为“股”；amount 用 close×volume 近似（非真实成交额）。
- turn/peTTM/isST 等字段无来源，返回空串；tradestatus 恒为 "1"（停牌日腾讯不出bar，等价于跳过）。
- query_stock_industry / query_forecast_report / 财务季频接口 返回空结果（error_code="0", 0 行）。
- 来源顺序：腾讯 proxy.finance.qq.com 镜像 → 腾讯 web.ifzq（易被 WAF 501）→ 新浪日K（不复权，
  此时 adjustflag 字段返回 "3" 以如实标注；周/月线由日线聚合）。WAF 拦截一次即熔断该源。
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

# 腾讯 K线：web.ifzq.gtimg.cn 常被 WAF 拦截（HTTP 501 / waf.tencent.com），
# proxy.finance.qq.com 的 newfqkline 为同源镜像，优先使用；两者均失败再降级到新浪日K。
_TX_URLS = (
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
)
_TX_URL = _TX_URLS[-1]  # 兼容旧引用
_SINA_KLINE = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
_SINA_HQ = "https://hq.sinajs.cn/list="
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://finance.sina.com.cn",
}
_TX_HEADERS = dict(_HEADERS, Referer="https://gu.qq.com/")
_TIMEOUT = float(os.environ.get("MUYUN_SHIM_TIMEOUT", "5"))
_MIN_INTERVAL = float(os.environ.get("MUYUN_SHIM_MIN_INTERVAL", "0.15"))
_BREAK_COOLDOWN = float(os.environ.get("MUYUN_SHIM_BREAK_COOLDOWN", "600"))
_lock = threading.Lock()
_last_call = [0.0]
_installed = False
# 源熔断：url -> 熔断时刻；WAF/501 一次即熔断，冷却期内直接走下一个源
_broken: dict = {}
# 记录每次 _tx_kline 结果的实际来源与复权口径：key -> (source, adj_used)
LAST_SOURCE: dict = {}


class WafBlocked(ConnectionError):
    """被 WAF 拦截/HTTP 5xx 拒绝（非瞬时故障，不重试）。"""


def _is_broken(url: str) -> bool:
    t = _broken.get(url)
    if t is None:
        return False
    if time.time() - t >= _BREAK_COOLDOWN:
        _broken.pop(url, None)
        return False
    return True


def _mark_broken(url: str):
    _broken[url] = time.time()


def reset_breakers():
    _broken.clear()


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


def _retries() -> int:
    return 1 if os.environ.get("MUYUN_FAST_FAIL") == "1" else 3


def _get(url, params=None, headers=None):
    """GET + 指数退避重试；WAF/501 立即抛 WafBlocked（不重试）。"""
    last = None
    n = _retries()
    for attempt in range(n):
        _throttle()
        try:
            r = requests.get(url, params=params, headers=headers or _HEADERS, timeout=_TIMEOUT,
                             proxies={"http": None, "https": None})
            head = r.text[:400]
            if "waf.tencent.com" in head or r.status_code in (403, 501):
                raise WafBlocked(f"shim blocked: {url} (HTTP {r.status_code} / WAF)")
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except WafBlocked:
            raise
        except requests.RequestException as e:
            last = e
        if attempt < n - 1:
            time.sleep(0.5 * (2 ** attempt))
    raise ConnectionError(f"shim fetch failed: {url} ({last})")


def _tx_code(bs_code: str) -> str:
    return bs_code.replace(".", "").lower()


def _tx_fetch(url: str, tx_code: str, period: str, adj: str, s: datetime, e: datetime) -> dict:
    out = {}
    # 腾讯单次最多约 640 根（返回截至 end 的最近 640 根）：日线按 1 年分段，周/月线一次取
    step = timedelta(days=365) if period == "day" else (e - s + timedelta(days=1))
    cur = s
    while cur <= e:
        seg_end = min(cur + step - timedelta(days=1), e)
        count = 640 if period == "day" else 2000
        param = f"{tx_code},{period},{cur:%Y-%m-%d},{seg_end:%Y-%m-%d},{count},{adj}"
        payload = _get(url, {"param": param}, headers=_TX_HEADERS).json()
        data = payload.get("data") or {}
        node = data.get(tx_code) if isinstance(data, dict) else None
        if isinstance(node, dict):
            key = f"{adj}{period}" if adj else period
            rows = node.get(key) or node.get(period) or []
            for row in rows:
                if len(row) >= 6 and f"{s:%Y-%m-%d}" <= row[0] <= f"{e:%Y-%m-%d}":
                    out[row[0]] = tuple(row[:6])
        cur = seg_end + timedelta(days=1)
    return out


def _sina_daily(tx_code: str, s: datetime, e: datetime) -> dict:
    """新浪日K（**不复权**）。最多返回最近 1023 根；volume 单位：个股为“股”，此处换算成“手”以对齐腾讯。"""
    days = (datetime.now() - s).days
    datalen = max(10, min(1023, int(days * 0.72) + 20))
    r = _get(_SINA_KLINE, {"symbol": tx_code, "scale": "240", "ma": "no", "datalen": str(datalen)})
    arr = r.json() or []
    is_index = _is_index(tx_code)
    out = {}
    for it in arr:
        d = str(it.get("day", ""))[:10]
        if not (f"{s:%Y-%m-%d}" <= d <= f"{e:%Y-%m-%d}"):
            continue
        v = float(it.get("volume") or 0)
        out[d] = (d, it["open"], it["close"], it["high"], it["low"], str(v if is_index else v / 100))
    return out


def _resample(daily: dict, freq: str) -> dict:
    if freq not in ("w", "m") or not daily:
        return daily
    df = pd.DataFrame(list(daily.values()), columns=["date", "o", "c", "h", "l", "v"])
    df["dt"] = pd.to_datetime(df["date"])
    for col in "ochlv":
        df[col] = df[col].astype(float)
    grp = df.groupby(df["dt"].dt.to_period("W-FRI" if freq == "w" else "M"))
    out = {}
    for _, g in grp:
        d = g["date"].iloc[-1]
        out[d] = (d, str(g["o"].iloc[0]), str(g["c"].iloc[-1]), str(g["h"].max()),
                  str(g["l"].min()), str(g["v"].sum()))
    return out


def _is_index(tx: str) -> bool:
    return tx.startswith("sh000") or tx.startswith("sz399")


def _fetch_kline(tx_code: str, freq: str, adj: str, start: str, end: str):
    """依次尝试 腾讯镜像 → 腾讯原站 → 新浪日K。返回 (rows_tuple, source, adj_used)。"""
    period = {"d": "day", "w": "week", "m": "month"}.get(freq, "day")
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    errs = []
    for url in _TX_URLS:
        if _is_broken(url):
            errs.append(f"{url}: circuit-open")
            continue
        try:
            out = _tx_fetch(url, tx_code, period, adj, s, e)
            if out:
                return tuple(out[k] for k in sorted(out)), "tencent", adj
            errs.append(f"{url}: empty")
        except WafBlocked as ex:
            _mark_broken(url)
            logger.warning(f"[bs_shim] 腾讯K线源被拦截，熔断 {_BREAK_COOLDOWN:.0f}s：{url}")
            errs.append(str(ex))
        except Exception as ex:  # noqa: BLE001
            errs.append(f"{url}: {ex}")
    if not _is_broken(_SINA_KLINE):
        try:
            out = _resample(_sina_daily(tx_code, s, e), freq)
            if out:
                if adj:
                    logger.warning(f"[bs_shim] {tx_code} 使用新浪降级源：不复权数据（请求 {adj}），adjustflag 标为 3")
                return tuple(out[k] for k in sorted(out)), "sina", ""
            errs.append("sina: empty")
        except WafBlocked as ex:
            _mark_broken(_SINA_KLINE)
            errs.append(str(ex))
        except Exception as ex:  # noqa: BLE001
            errs.append(f"sina: {ex}")
    raise ConnectionError("; ".join(errs))


def _disk_cache_on() -> bool:
    return os.environ.get("MUYUN_SHIM_DISK_CACHE", "1") != "0"


@lru_cache(maxsize=512)
def _tx_kline(tx_code: str, freq: str, adj: str, start: str, end: str) -> tuple:
    """返回 ((date, open, close, high, low, volume_raw), ...)，按日期升序，含 [start, end]。
    实际来源/复权口径写入 LAST_SOURCE[(tx_code, freq, adj, start, end)]。"""
    key = (tx_code, freq, adj, start, end)
    if _disk_cache_on():
        from src.data import robust_fetch as rf
        ttl = 6 * 3600 if end >= (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d") else 30 * 86400
        res = rf.robust_call("bs_shim_kline", "|".join(key), lambda: _fetch_kline(*key),
                             ttl=ttl, timeout=60, retries=0, quiet=True)
        if not res:
            raise ConnectionError(f"K线获取失败（所有源）：{tx_code}")
        rows, src, adj_used = res
    else:
        rows, src, adj_used = _fetch_kline(*key)
    LAST_SOURCE[key] = (src, adj_used)
    return rows


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
        src, adj_used = LAST_SOURCE.get((tx, frequency, adj, pre_start, end), ("tencent", adj))
        out_flag = str(adjustflag) if (adj_used == adj or is_index) else "3"
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
            "amount": _fmt(float(c) * vol), "adjustflag": out_flag, "turn": "",
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
    # 首选新浪交易日历（含当年未来交易日，与 baostock 语义一致）；失败时退回上证指数
    # 日 K 线推断——但 K 线只能证明「已过去」的日子，故此时只返回到最后一根 K 线为止，
    # 不把今天/未来误标为休市（旧行为：盘中构建会把当日及之后全标 0，基准日滞后）。
    trading, last_known = None, None
    try:
        import akshare as ak
        cal = ak.tool_trade_date_hist_sina()
        ds = sorted(str(d)[:10] for d in cal["trade_date"])
        if ds:
            trading, last_known = set(ds), ds[-1]
    except Exception as e:
        logger.debug(f"[bs_shim] 新浪交易日历不可用，退回 K 线推断: {e}")
    if trading is None:
        try:
            k = [r[0] for r in _tx_kline("sh000001", "d", "", start, end)]
        except Exception as e:
            return ShimResult(fields, [], error_code="10002007", error_msg=str(e))
        trading, last_known = set(k), (max(k) if k else start)
    stop = min(datetime.strptime(end, "%Y-%m-%d"), datetime.strptime(last_known, "%Y-%m-%d"))
    rows, cur = [], datetime.strptime(start, "%Y-%m-%d")
    while cur <= stop:
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
