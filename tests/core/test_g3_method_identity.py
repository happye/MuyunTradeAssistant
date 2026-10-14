"""G3-R06 composite method identity and shadow report denominator.

Synthetic records only. The report must not rewrite the capture file.
"""
import json
from datetime import datetime, timedelta, timezone

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
            item["mid_binding"] = {"horizon": "MID", "method_version": binding_method}
            if long_effective:
                item["long_binding"] = {"horizon": "LONG", "method_version": binding_method}
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
