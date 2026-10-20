"""Synthetic fixed inputs only; no real company or financial claim is established."""
import dataclasses
import importlib
import json
from datetime import datetime, timezone

import pytest

from src.data.research_snapshot import EvidenceRecord, EvidenceSnapshot

prototype = importlib.import_module("plan.fusion.iteration9.g4_cash_observation_prototype")

AS_OF = datetime(2025, 5, 1, tzinfo=timezone.utc)
PUBLISHED = datetime(2024, 4, 1, tzinfo=timezone.utc)


def record(metric, year=2023, end="12-31", **changes):
    data = dict(evidence_id=f"synthetic-{metric}-{year}-{end}", source_kind="financial",
                source_uri="synthetic://fixed-input/not-a-real-company", security_id="000001",
                subject_scope="security", metric_or_claim=metric,
                value={"CFOToNP": 1.25, "netProfit": 1000000.0, "roeAvg": .12}[metric],
                unit="元" if metric == "netProfit" else "倍", period_kind="cumulative",
                period_start=f"{year}-01-01", period_end=f"{year}-{end}",
                value_kind="FLOW" if metric == "netProfit" else "RATIO", period_basis="YTD",
                underlying_period_basis=None if metric == "netProfit" else "YTD",
                metric_definition_version=f"synthetic-{metric}-definition-v1",
                semantic_status="VERIFIED", semantic_note="Synthetic fixture definition only",
                knowledge_basis="AS_PUBLISHED_ARCHIVE", document_version_id=f"synthetic-{year}-v1",
                published_at=PUBLISHED, available_at=PUBLISHED, version_available_at=PUBLISHED,
                fetched_at=AS_OF, first_seen_at=PUBLISHED, source_version="synthetic-v1",
                revision_id="original", provenance_evidence_ids=["synthetic-source-proof"])
    data.update(changes)
    return EvidenceRecord(**data)


def industry(value="non_financial", **changes):
    data = dict(evidence_id="synthetic-industry", source_kind="announcement",
                source_uri="synthetic://industry-premise", security_id="000001",
                subject_scope="security", metric_or_claim="industry_classification", value=value,
                semantic_status="VERIFIED", knowledge_basis="AS_PUBLISHED_ARCHIVE",
                available_at=PUBLISHED, version_available_at=PUBLISHED)
    data.update(changes)
    return EvidenceRecord(**data)


def snapshot(records, strict=True):
    return EvidenceSnapshot.build("000001", AS_OF, records, strict=strict)


def observe(records, *, classification="non_financial", mode="strict_history", premise=True):
    snap = snapshot(records + [industry(classification)], strict=mode == "strict_history")
    external = prototype.ExternallyQualifiedIndustryPremise(
        security_id=snap.security_id, as_of=snap.as_of, classification=classification,
        evidence_ids=("synthetic-industry",), qualification_reference="synthetic external review")
    return prototype.observe_cash(snap, industry_premise=external if premise else None, mode=mode)


def pair(year=2023, end="12-31"):
    return [record("CFOToNP", year, end), record("netProfit", year, end)]


def test_comparable_years_are_observations_never_sustainability_true():
    result = observe(pair(2022) + pair(2023) + [record("roeAvg", 2022), record("roeAvg")])
    assert result.observations_only is True
    assert result.cash_sustainability == "UNKNOWN"
    assert len(result.cash_groups) == len(result.roe_groups) == 1
    assert len(result.cash_groups[0].points) == 2
    assert "capital_expenditure" in result.gaps
    assert "working_capital" in result.gaps and "one_off_items" in result.gaps
    assert "values_by_period" not in json.dumps(dataclasses.asdict(result), default=str)


@pytest.mark.parametrize("year", [2025, 2026])
@pytest.mark.parametrize("mode", ["current", "strict_history"])
def test_unfinished_or_future_reporting_period_cannot_contribute(year, mode):
    result = observe(pair(year) + [record("roeAvg", year)], mode=mode)
    assert result.cash_groups == result.roe_groups == ()
    assert any("reporting_period_after_as_of" in gap for gap in result.gaps)
    assert result.cash_sustainability == "UNKNOWN"


@pytest.mark.parametrize("mode", ["current", "strict_history"])
def test_completed_current_year_quarter_remains_observable(mode):
    published = datetime(2025, 4, 20, tzinfo=timezone.utc)
    records = [record(metric, 2025, "03-31", published_at=published,
                      available_at=published, version_available_at=published,
                      first_seen_at=published)
               for metric in ("CFOToNP", "netProfit", "roeAvg")]
    result = observe(records, mode=mode)
    assert len(result.cash_groups) == len(result.roe_groups) == 1
    assert result.cash_groups[0].points[0].period_end == "2025-03-31"
    assert result.cash_sustainability == "UNKNOWN"


def test_q1_q3_do_not_mix_and_missing_year_is_explicit():
    result = observe(pair(2021, "03-31") + pair(2023, "03-31") + pair(2023, "09-30"))
    assert len(result.cash_groups) == 2
    assert any(g.missing_years == (2022,) for g in result.cash_groups)


def test_duplicate_capture_preserves_lineage_but_one_period():
    original = record("CFOToNP")
    duplicate = original.model_copy(update={"evidence_id": "second-capture", "fetched_at": AS_OF})
    result = observe([original, duplicate, record("netProfit")])
    point = result.cash_groups[0].points[0]
    assert len(result.cash_groups[0].points) == 1
    assert {r.evidence.evidence_id for r in point.fields} == {
        original.evidence_id, "second-capture", record("netProfit").evidence_id}
    assert all(r.snapshot_id == result.snapshot_id for r in point.fields)
    assert point.fields[0].evidence.source_uri.startswith("synthetic://")


@pytest.mark.parametrize("change", [dict(period_start=None), dict(period_basis="UNKNOWN"),
    dict(underlying_period_basis=None), dict(metric_definition_version=""),
    dict(semantic_status="UNKNOWN"), dict(unit="%"), dict(period_start="2023-1-1"),
    dict(period_start="2024-01-01"), dict(value=True), dict(value=float("nan")),
    dict(value=float("inf")), dict(subject_scope="industry", security_id="000002")])
def test_invalid_ratio_never_contributes(change):
    result = observe([record("CFOToNP", **change), record("netProfit")])
    assert result.cash_groups == () and result.gaps


@pytest.mark.parametrize("records", [[record("CFOToNP")],
    [record("CFOToNP"), record("netProfit", year=2022)],
    [record("CFOToNP"), record("netProfit", period_start="2023-04-01")],
    [record("CFOToNP"), record("netProfit", security_id="000002")],
    [record("CFOToNP"), record("netProfit", value=0)]])
def test_missing_or_mismatched_denominator_unknown(records):
    assert observe(records).cash_groups == ()


def test_conflicting_versions_are_isolated_independent_of_order():
    old = record("CFOToNP")
    new = record("CFOToNP", value=2, evidence_id="revision", revision_id="restated")
    left = observe([old, new, record("netProfit")])
    right = observe([new, old, record("netProfit")])
    assert left.cash_groups == right.cash_groups == ()
    assert any("conflict" in gap for gap in left.gaps)


def test_future_revision_rejected_by_real_snapshot_gate():
    future = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snap = snapshot(pair() + [record("CFOToNP", value=8, evidence_id="future",
                                    version_available_at=future)])
    assert "future" in snap.dropped_ids
    result = prototype.observe_cash(snap, industry_premise=None)
    assert "future" in result.dropped_evidence_ids


def test_latest_only_current_visible_historical_rejected():
    records = [r.model_copy(update={"knowledge_basis": "LATEST_WITH_PUBLICATION_DATE"}) for r in pair()]
    current = observe(records, mode="current")
    historical = observe(records)
    assert len(current.cash_groups) == 1 and historical.cash_groups == ()
    assert "latest_only_current_version" in current.gaps
    assert current.cash_groups[0].points[0].fields[0].eligibility_reasons
    live_snapshot = snapshot(records + [industry()], strict=False)
    premise = prototype.ExternallyQualifiedIndustryPremise("000001", AS_OF, "non_financial",
                ("synthetic-industry",), "synthetic external review")
    assert prototype.observe_cash(live_snapshot, industry_premise=premise,
                                  mode="strict_history").cash_groups == ()


@pytest.mark.parametrize("classification,premise,expected", [
    ("financial", True, "NOT_APPLICABLE"), ("unknown", True, "UNKNOWN"),
    ("non_financial", False, "UNKNOWN")])
def test_industry_is_not_assumed(classification, premise, expected):
    result = observe(pair(), classification=classification, premise=premise)
    assert result.industry_status == expected and result.cash_groups == ()


@pytest.mark.parametrize("change", [dict(security_id="000002"),
    dict(as_of=PUBLISHED), dict(evidence_ids=("absent",)), dict(qualification_reference="")])
def test_external_industry_premise_requires_subject_time_and_member_evidence(change):
    snap = snapshot(pair() + [industry()])
    args = dict(security_id="000001", as_of=AS_OF, classification="non_financial",
                evidence_ids=("synthetic-industry",), qualification_reference="external synthetic check")
    args.update(change)
    result = prototype.observe_cash(snap, industry_premise=prototype.ExternallyQualifiedIndustryPremise(**args))
    assert result.industry_status == "UNKNOWN" and not result.cash_groups


def test_order_duplicate_and_serialized_restart_have_stable_sequence_identity():
    records = pair(2022) + pair(2023)
    a = observe(records)
    b = observe(list(reversed(records)) + [records[0]])
    assert a.series_id == b.series_id
    snap = snapshot(records + [industry()])
    restored = EvidenceSnapshot.model_validate_json(snap.model_dump_json())
    premise = prototype.ExternallyQualifiedIndustryPremise("000001", AS_OF, "non_financial",
                ("synthetic-industry",), "synthetic external review")
    assert prototype.observe_cash(restored, industry_premise=premise).series_id == a.series_id
    assert observe(pair(2022)).series_id != a.series_id


def test_no_input_mutation_or_production_module_calls():
    import sys
    records = pair() + [record("roeAvg"), industry()]
    snap = snapshot(records)
    before = snap.model_dump_json()
    calls = []
    def profile(frame, event, arg):
        if event == "call" and frame.f_globals.get("__name__", "") in {
            "src.core.research_service", "src.core.assessment_store", "src.core.research",
            "src.core.research_application", "src.core.shadow_diff"}:
            calls.append(frame.f_code.co_name)
    sys.setprofile(profile)
    try:
        premise = prototype.ExternallyQualifiedIndustryPremise("000001", AS_OF, "non_financial",
                    ("synthetic-industry",), "synthetic external review")
        result = prototype.observe_cash(snap, industry_premise=premise)
    finally:
        sys.setprofile(None)
    assert before == snap.model_dump_json() and calls == []
    assert len(result.cash_groups) == len(result.roe_groups) == 1
    result.cash_groups[0].points[0].fields[0].evidence.value = -999
    assert before == snap.model_dump_json()
    assert result.cash_sustainability == "UNKNOWN"


def test_mutated_snapshot_identity_rejected():
    snap = snapshot(pair())
    snap.records[0].value = 900
    result = prototype.observe_cash(snap, industry_premise=None)
    assert not result.cash_groups and "snapshot_identity_mismatch" in result.gaps


@pytest.mark.parametrize("metric", ["netProfit", "roeAvg"])
@pytest.mark.parametrize("value", [True, float("nan"), float("inf")])
def test_all_numeric_fields_reject_nonfinite_and_boolean(metric, value):
    records = [r for r in pair() if r.metric_or_claim != metric]
    result = observe(records + [record(metric, value=value)])
    assert (not result.cash_groups if metric == "netProfit" else not result.roe_groups)


def test_definition_changes_do_not_mix_and_roe_is_not_roic():
    records = pair(2022) + [record("CFOToNP", metric_definition_version="different-definition"),
                            record("netProfit"), record("roeAvg")]
    result = observe(records)
    assert len(result.cash_groups) == 2
    assert result.roe_groups[0].comparison_key[0] == "roe"
    assert {f.evidence.metric_or_claim for f in result.roe_groups[0].points[0].fields} == {"roeAvg"}


def test_shared_snapshot_suspect_gate_is_used():
    bad = record("CFOToNP", semantic_status="SUSPECT")
    snap = snapshot([bad, record("netProfit"), industry()])
    assert bad.evidence_id in snap.suspect_ids and bad not in snap.records
    result = prototype.observe_cash(snap, industry_premise=None)
    assert bad.evidence_id in result.dropped_evidence_ids and not result.cash_groups


def test_same_evidence_id_conflict_cannot_be_overwritten_by_input_order():
    conflict = industry("financial")
    result = observe(pair() + [conflict])
    assert result.industry_status == "UNKNOWN" and not result.cash_groups
    result = observe(pair() + [record("CFOToNP", value=1.9)])
    assert not result.cash_groups


def test_existing_factor_consumer_retains_legacy_sequence_gap():
    from src.core.factor_compute import cash_conversion_v1, roe_observed_v1
    inputs = [dict(metric=r.metric_or_claim, **r.model_dump(exclude={"metric_or_claim"}))
              for r in pair() + [record("roeAvg")]]
    for capability in (cash_conversion_v1(inputs), roe_observed_v1(inputs)):
        assert capability.status == "OK"
        assert "values_by_period" not in capability.provenance
    assert observe(pair() + [record("roeAvg")]).cash_sustainability == "UNKNOWN"
