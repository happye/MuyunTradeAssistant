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
    _install_fakes(monkeypatch, _MOUTAI_2023Q4)
    out = fd.get_financial_quarterly("600519", 2023, 4)
    records = [financial_record("600519", d) for d in out]
    np_rec = next(r for r in records if r.metric_or_claim == "netProfit")
    assert np_rec.source_kind == "financial"
    assert np_rec.available_at == datetime(2024, 4, 3, 23, 59, tzinfo=timezone(timedelta(hours=8)))
    # 严格快照：公布后可见
    snap = EvidenceSnapshot.build("600519",
                                  datetime(2024, 4, 10, tzinfo=timezone(timedelta(hours=8))),
                                  records, strict=True)
    assert snap.latest("netProfit") is not None
    assert snap.dropped_pit == 0
    # 公布前不可见（未来证据不进旧快照）
    snap_old = EvidenceSnapshot.build("600519",
                                      datetime(2024, 3, 1, tzinfo=timezone(timedelta(hours=8))),
                                      records, strict=True)
    assert snap_old.latest("netProfit") is None
    assert snap_old.dropped_pit == len(records)
