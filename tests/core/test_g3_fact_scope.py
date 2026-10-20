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
           quote_text=None, value=100, unit="万元", **updates):
    doc = SourceDocument(canonical_uri="synthetic://g3", body=body,
                         content_hash=SourceDocument.body_hash(body),
                         security_ids=["600000"], published_at=published_at)
    excerpt = body if quote_text is None else quote_text
    claim = ClaimRecord(security_id="600000", subject="600000",
                        statement=body if statement is None else statement,
                        event_type=event_type, value=value, unit=unit,
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
    assert TYPED_RULE_VERSION == "g3.binding_v4"
    assert CLAIM_VERIFICATION_PROTOCOL_VERSION == "cv6"


@pytest.mark.parametrize("body,statement,event_type,value,unit", [
    ("公司收入100万元。", "公司收入2100万元", "earnings", 100, "万元"),
    ("公司净利润100万元。", "公司净利润-100万元", "earnings", 100, "万元"),
    ("公司收入占比100%。", "公司收入占比200%", "exposure", 100, "%"),
])
def test_r01_amount_must_match_as_token_sign_and_unit(body, statement, event_type, value, unit):
    result = verify(body, statement, event_type, value=value, unit=unit)
    assert result.level is not VerificationLevel.FACT_CHECKED, result.failures
    assert "source_fact_scope_bound" not in result.checks


@pytest.mark.parametrize("body,statement,event_type,value,unit", [
    ("公司净利润-100万元。", "公司净利润-100万元", "earnings", -100, "万元"),
    ("公司签订1,900万元订单。", "公司签订1,900万元订单", "order", 1900, "万元"),
    ("公司收入1亿元。", "公司收入10000万元", "earnings", 1, "亿元"),
    ("公司收入100元。", "公司收入100元", "earnings", 100, "元"),
    ("公司收入1亿元。", "公司收入1亿元", "earnings", 1, "亿元"),
    ("公司收入占比100%。", "公司收入占比100%", "exposure", 100, "%"),
    ("公司收入2倍。", "公司收入2倍", "earnings", 2, "倍"),
    ("公司收入100股。", "公司收入100股", "earnings", 100, "股"),
    ("公司收入1万股。", "公司收入1万股", "earnings", 1, "万股"),
    ("公司收入1亿股。", "公司收入1亿股", "earnings", 1, "亿股"),
    ("公司收入1亿股。", "公司收入10000万股", "earnings", 1, "亿股"),
])
def test_r01_supported_quantity_forms_stay_fact_checked(body, statement, event_type, value, unit):
    result = verify(body, statement, event_type, value=value, unit=unit)
    assert result.level is VerificationLevel.FACT_CHECKED, result.failures


@pytest.mark.parametrize("body,statement,event_type,quote", [
    ("按特殊口径，公司收入100万元。", "公司收入100万元", "earnings", "收入100万元"),
    ("预计，公司收入100万元。", "公司收入100万元", "earnings", "收入100万元"),
    ("公司未签订，100万元订单。", "公司100万元订单", "order", "100万元订单"),
    ("公司收入100万元。该收入仅为2024年公司境外磷酸铁锂业务收入。",
     "公司收入100万元", "earnings", "收入100万元"),
])
def test_r03_unknown_modifier_or_anaphora_is_not_absent(body, statement, event_type, quote):
    result = verify(body, statement, event_type, quote_text=quote)
    assert result.level is not VerificationLevel.FACT_CHECKED, result.failures
    assert "source_fact_scope_bound" not in result.checks


def test_statement_amount_must_match_structured_value():
    result = verify(
        "2024年公司境外磷酸铁锂业务收入100万元。",
        "2024年公司境外磷酸铁锂业务收入200万元",
        quote_text="业务收入100万元")
    assert result.level is not VerificationLevel.FACT_CHECKED
    assert "source_fact_scope_bound" not in result.checks


def test_profit_substring_is_not_net_profit():
    result = verify("公司利润100万元。", "公司净利润100万元",
                    event_type="earnings", quote_text="利润100万元")
    assert result.level is not VerificationLevel.FACT_CHECKED


def test_same_metric_positive_still_fact_checked():
    result = verify("公司利润100万元。", "公司利润100万元", event_type="earnings")
    assert result.level is VerificationLevel.FACT_CHECKED, result.failures


def test_later_sentence_qualification_is_not_absent():
    result = verify(
        "公司收入100万元；上述收入仅为2024年公司境外磷酸铁锂业务收入。",
        "公司收入100万元", event_type="earnings", quote_text="收入100万元")
    assert result.level is not VerificationLevel.FACT_CHECKED


def test_unrelated_later_sentence_stays_positive():
    result = verify("公司收入100万元；公司签订新订单。", "公司收入100万元",
                    event_type="earnings", quote_text="收入100万元")
    assert result.level is VerificationLevel.FACT_CHECKED, result.failures


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


@pytest.mark.parametrize("reference,expected", [
    ("该净利润", VerificationLevel.EXCERPT_GROUNDED),
    ("该营收", VerificationLevel.FACT_CHECKED),
])
def test_later_anaphora_binds_target_amount_metric_in_multi_fact_sentence(reference, expected):
    body = ("公司实现营收1000万元、净利润100万元。"
            + reference + "仅为2024年公司境外业务" + reference.removeprefix("该") + "。")
    result = verify(body, "公司净利润100万元", event_type="earnings",
                    quote_text="净利润100万元")
    assert result.level is expected, result.failures
    assert ("source_fact_scope_bound" in result.checks) is (expected is VerificationLevel.FACT_CHECKED)


@pytest.mark.parametrize("body,statement,quote", [
    ("公司收入2,100万元。", "公司收入100万元", "收入2,100万元"),
    ("公司收入2,100万元。", "公司收入100万元", "100万元"),
    ("公司收入100万元。", "公司收入100万股", "收入100万元"),
    ("公司收入100万元。", "公司收入100%", "收入100万元"),
])
def test_complete_quantity_required_on_each_side(body, statement, quote):
    result = verify(body, statement, "earnings", quote_text=quote)
    assert result.level is not VerificationLevel.FACT_CHECKED, result.failures
    assert "source_fact_scope_bound" not in result.checks


@pytest.mark.parametrize("relation", ["仅包含", "包括", "系指", "对应", "源自", "仅为"])
def test_same_metric_anaphora_requires_supported_binding(relation):
    body = "公司收入100万元。该收入" + relation + "2024年公司境外磷酸铁锂业务收入。"
    result = verify(body, "公司收入100万元", "earnings", quote_text="收入100万元")
    assert result.level is not VerificationLevel.FACT_CHECKED, result.failures


@pytest.mark.parametrize("reference", ["该项收入", "该 收入", "上述相关业务收入", "此处所称收入", "前述\t收入"])
def test_nonadjacent_same_metric_reference_is_not_scope_absence(reference):
    body = "公司收入100万元。" + reference + "仅为2024年公司境外磷酸铁锂业务收入。"
    result = verify(body, "公司收入100万元", "earnings", quote_text="收入100万元")
    assert result.level is not VerificationLevel.FACT_CHECKED, result.failures
    assert "source_fact_scope_bound" not in result.checks


@pytest.mark.parametrize("later", [
    "该项订单仅为2024年公司境外磷酸铁锂业务订单。",
    "该项净利润仅为2024年公司境外磷酸铁锂业务净利润。",
    "该项合同另行公告。公司营业收入有所增长。",
    "该项合同参考公司收入安排履行。",
])
def test_different_metric_reference_does_not_qualify_current_income(later):
    result = verify("公司收入100万元。" + later, "公司收入100万元", "earnings",
                    quote_text="收入100万元")
    assert result.level is VerificationLevel.FACT_CHECKED, result.failures


def test_order_metric_nonadjacent_reference_also_requires_binding():
    result = verify("公司签订100万元订单。该项订单仅包含境外业务。",
                    "公司签订100万元订单", "order", quote_text="100万元订单")
    assert result.level is not VerificationLevel.FACT_CHECKED, result.failures


@pytest.mark.parametrize("body,statement,value,unit", [
    ("公司收入0.5亿元。", "公司收入5000万元", 5000, "万元"),
    ("公司净利润-0.5亿元。", "公司净利润-5000万元", -5000, "万元"),
])
def test_fractional_unit_conversion_remains_supported(body, statement, value, unit):
    result = verify(body, statement, "earnings", value=value, unit=unit)
    assert result.level is VerificationLevel.FACT_CHECKED, result.failures


@pytest.mark.parametrize("body,statement", [
    ("公司收入50-100万元。", "公司收入100万元"),
    ("公司收入50–100万元。", "公司收入100万元"),
])
def test_range_upper_endpoint_is_not_a_realized_point(body, statement):
    assert verify(body, statement, "earnings").level is not VerificationLevel.FACT_CHECKED


@pytest.mark.parametrize("literal,value,unit", [
    ("17,421.56万元", 17421.56, "万元"),
    ("17421.5600万元", 17421.56, "万元"),
    ("100.00万元", 100, "万元"),
    ("100.0000 万元", 100, "万元"),
    ("- 17,421.56 万元", -17421.56, "万元"),
    ("123456789012345元", 123456789012345, "元"),
    ("0.01742156亿元", 174.2156, "万元"),
])
def test_complete_numeric_token_formats_are_quantity_equivalent(literal, value, unit):
    result = verify("公司收入" + literal + "。", "公司收入" + str(value) + unit,
                    "earnings", value=value, unit=unit)
    assert result.level is VerificationLevel.FACT_CHECKED, result.failures


@pytest.mark.parametrize("literal,value,unit", [
    ("17,421.56万元", 421.56, "万元"),
    ("17,421.56万元", 17421.6, "万元"),
    ("123456789012346元", 123456789012345, "元"),
    ("17,421.56万股", 17421.56, "万元"),
    ("17,42.56万元", 1742.56, "万元"),
    ("17,,421.56万元", 17421.56, "万元"),
    ("1.2.100万元", 100, "万元"),
    ("50-100.00万元", 100, "万元"),
])
def test_invalid_or_unequal_numeric_tokens_cannot_check_fact(literal, value, unit):
    result = verify("公司收入" + literal + "。", "公司收入" + str(value) + unit,
                    "earnings", value=value, unit=unit)
    assert result.level is not VerificationLevel.FACT_CHECKED, result.failures


def test_numeric_parser_preserves_original_offsets_and_negative_sign():
    from src.core.claim_extraction import _find_value_occurrences
    text = "公司净利润- 17,421.5600 万元。"
    found = _find_value_occurrences(text, -17421.56, "万元")
    assert len(found) == 1
    occurrence = found[0]
    assert text[occurrence["start"]:occurrence["end"]] == "- 17,421.5600 万元"
    assert occurrence["text_negative"] is True


def test_exact_amount_parser_does_not_grant_unparsed_fragment_qualification():
    result = verify("合计17,421.56万元。", "公司收入17421.56万元", "earnings",
                    value=17421.56)
    assert result.level is not VerificationLevel.FACT_CHECKED


def test_large_statement_difference_is_not_hidden_by_relative_float_tolerance():
    result = verify("公司收入123456789012345元。", "公司收入123456789012346元",
                    "earnings", value=123456789012345, unit="元")
    assert result.level is not VerificationLevel.FACT_CHECKED, result.failures
