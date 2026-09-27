"""J1 映射边界与原件可复用回归测试（plan/fusion iteration3，DELIVERY_PLAN J1 验收）

锁死语义（DATA_DECISION §2 映射合同六条）：
1. N4 幂等：canonical = raw × factor 只从原值算一次——同 mapping 重复应用无变化，
   raw 不变（两次结果仍 0.7294）
2. 范围门：未核定证券 / 超出已观测报告期（供应商恢复/再变更窗口）→ 不乘 + SUSPECT 隔离；
   边界前报告期维持原状（已核定正常，不加噪声）
3. 多映射冲突 → SUSPECT；双供应商一致不自动升原件核定（verification_level 不变）
4. 原件离线可复用：load_raw / query_observations 读回留样；has_quarter_evidence +
   skip_archived 零重复抓取；select_ended_quarters 不含未结束当季
5. 原件核对记录：必需字段齐备才登记，追加式幂等
6. financial_record 透传 semantic_status（范围外 SUSPECT 进入证据记录）

纯 mock 测试（monkeypatch baostock），零网络，临时目录隔离。跑法：pytest tests/core/test_j1_mapping_boundary.py -q
"""
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data import financial_data as fd
from src.data.research_store import ResearchStore


def _fin(value=0.007294, period_end="2024-06-30", security_id="000002", **kw):
    d = {"metric": "liabilityToAsset", "source_version": fd.FINANCIAL_SOURCE_VERSION,
         "period_end": period_end, "value": value, "unit": "倍",
         "security_id": security_id, "period_kind": "cumulative"}
    d.update(kw)
    return d


# ── 1. N4：幂等与 raw 不变 ────────────────────────────────

def test_n4_apply_twice_idempotent_raw_unchanged():
    once = fd.apply_unit_drift_mapping(_fin())
    assert once["value"] == pytest.approx(0.7294)
    assert once["raw_value"] == pytest.approx(0.007294)
    twice = fd.apply_unit_drift_mapping(once)
    assert twice["value"] == pytest.approx(0.7294), "重复应用不得再 ×100"
    assert twice["raw_value"] == pytest.approx(0.007294), "raw 不变"
    assert twice["unit_drift_mapping_id"] == once["unit_drift_mapping_id"]


def test_n4_applied_marker_lost_still_idempotent():
    """标记丢失但 raw_value 在且 value==raw×factor → 识别已应用，不重复乘。"""
    once = fd.apply_unit_drift_mapping(_fin())
    stripped = {k: v for k, v in once.items() if k != "unit_drift_mapping_id"}
    again = fd.apply_unit_drift_mapping(stripped)
    assert again["value"] == pytest.approx(0.7294)


# ── 2. 范围门 ─────────────────────────────────────────────

def test_out_of_scope_security_suspect_not_multiplied():
    """未核定证券（映射范围只有 000002/600519）→ 不乘 + SUSPECT 隔离。"""
    out = fd.apply_unit_drift_mapping(_fin(security_id="600000"))
    assert out["value"] == pytest.approx(0.007294), "范围外不得自动换算"
    assert out["semantic_status"] == "SUSPECT"
    assert "范围外" in out["semantic_note"] or "未核定" in out["semantic_note"]


def test_restored_supplier_period_suspect_not_multiplied():
    """供应商恢复（超出已观测报告期的新值已是正常量级）→ 不乘 + SUSPECT——
    不能按所有未来报告期无限乘（N4 restored_supplier 形态）。"""
    out = fd.apply_unit_drift_mapping(_fin(value=0.7294, period_end="2026-06-30"))
    assert out["value"] == pytest.approx(0.7294), "边界后新报告期不得沿用旧映射自动 ×100"
    assert out["semantic_status"] == "SUSPECT"


def test_pre_boundary_period_untouched_without_noise():
    """边界前报告期已核定正常——原样返回，不加 SUSPECT 噪声。"""
    d = _fin(value=0.72707, period_end="2024-03-31")
    out = fd.apply_unit_drift_mapping(d)
    assert out["value"] == pytest.approx(0.72707)
    assert out.get("semantic_status") != "SUSPECT"
    assert "semantic_note" not in out or out["semantic_note"] == ""


def test_mapping_conflict_suspect(monkeypatch):
    """多映射同时命中 → SUSPECT + 冲突注记（不任选其一静默换算）。"""
    from src.data.financial_data import UnitDriftMapping
    second = fd.UnitDriftMapping(
        mapping_id="umd_test_conflict_v1", metric="liabilityToAsset",
        source_version=fd.FINANCIAL_SOURCE_VERSION,
        observed_scope=["000002", "600519"],
        effective_period_range=("2024-06-30", "2024-12-31"),
        factor=1.0, description="冲突测试映射",
        verification_level="cross_checked_pending_original",
        corrected_definition_version="test", registered_at="2026-09-27")
    monkeypatch.setattr(fd, "UNIT_DRIFT_MAPPINGS",
                        list(fd.UNIT_DRIFT_MAPPINGS) + [second])
    out = fd.apply_unit_drift_mapping(_fin())
    assert out["value"] == pytest.approx(0.007294), "冲突时不静默换算"
    assert out["semantic_status"] == "SUSPECT"
    assert "冲突" in out["semantic_note"]


def test_verification_level_not_auto_promoted():
    """双供应商一致不自动升原件核定——verification_level 保持待原件状态。"""
    m = fd.UNIT_DRIFT_MAPPINGS[0]
    assert m.verification_level == "cross_checked_pending_original"
    assert m.observed_scope == ["000002", "600519"], "适用范围=已核定证券，不是全市场"
    assert m.evidence_hashes, "证据 hash 可复现"


def test_register_mapping_affected_refs(tmp_path):
    """映射修订/启用登记受影响派生清单（追加式，不改写历史）。"""
    store = ResearchStore(tmp_path / "research")
    rec = fd.register_mapping_affected(
        store, fd.UNIT_DRIFT_MAPPINGS[0],
        affected_refs=[{"kind": "factor", "id": "balance_risk_v1"}])
    assert rec["cause_kind"] == "unit_drift"
    assert rec["affected_refs"] == [{"kind": "factor", "id": "balance_risk_v1"}]
    assert store.list_invalidations(metric="liabilityToAsset")


# ── 3. 原件离线可复用 ─────────────────────────────────────

def test_load_raw_offline_readback(tmp_path):
    store = ResearchStore(tmp_path / "research")
    digest, _ = store.archive_raw({"metric": "liabilityToAsset", "value": 0.007294},
                                  metadata={"security_id": "000002"})
    payload = store.load_raw(digest)
    assert payload["content"]["value"] == pytest.approx(0.007294)
    assert payload["metadata"]["security_id"] == "000002"
    assert store.load_raw("deadbeef") is None
    assert store.load_raw("../../etc/passwd") is None
    # 坏文件隔离不崩
    bad = store.dir / "raw" / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    assert store.load_raw("bad") is None


def test_query_observations_filters(tmp_path):
    store = ResearchStore(tmp_path / "research")
    store.append_observation({"kind": "financial_quarterly", "security_id": "000002",
                              "metric": "liabilityToAsset", "period_end": "2024-06-30",
                              "value": 0.007294, "unit": "倍", "raw_sha256": "x",
                              "source_version": fd.FINANCIAL_SOURCE_VERSION})
    store.append_observation({"kind": "financial_quarterly", "security_id": "600519",
                              "metric": "netProfit", "period_end": "2023-12-31",
                              "value": 1.0, "unit": "元", "raw_sha256": "y",
                              "source_version": fd.FINANCIAL_SOURCE_VERSION})
    hits = store.query_observations(security_id="000002", metric="liabilityToAsset",
                                    period_end="2024-06-30")
    assert len(hits) == 1 and hits[0]["security_id"] == "000002"
    assert store.query_observations(metric="netProfit")[0]["period_end"] == "2023-12-31"
    assert store.query_observations(security_id="999999") == []


def test_has_quarter_evidence_and_skip_archived(tmp_path, monkeypatch):
    """零重复抓取：已归档季度 has_quarter_evidence=True；skip_archived=True 时
    不再发接口请求（相同归档离线可读）。"""
    rows = {
        "query_balance_data": {"code": "sz.000002", "pubDate": "2024-08-30",
                               "statDate": "2024-06-30", "liabilityToAsset": "0.007294"},
    }
    calls = []

    def _mk(fn_name):
        def _f(code, year, quarter):
            calls.append((fn_name, code, year, quarter))
            row = rows.get(fn_name)
            fields = ["code", "pubDate", "statDate"] + [k for k in row
                                                        if k not in ("code", "pubDate", "statDate")]
            return SimpleNamespace(fields=fields, error_code="0", error_msg="",
                                   _row=[row.get(f, "") for f in fields], _used=False,
                                   next=lambda s=None: True, get_row_data=lambda: list(
                                       [row.get(f, "") for f in fields]))
        return _f

    import baostock as bs_mod
    for fn_name in fd._INTERFACE_FIELDS:
        monkeypatch.setattr(bs_mod, fn_name, _mk(fn_name))
    monkeypatch.setattr(fd, "_ensure_baostock_login", lambda: True)

    store = ResearchStore(tmp_path / "research")
    assert store.has_quarter_evidence("000002", 2024, 2) is False
    first = fd.capture_quarterly_evidence("000002", 2024, 2, store=store)
    assert first and any(r.metric_or_claim == "liabilityToAsset" for r in first)
    assert store.has_quarter_evidence("000002", 2024, 2) is True
    n_calls = len(calls)
    second = fd.capture_quarterly_evidence("000002", 2024, 2, store=store,
                                           skip_archived=True)
    assert second == [] and len(calls) == n_calls, "已归档季度零重复抓取"


def test_select_ended_quarters_excludes_unfinished():
    """报告期选择：不含未结束当季（不浪费请求）；由新到旧。"""
    out = fd.select_ended_quarters(datetime(2026, 9, 27, tzinfo=timezone.utc), 4)
    assert out == [(2026, 2), (2026, 1), (2025, 4), (2025, 3)], \
        "2026Q3 未结束不入列"
    out2 = fd.select_ended_quarters(datetime(2026, 1, 5, tzinfo=timezone.utc), 2)
    assert out2 == [(2025, 4), (2025, 3)]


# ── 4. 原件核对记录 ───────────────────────────────────────

def test_original_verification_roundtrip(tmp_path):
    store = ResearchStore(tmp_path / "research")
    entry = {"security_id": "000002", "period_end": "2024-06-30",
             "file_hash": "abc123", "consolidation_scope": "合并",
             "disclosure_proof": "巨潮 finalpage/2024-08-31/1221084198.PDF",
             "page_table": "合并资产负债表 P3", "raw_line_label": "负债合计",
             "raw_value": "135,296,952", "unit": "元",
             "derived_formula": "负债合计/资产合计", "extraction_method": "deterministic_text",
             "reviewer": "user", "verification_level": "verified_against_original"}
    rec = store.save_original_verification(entry)
    assert rec["verification_id"]
    # 幂等：同 证券+报告期+文件hash 不重复登记
    again = store.save_original_verification(dict(entry))
    assert again["verification_id"] == rec["verification_id"]
    assert len(store.list_original_verifications(security_id="000002")) == 1
    assert store.list_original_verifications(period_end="2024-06-30")
    # 双供应商一致不自动升核定：清单里没有原件条目的字段/时期不得声称 verified
    assert store.list_original_verifications(security_id="600000") == []
    with pytest.raises(ValueError):
        store.save_original_verification({"security_id": "000002"})


def test_financial_record_passes_semantic_status():
    """范围外 SUSPECT 经 financial_record 进入证据记录（快照资格隔离可达）。"""
    from src.data.research_snapshot import financial_record
    rec = financial_record("600000", _fin(security_id="600000",
                                          semantic_status="SUSPECT",
                                          semantic_note="映射范围外"))
    assert rec.semantic_status == "SUSPECT"
    assert "范围外" in rec.semantic_note
    rec2 = financial_record("600000", _fin(security_id="600000"))
    assert rec2.semantic_status == "UNKNOWN", "缺省行为不变"
