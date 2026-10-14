"""G3 checkpoint contract through real research, persisted plans and read-only consumers.

Fixtures are synthetic company evidence; none is a real MID research sample.
"""
from datetime import datetime, timezone

import pytest

from src.core.research import CheckpointCondition
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
