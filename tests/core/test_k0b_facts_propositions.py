"""K0b 事实核验绑定/命题规则/快照主体边界回归测试（plan/fusion iteration4 K0b）

锁死语义（架构师深审 D1×4/D5/D6——反例先红后绿）：
1. D1 词元与关键词不能核定同一个事实：数值与主张谓词必须绑定到**同一原文局部**
   （子句）；带符号数值绑定（摘录负值≠主张正值）；小数边界（0.900≠900）；
   否定对象绑定（无新订单≠不存在偿债风险）。无可靠解析→摘录级（不靠加关键词）
2. D5 能力状态与命题评价分开：FactorResult.OK 只表示可计算；多期持续性命题需要
   ≥2 期方向一致序列证据（单期不得建立）；无确定性规则的命题默认 UNKNOWN
3. D6 快照主体边界：财务记录要求精确主体匹配；错主体记录不入快照（显式 scope 除外）
4. 因子输入谱系：派生因子带成员报告期/公布时点/evidence_ids（K0b-3）

跑法：pytest tests/core/test_k0b_facts_propositions.py -q
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.claim_extraction import (
    ClaimRecord,
    ClaimRelation,
    SourceDocument,
    VerificationLevel,
    verify_claim_tiered,
)
from src.core.factor_compute import cash_conversion_v1, roe_observed_v1
from src.core.research import AssertionEvaluation, ThesisAssertion, evaluate_assertion
from src.data.research_snapshot import EvidenceRecord, EvidenceSnapshot

NOW = datetime(2026, 9, 27, 8, tzinfo=timezone.utc)
PUB = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _claim(body, statement, **kw):
    doc = SourceDocument(canonical_uri="probe://synthetic",
                         content_hash=SourceDocument.body_hash(body),
                         security_ids=["600000"], published_at=PUB, body=body)
    item = ClaimRecord(security_id="600000", subject="600000", statement=statement,
                       citation_uri=doc.canonical_uri, citation_hash=doc.content_hash,
                       quote_text=body, published_at=PUB, **kw)
    return item, doc


def _verify(body, statement, **kw):
    item, doc = _claim(body, statement, **kw)
    return verify_claim_tiered(item, [doc], as_of=NOW)


# ── 1. D1：四反例不再 FACT_CHECKED ─────────────────────────

def test_d1a_metric_binding_number_of_other_metric_not_fact_checked():
    """D1a：营收100/净利润10 的原文不能核定「净利润100万元」——数值与谓词必须
    绑定到同一原文局部（旧实现谓词关键词全局共现即通过）。"""
    r = _verify("公司营收100万元，净利润10万元。", "公司净利润100万元",
                event_type="earnings", value=100, unit="万元")
    assert r.level is not VerificationLevel.FACT_CHECKED, \
        f"错配数值不得 FACT_CHECKED: {r.level}"
    assert r.level is VerificationLevel.EXCERPT_GROUNDED, \
        "无可靠解析→摘录级（诚实缺项），不是 NEEDS_REVIEW 语义"


def test_d1b_sign_binding_negative_text_not_support_positive_claim():
    """D1b：摘录为净利润-100万元不能核定「净利润100万元」——带符号数值绑定。"""
    r = _verify("公司净利润-100万元。", "公司净利润100万元",
                event_type="earnings", value=100, unit="万元")
    assert r.level is not VerificationLevel.FACT_CHECKED
    assert r.level is VerificationLevel.REJECTED, "摘录符号与主张相反=已知错误"


def test_d1c_decimal_boundary_fraction_not_fact_checked():
    """D1c：0.900万元 不得命中 900万元（小数边界）——不能核定「签订900万元订单」。"""
    r = _verify("公司签订0.900万元订单。", "公司签订900万元订单",
                event_type="order", value=900, unit="万元")
    assert r.level is not VerificationLevel.FACT_CHECKED


def test_d1d_negation_object_binding():
    """D1d：「本期无新订单」不能核定「公司不存在偿债风险」——否定对象必须绑定
    （同一事件类型谓词同现于主张与摘录）。"""
    r = _verify("本期无新订单。", "公司不存在偿债风险",
                event_type="order", negation_flag=True)
    assert r.level is not VerificationLevel.FACT_CHECKED
    assert r.level is VerificationLevel.EXCERPT_GROUNDED


# ── 2. D1 正向合法资料不误封（K0b 同时保住 J0 合法路径）────

def test_d1_positive_single_clause_still_fact_checked():
    """正例：单句单主体单数值 → FACT_CHECKED（N1/J0 合法路径保持）。"""
    r = _verify("公司签订900万元订单。", "公司签订900万元订单",
                event_type="order", value=900, unit="万元")
    assert r.level is VerificationLevel.FACT_CHECKED, f"合法正例被误封: {r.failures}"


def test_d1_positive_multi_clause_bound_clause_still_fact_checked():
    """正例：多子句摘录中与谓词同子句的数值仍可核定（顿号分句）。"""
    r = _verify("公司实现营收1000万元、净利润100万元。", "公司净利润100万元",
                event_type="earnings", value=100, unit="万元")
    assert r.level is VerificationLevel.FACT_CHECKED, f"同子句绑定被误杀: {r.failures}"


def test_d1_positive_thousands_separator_and_negative_claim():
    """正例：千分位形态（1,900万元）与带负号负值主张（-100万元）均可核定。"""
    r = _verify("公司签订1,900万元订单。", "公司签订1,900万元订单",
                event_type="order", value=1900, unit="万元")
    assert r.level is VerificationLevel.FACT_CHECKED, f"千分位被误杀: {r.failures}"
    r2 = _verify("公司净利润-100万元。", "公司净利润-100万元",
                 event_type="earnings", value=-100, unit="万元")
    assert r2.level is VerificationLevel.FACT_CHECKED, f"带符号负值被误杀: {r2.failures}"


def test_d1b_interval_upper_bound_not_misjudged_as_negative():
    """监督审查 P2-5 回归：区间上界（净利润100-200万元，主张取 200）合法可核定——
    负号前后皆数字=区间连接符，不是负值标记；负值反例仍 REJECTED。"""
    r = _verify("公司净利润100-200万元。", "公司净利润200万元",
                event_type="earnings", value=200, unit="万元")
    assert r.level is VerificationLevel.FACT_CHECKED, f"区间上界被误封: {r.failures}"
    r2 = _verify("公司净利润-100万元。", "公司净利润100万元",
                 event_type="earnings", value=100, unit="万元")
    assert r2.level is VerificationLevel.REJECTED, "真负值主张正值仍必须拒绝"


def test_d1_positive_negation_same_object_still_fact_checked():
    """正例：否定对象一致（无新订单 ↔ 无新订单）→ FACT_CHECKED（CV-12 语义保持）。"""
    r = _verify("公司本期无新订单。", "公司本期无新订单",
                event_type="order", negation_flag=True)
    assert r.level is VerificationLevel.FACT_CHECKED, f"合法否定被误杀: {r.failures}"


def test_d1_unregistered_negation_type_capped():
    """闭集纪律：否定型主张的事件类型未注册确定性句式 → 摘录级（不加关键词扩闭集）。"""
    r = _verify("公司无重大变化。", "公司无重大变化",
                event_type="other", negation_flag=True)
    assert r.level is not VerificationLevel.FACT_CHECKED


# ── 3. D5：能力状态 ≠ 命题成立 ─────────────────────────────

class _FactorStub:
    """单期因子替身（status=OK 只表示可计算；provenance 仅单期观察）。"""

    def __init__(self, value, status="OK", periods=None, values=None):
        self.factor_id = "x"
        self.value, self.status, self.note = value, status, ""
        self.unit = "倍"
        self.components = {}
        self.provenance = {"periods_seen": periods or ["2026-06-30"],
                           "values_by_period": values or {}}


def _cash_sustainability_assertion():
    return ThesisAssertion(security_id="600519", horizon="LONG",
                           proposition_type="cash_sustainability",
                           description="盈利和现金持续性已建立", importance="required",
                           evidence_requirements=["cash_conversion_v1", "roe_observed_v1"])


def test_d5_single_period_ok_factors_do_not_establish_sustainability():
    """D5 主反例：单期 CFO/ROE status=OK → 多期持续性命题必须 UNKNOWN（不得 TRUE），
    求值说明解释缺口（多期需要 ≥2 期方向一致序列）。"""
    caps = {"cash_conversion_v1": _FactorStub(-2), "roe_observed_v1": _FactorStub(-0.1)}
    out = evaluate_assertion(_cash_sustainability_assertion(),
                             available_capabilities=caps, verified_evidence_ids=set(),
                             as_of=NOW)
    assert out.evaluation is AssertionEvaluation.UNKNOWN, \
        f"单期 OK 不得建立多期持续性: {out.evaluation}"
    assert "期" in (out.evaluation_note or ""), "UNKNOWN 必须解释缺口（人话）"
    assert out.evaluation_rule_version, "命题规则版本必须登记"


def test_d5_multiperiod_consistent_series_establishes_sustainability():
    """正例：≥2 期方向一致的 CFO/ROE 序列 → 规则判 TRUE（规则可建立路径存在）。"""
    caps = {
        "cash_conversion_v1": _FactorStub(1.2, periods=["2026-03-31", "2026-06-30"],
                                          values={"2026-03-31": 1.1, "2026-06-30": 1.2}),
        "roe_observed_v1": _FactorStub(0.08, periods=["2026-03-31", "2026-06-30"],
                                        values={"2026-03-31": 0.07, "2026-06-30": 0.08}),
    }
    out = evaluate_assertion(_cash_sustainability_assertion(),
                             available_capabilities=caps, verified_evidence_ids=set(),
                             as_of=NOW)
    assert out.evaluation is AssertionEvaluation.TRUE, f"多期一致序列应 TRUE: {out.evaluation_note}"


def test_d5_multiperiod_inconsistent_direction_not_established():
    """反例：多期但方向不一致（某期 ≤0）→ UNKNOWN。"""
    caps = {
        "cash_conversion_v1": _FactorStub(1.2, periods=["2026-03-31", "2026-06-30"],
                                          values={"2026-03-31": -0.5, "2026-06-30": 1.2}),
        "roe_observed_v1": _FactorStub(0.08, periods=["2026-03-31", "2026-06-30"],
                                        values={"2026-03-31": 0.07, "2026-06-30": 0.08}),
    }
    out = evaluate_assertion(_cash_sustainability_assertion(),
                             available_capabilities=caps, verified_evidence_ids=set(),
                             as_of=NOW)
    assert out.evaluation is AssertionEvaluation.UNKNOWN


def test_d5_no_rule_proposition_defaults_unknown_with_reason():
    """无确定性规则的命题默认 UNKNOWN——能力满足不再是 TRUE（资本约束不因分量存在为真）。"""
    a = ThesisAssertion(security_id="600519", horizon="LONG",
                        proposition_type="capital_constraint",
                        description="资本约束可承受", importance="required",
                        evidence_requirements=["balance_risk_v1"])
    out = evaluate_assertion(a, available_capabilities={"balance_risk_v1": _FactorStub(1.0)},
                             verified_evidence_ids=set(), as_of=NOW)
    assert out.evaluation is AssertionEvaluation.UNKNOWN
    assert "规则" in (out.evaluation_note or "")


def test_d5_verified_claim_path_unchanged():
    """已核验主张支撑路径（J0b）语义不变——claims 驱动的命题仍可 TRUE（不误封）。"""
    a = ThesisAssertion(security_id="600519", horizon="MID",
                        proposition_type="change_to_profit",
                        description="订单进入收入", importance="required",
                        supporting_evidence_ids=["c1"])
    out = evaluate_assertion(a, available_capabilities={}, verified_evidence_ids={"c1"},
                             as_of=NOW)
    assert out.evaluation is AssertionEvaluation.TRUE


# ── 4. K0b-3：因子输入谱系 ─────────────────────────────────

def test_factor_result_carries_provenance():
    """派生因子返回成员报告期/公布时点/evidence_ids/口径（不只绑定顶层 snapshot_id）。"""
    records = [{"metric": "CFOToNP", "value": 1.2, "period_end": "2026-06-30",
                "published_at": "2026-08-20", "evidence_id": "ev-1"},
               {"metric": "netProfit", "value": 500000, "period_end": "2026-06-30",
                "published_at": "2026-08-20", "evidence_id": "ev-2"},
               {"metric": "roeAvg", "value": 0.08, "period_end": "2026-06-30",
                "published_at": "2026-08-20", "evidence_id": "ev-3"}]
    out = cash_conversion_v1(records)
    assert out.status == "OK" and out.value == pytest.approx(1.2)
    prov = out.provenance
    assert prov["inputs"]["CFOToNP"]["period_end"] == "2026-06-30"
    assert prov["inputs"]["CFOToNP"]["published_at"] == "2026-08-20"
    assert "ev-1" in prov["inputs"]["CFOToNP"]["evidence_ids"]
    assert prov["inputs"]["netProfit"]["period_end"] == "2026-06-30"
    roe = roe_observed_v1(records)
    assert roe.status == "OK" and roe.value == pytest.approx(0.08)
    assert roe.provenance["inputs"]["roeAvg"]["period_end"] == "2026-06-30"
    assert "ev-3" in roe.provenance["inputs"]["roeAvg"]["evidence_ids"]


# ── 5. D6：快照主体边界 ────────────────────────────────────

def _fin_rec(security_id, metric="roeAvg", value=12.0):
    return EvidenceRecord(source_kind="financial", security_id=security_id,
                          metric_or_claim=metric, value=value, published_at=PUB,
                          available_at=PUB, fetched_at=PUB)


def test_d6_snapshot_rejects_foreign_subject_financial_record():
    """D6 主反例：600519 快照收进 000002 财务记录 → 必须拒收（strict/live 都不入）。"""
    r = _fin_rec("000002")
    snap = EvidenceSnapshot.build("600519", NOW, [r], strict=False)
    assert snap.records == [], f"错主体财务记录不得入快照: {[x.security_id for x in snap.records]}"
    assert snap.dropped_pit == 1 and snap.dropped_ids == [r.evidence_id]
    assert "subject_mismatch" in snap.drop_reasons, f"拒收原因必须可追溯: {snap.drop_reasons}"
    snap_strict = EvidenceSnapshot.build("600519", NOW, [_fin_rec("000002")], strict=True)
    assert snap_strict.records == []


def test_d6_snapshot_keeps_matching_subject_and_scoped_records():
    """正例：同主体财务记录保留；显式行业/宏观 scope 记录不被主体规则误杀。"""
    snap = EvidenceSnapshot.build("600519", NOW, [_fin_rec("600519")], strict=False)
    assert len(snap.records) == 1
    industry = EvidenceRecord(source_kind="financial", security_id="",
                              metric_or_claim="industry_gdp", value=5.0,
                              subject_scope="industry", published_at=PUB,
                              available_at=PUB, fetched_at=PUB)
    snap2 = EvidenceSnapshot.build("600519", NOW, [industry], strict=False)
    assert len(snap2.records) == 1, "显式 industry scope 的宏观/行业记录应放行"


def test_d6_snapshot_rejects_cross_subject_announcement():
    """跨主体公告同样不得混入（错公司公告不能支撑本主体快照）。"""
    ann = EvidenceRecord(source_kind="announcement", security_id="000002",
                         metric_or_claim="major_contract", value=1,
                         published_at=PUB, available_at=PUB, fetched_at=PUB)
    snap = EvidenceSnapshot.build("600519", NOW, [ann], strict=False)
    assert snap.records == []
    assert "subject_mismatch" in snap.drop_reasons
