"""G3-R06 composite method identity and shadow report denominator.

Synthetic records only. The report must not rewrite the capture file.
"""
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from src.core.decision_contract import ThesisStatus
from src.core.decision_policy import POLICY_ID_MID, HorizonPlan
from src.core.research import ASSERTION_RULE_VERSION, ThesisAssessment
from src.core.research_application import ResearchApplicationService
from src.core.research_service import (
    ASSERTION_METHOD_VERSION,
    ResearchService,
    composite_method_version,
    current_method_version,
)
from src.core.shadow_diff import SHADOW_DERIVATION_VERSION, _load_verified_assessment, build_shadow_report
from src.data.horizon_plans import HorizonPlanStore
from src.data.research_store import AssessmentStore, ResearchStore

NOW = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)


class _Snap:
    snapshot_id = "snap-method"
    drop_reasons = {}


def _run(service, method=None):
    def build(*_args, **_kwargs):
        return _Snap()

    if method is not None:
        service_module = __import__("src.core.research_service", fromlist=["current_method_version"])
        original = service_module.current_method_version
        service_module.current_method_version = lambda **_k: method
        try:
            return service.run("600519", as_of=NOW, horizons=("MID",),
                               evidence_records=[], snapshot_builder=build)
        finally:
            service_module.current_method_version = original
    return service.run("600519", as_of=NOW, horizons=("MID",),
                       evidence_records=[], snapshot_builder=build)


def test_composite_method_uses_seven_components_and_ignores_notes():
    current = current_method_version()
    assert current.startswith("g3.method_v1:")
    assert len(current.split(":", 1)[1]) == 64
    assert current == composite_method_version()
    assert current == ASSERTION_METHOD_VERSION
    assert ASSERTION_RULE_VERSION == "g3.assertion_v2"
    changed = composite_method_version(typed="g3.binding_other")
    assert changed != current
    assert composite_method_version(assertion="g3.assertion_v2") == current


def test_method_change_misses_cache_and_changes_assessment_id():
    service = ResearchService()
    first = _run(service)
    second = _run(service)
    assert second.run_id == first.run_id
    assert service.stats["bundle_cache_hits"] == 1
    again = _run(ResearchService(), method="g3.method_v1:" + "ab" * 32)
    assert again.run_id != first.run_id
    assert again.assessments["MID"]["method_version"] != first.assessments["MID"]["method_version"]


def test_old_method_stays_original_and_new_result_is_only_a_candidate(tmp_path):
    store = AssessmentStore(tmp_path / "research")
    old = ThesisAssessment(
        security_id="600519", horizon="MID", snapshot_id="snap-method",
        status=ThesisStatus.VALID, method_version="g3.assertion_v2",
        evaluated_as_of=NOW - timedelta(days=1))
    old_id = store.save(old)
    plans = HorizonPlanStore(tmp_path / "plans.json")
    plan = HorizonPlan(
        plan_id="p-old", security_id="600519", horizon="MID",
        policy_id=POLICY_ID_MID, intent="旧方法原件",
        snapshot_id="snap-method", assessment_id=old_id,
        policy_version="g3.research_service_v2")
    assert plans.save(plan)
    assert plans.accept("600519", "MID")[0]
    ref_before = plans.get_accepted_ref("600519", "MID")
    assert _load_verified_assessment(
        plans.get("600519", "MID"), "MID", "600519", store, as_of=NOW) is None
    fresh = old.model_copy(update={"method_version": current_method_version()})
    new_id = store.save(fresh)
    assert new_id != old_id
    assert store.load(old_id).method_version == "g3.assertion_v2"
    app = ResearchApplicationService(
        store=ResearchStore(tmp_path / "research"),
        plans_store=plans, assessment_store=store)
    draft = plan.model_dump(mode="json")
    draft["assessment_id"] = new_id
    draft.setdefault("facts_observed", [])
    draft.setdefault("fact_evidence_refs", {})
    draft.setdefault("review_triggers", [])
    draft.setdefault("gaps", [])
    draft.setdefault("next_checks", [])
    draft.setdefault("required_evidence_refs", [])
    draft.setdefault("thesis_id", "")
    kinds = {}
    app._save_draft(draft, draft_kinds=kinds)
    assert kinds["MID"] == "candidate"
    assert plans.get_accepted_ref("600519", "MID") == ref_before
    assert plans.get("600519", "MID").assessment_id == old_id
    assert plans.get_candidate("600519", "MID").assessment_id == new_id


def test_report_denominator_requires_current_method_and_does_not_rewrite(tmp_path):
    method = current_method_version()
    stamp = (datetime.now().astimezone() - timedelta(hours=2)).isoformat(timespec="seconds")

    def row(version, effective, binding_method, *, binding=True, long_effective=False):
        item = {
            "security_id": "600519", "as_of": stamp, "derivation_version": version,
            "mid_effective": effective, "long_effective": long_effective,
            "observation_kind": "effective" if effective else "diagnostic",
            "delta_reasons": [],
        }
        if binding:
            captured = _captured_record(tmp_path, "MID")
            item["as_of"] = captured.as_of
            item["mid_binding"] = deepcopy(captured.mid_binding)
            item["mid_binding"]["method_version"] = binding_method
            if long_effective:
                item["long_binding"] = deepcopy(_captured_record(tmp_path, "LONG").long_binding)
                item["long_binding"]["method_version"] = binding_method
        return item

    rows = [
        row(SHADOW_DERIVATION_VERSION, True, method),
        row(SHADOW_DERIVATION_VERSION, True, "g3.assertion_v2"),
        row(SHADOW_DERIVATION_VERSION, False, method),
        row(SHADOW_DERIVATION_VERSION, True, method, binding=False),
        row("shadow_v14", True, method),
    ]
    path = tmp_path / "shadow.jsonl"
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows),
                    encoding="utf-8")
    before = path.read_bytes()
    report = build_shadow_report(store_path=path, days=7)
    assert path.read_bytes() == before
    assert report["cur_mid_effective"] == 1
    assert report["cur_long_effective"] == 0
    assert report["effective_observations"] == 1
    assert report["cur_diagnostic"] == 3
    assert report["older_versions"]["shadow_v14"]["mid_effective"] == 1
    assert report["legacy_records"] == 1


def _captured_record(tmp_path, horizon, *, rule=None, monkeypatch=None, stamp=None, quote_stamp=None):
    """Real capture, isolated stores and a complete legal binding for this horizon."""
    from src.core.decision_contract import DecisionPacket
    from src.core.research import CheckpointCondition, CheckpointResult, ThesisAssertion
    import src.core.decision_policy as dp
    from src.core.research_service import RESEARCH_SERVICE_VERSION
    from src.core.shadow_diff import capture_shadow
    stamp = stamp or (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0)
    asm = ThesisAssessment(security_id="600519", horizon=horizon, snapshot_id="snap-report",
        status="VALID", method_version=current_method_version(), evaluated_as_of=stamp,
        required_assertions=[ThesisAssertion(security_id="600519", horizon=horizon,
            proposition_type="window_and_refutation", description="synthetic checkpoint",
            supporting_evidence_ids=["synthetic-ref"], evaluation="TRUE")],
        checkpoint_results=[CheckpointResult(evaluation="TRUE", condition=CheckpointCondition(
            security_id="600519", horizon=horizon, proposition_type="window_and_refutation",
            description="synthetic checkpoint", evidence_refs=["synthetic-ref"],
            derived_from="user_confirmed_risk", user_confirmed=True,
            review_at=stamp + timedelta(hours=1), refute_if="synthetic reversal",
            expiry_action="REVIEW_REQUIRED"))])
    asm_store = AssessmentStore(tmp_path / "research")
    aid = asm_store.save(asm)
    plans = HorizonPlanStore(tmp_path / "plans.json")
    plan = HorizonPlan(plan_id=f"report-{horizon}", security_id="600519", horizon=horizon,
        policy_id=dp.POLICY_ID_MID if horizon == "MID" else dp.POLICY_ID_LONG,
        intent="synthetic report version boundary", assessment_id=aid,
        snapshot_id=asm.snapshot_id, policy_version=RESEARCH_SERVICE_VERSION)
    assert plans.save(plan)
    assert plans.accept("600519", horizon)[0]
    packet = DecisionPacket(security_id="600519", as_of=stamp, policy_id="legacy_v1",
        desired_action="HOLD", confirmed_weight=.1, target_weight=.1)
    if rule:
        monkeypatch.setattr(dp, "DECISION_TABLE_VERSION", rule)
    record = capture_shadow(None, NS(position_action="HOLD_POSITION", sell_path=None),
        NS(effective_action=NS(value="HOLD"), blocked=False), NS(current_ratio=.1, trade_plan=None),
        packet=packet, as_of=stamp, config={"fusion": {"mode": "capture_only"}},
        plans_store=plans, assessment_store=asm_store, account_version="report-account",
        quote_as_of=(quote_stamp or stamp).isoformat(), store_path=tmp_path / "capture.jsonl")
    assert getattr(record, f"{horizon.lower()}_effective") is True
    return record


@pytest.mark.parametrize("horizon", ["MID", "LONG"])
@pytest.mark.parametrize("damage", ["rule", "policy", "missing", "bad", "horizon",
                                   "empty_as_of", "bad_as_of", "naive_as_of", "date_as_of",
                                   "false", "none", "valid"])
def test_report_revalidates_complete_binding_at_capture_time(tmp_path, horizon, damage):
    row = _captured_record(tmp_path, horizon).model_dump(mode="json")
    binding = row[f"{horizon.lower()}_binding"]
    if damage == "rule":
        binding["decision_rule_version"] = binding["policy_id"] + "@old"
    elif damage == "policy":
        binding["policy_version"] = "old"
    elif damage == "missing":
        del row[f"{horizon.lower()}_binding"]
    elif damage == "bad":
        binding["arms"] = ["broken"]
    elif damage == "horizon":
        binding["horizon"] = "LONG" if horizon == "MID" else "MID"
    elif damage in ("empty_as_of", "bad_as_of"):
        row["as_of"] = "" if damage == "empty_as_of" else "invalid-date"
    elif damage == "naive_as_of":
        row["as_of"] = datetime.fromisoformat(row["as_of"]).replace(tzinfo=None).isoformat()
    elif damage == "date_as_of":
        row["as_of"] = row["as_of"][:10]
    elif damage in ("false", "none"):
        row[f"{horizon.lower()}_effective"] = False if damage == "false" else None
    path = tmp_path / "report.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    before = path.read_bytes()
    report = build_shadow_report(store_path=path)
    assert report["effective_observations"] == int(damage == "valid")
    assert report[f"cur_{horizon.lower()}_effective"] == int(damage == "valid")
    assert report["cur_diagnostic"] == int(damage != "valid")
    if damage == "rule":
        assert report["cur_drop_counts"][f"unsupported_rule_version_{horizon.lower()}"] == 1
    if damage == "policy":
        assert report["cur_drop_counts"][f"unsupported_policy_version_{horizon.lower()}"] == 1
    assert path.read_bytes() == before


def test_real_old_rule_capture_is_diagnostic_after_table_upgrade(tmp_path, monkeypatch):
    import src.core.decision_policy as dp
    current = dp.DECISION_TABLE_VERSION
    assert current != "v3"
    with monkeypatch.context() as old:
        record = _captured_record(tmp_path, "MID", rule="v3", monkeypatch=old)
    path = tmp_path / "capture.jsonl"
    before = path.read_bytes()
    assert record.mid_binding["decision_rule_version"].endswith("@v3")
    report = build_shadow_report(store_path=path)
    assert report["effective_observations"] == 0
    assert report["cur_mid_effective"] == 0 and report["cur_diagnostic"] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize("horizon", ["MID", "LONG"])
def test_report_keeps_completed_capture_after_checkpoint_expires(tmp_path, horizon):
    record = _captured_record(tmp_path, horizon)
    plans = HorizonPlanStore(tmp_path / "plans.json")
    assessments = AssessmentStore(tmp_path / "research")
    plan = plans.get("600519", horizon)
    original = assessments.load(plan.assessment_id)
    assert original.checkpoint_results[0].condition.review_at < datetime.now(timezone.utc)
    # Today's decision must review the expired agreement; yesterday's completed capture remains.
    assert _load_verified_assessment(plan, horizon, "600519", assessments,
                                     as_of=datetime.now(timezone.utc)) is None
    path = tmp_path / "capture.jsonl"
    before = path.read_bytes()
    report = build_shadow_report(store_path=path)
    assert report[f"cur_{horizon.lower()}_effective"] == 1
    assert report["effective_observations"] == 1
    assert report["records"][0]["as_of"] == record.as_of
    assert path.read_bytes() == before


@pytest.mark.parametrize("horizon", ["MID", "LONG"])
@pytest.mark.parametrize("quote_lag_us", [0, 250000])
def test_report_preserves_capture_subsecond_cutoff(tmp_path, horizon, quote_lag_us):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=500000)
    record = _captured_record(tmp_path, horizon, stamp=stamp,
                              quote_stamp=stamp - timedelta(microseconds=quote_lag_us))
    assert datetime.fromisoformat(record.as_of) == stamp
    path = tmp_path / "capture.jsonl"
    before = path.read_bytes()
    report = build_shadow_report(store_path=path)
    assert report[f"cur_{horizon.lower()}_effective"] == 1
    assert report["cur_diagnostic"] == 0
    assert path.read_bytes() == before
