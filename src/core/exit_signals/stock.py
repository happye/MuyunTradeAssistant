"""个股层高位止盈信号（跳法A 阶段2 / v0.8.6.4）

笨总教学：个股见顶三信号 = 换手率>40% + 缩量加速上涨 + 实控人减持。
全部客观硬规则，不依赖 AI 判断。
换手率子信号因无数据源未实现（历史换手率需流通股本数据，StockData 无此字段，
2026-08-24 经用户拍板移除死代码；若未来接入换手率数据源再按教学恢复该分支）。
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
    d = _holder_cache_dir()
    f = d / f"{code}_{today}_{signal}.json"
    try:
        f.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    # ISS-091：日键文件跨日本就失效，顺带清理 30 天前的旧文件防无限累积
    try:
        import time as _time
        _now = _time.time()
        for old in d.glob("*.json"):
            try:
                if _now - old.stat().st_mtime > 30 * 86400:
                    old.unlink()
            except OSError:
                continue
    except Exception:
        pass


_MISS = object()  # 哨兵：缓存未命中（区别于缓存的 None 值）

# 融资余额全市场表的进程级 memo：(exchange, date_str) -> DataFrame|None（la 批量跨股共享一次下载）
_MARGIN_TABLE_MEMO: dict = {}

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


def check_stock_top_signal(stock_data, code: str, *,
                           announcements: Optional[list] = None,
                           live: bool = False,
                           mode: Optional[str] = None) -> Optional[str]:
    """检查个股层大顶信号，返回首个触发的描述（无则 None）。

    Args:
        stock_data: StockData（缩量加速判定用 volume/avg_volume/change_pct）
        code: 股票代码
        announcements: 近期公告列表 [{title,...}]。None 时不检查实控人减持
            （回测历史公告获取受限，诚实声明）

    Returns:
        Optional[str]: 信号描述，如 "个股:缩量加速(量比0.65,涨16.2%)"
    """
    # 信号2：缩量加速上涨（量比 < 0.7 且 涨幅 > 15%）
    accel = _check_shrink_acceleration(stock_data)
    if accel:
        return accel

    # 信号：实控人减持公告
    if announcements:
        reduce_sig = _check_holder_reduction(announcements)
        if reduce_sig:
            return reduce_sig

    # 信号：三倍定律+5日线破位（教学六/八）--从近60日低点涨≥3倍且破5日线
    # 报告①回测发现：气宗牛股被三倍定律 force-exit 致过早离场（与气宗"拿住整波主升浪"冲突）。
    # 设计修正：气宗(mode=qizong)跳过三倍定律（同 take_profit_trim 可压，气宗靠换手/减持/渗透率/旗手等真见顶信号逃顶）；
    # 剑宗/非气宗照常触发（一波流涨三倍该止盈就走）。
    if mode != "qizong":
        triple = _check_triple_up_rule(stock_data)
        if triple:
            return triple

    # 信号：股东户数激增 + 融资余额激增（教学八，akshare 网络+日缓存）
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
    """缩量加速上涨：成交量萎缩但价格大涨，典型赶顶背离。

    报告§2.2 连续性增强（2026-08-23）：单日缩量+大涨易误判，要求昨日也缩量
    才算"连续缩量加速"。昨日量比用近5日序列里的前几日均量近似。
    volume_series 仅 live 路径填充；缺失/长度不足时退回单日判定（回测路径行为不变）。
    """
    if stock_data is None:
        return None
    try:
        vol = getattr(stock_data, "volume", None)
        avg_vol = getattr(stock_data, "avg_volume_5", None) or getattr(stock_data, "avg_volume_20", None)
        change_pct = getattr(stock_data, "change_pct", None)
        if not vol or not avg_vol or avg_vol <= 0 or change_pct is None:
            return None
        ratio = vol / avg_vol
        if not (ratio < SHRINK_VOLUME_RATIO and change_pct > SHRINK_GAIN_PCT):
            return None
        # 连续性确认：昨日(vs[-2])相对其之前可得日的均量也要 <0.7。序列缺失或过短则跳过确认。
        vs = getattr(stock_data, "volume_series", None)
        if vs and len(vs) >= 5:
            prev_vol = float(vs[-2])
            base = [float(x) for x in list(vs[:-2]) if x]
            if prev_vol > 0 and base and sum(base) > 0:
                prev_ratio = prev_vol / (sum(base) / len(base))
                if prev_ratio >= SHRINK_VOLUME_RATIO:
                    return None  # 昨日未缩量，单日缩量不构成"连续缩量加速"
                return f"个股:缩量加速(量比{ratio:.2f},涨{change_pct:.1f}%,连续2日缩量)"
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
        exchange = "sse" if is_sse else "szse"

        # v0.8.7.2 审查修复：融资余额接口按"日期"返回全市场大表，而日缓存键是 code——
        # la 批量 N 只同交易所持仓会把同一批表重复下载 ~N 遍。加进程级表级 memo：
        # (exchange,date)->整张df，同批次跨股共享一次下载。
        def _get_margin_table(date_str):
            memo_key = (exchange, date_str)
            if memo_key in _MARGIN_TABLE_MEMO:
                return _MARGIN_TABLE_MEMO[memo_key]

            def _fetch():
                return (ak.stock_margin_detail_sse(date=date_str) if is_sse
                        else ak.stock_margin_detail_szse(date=date_str))
            df = _safe_call("margin_table_" + date_str, _fetch, timeout=20)
            if len(_MARGIN_TABLE_MEMO) > 16:   # 防长会话无界增长（一天最多约9个日期键）
                _MARGIN_TABLE_MEMO.clear()
            _MARGIN_TABLE_MEMO[memo_key] = df
            return df

        def _get_balance(date_str):
            df = _get_margin_table(date_str)
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

        # 取最近2个有数据的交易日，间隔≥4天（算"近5日增幅"，非1日波动，防误触发）
        # v0.8.7.5 审计修复 A20：原取最近9个自然日，周一/长假后首日的前3个日期常是
        # 周末或节假日（融资数据为空）→ 连续3次 None 即 break → 信号每周约1/5交易日
        # 静默失效。改为只取工作日并扩到 12 个（覆盖长假），熔断放宽到 8 次连续空
        # （节假日空返回是秒级的，放宽只影响网络挂死的最坏情形，且有日缓存兜底）。
        _cal = [datetime.now() - timedelta(days=i) for i in range(0, 18)]
        dates = [d.strftime("%Y%m%d") for d in _cal if d.weekday() < 5][:12]
        latest = prev = None
        _none_streak = 0
        for d in dates:
            v = _get_balance(d)
            if v is None or v <= 0:
                _none_streak += 1
                if _none_streak >= 8:
                    break  # 连续8次无数据，放弃（防网络挂死阻塞；节假日空返回秒级，代价可忽略）
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
