"""免费第三方数据源的稳健调用层：短超时 + 有限重试退避 + 磁盘「最近一次成功」缓存（TTL + 过期兜底）。

背景：同花顺概念板块 / stock_zyjs_ths / stock_individual_info_em 等免费接口夜间常超时或
返回非 JSON（JSONDecodeError），导致笨总评分维度全部降级。本模块：
- fresh：缓存未过 TTL 直接返回（不打网络）
- 否则调用 fn（每次硬超时 timeout 秒，失败按 backoff 退避重试 retries 次）
- 成功 → 写盘（pickle）；失败 → 返回过期但未超 stale_max 的旧值，并标 stale
- 都没有 → None（不编造数据）

缓存目录：$MUYUN_ROBUST_CACHE_DIR 或 ~/.muyun/robust_cache/。
另提供腾讯/新浪替代源：主营业务、申万行业/概念、PE/PB/市值（均为真实接口返回值）。
"""
from __future__ import annotations

import hashlib
import logging
import os
import pickle
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 最近一次 robust_call 的来源状态（fresh/live/stale/none），便于上层标注
LAST_STATUS: dict = {}


def cache_dir() -> Path:
    d = os.environ.get("MUYUN_ROBUST_CACHE_DIR") or str(Path.home() / ".muyun" / "robust_cache")
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(name: str, key: str) -> Path:
    safe = re.sub(r"[^\w.-]", "_", f"{name}_{key}")[:80]
    h = hashlib.md5(f"{name}|{key}".encode()).hexdigest()[:8]
    return cache_dir() / f"{safe}_{h}.pkl"


def _load(name, key):
    p = _path(name, key)
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            return pickle.load(f)          # {"ts": float, "value": Any}
    except Exception:
        return None


def _save(name, key, value):
    try:
        p = _path(name, key)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            pickle.dump({"ts": time.time(), "value": value}, f)
        os.replace(tmp, p)
    except Exception as e:
        logger.debug(f"robust_fetch 写缓存失败 {name}/{key}: {e}")


def _is_empty(v) -> bool:
    if v is None:
        return True
    empty = getattr(v, "empty", None)
    if isinstance(empty, bool):
        return empty
    try:
        return len(v) == 0
    except TypeError:
        return False


def _call_timeout(fn, timeout):
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn)
    try:
        return fut.result(timeout=timeout)
    finally:
        ex.shutdown(wait=False)   # 不 join 卡死线程


def robust_call(name: str, key: str, fn: Callable[[], Any], *, ttl: float = 86400,
                stale_max: float = 30 * 86400, timeout: float = 8.0, retries: int = 1,
                backoff: float = 1.0, use_cache: bool = True, quiet: bool = False) -> Any:
    """见模块说明。返回值或 None；状态写入 LAST_STATUS[(name, key)]。"""
    now = time.time()
    rec = _load(name, key) if use_cache else None
    if rec is not None and now - rec.get("ts", 0) <= ttl and not _is_empty(rec.get("value")):
        LAST_STATUS[(name, key)] = "fresh_cache"
        return rec["value"]
    last_err = None
    for attempt in range(retries + 1):
        try:
            v = _call_timeout(fn, timeout)
            if not _is_empty(v):
                if use_cache:
                    _save(name, key, v)
                LAST_STATUS[(name, key)] = "live"
                return v
            last_err = "empty"
        except FuturesTimeout:
            last_err = f"timeout>{timeout:.0f}s"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {str(e)[:60]}"
        if attempt < retries:
            time.sleep(backoff * (2 ** attempt))
    if rec is not None and now - rec.get("ts", 0) <= stale_max and not _is_empty(rec.get("value")):
        age_h = (now - rec["ts"]) / 3600
        logger.warning(f"robust_fetch {name}({key}) 失败({last_err})，使用 {age_h:.1f}h 前的缓存（stale）")
        LAST_STATUS[(name, key)] = "stale_cache"
        return rec["value"]
    (logger.debug if quiet else logger.warning)(f"robust_fetch {name}({key}) 失败({last_err})，无可用缓存")
    LAST_STATUS[(name, key)] = "none"
    return None


# ---------------- 替代源（腾讯 / 新浪） ----------------
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}


def _http_get(url, timeout=6.0):
    import requests
    r = requests.get(url, headers=_HEADERS, timeout=timeout, proxies={"http": None, "https": None})
    r.raise_for_status()
    return r


def _mkt(code: str) -> str:
    return "sh" if code.startswith(("6", "9")) else ("bj" if code.startswith(("4", "8")) else "sz")


def _num(x):
    try:
        v = float(x)
        return v
    except (TypeError, ValueError):
        return None


def tencent_quote_fundamentals(code: str) -> Optional[dict]:
    """腾讯行情：名称、PE(TTM)、PB、总市值/流通市值（亿元）。字段缺失为 None。"""
    r = _http_get(f"https://qt.gtimg.cn/q={_mkt(code)}{code}")
    txt = r.content.decode("gbk", errors="ignore")
    m = re.search(r'="([^"]*)"', txt)
    if not m:
        return None
    f = m.group(1).split("~")
    if len(f) < 47 or f[2] != code:
        return None
    return {"stock_name": f[1], "pe_ttm": _num(f[39]), "total_mcap_yi": _num(f[45]),
            "circ_mcap_yi": _num(f[44]), "pb": _num(f[46]), "quote_time": f[30],
            "source": "tencent_qt"}


def _strip_html(h: str) -> str:
    h = re.sub(r"<[^>]+>", " ", h).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", h).strip()


def sina_corp_info(code: str) -> Optional[dict]:
    """新浪公司简介页：主营业务、公司简介。"""
    r = _http_get(f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/{code}.phtml")
    h = r.content.decode("gbk", errors="ignore")
    out = {}
    for k in ("主营业务", "公司简介", "经营范围"):
        m = re.search(k + r"[：:]\s*</td>\s*<td[^>]*>(.*?)</td>", h, re.S)
        if m:
            v = _strip_html(m.group(1))
            if v:
                out[k] = v[:600]
    return out or None


def sina_industry_concepts(code: str) -> Optional[dict]:
    """新浪「所属行业/概念」页：申万行业 + 概念板块列表。"""
    r = _http_get(f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpOtherInfo/stockid/{code}/menu_num/2.phtml")
    t = _strip_html(r.content.decode("gbk", errors="ignore"))
    out = {}
    m = re.search(r"所属行业板块 所属行业板块 同行业个股 (\S+) 点击查看", t)
    if m:
        out["industry"] = m.group(1)
    i = t.find("所属概念板块")
    if i >= 0:
        seg = t[i:i + 2000]
        out["concepts"] = re.findall(r"(\S+) 点击查看", seg)[:20]
    return out or None
