"""N2 时间资格严格解析与同日未来检查回归测试（plan/fusion iteration7，DELIVERY_PLAN N2；R13 X3 反例）

锁死语义（R13 X3 四条转绿 + M1 披露收口；价格有效时点/抓取时点分离不回退）：
1. X3：非法日历（2026-02-30）/非法时刻（99:99:99）/垃圾尾巴（garbage）/
   当天未来时刻（23:59:59）→ 权重 None + 人话原因（不截取坏字符串救回日期）
2. 正例：合法日期、同一实际时刻不同偏移、新浪本地墙钟同日——可算不变
3. NAV 原时点保留偏移到资格门（account_nav/RequestAccountFacts 不先 strftime 丢时区）
4. finite：price/NAV 为 NaN/Inf → None（不混入资格）
5. 交易所午夜边界：aware 时刻越过上海午夜 → 与 shadow 分类一致（未来/异日拒绝）
6. 预取 dict 携带原 fetched_at（复用不打新时点）；无实时价 → price_source=kline_only
7. W2/M1 回归：旧价+当天 NAV 保持 None；同日合法组合可算；shadow 分类一致

隔离纪律：无持久化写入；零网络零 AI（供应商行离线注入）。
跑法：pytest tests/data_sources/test_n2_time_gate.py -q
"""
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import src.data.akshare_client as akshare_module
import src.data.portfolio as portfolio_module
from src.data.akshare_client import AKShareClient
from src.data.portfolio import _qualified_weight

TODAY = "2026-10-01"
QUANT = 100
PRICE = 10.0
NAV = 10000.0


def _w(price_at, nav_at=TODAY, *, price=PRICE, nav=NAV):
    return _qualified_weight(QUANT, price, price_at, nav, nav_at)


# ── 1. X3 四条：非法/未来 → None + 人话原因 ──────────────────────────

def test_x3_invalid_calendar_date_rejected():
    """2026-02-30（不存在的日期）→ None（旧实现截取前 10 位错锁 0.1）。"""
    w, reason = _w("2026-02-30", "2026-02-30")
    assert w is None, f"非法日历必须拒绝: {w} {reason}"
    assert reason


def test_x3_invalid_time_rejected():
    """2026-09-30T99:99:99（非法时刻）→ None。"""
    w, reason = _w("2026-09-30T99:99:99", "2026-09-30")
    assert w is None and reason


def test_x3_garbage_tail_rejected():
    """2026-09-30garbage（尾部垃圾）→ None（不截取救回）。"""
    w, reason = _w("2026-09-30garbage", "2026-09-30")
    assert w is None and reason


def test_x3_same_day_future_instant_rejected():
    """当天未来时刻（now 之后 23:59:59）配当天 NAV → None（先比实际时刻再取日）。"""
    now = datetime.now().astimezone()
    future_instant = (now + timedelta(hours=2)).isoformat(timespec="seconds")
    nav_day = now.date().isoformat()
    w, reason = _w(future_instant, nav_day)
    assert w is None, f"当天未来时刻必须拒绝: {w} {reason}"
    assert "未来" in reason


def test_missing_and_future_dates_rejected():
    """缺失/未来日期 → None（既有语义不回退）。"""
    future = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    for price_at in (future, None, "", "not-a-date"):
        w, reason = _w(price_at, TODAY)
        assert w is None and reason, f"{price_at!r} 必须拒绝"


# ── 2. 正例：合法输入仍可算 ──────────────────────────────────────────

def test_legal_day_precision_computes():
    w, reason = _w(TODAY, TODAY)
    assert w == pytest.approx(0.1) and "合格" in reason


def test_same_instant_different_offsets_computes():
    """同一实际时刻不同偏移（UTC 02:00 = 上海 10:00）→ 归一后同日可算。"""
    w, _ = _w(f"{TODAY}T02:00:00+00:00", TODAY)
    assert w == pytest.approx(0.1)


def test_sina_local_naive_time_same_day_computes():
    """新浪本地墙钟（naive 带时刻）显式按 Asia/Shanghai 解释——同日可算。"""
    w, _ = _w(f"{TODAY}T14:23:05", TODAY)
    assert w == pytest.approx(0.1)


# ── 3. NAV 原时点保留偏移 ────────────────────────────────────────────

def test_nav_aware_utc_offset_preserved():
    """NAV 定价时点为 aware-UTC（2026-09-30T18:00+00:00 = 上海 10-01 02:00）→
    资格门按上海日解释（10-01），不得先 strftime 丢偏移错成 09-30。"""
    # 经 _qualified_weight 直接验证：NAV isoformat 保留偏移 → 上海日 10-01
    w, _ = _w(f"{TODAY}T14:23:05",
              datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc).isoformat(timespec="seconds"))
    assert w == pytest.approx(0.1), "NAV aware-UTC 必须按上海日归一（10-01）后同日可算"


def test_account_nav_keeps_offset(tmp_path, monkeypatch):
    """account_nav 不再 strftime 丢偏移——返回 isoformat（含偏移信息）。"""
    from src.data.account_service import AccountService
    import src.data.account_service as account_module
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", ledger)
    svc = AccountService(ledger)
    svc.opening_import(opening_cash=10000.0, trade_date=TODAY)
    pm = portfolio_module.PortfolioManager(
        portfolio_path=str(tmp_path / "portfolio.yaml"),
        proposals_path=str(tmp_path / "proposals.json"))
    # 经 monkeypatch 给快照 NAV 时点（生产 NAV 事件未接线——验证传递语义）
    class _Snap:
        nav = 10000.0
        nav_priced_at = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)
    with patch_snap(_Snap):
        nav, nav_day = pm.account_nav()
    assert nav == 10000.0
    assert "T" in nav_day and ("+00:00" in nav_day), \
        f"account_nav 必须返回保留偏移的 isoformat: {nav_day!r}"
    # 资格门消费该形态 → 上海日 10-01
    w, _ = _w(f"{TODAY}T14:23:05", nav_day)
    assert w == pytest.approx(0.1)


class patch_snap:
    """临时替换 AccountService.snapshot 返回构造快照（传递语义验证用）。"""

    def __init__(self, snap):
        self._snap = snap

    def __enter__(self):
        from src.data.account_service import AccountService
        self._real = AccountService.snapshot
        AccountService.snapshot = lambda self_svc: self._snap
        return self

    def __exit__(self, *exc):
        from src.data.account_service import AccountService
        AccountService.snapshot = self._real
        return False


# ── 4. finite 检查 ───────────────────────────────────────────────────

def test_non_finite_price_or_nav_rejected():
    """price/NAV 为 NaN/Inf → None（不混入资格）。"""
    for kw in (dict(price=float("nan")), dict(price=float("inf")),
               dict(nav=float("nan")), dict(nav=float("inf"))):
        w, reason = _w(TODAY, TODAY, **kw)
        assert w is None and reason, f"{kw} 必须拒绝"


# ── 5. 交易所午夜边界与 shadow 分类一致 ──────────────────────────────

def test_exchange_midnight_boundary_consistent_with_shadow():
    """行情 aware 时刻越过上海午夜（次日凌晨）配前一交易日 NAV → 异日拒绝；
    同一输入给 shadow 分类亦为未来/不可比较（口径一致）。
    （动态构造未来时刻——硬编码日历日会随真实时钟跨午夜而失效。）"""
    now_utc = datetime.now(timezone.utc)
    # 上海时间已是次日凌晨的形态：UTC now+2h 在多数时段越过上海午夜——
    # 稳妥构造：取一个确定晚于 now 的 aware 时刻（未来 2 小时），NAV 用其「上海日-1」
    future = now_utc + timedelta(hours=2)
    sh = future.astimezone(timezone(timedelta(hours=8)))
    nav_day = (sh.date() - timedelta(days=1)).isoformat()
    w, reason = _w(future.isoformat(timespec="seconds"), nav_day)
    assert w is None, f"越过上海午夜的行情必须拒绝: {w} {reason}"
    from src.core.shadow_diff import _cutoff_in_future
    as_of = now_utc  # 捕获时点早于该行情时刻
    assert _cutoff_in_future(future.isoformat(timespec="seconds"), as_of), \
        "shadow 对同一未来时刻必须同样判未来（口径一致）"


# ── 6. 预取携带 fetched_at / kline_only 来源 ─────────────────────────

def test_prefetch_quote_carries_fetched_at(monkeypatch):
    """批量预取 dict 携带原 fetched_at——预取命中返回原对象（复用不打新时点）。"""
    import pandas as pd
    monkeypatch.setattr(AKShareClient, "_fetch_sina_batch", classmethod(lambda cls, codes: {}))
    em_df = pd.DataFrame([{"代码": "600519", "名称": "测试股", "最新价": 10.0,
                           "涨跌幅": 1.0, "成交量": 100, "今开": 9.9,
                           "最高": 10.2, "最低": 9.8, "昨收": 9.5}])
    monkeypatch.setattr(akshare_module.ak, "stock_zh_a_spot_em", lambda: em_df)
    out = AKShareClient.get_realtime_quotes(["600519"])
    assert "600519" in out
    q = out["600519"]
    assert q.get("fetched_at"), f"预取 dict 必须携带原抓取时刻: {q}"
    AKShareClient._QUOTE_PREFETCH["600519"] = (time.time(), q)
    try:
        hit = AKShareClient.get_realtime_quote("600519")
        assert hit is q, "预取命中返回原对象"
        assert hit.get("fetched_at") == q["fetched_at"]
    finally:
        AKShareClient._QUOTE_PREFETCH.pop("600519", None)


def test_calculate_prefers_quote_fetched_at(monkeypatch):
    """装配时优先消费 quote 携带的原 fetched_at（预取复用不再记成复用时刻）。"""
    monkeypatch.setattr(AKShareClient, "get_realtime_quote",
                        classmethod(lambda cls, code, retry=1: {
                            "stock_code": "600519", "stock_name": "测试股",
                            "price": 10.0, "volume": 100000, "change_pct": 1.0,
                            "source": "em_all", "effective_at": TODAY,
                            "fetched_at": "2026-10-01T09:00:00+08:00"}))
    days = [f"2026-09-{d:02d}" for d in range(1, 26)]
    import pandas as pd
    df = pd.DataFrame({"日期": days, "开盘": [10.0]*25, "最高": [10.5]*25,
                       "最低": [9.5]*25, "收盘": [10.0]*25, "成交量": [100000]*25,
                       "成交额": [1000000.0]*25, "preclose": [9.9]*25})
    monkeypatch.setattr(AKShareClient, "get_historical_kline",
                        classmethod(lambda cls, code, period="daily", adjust="qfq",
                                    start_date=None, end_date=None, retry=3: df))
    monkeypatch.setattr(AKShareClient, "_get_index_trend",
                        classmethod(lambda cls, *a, **k: None))
    import src.core.benzong.data_provider as bz_dp
    monkeypatch.setattr(bz_dp, "get_recent_announcements", lambda code: None)
    AKShareClient._stock_data_cache.clear()
    sd = AKShareClient._calculate_indicators_uncached("600519")
    assert sd.quote_fetched_at == "2026-10-01T09:00:00+08:00", \
        f"必须消费 quote 携带的原抓取时刻: {sd.quote_fetched_at}"
    assert sd.quote_as_of == TODAY
    assert sd.price_source == "em_all"


def test_no_quote_uses_kline_only_source(monkeypatch):
    """无实时价（纯 K 线回退）→ price_source=kline_only（说明已知来源日期、非实时）。"""
    monkeypatch.setattr(AKShareClient, "get_realtime_quote",
                        classmethod(lambda cls, code, retry=1: None))
    days = [f"2026-09-{d:02d}" for d in range(1, 26)]
    import pandas as pd
    df = pd.DataFrame({"日期": days, "开盘": [10.0]*25, "最高": [10.5]*25,
                       "最低": [9.5]*25, "收盘": [10.0]*25, "成交量": [100000]*25,
                       "成交额": [1000000.0]*25, "preclose": [9.9]*25})
    monkeypatch.setattr(AKShareClient, "get_historical_kline",
                        classmethod(lambda cls, code, period="daily", adjust="qfq",
                                    start_date=None, end_date=None, retry=3: df))
    monkeypatch.setattr(AKShareClient, "_get_index_trend",
                        classmethod(lambda cls, *a, **k: None))
    import src.core.benzong.data_provider as bz_dp
    monkeypatch.setattr(bz_dp, "get_recent_announcements", lambda code: None)
    AKShareClient._stock_data_cache.clear()
    sd = AKShareClient._calculate_indicators_uncached("600519")
    assert sd.price_source == "kline_only", \
        f"纯 K 线回退必须标注来源形态: {sd.price_source}"
    assert sd.quote_as_of == days[-1]
    assert sd.quote_fetched_at is not None


# ── 7. W2/M1 回归 ────────────────────────────────────────────────────

def test_w2_old_close_plus_current_nav_still_none():
    w, _ = _w("2026-09-29", TODAY)
    assert w is None, "W2 回归：旧日收盘配当天 NAV 必须 None"


def test_m1_120s_cache_still_frozen(monkeypatch):
    """120s 缓存随 StockData 冻结（M1 回归不回退）。"""
    monkeypatch.setattr(AKShareClient, "get_realtime_quote",
                        classmethod(lambda cls, code, retry=1: {
                            "stock_code": "600519", "stock_name": "测试股",
                            "price": 10.0, "volume": 100000, "change_pct": 1.0,
                            "source": "baostock", "effective_at": "2026-09-29"}))
    days = [f"2026-09-{d:02d}" for d in range(1, 26)]
    import pandas as pd
    df = pd.DataFrame({"日期": days, "开盘": [10.0]*25, "最高": [10.5]*25,
                       "最低": [9.5]*25, "收盘": [10.0]*25, "成交量": [100000]*25,
                       "成交额": [1000000.0]*25, "preclose": [9.9]*25})
    monkeypatch.setattr(AKShareClient, "get_historical_kline",
                        classmethod(lambda cls, code, period="daily", adjust="qfq",
                                    start_date=None, end_date=None, retry=3: df))
    monkeypatch.setattr(AKShareClient, "_get_index_trend",
                        classmethod(lambda cls, *a, **k: None))
    import src.core.benzong.data_provider as bz_dp
    monkeypatch.setattr(bz_dp, "get_recent_announcements", lambda code: None)
    AKShareClient._stock_data_cache.clear()
    sd1 = AKShareClient.calculate_indicators("600519")
    sd2 = AKShareClient.calculate_indicators("600519")
    assert sd1 is sd2, "120s 缓存必须返回同一冻结对象"
