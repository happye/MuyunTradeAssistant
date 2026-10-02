"""M1 行情来源时间与观察合同回归测试（plan/fusion iteration6，DELIVERY_PLAN M1；R12 W2 反例）

锁死语义（价格有效时点/抓取时点/缓存元数据分离——W2 根因收口）：
1. W2 主反例：Baostock 旧日收盘（2026-09-29）配当天 NAV（2026-10-01）→ 权重必须 None
   （旧实现 quote_as_of=抓取墙钟=当天 → 错误锁出权重 10%）
2. Baostock 提取保留原行 date → quote["effective_at"]（日精度）；新浪 fields[30]/[31]
   解析 → effective_at；EM/ETF 无源时点 → 不带（不靠墙钟补齐）
3. _calculate_indicators_uncached：quote_as_of ← 来源有效时点（**抓取墙钟永不做
   quote_as_of**）；StockData.quote_fetched_at=抓取墙钟、price_source=来源；
   无实时价用最近 K 线日期
4. 估值门：同日（交易所时区归一）可算；异日/未来/缺 → None；新浪本地墙钟按日期解释
5. shadow_v8：binding 增 quote_fetched_at（诊断记录、不入资格）；输出指纹排除抓取时点
   （纯抓取时间变化不虚增、行情真实变化留痕）；报告分桶通用化——当期协议 cur_*、
   旧版本（v6/v7）进 older_versions 只诊断不追认
6. 预取/120s 缓存随 dict/对象携带来源元数据

隔离纪律同 M0：持久化路径显式重定向（shadow/plans/research/ledger → tmp_path；
其余状态文件经 conftest 的临时 HOME 兜底）；零网络零 AI（供应商行离线注入）。
跑法：pytest tests/data_sources/test_m1_quote_time.py -q
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# 纪律（LRN-20260925-015）：patch 前先预导入被 patch 的真实模块
import src.core.shadow_diff as shadow_module
import src.data.account_service as account_module
import src.data.akshare_client as akshare_module
import src.data.portfolio as portfolio_module
from src.core.shadow_diff import SHADOW_DERIVATION_VERSION, build_shadow_report, capture_shadow
from src.data.akshare_client import AKShareClient
from src.data.models import StockData
from src.data.portfolio import PortfolioManager

BS_DAY = "2026-09-29"     # Baostock 最近已完成交易日（降级旧日收盘）
TODAY = "2026-10-01"

_BS_FIELDS = ['date', 'code', 'open', 'high', 'low', 'close', 'preclose', 'volume', 'amount']


def _bs_row(date=BS_DAY, close="10.0", preclose="9.5"):
    return [date, "sh.600519", "9.8", "10.2", "9.7", close, preclose, "100000", "1000000"]


# ── 1. 供应商行 → 来源有效时点 ───────────────────────────────────────

def test_baostock_row_keeps_source_date():
    """Baostock 日线行保留原行 date（日精度）——不再丢弃后由墙钟冒充。"""
    q = AKShareClient._baostock_quote_from_row(_BS_FIELDS, _bs_row(), "600519")
    assert q["source"] == "baostock"
    assert q["effective_at"] == BS_DAY, f"原行 date 必须保留为 effective_at: {q}"
    assert q["price"] == pytest.approx(10.0)
    # 脏行（缺 date 字段）→ 不带 effective_at（不编造）
    q2 = AKShareClient._baostock_quote_from_row(
        [f for f in _BS_FIELDS if f != "date"],
        [_bs_row()[i] for i, f in enumerate(_BS_FIELDS) if f != "date"], "600519")
    assert "effective_at" not in q2 or q2.get("effective_at") is None


def test_sina_batch_parses_source_time():
    """新浪 list 行解析 fields[30]/[31] → effective_at（交易所本地墙钟）；
    缺时间字段/坏值 → 不带 effective_at（明确未知）。"""
    base = "测试股,10.1,9.5,10.0,10.2,9.8,9.9,10.1,100000,1000000," + ",".join(["0"] * 20)
    # base 30 字段（下标 0..29）；fields[30]=日期、[31]=时间、共 32 字段
    line_ok = f'var hq_str_sh600519="{base},{TODAY},14:23:05"'
    out = AKShareClient._parse_sina_batch(line_ok)
    assert "600519" in out
    assert out["600519"].get("effective_at") == f"{TODAY}T14:23:05", \
        f"新浪源时间必须解析: {out['600519']}"
    assert out["600519"]["source"] == "sina_batch"
    # 缺时间字段（截断行 <32 字段）→ 解析失败整行跳过（既有守卫），不编造
    line_short = f'var hq_str_sh600519="{base}"'
    out2 = AKShareClient._parse_sina_batch(line_short)
    assert "600519" not in out2, "不足 32 字段的行必须整行拒绝（既有守卫不变）"


def test_em_quote_has_no_source_time(monkeypatch):
    """EM 全市场分支（真实分支）无源时点 → 产物不带 effective_at（不靠墙钟补齐）。"""
    import pandas as pd
    monkeypatch.setattr(AKShareClient, "_fetch_sina_batch",
                        classmethod(lambda cls, codes: {}))  # 跳过新浪层
    em_df = pd.DataFrame([{"代码": "600519", "名称": "测试股", "最新价": 10.0,
                           "涨跌幅": 1.0, "成交量": 100, "今开": 9.9,
                           "最高": 10.2, "最低": 9.8, "昨收": 9.5}])
    monkeypatch.setattr(akshare_module.ak, "stock_zh_a_spot_em", lambda: em_df)
    out = AKShareClient.get_realtime_quotes(["600519"])
    assert "600519" in out and out["600519"]["source"] == "em_all"
    assert "effective_at" not in out["600519"], \
        f"EM 无源时点必须缺省（不得编造）: {out['600519']}"


# ── 2. StockData 装配：来源时点 vs 抓取时点分离 ─────────────────────

def _kline_df(monkeypatch, last_day=BS_DAY):
    import pandas as pd
    days = [f"2026-09-{d:02d}" for d in range(1, 26)]
    days[-1] = last_day
    df = pd.DataFrame({
        "日期": days, "开盘": [10.0] * 25, "最高": [10.5] * 25,
        "最低": [9.5] * 25, "收盘": [10.0] * 25, "成交量": [100000] * 25,
        "成交额": [1000000.0] * 25, "preclose": [9.9] * 25,
    })
    monkeypatch.setattr(AKShareClient, "get_historical_kline",
                        classmethod(lambda cls, code, period="daily", adjust="qfq",
                                    start_date=None, end_date=None, retry=3: df))
    return df


def _no_network(monkeypatch):
    monkeypatch.setattr(AKShareClient, "_get_index_trend", classmethod(lambda cls, *a, **k: None))
    # 公告抓取（live 填充字段）与本卡无关——替身隔离（回测红线：不进 DataFeeder 路径）
    import src.core.benzong.data_provider as bz_dp
    monkeypatch.setattr(bz_dp, "get_recent_announcements", lambda code: None)


def test_quote_as_of_from_source_not_wall_clock(monkeypatch):
    """实时 quote 带 effective_at（Baostock 旧日）→ StockData.quote_as_of=旧日；
    quote_fetched_at=抓取墙钟；price_source=baostock（W2 主断言）。"""
    monkeypatch.setattr(AKShareClient, "get_realtime_quote",
                        classmethod(lambda cls, code, retry=1: {
                            "stock_code": "600519", "stock_name": "测试股",
                            "price": 10.0, "open": 9.8, "high": 10.2, "low": 9.7,
                            "close_yesterday": 9.5, "volume": 100000,
                            "change_pct": 5.26, "source": "baostock",
                            "effective_at": BS_DAY}))
    _kline_df(monkeypatch)
    _no_network(monkeypatch)
    AKShareClient._stock_data_cache.clear()
    before = datetime.now().astimezone()
    sd = AKShareClient._calculate_indicators_uncached("600519")
    assert sd is not None
    assert sd.quote_as_of == BS_DAY, \
        f"行情时点必须来自来源（旧日收盘），不得是抓取墙钟: {sd.quote_as_of}"
    assert sd.quote_fetched_at is not None, "抓取时点必须显式记录（诊断）"
    fetched = datetime.fromisoformat(sd.quote_fetched_at)
    assert fetched >= before.replace(microsecond=0) - timedelta(seconds=5)
    assert sd.price_source == "baostock"


def test_quote_as_of_kline_only_uses_kline_date(monkeypatch):
    """无实时价 → quote_as_of=最近 K 线日期（既有语义）；抓取时点仍记录。"""
    monkeypatch.setattr(AKShareClient, "get_realtime_quote",
                        classmethod(lambda cls, code, retry=1: None))
    _kline_df(monkeypatch, last_day=BS_DAY)
    _no_network(monkeypatch)
    AKShareClient._stock_data_cache.clear()
    sd = AKShareClient._calculate_indicators_uncached("600519")
    assert sd.quote_as_of == BS_DAY
    assert sd.quote_fetched_at is not None
    assert sd.price_source == "kline_only", "N2：纯 K 线回退显式标注来源形态（已知日期、非实时）"


def test_quote_as_of_unknown_source_time_stays_none(monkeypatch):
    """实时 quote 无源时点（EM 形态）→ quote_as_of=None（不靠墙钟补齐）。"""
    monkeypatch.setattr(AKShareClient, "get_realtime_quote",
                        classmethod(lambda cls, code, retry=1: {
                            "stock_code": "600519", "stock_name": "测试股",
                            "price": 10.0, "volume": 100000, "change_pct": 1.0,
                            "source": "em_all"}))
    _kline_df(monkeypatch)
    _no_network(monkeypatch)
    AKShareClient._stock_data_cache.clear()
    sd = AKShareClient._calculate_indicators_uncached("600519")
    assert sd.quote_as_of is None, f"无源时点必须明确未知: {sd.quote_as_of}"
    assert sd.quote_fetched_at is not None
    assert sd.price_source == "em_all"


def test_prefetch_preserves_source_metadata(monkeypatch):
    """预取命中返回原 dict（effective_at/来源随行保留）——复用不打新时点。"""
    stub = {"stock_code": "600519", "price": 10.0, "source": "baostock",
            "effective_at": BS_DAY}
    AKShareClient._QUOTE_PREFETCH["600519"] = (time.time(), stub)
    try:
        q = AKShareClient.get_realtime_quote("600519")
        assert q is stub, "预取命中必须返回原对象（元数据不重打）"
    finally:
        AKShareClient._QUOTE_PREFETCH.pop("600519", None)


# ── 3. W2 主反例：旧日收盘 × 当天 NAV → 权重 None ───────────────────

def test_w2_old_close_plus_current_nav_weight_unknown(tmp_path, monkeypatch):
    """完整链：Baostock 旧日价（effective 09-29）→ quote_as_of=09-29 →
    与 10-01 NAV 不同日 → 权重 None（旧实现拿抓取墙钟错锁 10%）。"""
    monkeypatch.setattr(AKShareClient, "get_realtime_quote",
                        classmethod(lambda cls, code, retry=1: {
                            "stock_code": "600519", "stock_name": "测试股",
                            "price": 10.0, "volume": 100000, "change_pct": 5.26,
                            "source": "baostock", "effective_at": BS_DAY}))
    _kline_df(monkeypatch)
    _no_network(monkeypatch)
    AKShareClient._stock_data_cache.clear()
    sd = AKShareClient._calculate_indicators_uncached("600519")
    assert sd.quote_as_of == BS_DAY
    w, reason = portfolio_module._qualified_weight(100, sd.price, sd.quote_as_of,
                                                   10000.0, TODAY)
    assert w is None, f"旧日收盘配当天 NAV 必须拒绝精确权重（W2 主反例）: {w}"
    assert "不一致" in reason or "同日" in reason


def test_same_day_legitimate_combo_computes():
    """新浪本地墙钟（naive 带时刻，按交易所日期解释）与 NAV 同日 → 可算。"""
    w, _ = portfolio_module._qualified_weight(100, 10.0, f"{TODAY}T14:23:05",
                                              10000.0, TODAY)
    assert w == pytest.approx(0.1), f"同日合法组合必须可算: {w}"


def test_timezone_same_instant_qualifies():
    """同一实际时刻的不同偏移（UTC 02:00 = 上海 10:00）→ 按交易所时区归一后同日可算。"""
    w, _ = portfolio_module._qualified_weight(100, 10.0, f"{TODAY}T02:00:00+00:00",
                                              10000.0, TODAY)
    assert w == pytest.approx(0.1), f"时区归一后同日必须可算: {w}"


def test_future_and_missing_source_time_rejected():
    """未来时点/缺时点拒绝精确权重（不靠墙钟补齐；日精度与时刻形态都覆盖）。"""
    future = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    for price_at in (future, None, ""):
        w, reason = portfolio_module._qualified_weight(100, 10.0, price_at, 10000.0, TODAY)
        assert w is None, f"未来/缺时点必须拒绝: {price_at!r} -> {w}"
        assert reason


# ── 4. shadow_v8 观察合同 ────────────────────────────────────────────

def _isolate_shadow(tmp_path, monkeypatch):
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "no-ledger.jsonl")
    import src.data.horizon_plans as hp
    import src.data.research_store as rs
    monkeypatch.setattr(hp, "PLANS_FILE", tmp_path / "plans.json")
    monkeypatch.setattr(rs, "RESEARCH_DIR", tmp_path / "research")
    return tmp_path


def _dr(action="SELL"):
    from types import SimpleNamespace
    return SimpleNamespace(decision=SimpleNamespace(value=action), score=0.8,
                           stock=SimpleNamespace(stock_code="600519", stock_name="测试股"),
                           warnings=[])


def _sd(action="CLOSE_ALL"):
    from src.data.models import PositionAction, SignalType, StrategyDecision, StrategyState, TradeLifecycle
    return StrategyDecision(decision=SignalType("SELL" if action != "HOLD_POSITION" else "HOLD"),
                            position_action=PositionAction(action), position_ratio=0.0,
                            lifecycle_before=TradeLifecycle.HOLD,
                            lifecycle_after=TradeLifecycle.HOLD,
                            strategy_reasons=["理由"], new_state=StrategyState())


def _eval():
    from types import SimpleNamespace
    return SimpleNamespace(effective_action=SimpleNamespace(value="HOLD"), blocked=False)


def _seed_accepted_mid(tmp_path):
    from datetime import timezone
    from src.data.horizon_plans import HorizonPlanStore
    from src.data.research_store import AssessmentStore
    from src.core.decision_policy import POLICY_ID_MID, HorizonPlan
    from src.core.research import ThesisAssessment
    from src.core.research_service import ASSERTION_METHOD_VERSION
    from src.core.decision_contract import ThesisStatus
    plans = HorizonPlanStore(tmp_path / "plans.json")
    asm_store = AssessmentStore(tmp_path / "research")
    plan = HorizonPlan(plan_id="p2_600519_m1", security_id="600519", horizon="MID",
                       policy_id=POLICY_ID_MID, intent="M1 测试",
                       policy_version="r4.research_service_v1")
    asm = ThesisAssessment(thesis_id="thesis_600519_MID", security_id="600519",
                           horizon="MID", snapshot_id="snap-m1",
                           status=ThesisStatus.VALID,
                           method_version=ASSERTION_METHOD_VERSION,
                           evaluated_as_of=datetime.now(timezone.utc) - timedelta(minutes=1))
    plan.assessment_id = asm_store.save(asm)
    plan.snapshot_id = asm.snapshot_id
    plans.save(plan)
    ok, msg = plans.accept("600519", "MID")
    assert ok, msg
    return plans, asm_store


def _capture(tmp_path, plans, asm_store, *, quote_as_of=BS_DAY, quote_fetched_at=""):
    from src.core.analysis_service import build_decision_packet
    packet = build_decision_packet(_dr(), _sd(), _eval(), confirmed_ratio=None,
                                   position_state="HELD", source="test")
    return capture_shadow(_dr(), _sd(), _eval(),
                          {"stock_code": "600519", "current_ratio": 0.0},
                          packet=packet, source="test",
                          config={"fusion": {"mode": "capture_only"}},
                          store_path=tmp_path / "shadow.jsonl",
                          plans_store=plans, assessment_store=asm_store,
                          account_version="v_m1", quote_as_of=quote_as_of,
                          quote_fetched_at=quote_fetched_at)


def test_shadow_v8_records_fetched_at_diagnostic(tmp_path, monkeypatch):
    """v8：binding 记 quote_fetched_at（采集时点，诊断）——缺它不降资格
    （资格门是有效时点 quote_cutoff，不是抓取墙钟）。"""
    _isolate_shadow(tmp_path, monkeypatch)
    plans, asm_store = _seed_accepted_mid(tmp_path)
    rec = _capture(tmp_path, plans, asm_store,
                   quote_fetched_at="2026-10-01T10:00:00+08:00")
    assert rec.derivation_version == "shadow_v11"
    mb = rec.mid_binding
    assert mb["quote_cutoff"] == BS_DAY
    assert mb["quote_fetched_at"] == "2026-10-01T10:00:00+08:00"
    # 抓取时点缺失 → 资格不受影响（有效时点在即可）
    rec2 = _capture(tmp_path, plans, asm_store, quote_fetched_at="")
    assert rec2.mid_binding["quote_fetched_at"] is None
    assert rec2.mid_binding["eligible"] == rec.mid_binding["eligible"]


def test_shadow_v8_dedup_ignores_fetch_time_only(tmp_path, monkeypatch):
    """纯抓取时间变化（同输入同输出语义）→ 去重折叠不虚增；
    行情真实变化（有效时点变化）→ 留痕。"""
    _isolate_shadow(tmp_path, monkeypatch)
    plans, asm_store = _seed_accepted_mid(tmp_path)
    rec1 = _capture(tmp_path, plans, asm_store, quote_fetched_at="2026-10-01T10:00:00+08:00")
    assert rec1.append_status == "saved"
    # 仅抓取时刻不同 + 捕获分钟推进 → 语义同 → 折叠
    # （datetime 类型不可变——以子类替身替换 shadow 模块的 datetime 引用，推进 2 分钟
    #   走「同日跨分钟」去重规则）
    from unittest.mock import patch as _patch
    shift = timedelta(minutes=2)
    base = datetime.now().astimezone()
    # 午夜窗口安全：+2min 跨日则改推 -2min（仍保证不同分钟、同日）
    if (base + shift).date() != base.date():
        shift = timedelta(minutes=-2)
    later = (base + shift).isoformat(timespec="seconds")

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(later)

    with _patch.object(shadow_module, "datetime", _FakeDT):
        rec2 = _capture(tmp_path, plans, asm_store,
                        quote_fetched_at="2026-10-01T10:05:00+08:00")
    assert rec2.append_status == "deduped", \
        f"纯抓取时间变化不得虚增观察: {rec2.append_status}"
    # 行情真实变化（有效时点 09-29 → 10-01）→ 输出语义变 → 留痕
    rec3 = _capture(tmp_path, plans, asm_store, quote_as_of=TODAY,
                    quote_fetched_at="2026-10-01T10:06:00+08:00")
    assert rec3.append_status == "saved", "行情真实变化必须留痕"


def test_shadow_v8_report_buckets_old_protocols(tmp_path, monkeypatch):
    """报告分桶通用化：v8 当期计分母；v7/v6 及更早进 older_versions 只诊断不追认。"""
    _isolate_shadow(tmp_path, monkeypatch)
    plans, asm_store = _seed_accepted_mid(tmp_path)
    store = tmp_path / "shadow.jsonl"
    old_v7 = {"security_id": "600519",
              "as_of": (datetime.now().astimezone() - timedelta(hours=1)).isoformat(timespec="seconds"),
              "legacy_action": "CLOSE_ALL", "legacy_desired": "EXIT",
              "fusion_mid_action": "EXIT", "fusion_long_action": "REVIEW",
              "derivation_version": "shadow_v7", "observation_kind": "effective",
              "mid_effective": True, "long_effective": False,
              "input_fingerprint": "v7_old", "output_fingerprint": "v7_old_out",
              "delta_reasons": ["agree"]}
    store.write_text(json.dumps(old_v7, ensure_ascii=False) + "\n", encoding="utf-8")
    rec = _capture(tmp_path, plans, asm_store)
    assert rec.derivation_version == "shadow_v11"
    assert rec.append_status == "saved", "新旧协议记录并存（不被去重吞掉）"
    report = build_shadow_report(store_path=store, days=7)
    assert report["protocol_version"] == "shadow_v11"
    assert report["legacy_records"] == 1
    assert report["cur_mid_effective"] == 1, "当期分母只数 shadow_v8"
    v7_bucket = report["older_versions"].get("shadow_v7")
    assert v7_bucket and v7_bucket["mid_effective"] == 1, \
        f"v7 进 older_versions 按原样计数: {report['older_versions']}"
    from src.core.shadow_diff import render_shadow_report
    text = render_shadow_report(report)
    assert "只诊断不追认" in text
