"""G3 checkpoint content identity and restart fidelity; isolated storage only."""
import hashlib
import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.core import research
from src.data.research_store import AssessmentStore


def condition(**changes):
    fields = dict(security_id="603906", horizon="MID",
                  proposition_type="window_and_refutation", description="Review disclosed profit",
                  evidence_refs=["ev_b", "ev_a"], derived_from="disclosure_schedule",
                  user_confirmed=True, review_at="2026-12-01T10:00:00+08:00",
                  refute_if="Profit conversion absent", expiry_action="REVIEW_REQUIRED")
    fields.update(changes)
    return research.CheckpointCondition(**fields)


def assessment(cp=None, *, evaluation="UNKNOWN", reasons=None):
    return research.ThesisAssessment(
        security_id="603906", horizon="MID", snapshot_id="snap_fixed",
        method_version="g3", evaluated_as_of=datetime(2026, 10, 14, tzinfo=timezone.utc),
        checkpoint_results=[research.CheckpointResult(
            condition=cp or condition(), evaluation=evaluation, reasons=reasons or ["Not evaluated"])] )


def test_checkpoint_content_id_ignores_random_id_and_ref_order():
    first = condition(condition_id="random_a")
    second = condition(condition_id="random_b", evidence_refs=["ev_a", "ev_b", "ev_a"])
    assert first.condition_id == second.condition_id
    assert first.semantic_payload() == second.semantic_payload()
    assert first.content_fingerprint() == second.content_fingerprint()
    assert condition(unconsumed_extra="ignored").condition_id == first.condition_id


@pytest.mark.parametrize("changes", [
    {"security_id": "600519"}, {"horizon": "LONG"},
    {"proposition_type": "longterm_invalidation"}, {"description": "Different condition"},
    {"evidence_refs": ["ev_c"]}, {"derived_from": "user_confirmed_risk"},
    {"user_confirmed": False}, {"review_at": "2026-12-02T10:00:00+08:00"},
    {"refute_if": "Delivery absent"}, {"expiry_action": None},
])
def test_every_consumed_condition_field_changes_identity(changes, tmp_path):
    original, changed = condition(), condition(**changes)
    assert original.condition_id != changed.condition_id
    store = AssessmentStore(tmp_path)
    assert store.save(assessment(original)) != store.save(assessment(changed))


def test_all_fields_roundtrip_and_unknown_is_default(tmp_path):
    cp = condition()
    result = research.CheckpointResult(condition=cp)
    assert result.evaluation == research.AssertionEvaluation.UNKNOWN
    asm = assessment(cp, evaluation="TRUE", reasons=["Explicit rule evaluation"])
    store = AssessmentStore(tmp_path)
    aid = store.save(asm)
    restored = AssessmentStore(tmp_path).load(aid)
    assert restored.model_dump(mode="json") == asm.model_dump(mode="json")
    assert json.loads((tmp_path / "assessments" / f"{aid}.json").read_text(encoding="utf-8"))["schema_version"] == 2
    assert store.save(assessment(condition(condition_id="another"), evaluation="TRUE",
                                 reasons=["Explicit rule evaluation"])) == aid


def test_result_evaluation_reasons_and_none_vs_empty_change_id(tmp_path):
    store = AssessmentStore(tmp_path)
    ids = {store.save(assessment()), store.save(assessment(evaluation="TRUE")),
           store.save(assessment(reasons=["Different reason"]))}
    assert len(ids) == 3
    old = research.ThesisAssessment(horizon="MID")
    new = research.ThesisAssessment(horizon="MID", checkpoint_results=[])
    assert store.assessment_id_of(old) != store.assessment_id_of(new)


def test_old_schema_without_checkpoint_fields_retains_old_address(tmp_path):
    raw = research.ThesisAssessment(horizon="MID", security_id="603906").model_dump(mode="json")
    raw.pop("checkpoint_results", None)
    payload = {key: raw[key] for key in (
        "security_id", "horizon", "snapshot_id", "method_version", "status",
        "required_assertions", "supporting_assertions", "invalidation_results", "next_checks")}
    payload["evaluated_as_of"] = ""
    aid = "asm_" + hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                            default=str).encode("utf-8")).hexdigest()[:16]
    directory = tmp_path / "assessments"
    directory.mkdir()
    path = directory / f"{aid}.json"
    path.write_text(json.dumps(dict(schema_version=1, assessment_id=aid,
                                   content_hash=aid[4:], assessment=raw)), encoding="utf-8")
    before = path.read_bytes()
    store = AssessmentStore(tmp_path)
    loaded = store.load(aid)
    assert loaded is not None
    assert loaded.checkpoint_results is None
    assert store.assessment_id_of(loaded) == aid
    assert store.save(loaded) == aid
    assert path.read_bytes() == before
    old_cp = research.CheckpointCondition(proposition_type="window_and_refutation",
                                          description="Old text", derived_from="user_confirmed_risk",
                                          user_confirmed=True)
    assert old_cp.review_at is None and old_cp.refute_if == "" and old_cp.expiry_action is None


@pytest.mark.parametrize("review_at", ["garbage", "2026-12-01", "2026-12-01T10:00:00",
                                      datetime(2026, 12, 1), 1796071200, True])
def test_review_time_strictly_rejects_invalid_or_naive_input(review_at):
    with pytest.raises(ValidationError):
        condition(review_at=review_at)


def test_review_time_same_instant_has_same_identity():
    assert condition().condition_id == condition(review_at="2026-12-01T02:00:00Z").condition_id


def test_old_schema_extra_checkpoint_cannot_grant_new_qualification(tmp_path):
    store = AssessmentStore(tmp_path)
    aid = store.save(research.ThesisAssessment(horizon="MID"))
    path = tmp_path / "assessments" / f"{aid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["assessment"]["checkpoint_results"] = assessment(evaluation="TRUE").model_dump(mode="json")["checkpoint_results"]
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = store.load(aid)
    assert loaded is not None
    assert loaded.checkpoint_results is None


@pytest.mark.parametrize("schema", [0, 3, "2", True])
def test_unknown_schema_cannot_grant_new_qualification(tmp_path, schema):
    store = AssessmentStore(tmp_path)
    aid = store.save(assessment())
    path = tmp_path / "assessments" / f"{aid}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = schema
    path.write_text(json.dumps(data), encoding="utf-8")
    assert store.load(aid) is None
