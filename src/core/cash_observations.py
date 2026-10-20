"""G4 offline, read-only YTD observations; never an assessment or trading input.

Only built EvidenceSnapshot members are consumed. Existing snapshot eligibility is
reapplied for the requested use; this module supplies no alternative PIT policy.
External industry qualification and field semantic verification remain premises,
not verification capabilities implemented here. Conflicting economic versions are
quarantined even if a caller believes it can order them. No source is fetched.
"""
import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from src.core.decision_contract import FactStatus
from src.data.research_snapshot import (
    EvidenceRecord, EvidenceSnapshot, assess_evidence_eligibility,
)

CONTRACT_VERSION = "g4.cash_observations.prototype.v1"


@dataclass(frozen=True)
class ExternallyQualifiedIndustryPremise:
    """Caller-supplied external qualification, NOT a production industry verifier."""
    security_id: str
    as_of: datetime
    classification: Literal["financial", "non_financial", "unknown"]
    evidence_ids: tuple[str, ...]
    qualification_reference: str


@dataclass(frozen=True)
class FieldObservation:
    snapshot_id: str
    security_id: str
    as_of: datetime
    evidence: EvidenceRecord  # deep copy, retaining ALL original fields and extras
    eligibility_status: str
    eligibility_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ObservationPoint:
    point_id: str
    period_start: str
    period_end: str
    fields: tuple[FieldObservation, ...]


@dataclass(frozen=True)
class ComparableGroup:
    group_id: str
    comparison_key: tuple[str, ...]
    points: tuple[ObservationPoint, ...]
    missing_years: tuple[int, ...]


@dataclass(frozen=True)
class ObservationResult:
    contract_version: str
    snapshot_id: str
    security_id: str
    as_of: datetime
    mode: str
    series_id: str
    industry_status: str
    industry_premise: ExternallyQualifiedIndustryPremise | None
    industry_verification: str
    dropped_evidence_ids: tuple[str, ...]
    gaps: tuple[str, ...]
    cash_groups: tuple[ComparableGroup, ...]
    roe_groups: tuple[ComparableGroup, ...]
    observations_only: bool = True
    cash_sustainability: str = "UNKNOWN"


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def _period(record: EvidenceRecord) -> tuple[str, str] | None:
    start, end = getattr(record, "period_start", None), record.period_end
    if not all(isinstance(p, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p)
               for p in (start, end)):
        return None
    try:
        left, right = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return None
    # This bounded prototype only supports explicitly defined calendar-year YTD.
    # No period start is inferred from the end or from period_kind=cumulative.
    if (left.year != right.year or left.month != 1 or left.day != 1
            or end[5:] not in {"03-31", "06-30", "09-30", "12-31"}):
        return None
    return start, end


def _field_key(record: EvidenceRecord) -> tuple[str, ...]:
    return (record.metric_or_claim, record.unit, record.currency,
            record.value_kind, record.period_basis, record.underlying_period_basis or "",
            record.metric_definition_version)


def _field_problem(record: EvidenceRecord, security: str) -> str | None:
    if record.security_id != security or record.subject_scope not in {"security", "unknown"}:
        return "subject_mismatch"
    if record.source_kind != "financial" or record.quality_status != FactStatus.OBSERVED:
        return "not_observed_financial"
    if (record.semantic_status != "VERIFIED" or not record.metric_definition_version
            or not record.source_uri or not record.source_version):
        return "missing_field_definition_or_source"
    if _period(record) is None:
        return "missing_or_unsupported_period"
    if record.period_basis != "YTD" or record.period_kind != "cumulative":
        return "unsupported_field_basis"
    if record.metric_or_claim == "netProfit":
        if record.value_kind != "FLOW" or record.unit != "元":
            return "unsupported_profit_definition_or_unit"
    elif (record.value_kind != "RATIO" or record.unit != "倍"
          or record.underlying_period_basis != "YTD"):
        return "unsupported_ratio_definition_or_unit"
    if isinstance(record.value, bool) or not isinstance(record.value, (int, float)):
        return "non_numeric_value"
    if not math.isfinite(record.value):
        return "non_finite_value"
    return None


def _industry_status(snapshot, premise):
    if (not isinstance(premise, ExternallyQualifiedIndustryPremise)
            or premise.security_id != snapshot.security_id or premise.as_of != snapshot.as_of
            or not premise.qualification_reference.strip() or not premise.evidence_ids
            or premise.classification not in {"financial", "non_financial"}):
        return "UNKNOWN"
    members = {r.evidence_id: r for r in snapshot.records}
    for evidence_id in premise.evidence_ids:
        record = members.get(evidence_id)
        matches = [r for r in snapshot.records if r.evidence_id == evidence_id]
        if (record is None or record.security_id != snapshot.security_id
                or record.subject_scope != "security" or not record.source_uri
                or record.metric_or_claim != "industry_classification"
                or record.value != premise.classification or record.semantic_status != "VERIFIED"
                or record.quality_status != FactStatus.OBSERVED
                or len({r.content_fingerprint() for r in matches}) != 1):
            return "UNKNOWN"
    return "NOT_APPLICABLE" if premise.classification == "financial" else "NON_FINANCIAL_EXTERNAL_PREMISE"


def _groups(kind, periods, snapshot, mode, gaps):
    groups = defaultdict(list)
    for (start, end), metrics in sorted(periods.items()):
        required = ("CFOToNP", "netProfit") if kind == "cash" else ("roeAvg",)
        if not any(metric in metrics for metric in required):
            continue
        if any(not metrics.get(metric) for metric in required):
            gaps.add(f"{kind}:{end}:missing_paired_field")
            continue
        fields = []
        keys = []
        identities = []
        conflict = False
        for metric in required:
            records = metrics[metric]
            # Values agreeing under identical field definitions can share a point;
            # distinct captures and source revisions remain visible in its fields.
            signatures = {_hash((_field_key(r), r.value)) for r in records}
            if len(signatures) != 1:
                gaps.add(f"{kind}:{end}:{metric}:conflicting_versions")
                conflict = True
                break
            keys.extend(_field_key(records[0]))
            for r in sorted(records, key=lambda r: (r.evidence_id, r.content_fingerprint())):
                eligibility = assess_evidence_eligibility(r, as_of=snapshot.as_of,
                                                         strict=mode == "strict_history")
                reasons = set(eligibility.reasons)
                if mode == "current":
                    historical = assess_evidence_eligibility(r, as_of=snapshot.as_of, strict=True)
                    if historical.status != "STRICT_ELIGIBLE":
                        reasons.update(historical.reasons)
                fields.append(FieldObservation(snapshot.snapshot_id, snapshot.security_id,
                    snapshot.as_of, r.model_copy(deep=True), eligibility.status,
                    tuple(sorted(reasons))))
                identities.append(r.content_fingerprint())
        if conflict:
            continue
        if kind == "cash" and metrics["netProfit"][0].value == 0:
            gaps.add(f"cash:{end}:zero_denominator")
            continue
        comparison_key = (kind, start[5:], end[5:], *keys)
        point_id = _hash((CONTRACT_VERSION, snapshot.security_id, start, end, sorted(set(identities))))
        groups[comparison_key].append(ObservationPoint(point_id, start, end, tuple(fields)))
    result = []
    for key, points in sorted(groups.items()):
        years = {int(point.period_end[:4]) for point in points}
        missing = tuple(sorted(set(range(min(years), max(years) + 1)) - years))
        if missing:
            gaps.add(f"{kind}:{key[2]}:missing_years:{','.join(map(str, missing))}")
        result.append(ComparableGroup(_hash((CONTRACT_VERSION, snapshot.security_id, key)),
                                      key, tuple(points), missing))
    return tuple(result)


def observe_cash(snapshot: EvidenceSnapshot, *,
                 industry_premise: ExternallyQualifiedIndustryPremise | None,
                 mode: Literal["current", "strict_history"] = "strict_history") -> ObservationResult:
    """Read observations, never populate legacy factor provenance or persist state.

    Caller must explicitly select current use to display latest-only evidence.
    Unsupported definitions, non-calendar YTD, revisions with conflicting values,
    and industry qualification without member evidence stay explicit gaps.
    No minimum count, financial investment threshold, differencing or TTM is added.
    """
    if not isinstance(snapshot, EvidenceSnapshot):
        raise TypeError("input must be EvidenceSnapshot, not a raw financial projection")
    if mode not in {"current", "strict_history"}:
        raise ValueError("mode must be current or strict_history")
    gaps = {"capital_expenditure", "working_capital", "one_off_items",
            "observations_do_not_establish_profit_mechanism",
            "external_industry_qualification_not_implemented"}
    identity_valid = bool(snapshot.snapshot_id) and snapshot.snapshot_id == snapshot._content_hash()
    if not identity_valid:
        gaps.add("snapshot_identity_mismatch")
    # Reuse the actual shared snapshot gate, including security and quality checks.
    qualified = EvidenceSnapshot.build(snapshot.security_id, snapshot.as_of,
        snapshot.records if identity_valid else [], strict=mode == "strict_history")
    dropped = tuple(sorted(set(snapshot.dropped_ids + qualified.dropped_ids)))
    for reason, metrics in {**snapshot.drop_reasons, **qualified.drop_reasons}.items():
        gaps.add(f"snapshot:{reason}:{','.join(sorted(metrics))}")
    status = _industry_status(qualified, industry_premise)
    if status != "NON_FINANCIAL_EXTERNAL_PREMISE":
        gaps.add(f"industry:{status}")
    periods = defaultdict(lambda: defaultdict(list))
    id_versions = defaultdict(set)
    for record in qualified.records:
        id_versions[record.evidence_id].add(record.content_fingerprint())
    for record in qualified.records:
        if record.metric_or_claim not in {"CFOToNP", "netProfit", "roeAvg"}:
            continue
        problem = _field_problem(record, snapshot.security_id)
        if problem is None and date.fromisoformat(record.period_end) > snapshot.as_of.date():
            problem = "reporting_period_after_as_of"
        if not record.evidence_id or len(id_versions[record.evidence_id]) != 1:
            problem = "conflicting_or_missing_evidence_identity"
        if problem:
            gaps.add(f"{record.evidence_id}:{problem}")
            continue
        if record.knowledge_basis == "LATEST_WITH_PUBLICATION_DATE":
            gaps.add("latest_only_current_version")
        if status == "NON_FINANCIAL_EXTERNAL_PREMISE":
            periods[_period(record)][record.metric_or_claim].append(record)
    cash = _groups("cash", periods, snapshot, mode, gaps)
    roe = _groups("roe", periods, snapshot, mode, gaps)
    if not cash:
        gaps.add("no_qualified_cash_observations")
    if not roe:
        gaps.add("no_qualified_roe_observations")
    series_id = _hash((CONTRACT_VERSION, snapshot.security_id, snapshot.as_of, mode, status,
        [(g.group_id, [p.point_id for p in g.points]) for g in cash + roe]))
    return ObservationResult(CONTRACT_VERSION, snapshot.snapshot_id, snapshot.security_id,
        snapshot.as_of, mode, series_id, status, industry_premise,
        "EXTERNAL_PREMISE_ONLY_NOT_PRODUCTION_VERIFIED", dropped, tuple(sorted(gaps)), cash, roe)


CURRENT_CONTRACT_VERSION = 'g4.cash_observations.current_official.v2'


def observe_current_official(context, industry, *, requested_as_of=None):
    """Consume only this request's private official execution contexts.

    The seals prevent accidental JSON/snapshot replay, not hostile in-process code.
    Original COMPARATIVE and derived identities remain intact in every point.
    """
    from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
    from src.core import cash_source_qualification as source
    from src.core import cash_industry_qualification as sectors
    from src.core.cash_material_intake import _encoded
    if (not isinstance(context, source._CurrentCashContext) or context._seal is not source._CURRENT_SEAL
            or not isinstance(industry, sectors._CurrentIndustry) or industry._seal is not sectors._INDUSTRY_SEAL):
        raise TypeError('current official execution contexts required; JSON and snapshots are not credentials')
    now = datetime.now().astimezone()
    as_of = requested_as_of or now
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ValueError('aware current observation cutoff required')
    gaps = {'capital_expenditure', 'working_capital', 'one_off_items', 'cash_sustainability_unknown',
            'historical_industry_unknown', 'comparative_column_not_original_publication', 'roe_not_assessed'}
    records = list(context.snapshot.records)
    if industry.record is not None:
        records.append(industry.record)
    joint = EvidenceSnapshot.build(context.security_id, as_of, records, strict=False)
    accepted = {r.evidence_id: r for r in joint.records}
    industry_status = industry.public.get('status', 'UNKNOWN')
    if (industry.security_id != context.security_id or industry.record is None
            or industry.record.evidence_id not in accepted
            or industry.record.security_id != context.security_id
            or industry.record.metric_or_claim != 'industry_classification'
            or industry.record.source_version != sectors.VERSION
            or industry.record.source_uri != sectors._uri(context.security_id)
            or industry.record.source_document_hash != industry.public.get('response_sha256')
            or industry.record.value != industry_status):
        industry_status = 'UNKNOWN'
    if industry_status != 'CURRENT_SSE_REPORTED_NON_FINANCIAL':
        gaps.add('industry:' + industry_status)
    valid_context = (context.snapshot.snapshot_id == context.snapshot._content_hash()
        and context.snapshot.security_id == context.security_id
        and re.fullmatch('[0-9a-f]{64}', context.pdf_sha256)
        and re.fullmatch('[0-9a-f]{64}', context.material_id))
    try:
        source.validate_uri(context.source_uri)
    except ValueError:
        valid_context = False
    if not valid_context:
        gaps.add('controlled_context_identity_invalid')
    fields = defaultdict(list)
    for field in context.fields:
        fields[field.get('evidence_id')].append(field)
    buckets, id_versions = defaultdict(list), defaultdict(set)
    for r in context.snapshot.records:
        id_versions[r.evidence_id].add(r.content_fingerprint())
    for r in context.snapshot.records if valid_context else []:
        try:
            candidates = fields[r.evidence_id]
            if not candidates or len({_hash(f) for f in candidates}) != 1 or len(id_versions[r.evidence_id]) != 1:
                raise ValueError('conflicting_or_missing_field_identity')
            f = candidates[0]
            metric = r.metric_or_claim
            if metric not in {'netProfit', 'operatingCashFlow', 'CFOToNP'}:
                raise ValueError('unapproved_metric_or_derived')
            ratio = metric == 'CFOToNP'
            expected_definition = source.FORMULA_VERSION if ratio else source.FIELD_VERSION
            expected_kind, expected_unit = ('RATIO', '倍') if ratio else ('FLOW', '元')
            if (r.evidence_id not in accepted or r.security_id != context.security_id or r.subject_scope != 'security'
                    or r.source_kind != ('derived' if ratio else 'financial')
                    or r.source_version != source.SOURCE_VERSION or r.source_document_hash != context.pdf_sha256
                    or r.document_version_id != context.pdf_sha256 or r.revision_id != context.pdf_sha256
                    or r.source_uri != context.source_uri or r.quality_status != FactStatus.OBSERVED
                    or r.semantic_status != 'VERIFIED' or r.knowledge_basis != 'UNKNOWN'
                    or r.published_at is not None or r.version_available_at is not None
                    or r.available_at is None or r.available_at != r.fetched_at
                    or r.metric_definition_version != expected_definition
                    or r.value_kind != expected_kind or r.unit != expected_unit or r.raw_unit != expected_unit
                    or r.currency != 'CNY' or r.period_kind != 'cumulative'
                    or getattr(r, 'qualification_version', None) != source.CURRENT_FIELD_PROTOCOL
                    or getattr(r, 'consolidation_scope', None) != 'CONSOLIDATED'
                    or f.get('qualification_version') != source.CURRENT_FIELD_PROTOCOL
                    or f.get('consolidation_scope') != 'CONSOLIDATED'
                    or f.get('typed_status') != 'OBSERVED' or f.get('metric') != metric):
                raise ValueError('field_source_definition_or_qualification_invalid')
            period = _period(r)
            role = getattr(r, 'column_role', None)
            if (period is None or period[1][5:] != '03-31' or period[1] > as_of.date().isoformat()
                    or period != (f.get('period_start'), f.get('period_end'))
                    or role not in {'current', 'comparative'} or role != f.get('role')
                    or r.period_basis != ('YTD' if role == 'current' else 'COMPARATIVE')):
                raise ValueError('period_window_or_role_invalid')
            old_id = f.get('original_evidence_id')
            expected_id = hashlib.sha256(_encoded([source.CURRENT_FIELD_PROTOCOL, context.material_id, context.pdf_sha256, old_id])).hexdigest()
            if r.evidence_id != expected_id or getattr(r, 'original_evidence_id', None) != old_id:
                raise ValueError('successor_identity_invalid')
            decimal = Decimal(f['display_value'])
            if (not decimal.is_finite() or r.raw_value != f['display_value']
                    or isinstance(r.value, bool) or not isinstance(r.value, (float, int))
                    or source._typed(decimal) != r.value):
                raise ValueError('decimal_or_typed_value_invalid')
            if ratio:
                if (r.underlying_period_basis != 'YTD' or f.get('expression') != 'CFO / NP'
                        or f.get('formula_version') != source.FORMULA_VERSION
                        or f.get('precision') != '34 significant digits; ROUND_HALF_EVEN'
                        or getattr(r, 'formula', None) != f or r.lineage_ids != f.get('operand_evidence_ids')):
                    raise ValueError('unapproved_ratio_formula_or_lineage')
            else:
                row = getattr(r, 'field_locator', None)
                if (not isinstance(row, dict) or row.get('table') != ('NP' if metric == 'netProfit' else 'CFO')
                        or row.get('role') != role or row.get('period_start') != period[0] or row.get('period_end') != period[1]
                        or row.get('column') != (0 if role == 'current' else 1)
                        or source.numeric._number(row['token']) != decimal or f.get('decimal_value') != f['display_value']
                        or hashlib.sha256(_encoded([source.VERSION, context.material_id, context.pdf_sha256, row])).hexdigest() != old_id):
                    raise ValueError('original_amount_row_or_identity_invalid')
            buckets[period].append((r, f))
        except (ValueError, InvalidOperation, TypeError, KeyError, OverflowError) as error:
            gaps.add(r.evidence_id + ':' + str(error))
    points = []
    for (start, end), rows in sorted(buckets.items()):
        by_metric = defaultdict(dict)
        for r, field in rows:
            by_metric[r.metric_or_claim][r.evidence_id] = (r, field)
        if set(by_metric) != {'netProfit', 'operatingCashFlow', 'CFOToNP'} or any(len(v) != 1 for v in by_metric.values()):
            gaps.add(end + ':missing_or_conflicting_paired_fields')
            continue
        np, cfo, ratio = (next(iter(by_metric[k].values())) for k in ('netProfit', 'operatingCashFlow', 'CFOToNP'))
        required_ids = [cfo[0].evidence_id, np[0].evidence_id]
        denominator, numerator = Decimal(np[1]['decimal_value']), Decimal(cfo[1]['decimal_value'])
        if denominator == 0:
            gaps.add(end + ':zero_denominator')
            continue
        with localcontext() as precision:
            precision.prec = 34
            precision.rounding = ROUND_HALF_EVEN
            quotient = numerator / denominator
        old_ratio = hashlib.sha256(_encoded([source.FORMULA_VERSION,
            [cfo[1]['original_evidence_id'], np[1]['original_evidence_id']]])).hexdigest()
        if (ratio[1]['operand_evidence_ids'] != required_ids
                or ratio[1]['operands'] != {'CFO': str(numerator), 'NP': str(denominator)}
                or ratio[1]['display_value'] != str(quotient)
                or ratio[1]['original_evidence_id'] != old_ratio
                or len({f['role'] for _, f in (np, cfo, ratio)}) != 1):
            gaps.add(end + ':ratio_operands_or_decimal_recalculation_invalid')
            continue
        public_fields = [dict(field=f, evidence=r.model_dump(mode='json')) for r, f in (np, cfo, ratio)]
        points.append(dict(point_id=_hash((CURRENT_CONTRACT_VERSION, context.pdf_sha256, required_ids, ratio[0].evidence_id)),
            period_start=start, period_end=end, column_role=np[1]['role'], fields=public_fields))
    groups = []
    if industry_status == 'CURRENT_SSE_REPORTED_NON_FINANCIAL':
        if (len(points) == 2 and {p['column_role'] for p in points} == {'current', 'comparative'}
                and int(next(p for p in points if p['column_role'] == 'current')['period_end'][:4])
                == int(next(p for p in points if p['column_role'] == 'comparative')['period_end'][:4]) + 1):
            key = (context.security_id, context.pdf_sha256, '01-01', '03-31', 'CNY', '元', '倍',
                   'CONSOLIDATED', source.FIELD_VERSION, source.FORMULA_VERSION)
            groups.append(dict(group_id=_hash((CURRENT_CONTRACT_VERSION, key)), comparison_key=key, points=points))
        else:
            gaps.add('incomplete_current_and_comparative_pair')
    else:
        points = []
    return dict(contract_version=CURRENT_CONTRACT_VERSION, security_id=context.security_id, as_of=as_of.isoformat(),
        joint_snapshot=joint.model_dump(mode='json'), industry_status=industry_status,
        observations_only=True, cash_groups=groups, observation_points=[dict(
            point_id=p['point_id'], period_start=p['period_start'], period_end=p['period_end'],
            column_role=p['column_role'], evidence_ids=[f['evidence']['evidence_id'] for f in p['fields']]) for p in points],
        comparable_periods=2 if groups else 0, group_count=len(groups), gaps=sorted(gaps),
        cash_sustainability='UNKNOWN', roe_status='NOT_ASSESSED', historical_industry='UNKNOWN',
        summary=f'{len(groups)}个当前条件化可比组、{2 if groups else 0}期；仅当次SSE转述适用性；历史与持续性未知')
