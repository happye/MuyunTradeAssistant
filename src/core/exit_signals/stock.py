"""个股层高位止盈信号（跳法A 阶段2 / v0.8.6.4 + ISS-117 S0 权限收口 v0.8.29）

笨总教学：个股见顶三信号 = 换手率>40% + 缩量加速上涨 + 实控人减持。
换手率子信号因无数据源未实现（2026-08-24 经用户拍板移除死代码）。

v0.8.29（ISS-117 S0/S1，架构师裁决 plan/fusion/signal_authority_review/）：
- 「检测到现象」与「获准改变动作」分离——本模块子信号除三倍定律（既有周期策略纪律，
  bar 衍生 point-in-time）外全部降级为 research 资格（SignalFinding.action_scope），
  不再单独触发 CLOSE_ALL；消费方按 action_scope 分流（orchestrator）。
- E1：减持标题只是线索——否定/取消/澄清/未实施语义、主体证券匹配、原件与时点均未
  核验，一律 research + 待核验说明；不靠否定词黑名单恢复硬权限。
- E2：股东户数按明确日期列倒序取最新一期（原 iloc[0] 在 akshare 升序数据上取到
  最旧一期），去重/无效日期剔除。
- E3：缩量加速连续性证据（volume_series）缺失时如实标 UNKNOWN，不再放宽为单日触发。
- 缓存键 v2：旧缓存（纯字符串结果）不复活为信号。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.data.models import SignalFinding

logger = logging.getLogger(__name__)

# 研究类发现统一绑定本裁决版本（strategy_binding 供上游追溯）
_RESEARCH_BINDING = "iss117-s0/ruling-2026-10-09"
_HARD_BINDING = "jiaoxue6-8/mode-rule（既有周期策略纪律）"


def _holder_cache_dir() -> Path:
    d = Path.home() / ".muyun" / "exit_signal_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _daily_cache_get(code: str, signal: str):
    """读日缓存 v2（key=code_date_signal_v2）。命中返回缓存 dict，未命中返回 _MISS。

    v2 记录必须是含 signal_id 的 SignalFinding dict——旧版纯字符串缓存不复活
    （ISS-117 S1：不让旧缓存恢复旧权限语义）。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    f = _holder_cache_dir() / f"{code}_{today}_{signal}_v2.json"
    if f.exists():
        try:
            v = json.loads(f.read_text(encoding="utf-8"))
            if v is None:
                return None   # 缓存的「无信号」值（与 _MISS 区分）
            if isinstance(v, dict) and v.get("signal_id"):
                return v
        except Exception:
            return _MISS
    return _MISS


def _daily_cache_set(code: str, signal: str, finding: Optional[SignalFinding]) -> None:
    global _cleanup_done_day
    today = datetime.now().strftime("%Y-%m-%d")
    d = _holder_cache_dir()
    f = d / f"{code}_{today}_{signal}_v2.json"
    try:
        payload = "null" if finding is None else json.dumps(
            json.loads(finding.model_dump_json()), ensure_ascii=False)
        f.write_text(payload, encoding="utf-8")
    except Exception:
        pass
    # ISS-091：日键文件跨日本就失效，顺带清理 30 天前的旧文件防无限累积
    if _cleanup_done_day == today:
        return
    try:
        import time as _time
        _now = _time.time()
        for old in d.glob("*.json"):
            try:
                if _now - old.stat().st_mtime > 30 * 86400:
                    old.unlink()
            except OSError:
                continue
        _cleanup_done_day = today
    except Exception:
        pass


_MISS = object()  # 哨兵：缓存未命中（区别于缓存的 None 值）

_cleanup_done_day: str = ""

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
# E1：否定/澄清/未实施语义——仅用于区分线索的可信度说明（不作为恢复硬权限的黑名单）
_NEGATIVE_HINTS = ("终止", "取消", "撤销", "未减持", "不减持", "澄清", "更正")


def check_stock_top_signal(stock_data, code: str, *,
                           announcements: Optional[list] = None,
                           live: bool = False,
                           mode: Optional[str] = None) -> list:
    """检查个股层大顶信号，返回全部 SignalFinding（ISS-117 S0 权限收口）。

    资格划分（架构师裁决 plan/fusion/signal_authority_review/）：
    - 三倍定律+破5日线：既有周期策略纪律（bar 衍生），保持 exit 资格，气宗(mode=qizong)跳过
    - 缩量加速 / 股东户数激增 / 融资余额激增：research（经验信号，不单独构成清仓依据）
    - 减持标题线索：research + 待核验（否定/主体/原件/时点均未核实，E1）

    Returns:
        list[SignalFinding]，可能为空
    """
    findings = []

    def _safe(fn, *a, **k):
        """Q6：子信号隔离——单个子信号异常只损失该子信号（显式诊断条目），
        不吞掉已合格/后续独立保护（如三倍定律 exit 资格）。"""
        try:
            return fn(*a, **k)
        except Exception as e:
            logger.warning(f"子信号 {getattr(fn, '__name__', fn)} 异常（已隔离）: {e}")
            return SignalFinding(
                signal_id=f"diag.stock.{getattr(fn, '__name__', 'subsignal')}",
                source="子信号隔离诊断", data_quality="UNKNOWN", action_scope="none",
                detail=f"个股子信号检查失败（已隔离）: {type(e).__name__}: {e}",
                reason="Q6 逐子信号隔离：失败不吞掉其他独立保护")

    f = _safe(_check_shrink_acceleration, stock_data, code=code)
    if f:
        findings.append(f)

    if announcements:
        # Q6：规范化旧格式输入——字符串标题（旧缓存/手工构造）包装为 dict，防 .get 崩
        norm = []
        for a in announcements:
            if isinstance(a, str):
                norm.append({"title": a, "date": "", "content": "", "source": "legacy-str"})
            elif isinstance(a, dict):
                norm.append(a)
        if norm:
            _res = _safe(_check_holder_reduction, norm, code=code)
            findings.extend(_res if isinstance(_res, list) else ([_res] if _res else []))

    if mode != "qizong":
        f = _safe(_check_triple_up_rule, stock_data, code=code)
        if f:
            findings.append(f)

    if live and code:
        f = _safe(_check_holder_count_surge, code)
        if f:
            findings.append(f)
        f = _safe(_check_margin_surge, code)
        if f:
            findings.append(f)

    return findings


def _check_shrink_acceleration(stock_data, code: str = "") -> Optional[SignalFinding]:
    """缩量加速上涨：成交量萎缩但价格大涨，典型赶顶背离（E3：连续性证据缺失=UNKNOWN）。"""
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
        base_reason = "经验研究信号（ISS-117 A12 降级）：量价背离值得复核，不单独构成清仓依据"
        # 连续性确认：昨日(vs[-2])相对其之前可得日的均量也要 <0.7。序列缺失/过短 → E3：
        # 不再把两日条件放宽成单日触发——如实标 UNKNOWN 只作低资格研究线索。
        vs = getattr(stock_data, "volume_series", None)
        if vs and len(vs) >= 5:
            prev_vol = float(vs[-2])
            base = [float(x) for x in list(vs[:-2]) if x]
            if prev_vol > 0 and base and sum(base) > 0:
                prev_ratio = prev_vol / (sum(base) / len(base))
                if prev_ratio >= SHRINK_VOLUME_RATIO:
                    return None  # 昨日未缩量，单日缩量不构成"连续缩量加速"
                return SignalFinding(
                    signal_id="research.stock.shrink_accel",
                    source="StockData.volume/avg_volume_5/volume_series",
                    securities=[code] if code else [], data_quality="OK",
                    action_scope="research", verified=False,
                    strategy_binding=_RESEARCH_BINDING,
                    detail=f"个股:缩量加速(量比{ratio:.2f},涨{change_pct:.1f}%,连续2日缩量)",
                    reason=base_reason)
        return SignalFinding(
            signal_id="research.stock.shrink_accel",
            source="StockData.volume/avg_volume_5",
            securities=[code] if code else [], data_quality="UNKNOWN",
            action_scope="research", verified=False,
            strategy_binding=_RESEARCH_BINDING,
            detail=f"个股:单日缩量大涨(量比{ratio:.2f},涨{change_pct:.1f}%,连续性证据缺失)",
            reason="volume_series 缺失/过短，两日连续缩量无法确认（E3：缺证据不升格）——低资格研究线索")
    except Exception as e:
        logger.debug(f"缩量加速判定异常: {e}")
    return None


def _check_holder_reduction(announcements: list, code: str = "") -> list:
    """扫公告/新闻标题：命中（减持词+主体词）的条目 → research 待核验线索（E1）。

    标题只是线索：否定/取消/澄清/未实施语义、主体证券匹配、原件、来源时点均未核实，
    故一律 research + 待核验说明（否定语义在 reason 里如实标注），不作为强制清仓依据。
    """
    findings = []
    for ann in announcements or []:
        title = (ann.get("title") if isinstance(ann, dict) else str(ann)) or ""
        if not title:
            continue
        if not (any(k in title for k in _REDUCE_KEYWORDS) and any(k in title for k in _HOLDER_KEYWORDS)):
            continue
        negative_hit = any(k in title for k in _NEGATIVE_HINTS)
        findings.append(SignalFinding(
            signal_id="research.stock.reduction_title",
            source="recent_announcements(东财个股新闻主源/巨潮备用——混有市场级新闻，未经原件核验)",
            # securities 留空：主体证券匹配未核验，不得把别家减持挂到本股（guard P2）
            securities=[], as_of=str(ann.get("date") or "")[:10] or None,
            data_quality="UNKNOWN", action_scope="research", verified=False,
            strategy_binding=_RESEARCH_BINDING,
            detail=f"个股:标题命中减持线索({title[:30]})",
            reason=("标题含否定/澄清/未实施语义——按待核验线索处理，需核对原件确认事件状态"
                    if negative_hit else
                    "标题仅为线索：减持原件、主体证券匹配、事件状态（计划/实施/终止）与来源时点均未核验")))
    return findings


def _check_triple_up_rule(stock_data, code: str = "") -> Optional[SignalFinding]:
    """三倍定律+5日线破位（笨总教学六/八）——既有周期策略纪律，保持 exit 资格。

    "一口气涨三倍以上且持续拉升 -> 清五日线减仓或清仓"。
    判定：price / low_60d >= 3.0 且 price < MA5（高位破5日线即卖，不等MA20确认）。
    """
    if stock_data is None:
        return None
    try:
        price = getattr(stock_data, "price", None)
        ma5 = getattr(stock_data, "ma5", None)
        low_60d = getattr(stock_data, "low_60d", None)
        # Q5：价格/均线/低点必须有限正值；NaN/Inf/零负任一不符即无资格（不假触发，
        # 也不抹掉其他独立退出）
        vals = [price, ma5, low_60d]
        if any(v is None or not isinstance(v, (int, float)) or not (v > 0) or v != v
               or v in (float("inf"), float("-inf")) for v in vals):
            return None
        # Q5：as_of 必须来自来源数据（quote_as_of，M1）；缺失 → data_quality=UNKNOWN
        # → 消费边界资格门将拒绝其硬权限（陈旧行情不得获硬退出）
        raw_as_of = getattr(stock_data, "quote_as_of", None)
        as_of = str(raw_as_of)[:10] if raw_as_of else None
        multiple = price / low_60d
        if multiple >= TRIPLE_UP_MULTIPLE and price < ma5:
            return SignalFinding(
                signal_id="exit.stock.triple_up",
                source="StockData.price/ma5/low_60d（bar 衍生，point-in-time）",
                securities=[code] if code else [], as_of=as_of,
                data_quality="OK" if as_of else "UNKNOWN",
                action_scope="exit", verified=True,
                strategy_binding=_HARD_BINDING,
                detail=f"个股:三倍定律+破5日线(近60日涨{multiple:.1f}倍,price<MA5)",
                reason="既有周期策略纪律（教学六/八，剑宗/默认档）：预先约定的退出条件，非新获得的事实核验")
    except Exception as e:
        logger.debug(f"三倍定律判定异常: {e}")
    return None


def _check_holder_count_surge(code: str) -> Optional[SignalFinding]:
    """股东户数激增（笨总教学八）：近两期增幅>50% → research 线索（E2 已修排序）。

    E2（S1）：akshare stock_gdhs_detail_em 按"统计截止日"升序返回——原 iloc[0] 取到
    最旧一期。现按明确日期列倒序 + 去重取最新一期；无效日期行剔除。
    数据本身仍是研究线索（人数变化≠主力派发已证实），不获强制清仓资格。
    """
    cached = _daily_cache_get(code, "holder_count")
    if cached is not _MISS:
        try:
            return SignalFinding(**cached) if cached else None
        except Exception:
            pass
    finding = None
    try:
        import pandas as pd
        from src.core.benzong.data_provider import _safe_call

        def _fetch():
            import akshare as ak
            return ak.stock_zh_a_gdhs_detail_em(symbol=code)

        df = _safe_call("stock_zh_a_gdhs_detail_em", _fetch, timeout=15)
        if df is not None and len(df) >= 1:
            cur_col = next((c for c in df.columns if "本次" in c), None)
            prev_col = next((c for c in df.columns if "上次" in c), None)
            date_col = next((c for c in df.columns
                             if ("日期" in c or "截止" in c or "时间" in c)), None)
            if cur_col and prev_col:
                work = df.copy()
                as_of = None
                if date_col:
                    work["_d"] = pd.to_datetime(work[date_col], errors="coerce")
                    work = work.dropna(subset=["_d"])
                    # Q7：未来统计日（数据异常）无资格——不得标 OK
                    work = work[work["_d"] <= pd.Timestamp.now()]
                    if work.empty:
                        finding = None
                        _daily_cache_set(code, "holder_count", None)
                        return finding
                    work = work.sort_values("_d", ascending=False)
                    work = work.drop_duplicates(subset=["_d"], keep="first")
                    as_of = str(work.iloc[0]["_d"].date())
                latest_cur = float(work.iloc[0][cur_col])
                latest_prev = float(work.iloc[0][prev_col])
                if latest_prev > 0:
                    surge = (latest_cur - latest_prev) / latest_prev * 100
                    if surge > HOLDER_COUNT_SURGE_PCT:
                        finding = SignalFinding(
                            signal_id="research.stock.holder_count_surge",
                            source="akshare stock_zh_a_gdhs_detail_em（按统计截止日倒序取最新一期）",
                            securities=[code], as_of=as_of, data_quality="OK" if as_of else "UNKNOWN",
                            action_scope="research", verified=False,
                            strategy_binding=_RESEARCH_BINDING,
                            detail=(f"个股:股东户数激增({latest_prev:.0f}->{latest_cur:.0f},"
                                    f"+{surge:.0f}%{('，截至' + as_of) if as_of else ''})"),
                            reason="两期户数变化是研究线索（ISS-117 A12：户数增加≠主力派发已证实），"
                                   "不单独构成清仓依据")
    except Exception as e:
        logger.debug(f"股东户数判定异常({code}): {e}")
    _daily_cache_set(code, "holder_count", finding)
    return finding


def _check_margin_surge(code: str) -> Optional[SignalFinding]:
    """融资余额激增（笨总教学八）：近5日增幅>10% → research 线索。

    窗口语据（S1 如实披露）：按自然日取样、两观测间隔≥4个自然日近似"近5日"——
    实际交易日跨度随节假日浮动，findings 里如实带出两端日期。
    """
    cached = _daily_cache_get(code, "margin")
    if cached is not _MISS:
        try:
            return SignalFinding(**cached) if cached else None
        except Exception:
            pass
    finding = None
    try:
        import akshare as ak
        from datetime import datetime, timedelta
        from src.core.benzong.data_provider import _safe_call
        is_sse = code.startswith("6")
        exchange = "sse" if is_sse else "szse"

        def _get_margin_table(date_str):
            memo_key = (exchange, date_str)
            if memo_key in _MARGIN_TABLE_MEMO:
                return _MARGIN_TABLE_MEMO[memo_key]

            def _fetch():
                return (ak.stock_margin_detail_sse(date=date_str) if is_sse
                        else ak.stock_margin_detail_szse(date=date_str))
            df = _safe_call("margin_table_" + date_str, _fetch, timeout=20)
            if len(_MARGIN_TABLE_MEMO) > 16:
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

        _cal = [datetime.now() - timedelta(days=i) for i in range(0, 18)]
        dates = [d.strftime("%Y%m%d") for d in _cal if d.weekday() < 5][:12]
        latest = prev = None
        _none_streak = 0
        for d in dates:
            v = _get_balance(d)
            if v is None or v <= 0:
                _none_streak += 1
                if _none_streak >= 8:
                    break
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
        if not latest or not prev or prev[1] <= 0:
            pass
        else:
            surge = (latest[1] - prev[1]) / prev[1] * 100
            if surge > MARGIN_SURGE_PCT:
                finding = SignalFinding(
                    signal_id="research.stock.margin_surge",
                    source="akshare 融资余额明细（自然日取样，两观测间隔≥4自然日）",
                    securities=[code], as_of=f"{latest[0][:4]}-{latest[0][4:6]}-{latest[0][6:]}", data_quality="UNKNOWN",
                    action_scope="research", verified=False,
                    strategy_binding=_RESEARCH_BINDING,
                    detail=(f"个股:融资余额激增({prev[0]}->{latest[0]},+{surge:.1f}%)"),
                    reason="杠杆余额变化是研究线索（ISS-117 A12：杠杆增多≠踩踏已发生）；"
                           "窗口为自然日近似，实际交易日跨度随节假日浮动，不单独构成清仓依据")
    except Exception as e:
        logger.debug(f"融资余额判定异常({code}): {e}")
    _daily_cache_set(code, "margin", finding)
    return finding
