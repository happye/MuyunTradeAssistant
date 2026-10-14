"""G3 checkpoint contract through real research, persisted plans and read-only consumers.

Fixtures are synthetic company evidence; none is a real MID research sample.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.core.research import CheckpointCondition, CheckpointResult
from src.core.research_service import checkpoint_display, checkpoint_summary
from src.core.research_application import ResearchApplicationService
from src.data.horizon_plans import HorizonPlanStore
from src.data.research_store import AssessmentStore, ResearchStore

AS_OF = datetime(2026, 10, 14, 8, tzinfo=timezone.utc)
REVIEW_AT = datetime(2026, 10, 30, 8, tzinfo=timezone.utc)
SECURITY = "600519"


def _app(root):
    return ResearchApplicationService(
        store=ResearchStore(root / "research"),
        plans_store=HorizonPlanStore(root / "plans.json"),
        assessment_store=AssessmentStore(root / "research"))


def _documents():
    out = []
    for cid, event_type, body, value, unit in (
        ("exposure", "exposure", "2024年公司境外磷酸铁锂业务收入100万元", 100, "万元"),
        ("order", "order", "公司签订900万元订单", 900, "万元"),
    ):
        uri = "fixture://g3/" + cid
        out.append({"claim": {
            "claim_id": cid, "security_id": SECURITY, "subject": SECURITY,
            "event_type": event_type, "statement": body,
            "value": value, "unit": unit, "quote_text": body,
            "citation_uri": uri, "published_at": "2026-10-01T08:00:00+00:00",
            "verification_scope": "typed_numeric_event",
        }, "document": {
            "canonical_uri": uri, "body": body, "security_ids": [SECURITY],
            "published_at": "2026-10-01T08:00:00+00:00",
        }})
    return out


def _condition(**changes):
    fields = dict(
        condition_id="caller-random-one", security_id=SECURITY, horizon="MID",
        proposition_type="window_and_refutation", description="核验订单收入转化",
        evidence_refs=["order", "exposure"], derived_from="user_confirmed_risk",
        user_confirmed=True, review_at=REVIEW_AT,
        refute_if="原件证实合同取消或收入转化未发生", expiry_action="REVIEW_REQUIRED",
    )
    fields.update(changes)
    return CheckpointCondition(**fields)


def _window(app, result):
    asm = app.assessment_store.load(result.assessment_ids["MID"])
    return asm, next(a for a in asm.required_assertions
                     if a.proposition_type == "window_and_refutation")


@pytest.mark.parametrize("missing", ["review_at", "refute_if", "expiry_action"])
def test_incomplete_checkpoint_does_not_establish_window(tmp_path, missing):
    app = _app(tmp_path)
    cp = _condition(**{missing: "" if missing == "refute_if" else None})
    result = app.run(SECURITY, as_of=AS_OF, horizons=("MID",),
                     documents=_documents(), checkpoints=[cp])
    asm, window = _window(app, result)
    assert result.verified_claims == 2
    assert window.evaluation.value == "UNKNOWN"
    assert asm.status.value != "VALID"
    assert missing in " ".join(asm.unresolved_gaps)


def test_expired_checkpoint_requires_review_not_exit(tmp_path):
    app = _app(tmp_path)
    cp = _condition(review_at=AS_OF)
    result = app.run(SECURITY, as_of=AS_OF, horizons=("MID",),
                     documents=_documents(), checkpoints=[cp])
    asm, window = _window(app, result)
    assert window.evaluation.value == "UNKNOWN"
    assert asm.status.value == "REVIEW_REQUIRED"
    assert "到期" in " ".join(asm.unresolved_gaps)
    assert "position_action" not in asm.model_dump()


def test_restart_retains_condition_and_same_semantics_do_not_create_candidate(tmp_path):
    app = _app(tmp_path)
    first = app.run(SECURITY, as_of=AS_OF, horizons=("MID",),
                    documents=_documents(), checkpoints=[_condition()])
    asm, window = _window(app, first)
    assert asm.status.value == "VALID"
    assert window.evaluation.value == "TRUE"
    assert app.plans_store.accept(SECURITY, "MID")[0]
    assert app.plans_store.set_active_ref(SECURITY, app.plans_store.get(SECURITY, "MID"))[0]
    tree_before = {str(p.relative_to(tmp_path)): p.read_bytes()
                   for p in tmp_path.rglob("*") if p.is_file()}
    restarted = _app(tmp_path)
    loaded = restarted.assessment_store.load(first.assessment_ids["MID"])
    assert loaded.checkpoint_results[0].condition.refute_if == _condition().refute_if
    assert loaded.checkpoint_results[0].condition.review_at == REVIEW_AT
    second = restarted.run(SECURITY, as_of=AS_OF, horizons=("MID",),
                           documents=_documents(), checkpoints=[_condition(condition_id="other-random")])
    assert second.run_id == first.run_id
    assert second.assessment_ids == first.assessment_ids
    assert second.draft_kinds["MID"] == "idempotent"
    assert restarted.plans_store.get_candidate(SECURITY, "MID") is None
    # Research manifests are immutable. Fixed input must not rewrite an accepted plan.
    assert {str(p.relative_to(tmp_path)): p.read_bytes()
            for p in tmp_path.rglob("*") if p.is_file()} == tree_before
    view = restarted.get_daily_view(SECURITY, as_of=AS_OF)
    assert view["horizons"]["MID"]["accepted"] is True
    assert view["horizons"]["MID"]["assessment_status"] == "VALID"
    assert view["horizons"]["MID"]["checkpoint_results"][0]["condition"]["review_at"]


def test_read_only_query_at_expiry_does_not_rewrite_assessment(tmp_path):
    app = _app(tmp_path)
    result = app.run(SECURITY, as_of=AS_OF, horizons=("MID",),
                     documents=_documents(), checkpoints=[_condition()])
    assert app.plans_store.accept(SECURITY, "MID")[0]
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    view = _app(tmp_path).get_daily_view(SECURITY, as_of=REVIEW_AT)
    assert view["horizons"]["MID"]["assessment_status"] == "REVIEW_REQUIRED"
    assert "到期" in " ".join(view["horizons"]["MID"]["assessment_gaps"])
    assert app.assessment_store.load(result.assessment_ids["MID"]).status.value == "VALID"
    assert {str(p.relative_to(tmp_path)): p.read_bytes()
            for p in tmp_path.rglob("*") if p.is_file()} == before


def test_same_instant_different_offset_keeps_run_asm_and_accepted_ref(tmp_path):
    utc = datetime(2026, 10, 30, 8, tzinfo=timezone.utc)
    shanghai = utc.astimezone(timezone(timedelta(hours=8)))
    assert shanghai.isoformat() == "2026-10-30T16:00:00+08:00"
    app = _app(tmp_path)
    first = app.run(SECURITY, as_of=AS_OF, horizons=("MID",),
                    documents=_documents(), checkpoints=[_condition(review_at=utc)])
    assert app.plans_store.accept(SECURITY, "MID")[0]
    ref_before = app.plans_store.get_accepted_ref(SECURITY, "MID")
    main_before = app.plans_store.get(SECURITY, "MID").model_dump(mode="json")
    tree_before = {str(p.relative_to(tmp_path)): p.read_bytes()
                   for p in tmp_path.rglob("*") if p.is_file()}
    restarted = _app(tmp_path)
    second = restarted.run(SECURITY, as_of=AS_OF, horizons=("MID",),
                           documents=_documents(),
                           checkpoints=[_condition(review_at=shanghai)])
    assert second.run_id == first.run_id
    assert second.assessment_ids == first.assessment_ids
    assert second.draft_kinds["MID"] == "idempotent"
    assert restarted.plans_store.get_candidate(SECURITY, "MID") is None
    assert restarted.plans_store.get_accepted_ref(SECURITY, "MID") == ref_before
    assert restarted.plans_store.get(SECURITY, "MID").model_dump(mode="json") == main_before
    assert {str(p.relative_to(tmp_path)): p.read_bytes()
            for p in tmp_path.rglob("*") if p.is_file()} == tree_before
    canonical = checkpoint_summary(CheckpointResult(
        condition=_condition(review_at=shanghai), evaluation="TRUE"))
    assert canonical == checkpoint_summary(CheckpointResult(
        condition=_condition(review_at=utc), evaluation="TRUE"))
    assert second.next_checks == [canonical]
    assert "+08:00" not in canonical and "16:00" not in canonical
    assert "2026-10-30T08:00:00+00:00" in canonical
    shown = checkpoint_display(CheckpointResult(
        condition=_condition(review_at=shanghai), evaluation="TRUE"))
    assert shown.startswith(canonical)
    assert "2026-10-30T16:00:00+08:00" in shown
    assert "原始时区" not in " ".join(second.next_checks)


def test_addressing_text_includes_every_semantic_field():
    base_cp = _condition()
    base = checkpoint_summary(CheckpointResult(condition=base_cp, evaluation="TRUE"))
    payload = base_cp.semantic_payload()
    assert payload["review_at"] in base
    assert payload["security_id"] in base
    assert payload["horizon"] in base
    assert payload["proposition_type"] in base
    assert payload["description"] in base
    assert ",".join(payload["evidence_refs"]) in base
    assert payload["derived_from"] in base
    assert payload["refute_if"] in base
    assert "已确认" in base
    assert "到期待复核" in base
    variants = [
        {"security_id": "000001"}, {"horizon": "LONG"},
        {"proposition_type": "longterm_invalidation"}, {"description": "另一核验条件"},
        {"evidence_refs": ["only-one"]}, {"derived_from": "verified_calendar"},
        {"user_confirmed": False},
        {"review_at": datetime(2026, 11, 30, 8, tzinfo=timezone.utc)},
        {"refute_if": "原件确认订单取消"}, {"expiry_action": None},
    ]
    for changes in variants:
        other = checkpoint_summary(CheckpointResult(
            condition=_condition(**changes), evaluation="TRUE"))
        assert other != base, changes


@pytest.mark.parametrize("changes", [
    {"review_at": datetime(2026, 11, 30, 8, tzinfo=timezone.utc)},
    {"expiry_action": None},
])
def test_review_time_or_expiry_change_still_creates_candidate(tmp_path, changes):
    app = _app(tmp_path)
    first = app.run(SECURITY, as_of=AS_OF, horizons=("MID",),
                    documents=_documents(), checkpoints=[_condition()])
    assert app.plans_store.accept(SECURITY, "MID")[0]
    main_before = app.plans_store.get(SECURITY, "MID").model_dump(mode="json")
    second = app.run(SECURITY, as_of=AS_OF, horizons=("MID",),
                     documents=_documents(), checkpoints=[_condition(**changes)])
    assert second.run_id != first.run_id
    assert second.assessment_ids != first.assessment_ids
    assert second.draft_kinds["MID"] == "candidate"
    assert app.plans_store.get(SECURITY, "MID").model_dump(mode="json") == main_before
    assert app.plans_store.get_candidate(SECURITY, "MID").assessment_id == second.assessment_ids["MID"]


def test_semantic_change_creates_candidate_and_preserves_accepted_main(tmp_path):
    app = _app(tmp_path)
    first = app.run(SECURITY, as_of=AS_OF, horizons=("MID",),
                    documents=_documents(), checkpoints=[_condition()])
    assert app.plans_store.accept(SECURITY, "MID")[0]
    main_before = app.plans_store.get(SECURITY, "MID").model_dump(mode="json")
    second = app.run(SECURITY, as_of=AS_OF, horizons=("MID",),
                     documents=_documents(), checkpoints=[_condition(refute_if="原件确认订单取消")])
    assert second.run_id != first.run_id
    assert second.assessment_ids != first.assessment_ids
    assert second.draft_kinds["MID"] == "candidate"
    assert app.plans_store.get(SECURITY, "MID").model_dump(mode="json") == main_before
    assert app.plans_store.get_candidate(SECURITY, "MID").assessment_id == second.assessment_ids["MID"]
