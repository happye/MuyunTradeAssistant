"""个股层高位止盈信号（跳法A 阶段2 / v0.8.6.4）

笨总教学：个股见顶三信号 = 换手率>40% + 缩量加速上涨 + 实控人减持。
全部客观硬规则，不依赖 AI 判断。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# 报告② 日缓存助手：股东户数(季报，日级缓存足够)/融资余额(日级)避免每次分析都打 akshare
def _holder_cache_dir() -> Path:
    d = Path.home() / ".muyun" / "exit_signal_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _daily_cache_get(code: str, signal: str) -> Optional[object]:
    """读日缓存（key=code_date_signal）。命中返回缓存值（可能是 None哨兵），未命中返回 _MISS。"""
    today = datetime.now().strftime("%Y-%m-%d")
    f = _holder_cache_dir() / f"{code}_{today}_{signal}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return _MISS
    return _MISS


def _daily_cache_set(code: str, signal: str, value) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    f = _holder_cache_dir() / f"{code}_{today}_{signal}.json"
    try:
        f.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


_MISS = object()  # 哨兵：缓存未命中（区别于缓存的 None 值）

# 个股换手率见顶阈值(%)
TURNOVER_TOP_PCT = 40.0
# 缩量加速：近期量比阈值（当前量 / 均量 < 此值视为缩量）
SHRINK_VOLUME_RATIO = 0.7
# 缩量加速：涨幅阈值(%)，缩量+大涨=加速赶顶
SHRINK_GAIN_PCT = 15.0
# 三倍定律（教学六/八）：从近60日低点涨幅≥3倍 且 破5日线 -> 减仓/清仓
TRIPLE_UP_MULTIPLE = 3.0
# 股东户数激增（教学八）：近两期增幅>50% = 筹码分散主力派发
HOLDER_COUNT_SURGE_PCT = 50.0
# 融资余额激增（教学八）：近5日增幅>10% = 杠杆踩踏风险
MARGIN_SURGE_PCT = 10.0

# 实控人减持关键词（标题需同时命中减持词 + 主体词）
_REDUCE_KEYWORDS = ("减持", "拟减持")
_HOLDER_KEYWORDS = ("控股股东", "实际控制人", "实控人", "大股东")


def check_stock_top_signal(stock_data, code: str, *, turnover_pct: Optional[float] = None,
                           announcements: Optional[list] = None,
                           live: bool = False,
                           mode: Optional[str] = None) -> Optional[str]:
    """检查个股层大顶信号，返回首个触发的描述（无则 None）。

    Args:
        stock_data: StockData（缩量加速判定用 volume/avg_volume/change_pct）
        code: 股票代码
        turnover_pct: 当日换手率(%)。live 路径可从 MarketCache 快照传入；
            回测路径通常 None（历史换手率需流通股本，data_provider 未提供）→ 跳过该子信号
        announcements: 近期公告列表 [{title,...}]。None 时不检查实控人减持
            （回测历史公告获取受限，诚实声明）

    Returns:
        Optional[str]: 信号描述，如 "个股:换手率超40%(45.2%)"
    """
    # 信号1：换手率 > 40%
    if turnover_pct is not None and turnover_pct > TURNOVER_TOP_PCT:  # 严格大于，40.0%整数值(主板涨停常见)不误触发
        return f"个股:换手率超40%({turnover_pct:.1f}%)"

    # 信号2：缩量加速上涨（量比 < 0.7 且 涨幅 > 15%）
    accel = _check_shrink_acceleration(stock_data)
    if accel:
        return accel

    # 信号3：实控人减持公告
    if announcements:
        reduce_sig = _check_holder_reduction(announcements)
        if reduce_sig:
            return reduce_sig

    # 信号4：三倍定律+5日线破位（教学六/八）--从近60日低点涨≥3倍且破5日线
    # 报告①回测发现：气宗牛股被三倍定律 force-exit 致过早离场（与气宗"拿住整波主升浪"冲突）。
    # 设计修正：气宗(mode=qizong)跳过三倍定律（同 take_profit_trim 可压，气宗靠换手/减持/渗透率/旗手等真见顶信号逃顶）；
    # 剑宗/非气宗照常触发（一波流涨三倍该止盈就走）。
    if mode != "qizong":
        triple = _check_triple_up_rule(stock_data)
        if triple:
            return triple

    # 信号5/6：股东户数激增 + 融资余额激增（教学八，akshare 网络+日缓存）
    # 仅 live 路径启用（回测 point-in-time 不可用+网络成本）；日缓存避免重复打 akshare
    if live and code:
        hc = _check_holder_count_surge(code)
        if hc:
            return hc
        mg = _check_margin_surge(code)
        if mg:
            return mg

    return None


def _check_shrink_acceleration(stock_data) -> Optional[str]:
    """缩量加速上涨：成交量萎缩但价格大涨，典型赶顶背离。"""
    if stock_data is None:
        return None
    try:
        vol = getattr(stock_data, "volume", None)
        avg_vol = getattr(stock_data, "avg_volume_5", None) or getattr(stock_data, "avg_volume_20", None)
        change_pct = getattr(stock_data, "change_pct", None)
        if not vol or not avg_vol or avg_vol <= 0 or change_pct is None:
            return None
        ratio = vol / avg_vol
        if ratio < SHRINK_VOLUME_RATIO and change_pct > SHRINK_GAIN_PCT:
            return f"个股:缩量加速(量比{ratio:.2f},涨{change_pct:.1f}%)"
    except Exception as e:
        logger.debug(f"缩量加速判定异常: {e}")
    return None


def _check_holder_reduction(announcements: list) -> Optional[str]:
    """扫公告标题：同时命中减持词 + 控股股东/实控人主体词。"""
    for ann in announcements:
        title = (ann.get("title") if isinstance(ann, dict) else str(ann)) or ""
        if any(k in title for k in _REDUCE_KEYWORDS) and any(k in title for k in _HOLDER_KEYWORDS):
            return f"个股:实控人减持({title[:20]})"
    return None


def _check_triple_up_rule(stock_data) -> Optional[str]:
    """三倍定律+5日线破位（笨总教学六/八）。

    "一口气涨三倍以上且持续拉升 -> 清五日线减仓或清仓"。
    判定：price / low_60d >= 3.0 且 price < MA5（高位破5日线即卖，不等MA20确认）。
    基准用 low_60d（近60日低点），捕获近期主升浪的"一口气涨三倍"。
    """
    if stock_data is None:
        return None
    try:
        price = getattr(stock_data, "price", None)
        ma5 = getattr(stock_data, "ma5", None)
        low_60d = getattr(stock_data, "low_60d", None)
        if not price or not ma5 or not low_60d or low_60d <= 0:
            return None
        multiple = price / low_60d
        if multiple >= TRIPLE_UP_MULTIPLE and price < ma5:
            return f"个股:三倍定律+破5日线(近60日涨{multiple:.1f}倍,price<MA5)"
    except Exception as e:
        logger.debug(f"三倍定律判定异常: {e}")
    return None


def _check_holder_count_surge(code: str) -> Optional[str]:
    """股东户数激增（笨总教学八）：近两期增幅>50% = 筹码分散主力派发。

    "5万->15-20万=筹码从主力分散到散户=主力派发出货"。
    akshare stock_zh_a_gdhs_detail_em(symbol=代码) 返回该股历次股东户数。
    日缓存（季报数据，日内不变）避免每次分析都打 akshare。
    回测路径不应调用（point-in-time）；由 check_stock_top_signal live 门控。
    """
    cached = _daily_cache_get(code, "holder_count")
    if cached is not _MISS:
        return cached
    result = None
    try:
        from src.core.benzong.data_provider import _safe_call

        def _fetch():
            import akshare as ak
            return ak.stock_zh_a_gdhs_detail_em(symbol=code)

        df = _safe_call("stock_zh_a_gdhs_detail_em", _fetch, timeout=15)
        if df is not None and len(df) >= 1:
            cur_col = next((c for c in df.columns if "本次" in c), None)
            prev_col = next((c for c in df.columns if "上次" in c), None)
            if cur_col and prev_col:
                latest_cur = float(df.iloc[0][cur_col])
                latest_prev = float(df.iloc[0][prev_col])
                if latest_prev > 0:
                    surge = (latest_cur - latest_prev) / latest_prev * 100
                    if surge > HOLDER_COUNT_SURGE_PCT:
                        result = f"个股:股东户数激增({latest_prev:.0f}->{latest_cur:.0f},+{surge:.0f}%)"
    except Exception as e:
        logger.debug(f"股东户数判定异常({code}): {e}")
    _daily_cache_set(code, "holder_count", result)
    return result


def _check_margin_surge(code: str) -> Optional[str]:
    """融资余额激增（笨总教学八）：近5日增幅>10% = 杠杆踩踏风险。

    akshare 融资余额 API 按日期返回全市场（非按个股），需逐日查+过滤代码。
    查近2个有数据的交易日算增幅。用 _safe_call 包超时 fail-open。
    日缓存避免重复打 akshare。回测路径不应调用；由 check_stock_top_signal live 门控。
    """
    cached = _daily_cache_get(code, "margin")
    if cached is not _MISS:
        return cached
    result = None
    try:
        import akshare as ak
        from datetime import datetime, timedelta
        from src.core.benzong.data_provider import _safe_call
        is_sse = code.startswith("6")

        def _get_balance(date_str):
            def _fetch():
                df = (ak.stock_margin_detail_sse(date=date_str) if is_sse
                      else ak.stock_margin_detail_szse(date=date_str))
                if df is None or df.empty:
                    return None
                mcol = next((c for c in df.columns if "融资余额" in str(c)), None)
                code_col = next((c for c in df.columns if "代码" in str(c) or "code" in str(c).lower()), None)
                if not mcol or not code_col:
                    return None
                row = df[df[code_col].astype(str).str.contains(code, na=False)]
                if row.empty:
                    return None
                return float(row.iloc[0][mcol])
            return _safe_call("margin_detail_" + date_str, _fetch, timeout=15)

        # 取最近2个有数据的交易日，间隔≥4天（算"近5日增幅"，非1日波动，防误触发）
        dates = [(datetime.now() - timedelta(days=i)).strftime("%Y%m%d") for i in range(0, 9)]
        latest = prev = None
        _none_streak = 0
        for d in dates:
            v = _get_balance(d)
            if v is None or v <= 0:
                _none_streak += 1
                if _none_streak >= 3:
                    break  # 连续3次无数据，放弃（防最坏9×15s=135s阻塞）
                continue
            _none_streak = 0
            if latest is None:
                latest = (d, v)
            else:
                d_date = datetime.strptime(d, "%Y%m%d")
                latest_date = datetime.strptime(latest[0], "%Y%m%d")
                if (latest_date - d_date).days >= 4:
                    prev = (d, v)
                    break
                # 否则继续往更早日期找间隔≥4天的
        if not latest or not prev or prev[1] <= 0:
            # 无数据 -> result 保持 None，落到末尾缓存+返回（避免下次重打 akshare）
            pass
        else:
            surge = (latest[1] - prev[1]) / prev[1] * 100
            if surge > MARGIN_SURGE_PCT:
                result = f"个股:融资余额激增({prev[0]}->{latest[0]},+{surge:.1f}%,杠杆踩踏风险)"
    except Exception as e:
        logger.debug(f"融资余额判定异常({code}): {e}")
    _daily_cache_set(code, "margin", result)
    return result
