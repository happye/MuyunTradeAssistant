"""个股层高位止盈信号（跳法A 阶段2 / v0.8.6.4）

笨总教学：个股见顶三信号 = 换手率>40% + 缩量加速上涨 + 实控人减持。
全部客观硬规则，不依赖 AI 判断。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

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
                           announcements: Optional[list] = None) -> Optional[str]:
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
    triple = _check_triple_up_rule(stock_data)
    if triple:
        return triple

    # 信号5/6：股东户数激增 + 融资余额激增（教学八）--已实现为独立函数
    # _check_holder_count_surge / _check_margin_surge，但不在此自动调用：
    # (a) 每次分析打 akshare 太重（import 慢 + 网络）；(b) 回测 point-in-time 不可用。
    # 激活需 fetch+cache 层（类似 announcements 的预取传入），留后续。函数可独立调用。
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
    akshare stock_zh_a_gdhs_detail_em(symbol=代码) 返回该股历次股东户数，
    用 _safe_call 包超时（fail-open）。注意 stock_zh_a_gdhs 参数是日期非代码（会 hang）。
    回测路径不应调用（point-in-time 问题）；live 持仓检查用，当前未自动触发。
    """
    try:
        from src.core.benzong.data_provider import _safe_call

        def _fetch():
            import akshare as ak
            return ak.stock_zh_a_gdhs_detail_em(symbol=code)

        df = _safe_call("stock_zh_a_gdhs_detail_em", _fetch, timeout=15)
        if df is None or len(df) < 2:
            return None
        # 列：股东户数-本次 / 股东户数-上次（detail_em 结构）
        cur_col = next((c for c in df.columns if "本次" in c), None)
        prev_col = next((c for c in df.columns if "上次" in c), None)
        if cur_col is None or prev_col is None:
            return None
        latest_cur = float(df.iloc[0][cur_col])
        latest_prev = float(df.iloc[0][prev_col])
        if latest_prev <= 0:
            return None
        surge = (latest_cur - latest_prev) / latest_prev * 100
        if surge > HOLDER_COUNT_SURGE_PCT:
            return f"个股:股东户数激增({latest_prev:.0f}->{latest_cur:.0f},+{surge:.0f}%)"
    except Exception as e:
        logger.debug(f"股东户数判定异常({code}): {e}")
    return None


def _check_margin_surge(code: str) -> Optional[str]:
    """融资余额激增（笨总教学八）：近5日增幅>10% = 杠杆踩踏风险。

    akshare 融资余额 API 按日期返回全市场（非按个股），需逐日查+过滤代码。
    查近2个有数据的交易日（间隔~5天）算增幅。用 _safe_call 包超时 fail-open。
    回测路径不应调用；live 持仓检查用，且当前未自动触发（需 cache 层激活）。
    """
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

        # 取最近2个有数据的交易日（间隔尽量≥4天）
        dates = [(datetime.now() - timedelta(days=i)).strftime("%Y%m%d") for i in range(0, 9)]
        latest = prev = None
        for d in dates:
            v = _get_balance(d)
            if v is not None and v > 0:
                if latest is None:
                    latest = (d, v)
                elif prev is None:
                    # 取间隔较远的一个
                    prev = (d, v)
                    break
        if not latest or not prev or prev[1] <= 0:
            return None
        surge = (latest[1] - prev[1]) / prev[1] * 100
        if surge > MARGIN_SURGE_PCT:
            return f"个股:融资余额激增({prev[0]}->{latest[0]},+{surge:.1f}%,杠杆踩踏风险)"
    except Exception as e:
        logger.debug(f"融资余额判定异常({code}): {e}")
    return None
