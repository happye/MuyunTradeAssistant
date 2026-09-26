"""baostock 季频财务接线回归测试（plan/fusion，src/data/financial_data.py）

锁死语义：
1. 五接口全字段映射：metric/value/unit/period_kind=cumulative/period_end/published_at
2. 单位实测锚定：货币=元、比例=倍（小数比值非百分数）、周转=次、天数=天
3. 缺失值（空串 ""）→ None 绝不补 0；畸形值 → None
4. 无公布日（pubDate 空）→ 该期整接口不产证据（严格 PIT 判据缺失不造假，G15）
5. 单接口错误/超时 → 告警 + 该接口字段缺失，不拖垮其余接口（三层超时纪律）
6. evidence 集成：financial_record 可直接消费；available_at=pubDate 23:59+08:00；
   严格快照按 as_of 拒收未来证据

纯 mock 测试（monkeypatch baostock 模块属性），零网络。
跑法：pytest tests/core/test_financial_data.py -q
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data import financial_data as fd
from src.data.research_snapshot import EvidenceSnapshot, financial_record

_MOUTAI_2023Q4 = {
    "query_profit_data": {"code": "sh.600519", "pubDate": "2024-04-03", "statDate": "2023-12-31",
                          "roeAvg": "0.361755", "npMargin": "0.524880", "gpMargin": "0.919649",
                          "netProfit": "77521476277.800000", "epsTTM": "59.492280",
                          "MBRevenue": "147218996281.040000", "totalShare": "1256197800.00",
                          "liqaShare": "1256197800.00"},
    "query_balance_data": {"pubDate": "2024-04-03", "statDate": "2023-12-31",
                           "currentRatio": "4.623892", "quickRatio": "3.670351",
                           "cashRatio": "1.426576", "YOYLiability": "-0.010483",
                           "liabilityToAsset": "0.179843", "assetToEquity": "1.219279"},
    "query_cash_flow_data": {"pubDate": "2024-04-03", "statDate": "2023-12-31",
                             "CAToAsset": "0.825716", "NCAToAsset": "0.174284",
                             "tangibleAssetToAsset": "0.741007", "ebitToInterest": "",
                             "CFOToOR": "0.450888", "CFOToNP": "0.859030", "CFOToGr": "0.442303"},
    "query_operation_data": {"pubDate": "2024-04-03", "statDate": "2023-12-31",
                             "NRTurnRatio": "1471.805290", "NRTurnDays": "0.244598",
                             "INVTurnRatio": "0.278380", "INVTurnDays": "1293.103448",
                             "CATurnRatio": "0.681602", "AssetTurnRatio": "0.571317"},
    "query_growth_data": {"pubDate": "2024-04-03", "statDate": "2023-12-31",
                          "YOYEquity": "0.092103", "YOYAsset": "0.071508", "YOYNI": "0.185778",
                          "YOYEPSBasic": "0.191468", "YOYPNI": "0.191599"},
}


class _FakeRS:
    def __init__(self, row, error_code="0"):
        self.fields = ["code", "pubDate", "statDate"] + [k for k in row if k not in
                                                         ("code", "pubDate", "statDate")] if row else []
        self._row = list(self.fields and [row.get(f, "") for f in self.fields]) if row else []
        self.error_code = error_code
        self.error_msg = "" if error_code == "0" else "fake error"
        self._used = False

    def next(self):
        if self._row and not self._used:
            self._used = True
            return True
        return False

    def get_row_data(self):
        return list(self._row)


def _install_fakes(monkeypatch, rows_by_fn, error_fn=None):
    """monkeypatch baostock 五接口 + 登录桩；返回调用记录。"""
    import baostock as bs_mod
    calls = []

    def _mk(fn_name):
        def _f(code, year, quarter):
            calls.append((fn_name, code, year, quarter))
            if error_fn == fn_name:
                return _FakeRS({}, error_code="10001")
            row = rows_by_fn.get(fn_name)
            return _FakeRS(row) if row else _FakeRS(None)
        return _f

    for fn_name in fd._INTERFACE_FIELDS:
        monkeypatch.setattr(bs_mod, fn_name, _mk(fn_name))
    monkeypatch.setattr(fd, "_ensure_baostock_login", lambda: True)  # 登录桩返回真值
    return calls


def test_maps_all_five_interfaces(monkeypatch):
    calls = _install_fakes(monkeypatch, _MOUTAI_2023Q4)
    out = fd.get_financial_quarterly("600519", 2023, 4)
    assert len(calls) == 5 and all(c[1] == "sh.600519" for c in calls)
    assert len(out) == 8 + 6 + 7 + 6 + 5  # 各接口登记字段数
    np_rec = next(r for r in out if r["metric"] == "netProfit")
    assert np_rec["value"] == pytest.approx(77521476277.8)
    assert np_rec["unit"] == "元"
    assert np_rec["period_kind"] == "cumulative"  # 年初累计口径（实测锚定）
    assert np_rec["period_end"] == "2023-12-31"
    assert np_rec["published_at"] == "2024-04-03"
    assert np_rec["source_version"] == fd.FINANCIAL_SOURCE_VERSION
    roe = next(r for r in out if r["metric"] == "roeAvg")
    assert roe["unit"] == "倍" and roe["value"] == pytest.approx(0.361755)
    assert all(r["source_uri"].startswith("baostock.") for r in out)


def test_empty_string_maps_none_not_zero(monkeypatch):
    _install_fakes(monkeypatch, _MOUTAI_2023Q4)
    out = fd.get_financial_quarterly("600519", 2023, 4)
    ebit = next(r for r in out if r["metric"] == "ebitToInterest")
    assert ebit["value"] is None  # 空串=缺失，绝不补 0


def test_error_interface_degrades_others_survive(monkeypatch):
    _install_fakes(monkeypatch, _MOUTAI_2023Q4, error_fn="query_operation_data")
    out = fd.get_financial_quarterly("600519", 2023, 4)
    metrics = {r["metric"] for r in out}
    assert "NRTurnRatio" not in metrics  # 出错接口字段缺失
    assert "netProfit" in metrics and "YOYNI" in metrics  # 其余接口照常


def test_timeout_interface_degrades(monkeypatch):
    _install_fakes(monkeypatch, _MOUTAI_2023Q4)
    from concurrent.futures import TimeoutError as _FT

    # 只对 profit 接口注入超时（替换 _query_one 层——循环层防线应接住并降级）
    real_query = fd._query_one

    def _fake_query(fn_name, bs_code, year, quarter, timeout):
        if fn_name == "query_profit_data":
            raise _FT()
        return real_query(fn_name, bs_code, year, quarter, timeout)

    monkeypatch.setattr(fd, "_query_one", _fake_query)
    out = fd.get_financial_quarterly("600519", 2023, 4)
    metrics = {r["metric"] for r in out}
    assert "netProfit" not in metrics and "YOYNI" in metrics


def test_missing_pubdate_skips_interface(monkeypatch):
    rows = {k: dict(v) for k, v in _MOUTAI_2023Q4.items()}
    rows["query_growth_data"] = {k: v for k, v in rows["query_growth_data"].items()
                                 if k != "pubDate"}
    _install_fakes(monkeypatch, rows)
    out = fd.get_financial_quarterly("600519", 2023, 4)
    metrics = {r["metric"] for r in out}
    assert "YOYNI" not in metrics  # 无公布日整期不产证据（G15）
    assert "netProfit" in metrics


def test_illegal_code_rejected():
    with pytest.raises(ValueError):
        fd.get_financial_quarterly("abc", 2023, 4)


def test_login_failure_returns_empty(monkeypatch):
    """登录失败提前返回空（不发 5 次注定失败的请求）。"""
    calls = _install_fakes(monkeypatch, _MOUTAI_2023Q4)
    monkeypatch.setattr(fd, "_ensure_baostock_login", lambda: False)
    out = fd.get_financial_quarterly("600519", 2023, 4)
    assert out == []
    assert calls == []  # 未发任何接口请求


def test_evidence_integration_and_pit_gate(monkeypatch):
    """F3 集成 + R0 资格止血（A01）：live 抓取的 latest-only 财务证据——
    历史严格快照必须拒（版本不可追溯，探针 P4 反例）；当时抓取/带版本才可入；
    公布前不可见的原 F3 语义保持。"""
    _install_fakes(monkeypatch, _MOUTAI_2023Q4)
    out = fd.get_financial_quarterly("600519", 2023, 4)
    records = [financial_record("600519", d) for d in out]
    np_rec = next(r for r in records if r.metric_or_claim == "netProfit")
    assert np_rec.source_kind == "financial"
    assert np_rec.available_at == datetime(2024, 4, 3, 23, 59, tzinfo=timezone(timedelta(hours=8)))
    # 历史严格快照（2024-04-10）：今天抓的 latest-only 财务全部拒——版本不可追溯
    snap = EvidenceSnapshot.build("600519",
                                  datetime(2024, 4, 10, tzinfo=timezone(timedelta(hours=8))),
                                  records, strict=True)
    assert snap.records == []
    assert snap.drop_reasons.get("latest_only_unverifiable")
    # 同批证据「当时捕获」（first_seen=公布次日）→ 正常入历史快照（R1 CONTEMPORANEOUS_CAPTURE）
    backthen = [r.model_copy(update={
        "fetched_at": datetime(2024, 4, 5, tzinfo=timezone(timedelta(hours=8))),
        "knowledge_basis": "CONTEMPORANEOUS_CAPTURE",
        "first_seen_at": datetime(2024, 4, 5, tzinfo=timezone(timedelta(hours=8)))})
        for r in records]
    snap_ok = EvidenceSnapshot.build("600519",
                                     datetime(2024, 4, 10, tzinfo=timezone(timedelta(hours=8))),
                                     backthen, strict=True)
    assert snap_ok.latest("netProfit") is not None
    assert snap_ok.dropped_pit == 0
    # 公布前不可见（未来证据不进旧快照——原 F3 语义保持）
    snap_old = EvidenceSnapshot.build("600519",
                                      datetime(2024, 3, 1, tzinfo=timezone(timedelta(hours=8))),
                                      backthen, strict=True)
    assert snap_old.latest("netProfit") is None
    assert snap_old.dropped_pit == len(records)


# ── R1：字段语义登记表 / 采集管线 / 语义筛查（DATA_TRUST §2/§3）──────────

def test_field_registry_covers_all_interface_fields():
    """登记表完整且关键类别正确：不登记语义的字段不产证据（派生规则分派不了就拒绝）。"""
    for fn_name, fields in fd._INTERFACE_FIELDS.items():
        for metric, _unit in fields:
            d = fd.FIELD_DEFINITIONS.get(metric)
            assert d is not None, f"{metric} 未登记语义"
            assert d.interface == fn_name
            assert d.verification_status == "name_semantics_pending_original", \
                "未经原始资料复核的字段不得自称 verified"
    assert fd.FIELD_DEFINITIONS["netProfit"].value_kind == "FLOW"
    assert fd.FIELD_DEFINITIONS["netProfit"].period_basis == "YTD"
    assert fd.FIELD_DEFINITIONS["epsTTM"].value_kind == "PER_SHARE_TTM"
    assert fd.FIELD_DEFINITIONS["epsTTM"].period_basis == "TTM"
    assert fd.FIELD_DEFINITIONS["totalShare"].value_kind == "STOCK"
    assert fd.FIELD_DEFINITIONS["totalShare"].period_basis == "POINT_IN_TIME"
    assert fd.FIELD_DEFINITIONS["YOYNI"].value_kind == "GROWTH"
    assert fd.FIELD_DEFINITIONS["YOYNI"].period_basis == "COMPARATIVE"
    assert fd.FIELD_DEFINITIONS["liabilityToAsset"].value_kind == "RATIO"
    assert fd.FIELD_DEFINITIONS["liabilityToAsset"].period_basis == "POINT_IN_TIME"


def test_financial_records_carry_registry_semantics(monkeypatch):
    """证据记录带登记语义 + latest-only 定级 + 版本化字段（R1 契约）。"""
    _install_fakes(monkeypatch, _MOUTAI_2023Q4)
    records = fd.capture_quarterly_evidence("600519", 2023, 4, store=None)
    np_rec = next(r for r in records if r.metric_or_claim == "netProfit")
    assert np_rec.value_kind == "FLOW" and np_rec.period_basis == "YTD"
    assert np_rec.knowledge_basis == "LATEST_WITH_PUBLICATION_DATE"
    assert np_rec.document_version_id == np_rec.source_document_hash
    assert np_rec.first_seen_at is not None and np_rec.metric_definition_version
    assert np_rec.raw_value == np_rec.value and np_rec.raw_unit == "元"
    yoy = next(r for r in records if r.metric_or_claim == "YOYNI")
    assert yoy.value_kind == "GROWTH" and yoy.period_basis == "COMPARATIVE"


def test_capture_archives_raw_idempotent(tmp_path, monkeypatch):
    """采集管线：原始响应逐字段留样（写一次幂等）+ 观测索引 + 筛查接线。"""
    from src.data.research_store import ResearchStore
    _install_fakes(monkeypatch, _MOUTAI_2023Q4)
    store = ResearchStore(tmp_path / "research")
    first = fd.capture_quarterly_evidence("600519", 2023, 4, store=store)
    raw_files = list((tmp_path / "research" / "raw").glob("*.json"))
    assert len(raw_files) == len(first)
    # 幂等重跑：同内容不覆盖原留样
    second = fd.capture_quarterly_evidence("600519", 2023, 4, store=store)
    assert len(list((tmp_path / "research" / "raw").glob("*.json"))) == len(first)
    obs = list((tmp_path / "research" / "observations").glob("*.jsonl"))
    assert obs and obs[0].read_text(encoding="utf-8").strip().count("\n") >= len(first) * 2 - 1
    assert len(second) == len(first)


# ISS-114 形态夹具（structure only——真值以原始财报核定，见 probe_fin_semantics.py；
# 此处锁的是「筛查隔离、不自动 ×100」的行为，不是万科数值本身）
_ISS114_SHAPES = {
    "2023-09-30": {"pubDate": "2023-10-27", "statDate": "2023-09-30",
                   "liabilityToAsset": "0.7322"},
    "2023-12-31": {"pubDate": "2024-03-29", "statDate": "2023-12-31",
                   "liabilityToAsset": "0.0073"},  # 跨期量级漂移形态（≈百倍）
}


def test_iss114_shape_isolated_not_corrected(monkeypatch, tmp_path):
    """ISS-114 纪律（R1 验收4）：相邻期量级跳变 → SUSPECT 隔离 + 纠错账本登记；
    不自动 ×100，不换算，其余字段不受累。"""
    from src.data.research_store import ResearchStore
    rows = {}
    for period, row in _ISS114_SHAPES.items():
        rows[period] = {k: dict(v) for k, v in _MOUTAI_2023Q4.items()}
        rows[period]["query_balance_data"] = dict(row)
        rows[period]["query_balance_data"].setdefault("code", "sz.000002")
    store = ResearchStore(tmp_path / "research")
    all_records = []
    for year, quarter, period in [(2023, 3, "2023-09-30"), (2023, 4, "2023-12-31")]:
        _install_fakes(monkeypatch, rows[period])
        all_records.extend(fd.capture_quarterly_evidence("000002", year, quarter, store=store))
    # 相邻期筛查在累积层做（跨季度比较——capture 单季不做）
    from src.data.research_snapshot import screen_semantic_anomalies
    all_records = screen_semantic_anomalies(all_records)
    lta = [r for r in all_records if r.metric_or_claim == "liabilityToAsset"]
    assert len(lta) == 2
    statuses = {r.semantic_status for r in lta}
    assert "SUSPECT" in statuses, "漂移期被隔离标记"
    suspect = next(r for r in lta if r.semantic_status == "SUSPECT")
    assert suspect.value == pytest.approx(0.0073), "值保持原样——不自动 ×100 纠偏"
    assert "原始财报" in suspect.semantic_note or "人工核对" in suspect.semantic_note
    # 其余字段不受累（不冻结全产品）
    others = [r for r in all_records if r.metric_or_claim != "liabilityToAsset"]
    assert others and all(r.semantic_status != "SUSPECT" for r in others)
    # 纠错账本：SUSPECT 可登记为待核对事项（隔离 ≠ 接受）
    inv = store.append_invalidation(
        cause_kind="unit_drift", security_id="000002", metric="liabilityToAsset",
        period_end=suspect.period_end,
        description="相邻期量级跳变 >10x（筛查命中）——待原始财报核定，未自动纠偏")
    assert store.list_invalidations(metric="liabilityToAsset")
    assert inv["status"] == "open"


def test_unit_drift_mapping_scoped_and_transparent(monkeypatch, tmp_path):
    """ISS-114 版本化映射（DATA_TRUST §2.4）：只对适用范围（该字段×该源×边界后）生效；
    raw_value 保供应商原值；note 带证据指针——透明可审计，不是静默 ×100。"""
    from src.data.research_store import ResearchStore
    def row_for(period_end, pub, lta):
        rows = {k: dict(v) for k, v in _MOUTAI_2023Q4.items()}
        rows["query_balance_data"] = {"code": "sz.000002", "pubDate": pub,
                                      "statDate": period_end, "liabilityToAsset": str(lta)}
        return rows

    store = ResearchStore(tmp_path / "research")
    records = []
    for year, quarter, period, pub, lta in [
            (2024, 1, "2024-03-31", "2024-04-27", "0.72707"),   # 边界前：不映射
            (2024, 2, "2024-06-30", "2024-08-30", "0.007294")]:  # 边界后：×100
        _install_fakes(monkeypatch, row_for(period, pub, lta))
        records.extend(fd.capture_quarterly_evidence("000002", year, quarter, store=store))
    lta_recs = {r.period_end: r for r in records if r.metric_or_claim == "liabilityToAsset"}
    before = lta_recs["2024-03-31"]
    after = lta_recs["2024-06-30"]
    assert before.value == pytest.approx(0.72707), "边界前不动"
    assert before.semantic_note == ""
    assert after.value == pytest.approx(0.7294), "边界后映射 ×100"
    assert after.raw_value == pytest.approx(0.007294), "供应商原值保留"
    assert "×100" in after.semantic_note and "证据" in after.semantic_note
    assert after.metric_definition_version.endswith("iss114_driftfix")
    # 其他字段不受映射影响（即便同在边界后）
    other = [r for r in records if r.period_end == "2024-06-30"
             and r.metric_or_claim != "liabilityToAsset"]
    assert all(r.semantic_note == "" for r in other)
    # 原始留样仍是供应商原值（归档发生在映射前）
    raws = [json.loads(p.read_text(encoding="utf-8"))
            for p in (tmp_path / "research" / "raw").glob("*.json")]
    lta_raws = [r for r in raws if r["content"]["metric"] == "liabilityToAsset"]
    values = sorted(r["content"]["value"] for r in lta_raws)
    assert values == [0.007294, 0.72707], "留样=供应商原貌（含漂移值本身）"
