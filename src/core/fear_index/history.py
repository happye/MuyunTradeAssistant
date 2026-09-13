"""恐慌指数历史数据层：快照落盘 / 序列回填 / 合成历史序列（point-in-time）。

目录布局（~/.muyun/fear_index/，与其他缓存同级）：
  trade_dates.json                交易日历（baostock，7 天有效）
  snapshots/market_YYYYMMDD.json  每日恐慌总分快照（当日实时计算结果）
  hist/hist_index_000300.json     沪深300 日线 close（baostock）
  hist/hist_turnover.json         两市成交额序列（上证综指+深证综指 amount 相加，万亿）
  hist/hist_margin.json           两市融资余额序列（东财宏观接口沪+深相加，亿元）
  hist/hist_erp.json              股债风险溢价序列（100/沪深300滚动PE - 10Y国债，百分点）
  hist/hist_zt/YYYYMMDD.json      涨停/跌停/炸板家数（东财涨停池，逐日）
  syn_history.json                合成历史恐慌序列缓存
  charts/fear_market_YYYYMMDD.png 多周期走势图

口径纪律（方案 6.5 定稿）：
- 「当日实时口径」与「历史回填口径」同源：波动/动量/成交额历史一律走 baostock
  指数日线；margin/ERP 走整段历史接口。滚动分位不做跨口径比较。
- turnover 统一存万亿（baostock amount 元 -> 亿 -> 万亿），与 benzong
  get_market_turnover 的实时口径（万亿）直接可比。
- 所有文件带 version 字段，FEAR_CACHE_VERSION 不符视为无效重建。
- point-in-time：历史分位用 rolling rank，窗口只含截至当日的样本，杜绝未来函数。
- baostock 读取循环全部包 _call_with_timeout 硬超时（v0.8.9.5 纪律）。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from src.data.net_guard import circuit_breaker, rate_limiter

logger = logging.getLogger(__name__)

# 由 __init__.py 注入的实际版本号（避免循环 import，import 时同步一次）
FEAR_VERSION = "v0.8.10"

BASE_DIR = Path.home() / ".muyun" / "fear_index"

INDEX_CODE = "sh.000300"            # 沪深300（波动/动量成分）
TURNOVER_CODES = ("sh.000001", "sz.399106")  # 上证综指+深证综指，amount 相加≈两市成交额
INDEX_LOOKBACK_DAYS = 750           # 自然日（约 510 交易日：125 均线 + 250 分位窗 + 余量）
ZT_BACKFILL_DEFAULT_DAYS = 250

# 硬超时（秒）：网络读取统一走线程级硬超时，绝不裸调
_NET_TIMEOUT = 30.0


# ── 目录与文件原语 ────────────────────────────────────────

def _ensure_dirs() -> None:
    (BASE_DIR / "snapshots").mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "hist" / "hist_zt").mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "charts").mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, data: dict) -> None:
    """tmp + os.replace 原子写（项目缓存统一纪律）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _load_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:  # 损坏文件不阻断，视为无效重建
        logger.warning(f"恐慌指数缓存文件损坏已忽略重建: {path.name} ({e})")
        return None


def _valid_cache(payload: Optional[dict]) -> bool:
    return bool(payload) and payload.get("version") == FEAR_VERSION


# ── 交易日历 ──────────────────────────────────────────────

def trading_days() -> list[str]:
    """交易日列表（YYYY-MM-DD 升序，覆盖去年至今），baostock，缓存 7 天。

    失败抛 RuntimeError（调用方决定降级方式）。
    """
    _ensure_dirs()
    path = BASE_DIR / "trade_dates.json"
    payload = _load_json(path)
    if _valid_cache(payload):
        age = datetime.now() - datetime.fromisoformat(payload.get("built_at"))
        if age < timedelta(days=7) and payload.get("dates"):
            return payload["dates"]
    from src.data.akshare_client import _call_with_timeout, _ensure_baostock_login
    import baostock as bs

    if not _ensure_baostock_login():
        raise RuntimeError("baostock 登录失败，无法获取交易日历")
    start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    def _q():
        rs = bs.query_trade_dates(start_date=start, end_date=end)
        out = []
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if row[1] == "1":
                out.append(row[0])
        return out

    dates = _call_with_timeout(_q, timeout=_NET_TIMEOUT)
    if not dates:
        raise RuntimeError("交易日历查询为空")
    _atomic_write_json(path, {
        "version": FEAR_VERSION,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "dates": dates,
    })
    return dates


def recent_trade_date(now: Optional[datetime] = None) -> str:
    """基准交易日（YYYY-MM-DD）：当日 15:00 后且为交易日取当日，否则最近上一交易日。"""
    now = now or datetime.now()
    try:
        days = trading_days()
        today = now.strftime("%Y-%m-%d")
        cutoff = now.replace(hour=15, minute=0, second=0, microsecond=0)
        for d in reversed(days):
            if d < today:
                return d
            if d == today and now >= cutoff:
                return d
        return days[-1] if days else today
    except RuntimeError as e:
        # 兜底：上一工作日（可能误判节假日，后续涨停池空数据会被显式标 MISSING 不会造假）
        logger.warning(f"恐慌指数交易日历获取失败，暂按自然日推算基准日: {e}")
        d = now
        while d.weekday() >= 5 or (d.strftime("%H%M") < "1500" and d.date() == now.date()):
            d -= timedelta(days=1)
        return d.strftime("%Y-%m-%d")


# ── baostock 指数日线 ─────────────────────────────────────

def _bs_index_kline(code: str, fields: str, start: str, end: str) -> list[list[str]]:
    """baostock 指数日线查询，读取循环包硬超时。失败抛异常。"""
    from src.data.akshare_client import _call_with_timeout, _ensure_baostock_login
    import baostock as bs

    if not _ensure_baostock_login():
        raise RuntimeError("baostock 登录失败")
    rate_limiter.wait("baostock")

    def _q():
        rs = bs.query_history_k_data_plus(
            code, fields, start_date=start, end_date=end, frequency="d", adjustflag="3")
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        return rows, rs.error_code, rs.error_msg

    rows, err_code, err_msg = _call_with_timeout(_q, timeout=_NET_TIMEOUT)
    if err_code != "0":
        raise RuntimeError(f"baostock {code} 查询失败: {err_msg}")
    if not rows:
        raise RuntimeError(f"baostock {code} 返回空数据")
    return rows


def _hist_path(name: str) -> Path:
    _ensure_dirs()
    return BASE_DIR / "hist" / name


def _load_series(name: str) -> list[dict]:
    payload = _load_json(_hist_path(name))
    if not _valid_cache(payload):
        return []
    return payload.get("rows", [])


def _save_series(name: str, rows: list[dict]) -> None:
    _atomic_write_json(_hist_path(name), {
        "version": FEAR_VERSION,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
    })


def _refresh_index_series(name: str, code: str, field: str, transform=lambda v: float(v),
                          baseline: str = "") -> bool:
    """增量刷新单代码指数序列（close / amount），最后日期 >= baseline 则跳过。"""
    rows = _load_series(name)
    if rows and rows[-1]["date"] >= baseline:
        return False
    start = (rows[-1]["date"] if rows
             else (datetime.now() - timedelta(days=INDEX_LOOKBACK_DAYS)).strftime("%Y-%m-%d"))
    end = datetime.now().strftime("%Y-%m-%d")
    raw = _bs_index_kline(code, f"date,{field}", start, end)
    new_rows = []
    for d, v in raw:
        if not v or v in ("", "0.000000"):
            continue  # 停市行/空值跳过
        try:
            new_rows.append({"date": d, field: transform(float(v))})
        except (TypeError, ValueError):
            continue
    if not new_rows:
        raise RuntimeError(f"{code} 无新增数据")
    # 合并去重（增量重叠区）
    merged = {r["date"]: r for r in rows}
    for r in new_rows:
        merged[r["date"]] = r
    out = [merged[k] for k in sorted(merged)]
    _save_series(name, out)
    return True


# ── 轻量自动回填（一次请求拿整段历史的源） ────────────────

def _em_full_series(label: str, fetch_fn: Callable[[], object],
                    pick: Callable[[object], list[dict]]) -> list[dict]:
    """东财/乐咕整段历史接口通用包装：绕代理 + 节流 + 熔断 + 硬超时。"""
    if not circuit_breaker.allow(f"em_{label}"):
        raise RuntimeError(f"{label} 数据源熔断中（连续失败保护），稍后再试")
    from src.scanner.market_cache import MarketCache
    rate_limiter.wait("eastmoney")
    try:
        with MarketCache._without_proxy():
            from src.data.akshare_client import _call_with_timeout
            df = _call_with_timeout(fetch_fn, timeout=_NET_TIMEOUT)
        rows = pick(df)
        circuit_breaker.record_success(f"em_{label}")
        return rows
    except Exception:
        circuit_breaker.record_failure(f"em_{label}")
        raise


def _fetch_margin_rows() -> list[dict]:
    """两市合计融资余额（亿元）：macro 接口沪+深按日期相加，整段历史。"""
    import akshare as ak

    def _pick(df):
        out = {}
        for _, r in df.iterrows():
            d = r["日期"]
            d = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            v = r.get("融资余额")
            if v is not None and pd.notna(v):
                out[d] = out.get(d, 0.0) + float(v) / 1e8  # 元 -> 亿
        return [{"date": k, "balance_yi": round(out[k], 2)} for k in sorted(out)]

    sh = _em_full_series("margin_sh", lambda: ak.macro_china_market_margin_sh(), _pick)
    sz = _em_full_series("margin_sz", lambda: ak.macro_china_market_margin_sz(), _pick)
    merged = {r["date"]: r["balance_yi"] for r in sh}
    for r in sz:
        merged[r["date"]] = merged.get(r["date"], 0.0) + r["balance_yi"]
    return [{"date": k, "balance_yi": round(merged[k], 2)} for k in sorted(merged)]


def _fetch_erp_rows() -> list[dict]:
    """股债风险溢价序列：每日盈利收益率 - 10Y国债收益率（百分点），自算日频。

    乐咕/中证的指数 PE 历史均为稀疏采样（近3年仅38-40个点，不够分位窗），故：
    1. 用乐咕沪深300 PE 采样点反推盈利阶梯 E_k = close_k / PE_k（盈利是季度级
       慢变量，阶梯化符合财报节奏，point-in-time：截至 t 用最近的 E_k）
    2. 每日盈利收益率 = E_latest(t) / close_t（close 走 baostock 日频，真实价格）
    3. ERP = 盈利收益率 × 100 - 10Y（CalendarClient），任一缺即弃该日（不脑补）
    """
    import akshare as ak

    def _pick_pe(df):
        out = []
        for _, r in df.iterrows():
            d, pe = r.get("日期"), r.get("滚动市盈率")
            if pe is None or pd.isna(pe) or not pe:
                continue
            d = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            pe = float(pe)
            if pe > 0:
                out.append((d, pe))
        return out

    pe_points = _em_full_series("csi300_pe", lambda: ak.stock_index_pe_lg(symbol="沪深300"), _pick_pe)

    closes = {r["date"]: float(r["close"])
              for r in _load_series("hist_index_000300.json") if r.get("close")}
    if not closes:
        raise RuntimeError("沪深300日线序列缺失，无法自算盈利收益率（先刷新指数序列）")

    # 盈利阶梯：采样日 E = close/PE；对齐不到 close 的采样点弃（不猜价格）
    earnings = []
    for d, pe in pe_points:
        c = closes.get(d)
        if c:
            earnings.append((d, c / pe))
    earnings.sort()
    if len(earnings) < 8:
        raise RuntimeError(f"PE 采样点可对齐不足({len(earnings)})，盈利阶梯不可靠")
    earn_dates = [d for d, _ in earnings]

    from src.data.calendar_client import CalendarClient
    bond = CalendarClient.get_bond_yield_history(years=3)
    if bond is None or bond.empty:
        raise RuntimeError("10Y 国债收益率获取失败（CalendarClient）")
    rate_col = next((c for c in bond.columns if "中国" in c and "10年" in c and "-" not in c), None)
    if rate_col is None:
        raise RuntimeError("bond_zh_us_rate 未找到中国10年国债列")
    rates = {}
    for _, r in bond.iterrows():
        d, v = r["日期"], r[rate_col]
        if v is None or pd.isna(v):
            continue
        d = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
        rates[d] = float(v)

    # 逐交易日：盈利用截至该日的最近采样点（阶梯/point-in-time），价格真实日频
    out = []
    ei = 0
    last_e = None
    for d in sorted(closes):
        while ei < len(earnings) and earnings[ei][0] <= d:
            last_e = earnings[ei][1]
            ei += 1
        if last_e is None:
            continue  # 首个采样点之前的日期弃
        y10 = rates.get(d)
        if y10 is None:
            continue
        earn_yield = last_e / closes[d] * 100.0
        out.append({"date": d, "erp_pct": round(earn_yield - y10, 4)})
    if len(out) < 60:
        raise RuntimeError(f"ERP 日频序列不足({len(out)}<60)")
    return out


def ensure_light_history(baseline: str, force: bool = False) -> dict:
    """轻量自动回填：指数/成交额/两融/ERP 序列缺则补（一次请求拿整段历史，秒级）。

    Returns: {"<series>": "fresh"|"skipped"|"failed", ...}
    """
    status = {}
    jobs = [
        ("index_000300", lambda: _refresh_index_series(
            "hist_index_000300.json", INDEX_CODE, "close", baseline=baseline)),
        ("turnover_sh", lambda: _refresh_index_series(
            "hist_turnover_sh.json", TURNOVER_CODES[0], "amount",
            transform=lambda v: round(v / 1e12, 4), baseline=baseline)),   # 元 -> 万亿
        ("turnover_sz", lambda: _refresh_index_series(
            "hist_turnover_sz.json", TURNOVER_CODES[1], "amount",
            transform=lambda v: round(v / 1e12, 4), baseline=baseline)),
    ]
    for name, fn in jobs:
        try:
            status[name] = "fresh" if fn() else "skipped"
        except Exception as e:
            status[name] = "failed"
            logger.warning(f"恐慌指数历史数据({name})更新失败，沿用已有序列: {e}")
    # margin / ERP：整段接口，当日已拉过则跳过（当日有效缓存语义）
    for name, path_name, fn in (
        ("margin", "hist_margin.json", _fetch_margin_rows),
        ("erp", "hist_erp.json", _fetch_erp_rows),
    ):
        path = _hist_path(path_name)
        payload = _load_json(path)
        if not force and _valid_cache(payload):
            built = str(payload.get("updated_at", ""))[:10]
            if built == datetime.now().strftime("%Y-%m-%d"):
                status[name] = "skipped"
                continue
        try:
            rows = fn()
            _save_series(path_name, rows)
            status[name] = "fresh"
        except Exception as e:
            status[name] = "failed"
            logger.warning(f"恐慌指数历史数据({name})更新失败，沿用已有序列: {e}")
    return status


# ── 涨停池（当日 + 显式回填） ─────────────────────────────

def fetch_zt_counts(date_yyyymmdd: str) -> Optional[dict]:
    """拉某交易日涨停/跌停/炸板家数（东财三池），落盘 hist_zt/。

    返回 {"date","zt","dt","zb","zb_rate"}；主池失败或空数据返回 None（不落盘、
    不把接口异常当真实 0——防污染）。调用方负责节流（本函数内置 eastmoney 限速）。
    """
    import akshare as ak

    if not circuit_breaker.allow("em_zt_pool"):
        return None
    rates = {}
    ok = True
    for key, fn in (
        ("zt", lambda: ak.stock_zt_pool_em(date=date_yyyymmdd)),
        ("zb", lambda: ak.stock_zt_pool_zbgc_em(date=date_yyyymmdd)),
        ("dt", lambda: ak.stock_zt_pool_dtgc_em(date=date_yyyymmdd)),
    ):
        try:
            from src.scanner.market_cache import MarketCache
            rate_limiter.wait("eastmoney")
            with MarketCache._without_proxy():
                from src.data.akshare_client import _call_with_timeout
                df = _call_with_timeout(fn, timeout=25.0)
            if df is None or len(df) == 0:
                if key == "zt":
                    ok = False  # 主池空=非交易日或接口异常，整体放弃
                    break
                rates[key] = 0
            else:
                rates[key] = int(len(df))
            circuit_breaker.record_success("em_zt_pool")
        except Exception as e:
            circuit_breaker.record_failure("em_zt_pool")
            if key == "zt":
                logger.debug(f"涨停池 {date_yyyymmdd} 拉取失败: {e}")
                ok = False
                break
            rates[key] = None  # 辅池失败不致命，该计数缺失
    if not ok or "zt" not in rates:
        return None
    zt, zb = rates.get("zt", 0), rates.get("zb")
    zb_rate = round(zb / (zt + zb) * 100, 2) if zb is not None and (zt + zb) > 0 else None
    return {
        "date": f"{date_yyyymmdd[:4]}-{date_yyyymmdd[4:6]}-{date_yyyymmdd[6:]}",
        "zt": zt, "dt": rates.get("dt"), "zb": zb, "zb_rate": zb_rate,
    }


def load_zt_series() -> list[dict]:
    """已落盘的涨停池序列（升序）。"""
    _ensure_dirs()
    out = []
    zt_dir = BASE_DIR / "hist" / "hist_zt"
    for p in sorted(zt_dir.glob("*.json")):
        payload = _load_json(p)
        if _valid_cache(payload) and not payload.get("empty"):
            out.append(payload)
    return out


def backfill_zt_pools(days: int = ZT_BACKFILL_DEFAULT_DAYS,
                      progress: Optional[Callable[[int, int, str, bool], None]] = None) -> dict:
    """涨停池显式回填：逐日 3 池，断点续传（已落盘日期跳过）。

    实测约束（2026-09-13）：东财涨停池历史接口仅保留约 20-30 个交易日，
    更早日期一律空数据——故连续 15 个交易日拿不到数据即视为到达保留边界，
    提前停止（不再对更早日期白白打接口）。进度按实际探测进度计。

    progress(i, total, date, ok) 供 CLI 进度显示。返回统计 dict。
    """
    baseline = recent_trade_date()
    try:
        # 只回填 <= 基准日的交易日（trading_days 含未来 30 天，未来日期东财必然空）
        days_list = [d for d in trading_days() if d <= baseline][-days:]
    except RuntimeError as e:
        return {"error": f"交易日历不可用: {e}"}
    _ensure_dirs()
    zt_dir = BASE_DIR / "hist" / "hist_zt"
    filled = skipped = failed = 0
    total = len(days_list)
    consecutive_empty = 0
    # 从新到旧遍历：先拿最近的数据，碰到接口保留边界立即停，不对更早日期白打请求
    for i, d in enumerate(reversed(days_list), 1):
        ymd = d.replace("-", "")
        if (zt_dir / f"{ymd}.json").exists():
            skipped += 1
            consecutive_empty = 0
            if progress:
                progress(i, total, d, True)
            continue
        counts = fetch_zt_counts(ymd)
        ok = counts is not None
        if ok:
            counts["version"] = FEAR_VERSION
            _atomic_write_json(zt_dir / f"{ymd}.json", counts)
            filled += 1
            consecutive_empty = 0
        else:
            failed += 1
            consecutive_empty += 1
            if consecutive_empty >= 15:
                logger.info(
                    f"涨停池回填到达接口保留边界（连续{consecutive_empty}个交易日无数据），"
                    f"停止回填：共填{filled}天，接口仅保留近期约20-30个交易日")
                if progress:
                    progress(i, total, d, False)
                break
        if progress:
            progress(i, total, d, ok)
    return {"total": total, "filled": filled, "skipped": skipped, "failed": failed,
            "note": "接口仅保留近期数据，更早历史不可回填（分位随每日快照积累扩展）"}


# ── 每日快照 ──────────────────────────────────────────────

def snapshot_path(date_yyyymmdd: str) -> Path:
    _ensure_dirs()
    return BASE_DIR / "snapshots" / f"market_{date_yyyymmdd}.json"


def save_snapshot(snap: dict) -> None:
    snap = dict(snap)
    snap["version"] = FEAR_VERSION
    _atomic_write_json(snapshot_path(snap["date"].replace("-", "")), snap)


def load_snapshots() -> dict:
    """{date(YYYY-MM-DD): snapshot}，版本不符的文件忽略。"""
    _ensure_dirs()
    out = {}
    for p in sorted((BASE_DIR / "snapshots").glob("market_*.json")):
        payload = _load_json(p)
        if _valid_cache(payload) and payload.get("date"):
            out[payload["date"]] = payload
    return out


# ── 合成历史恐慌序列（point-in-time 滚动分位） ────────────

def synthesize_history(days: int = 250, baseline: Optional[str] = None) -> list[dict]:
    """合成最近 days 个交易日的恐慌总分序列（每次全量重算，不缓存）。

    不缓存的原因（对抗审查发现）：回填/快照随时在补历史，任何基于
    baseline 的缓存判据都会把「成分还没回齐时算出的旧序列」长期留住
    （zt 成分将在缓存里永远缺失）。pandas 向量化下 510 行 × 7 成分的
    rolling rank 为毫秒级，无缓存必要。

    每个历史日的成分分位只用截至该日的数据（pandas rolling rank，point-in-time）；
    breadth 依赖每日快照（不可回填），缺快照的日期该成分缺失并重归一。
    返回升序 [{date, score, n_components}]。
    """
    baseline = baseline or recent_trade_date()

    closes = _series_map(_load_series("hist_index_000300.json"), "close")
    marg = _series_map(_load_series("hist_margin.json"), "balance_yi")
    erp = _series_map(_load_series("hist_erp.json"), "erp_pct")
    if not closes:
        logger.warning("恐慌指数合成序列缺少沪深300日线，历史序列不可用")
        return []

    dates = sorted(closes)
    s_close = pd.Series([closes[d] for d in dates], index=dates, dtype=float)
    ret = s_close.pct_change() * 100.0
    vol20 = ret.rolling(20).std()                       # 20日已实现波动（%/日）
    ma125 = s_close.rolling(125).mean()
    mom = (s_close / ma125 - 1.0) * 100.0               # MA125 乖离率 %
    # 成交额：沪+深**双源齐才算数**（交集语义）——单边缺时 None，绝不把单边当双边
    sh_map = _series_map(_load_series("hist_turnover_sh.json"), "amount")
    sz_map = _series_map(_load_series("hist_turnover_sz.json"), "amount")
    s_turn = pd.Series(
        [sh_map[d] + sz_map[d] if d in sh_map and d in sz_map else None for d in dates],
        index=dates, dtype=float)                        # 万亿
    s_marg = pd.Series([marg.get(d) for d in dates], index=dates, dtype=float)
    marg_chg = s_marg.pct_change(5) * 100.0             # 融资余额5日变化率 %
    s_erp = pd.Series([erp.get(d) for d in dates], index=dates, dtype=float)

    zt = {r["date"]: r for r in load_zt_series()}
    s_dt = pd.Series([ (zt.get(d) or {}).get("dt") for d in dates], index=dates, dtype=float)
    s_zb = pd.Series([ (zt.get(d) or {}).get("zb_rate") for d in dates], index=dates, dtype=float)

    snaps = load_snapshots()
    s_breadth = pd.Series([
        ((snaps.get(d) or {}).get("metrics") or {}).get("breadth")
        for d in dates], index=dates, dtype=float)

    def _rank(s: pd.Series, higher_is_panic: bool) -> pd.Series:
        r = s.rolling(250, min_periods=60).rank(pct=True) * 100.0
        return r if higher_is_panic else 100.0 - r

    from .normalizer import clamp_score, threshold_panic

    vol_score = _rank(vol20, True)
    turn_score = _rank(s_turn, False)
    marg_score = _rank(marg_chg, False)
    erp_score = _rank(s_erp, True)
    dt_score = _rank(s_dt, True)
    zb_score = _rank(s_zb, True)
    mom_score = mom.map(lambda v: threshold_panic(v, _MOM_STOPS) if pd.notna(v) else None)
    # breadth：快照积累足够走分位，否则阈值兜底（方案 §3.3 双模式）
    breadth_score = _rank(s_breadth.dropna(), False).reindex(dates)
    breadth_score = breadth_score.where(
        s_breadth.notna() & breadth_score.notna(),
        s_breadth.map(lambda v: threshold_panic(v, _BREADTH_STOPS) if pd.notna(v) else None),
    )

    frames = {
        "breadth": breadth_score, "zt_heat": None, "volatility": vol_score,
        "momentum": mom_score, "turnover": turn_score, "margin": marg_score, "erp": erp_score,
    }
    zt_heat = pd.concat([dt_score, zb_score], axis=1).mean(axis=1, skipna=True)
    zt_heat[zt_heat.isna() | ((dt_score.isna()) & (zb_score.isna()))] = None
    frames["zt_heat"] = zt_heat

    weights = {"breadth": 1.0, "zt_heat": 1.0, "volatility": 1.0, "momentum": 1.0,
               "turnover": 1.0, "margin": 1.0, "erp": 1.0}
    stack = pd.DataFrame(frames)
    w = pd.Series(weights)
    avail = stack.notna()
    wsum = (avail * w).sum(axis=1)
    score = (stack.fillna(0) * w).sum(axis=1) / wsum.where(wsum > 0)
    n_comp = avail.sum(axis=1)

    points = []
    for d in dates[-days:]:
        sc = score.get(d)
        if sc is None or pd.isna(sc):
            continue
        points.append({"date": d, "score": round(float(clamp_score(sc)), 1),
                       "n_components": int(n_comp.get(d, 0))})
    return points


def _series_map(rows: list[dict], key: str) -> dict:
    return {r["date"]: float(r[key]) for r in rows
            if r.get(key) is not None and r.get("date")}


# 动量乖离率(%) -> 恐慌分 锚点（方案方向口径定稿：价高=贪婪=低恐慌分）
_MOM_STOPS = [(-15.0, 95.0), (-5.0, 75.0), (0.0, 50.0), (5.0, 25.0), (15.0, 5.0)]
# 上涨家数占比(0-1) -> 恐慌分 锚点（threshold 兜底口径，快照分位积累后优先分位）
_BREADTH_STOPS = [(0.20, 95.0), (0.30, 75.0), (0.50, 50.0), (0.70, 25.0), (0.80, 5.0)]
