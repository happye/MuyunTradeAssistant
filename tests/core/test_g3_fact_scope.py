"""G3 bounded Chinese fact scope: synthetic source data, no external IO."""
from datetime import datetime, timezone

import pytest

from src.core.claim_extraction import (
    CLAIM_VERIFICATION_PROTOCOL_VERSION, TYPED_RULE_VERSION,
    ClaimRecord, SourceDocument, VerificationLevel, verify_claim_tiered,
)

PUB = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
BODY = "2024年公司境外磷酸铁锂业务收入100万元"


def verify(body, statement=None, event_type="exposure", published_at=PUB,
           quote_text=None, **updates):
    doc = SourceDocument(canonical_uri="synthetic://g3", body=body,
                         content_hash=SourceDocument.body_hash(body),
                         security_ids=["600000"], published_at=published_at)
    excerpt = body if quote_text is None else quote_text
    claim = ClaimRecord(security_id="600000", subject="600000",
                        statement=body if statement is None else statement,
                        event_type=event_type, value=100, unit="万元",
                        fact_stage="revenue_recognized", citation_uri=doc.canonical_uri,
                        citation_hash=doc.content_hash, quote_text=excerpt,
                        published_at=PUB, **updates)
    return verify_claim_tiered(claim, [doc], as_of=NOW)


@pytest.mark.parametrize("event_type", ["exposure", "earnings"])
@pytest.mark.parametrize("statement", [
    "2025年公司境外磷酸铁锂业务收入100万元",
    "2024年公司境内磷酸铁锂业务收入100万元",
    "2024年公司全公司磷酸铁锂业务收入100万元",
    "2024年公司境外碳酸锂业务收入100万元",
])
def test_explicit_scope_conflict_rejected(event_type, statement):
    result = verify(BODY, statement, event_type)
    assert result.level is VerificationLevel.REJECTED, result.failures


@pytest.mark.parametrize("statement", [
    "公司境外磷酸铁锂业务收入100万元",
    "2024年公司磷酸铁锂业务收入100万元",
    "2024年公司境外业务收入100万元",
    "公司业务收入100万元",
])
def test_omitted_scope_capped(statement):
    result = verify(BODY, statement)
    assert result.level is VerificationLevel.EXCERPT_GROUNDED, result.failures


@pytest.mark.parametrize("body", [
    BODY, "2024年公司境外硅基新材料业务收入100万元",
    "2024年上半年公司境内储能设备业务收入100万元",
    "公司营业收入100万元", "公司净利润100万元",
])
def test_same_scope_positive(body):
    result = verify(body, event_type="earnings")
    assert result.level is VerificationLevel.FACT_CHECKED, result.failures
    assert "source_fact_scope_bound" in result.checks


@pytest.mark.parametrize("body", [
    "2024年公司境外磷酸铁锂业务收入约100万元",
    "2024年公司按特殊口径境外磷酸铁锂业务收入100万元",
    "2024年公司境外磷酸铁锂及碳酸锂业务收入100万元",
    "2024年和2025年公司境外磷酸铁锂业务收入100万元",
    "2024年公司境内及境外磷酸铁锂业务收入100万元",
    BODY + "，2025年公司境外磷酸铁锂业务收入100万元",
])
def test_unknown_or_ambiguous_scope_capped_even_identical(body):
    assert verify(body).level is not VerificationLevel.FACT_CHECKED


def test_simple_order_positive():
    result = verify("公司签订100万元订单", event_type="order")
    assert result.level is VerificationLevel.FACT_CHECKED, result.failures


# 正文带句号。quote 短于 SourceDocument.body，不能靠裁掉期间/地域/产品来证明“没有这些限定”。
TRIMMED_BODY = "2024年公司境外磷酸铁锂业务收入100万元。"


@pytest.mark.parametrize("event_type,quote,statement", [
    ("exposure", "业务收入100万元", "公司业务收入100万元"),
    ("earnings", "收入100万元", "公司收入100万元"),
])
def test_trimmed_quote_does_not_prove_scope_absent(event_type, quote, statement):
    assert len(quote) < len(TRIMMED_BODY)
    assert quote in TRIMMED_BODY
    result = verify(TRIMMED_BODY, statement, event_type, quote_text=quote)
    assert result.claim.quote_text == quote
    assert result.claim.quote_text != TRIMMED_BODY
    assert result.claim.citation_hash == SourceDocument.body_hash(TRIMMED_BODY)
    assert result.level is not VerificationLevel.FACT_CHECKED, result.failures
    assert "source_fact_scope_bound" not in result.checks


def test_explicit_conflict_takes_priority_over_another_missing_scope():
    result = verify(BODY, "公司境外碳酸锂业务收入100万元")
    assert result.level is VerificationLevel.REJECTED, result.failures


def test_business_boundary_kind_cannot_be_rewritten():
    result = verify(BODY, "2024年公司境外磷酸铁锂项目收入100万元", event_type="earnings")
    assert result.level is VerificationLevel.REJECTED, result.failures


def test_cross_clause_scope_is_not_silently_discarded():
    result = verify("2024年公司境外磷酸铁锂业务收入200万元，净利润100万元",
                    "公司净利润100万元", event_type="earnings")
    assert result.level is VerificationLevel.EXCERPT_GROUNDED, result.failures


def test_one_qualifying_clause_still_requires_single_statement():
    result = verify(BODY, BODY + "，2025年公司境外磷酸铁锂业务收入100万元")
    assert result.level is VerificationLevel.EXCERPT_GROUNDED, result.failures


def test_english_unimplemented_is_honestly_capped():
    result = verify("2024 overseas lithium revenue 100万元", event_type="earnings")
    assert result.level is not VerificationLevel.FACT_CHECKED


def test_scope_rule_and_verification_protocol_are_new_semantics():
    assert TYPED_RULE_VERSION == "g3.binding_v2"
    assert CLAIM_VERIFICATION_PROTOCOL_VERSION == "cv4"


def test_matching_sentence_does_not_bypass_publication_gate():
    assert verify(BODY, published_at=None).level is not VerificationLevel.FACT_CHECKED


def test_self_reported_scope_does_not_override_source():
    result = verify(BODY, "2025年公司境外磷酸铁锂业务收入100万元",
                    reporting_period="2024", geography="境外", business_object="磷酸铁锂")
    assert result.level is VerificationLevel.REJECTED


def test_same_scope_does_not_bypass_hash_gate():
    doc = SourceDocument(canonical_uri="synthetic://g3", body=BODY,
                         content_hash=SourceDocument.body_hash(BODY),
                         security_ids=["600000"], published_at=PUB)
    claim = ClaimRecord(security_id="600000", statement=BODY, event_type="exposure",
                        value=100, unit="万元", quote_text=BODY,
                        citation_uri=doc.canonical_uri, citation_hash="wrong-version")
    assert verify_claim_tiered(claim, [doc], as_of=NOW).level is VerificationLevel.REJECTED
