"""F6 结构化事实提取与事件谱系回归测试（plan/fusion TASKS.md F6 验收条款）

锁死语义（DESIGN ADR-F06）：
1. 不凭 LLM 自报 confidence 加仓（model_confidence 只进诊断字段，无计算通路）
2. 非法引用拒收（无引用/引用不存在/未来日期/单位不合法 → ClaimVerificationError）
3. 重复新闻不重复计票（事件谱系去重；更正公告新版本不被吞）
4. 无 AI 时模板行动卡可用（确定性提取器零 AI 产出带引用 claim；F2 行动卡测试独立存在）
5. 同输入不重复付费（extract 缓存：同 hash 只调一次 _extract_impl）
6. 冻结标注集六类场景（错误实体/旧闻重炒/财报更正/上下游影响相反/缺证据/注入文本）
   全部按期望通过——注入文本不改核验结果

纯内存测试，无网络无 AI。跑法：pytest tests/core/test_claim_extraction.py -q
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.claim_extraction import (
    ClaimExtractor,
    ClaimRecord,
    ClaimRelation,
    ClaimVerificationError,
    DeterministicExtractor,
    MockLLMExtractor,
    derive_event_id,
    dedup_claims,
    verify_claim,
)

ANNO_PATH = Path(__file__).resolve().parents[1] / "ai_eval" / "claim_annotations.json"
EVIDENCE_POOL = [
    {"uri": "巨潮公告", "hash": None, "security_id": None},
    {"uri": "东方财富", "hash": None, "security_id": None},
]


def _claim(**kw) -> ClaimRecord:
    base = dict(
        security_id="600519", subject="600519", event_type="order",
        statement="公司公告获得 5 亿元订单", relation=ClaimRelation.SUPPORTS,
        occurred_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        value=5.0, unit="亿元",
        citation_uri="巨潮公告", citation_hash="abc123",
        extracted_by="test",
    )
    base.update(kw)
    return ClaimRecord(**base)


# ── 1. 不凭 LLM 自报 confidence 加仓 ─────────────────────

def test_confidence_is_diagnostic_only():
    """model_confidence 只存在于诊断字段——VerifiedClaim 与任何计算契约无 confidence
    通路（DESIGN ADR-F06：自报置信度不能直接进入仓位计算）。"""
    c = _claim(model_confidence=0.9)
    v = verify_claim(c, evidence_pool=EVIDENCE_POOL)
    assert v.claim.model_confidence == 0.9, "自报 confidence 可存诊断"
    # 核验结果不随 confidence 变化：0.9 与 0.1 的核验结论一致（不放大不衰减）
    v_low = verify_claim(_claim(model_confidence=0.1), evidence_pool=EVIDENCE_POOL)
    assert v.fact_status is v_low.fact_status
    assert set(v.checks) == set(v_low.checks)


# ── 2. 非法引用拒收 ──────────────────────────────────────

def test_no_citation_rejected():
    with pytest.raises(ClaimVerificationError, match="无引用"):
        verify_claim(_claim(citation_uri="", citation_hash=""))


def test_unknown_citation_rejected():
    with pytest.raises(ClaimVerificationError, match="引用不存在"):
        verify_claim(_claim(citation_uri="不存在的源", citation_hash="deadbeef"),
                     evidence_pool=EVIDENCE_POOL)


def test_future_date_rejected():
    with pytest.raises(ClaimVerificationError, match="未来日期"):
        verify_claim(_claim(published_at=datetime(2099, 1, 1, tzinfo=timezone.utc)),
                     evidence_pool=EVIDENCE_POOL)


def test_bad_unit_rejected():
    with pytest.raises(ClaimVerificationError, match="单位不合法"):
        verify_claim(_claim(unit="手"), evidence_pool=EVIDENCE_POOL)


def test_valid_claim_passes_all_checks():
    v = verify_claim(_claim(), evidence_pool=EVIDENCE_POOL)
    assert v.fact_status.value == "MODEL_INFERRED"  # AI 提取产出不冒充 OBSERVED
    assert set(v.checks) == {"citation_present", "citation_resolves", "no_future_date",
                             "unit_allowed", "statement_present"}


# ── 3. 重复新闻不重复计票（事件谱系）─────────────────────

def test_duplicate_news_deduped():
    a = _claim()
    b = _claim()  # 同内容转载（不同 claim_id）
    out, dropped = dedup_claims([a, b])
    assert len(out) == 1 and dropped == 1, "转载只计一票"


def test_correction_not_deduped():
    """更正公告是新版本（revision 不同→指纹不同）——不被去重吞掉（ADR-F06）。"""
    orig = _claim(value=5.0)
    correction = _claim(value=8.0, revision=2)
    out, dropped = dedup_claims([orig, correction])
    assert len(out) == 2 and dropped == 0, "更正保留两版（可审计）"


def test_same_event_id_shared_across_transports():
    eid = derive_event_id("600519", "order", datetime(2026, 9, 1, tzinfo=timezone.utc),
                          5.0, content_hash="x")
    c1 = _claim(event_id=eid)
    c2 = _claim(event_id=eid, security_id="000001")  # 同事件关联多公司
    assert c1.event_id == c2.event_id
    # 影响不复制：两 claim 各自带自家 security_id（关系/暴露各自评估）


# ── 4. 无 AI 时确定性通道可用 ────────────────────────────

def test_deterministic_extractor_offline_usable():
    ext = DeterministicExtractor()
    claims = ext.extract({"title": "公司公告获得5亿元订单", "content": "…",
                          "date": "2026-09-01", "source": "巨潮公告",
                          "security_id": "600519", "event_type": "order",
                          "value": 5.0, "unit": "亿元"})
    assert len(claims) == 1
    v = verify_claim(claims[0], evidence_pool=EVIDENCE_POOL)
    assert v.claim.extracted_by == "deterministic"
    assert v.claim.model_confidence is None, "确定性通道无自报置信度"


def test_empty_input_yields_no_claims():
    ext = DeterministicExtractor()
    assert ext.extract({"title": "", "content": ""}) == [], "无法结构化不硬凑"


# ── 5. 同输入不重复付费 ──────────────────────────────────

def test_same_input_not_double_charged():
    doc = {"title": "公告", "content": "x", "date": "2026-09-01", "source": "巨潮公告",
           "security_id": "600519"}
    scripted = DeterministicExtractor()
    scripted.extract(doc)
    scripted.extract(doc)
    scripted.extract(doc)
    assert scripted.calls == 1, "同输入 hash 命中缓存，不重复调用（不重复付费）"
    mock = MockLLMExtractor(scripted=[])
    mock.extract(doc); mock.extract(doc)
    assert mock.calls == 1, "LLM 替身同样只调一次"
    mock.reset_cache()
    mock.extract(doc)
    assert mock.calls == 2, "显式清缓存后允许重算（--refresh 语义）"


# ── 6. 冻结标注集六类场景 ────────────────────────────────

def test_frozen_annotation_set_all_cases():
    """冻结标注集（tests/ai_eval/claim_annotations.json）全部用例按期望通过。
    真实 LLM 提取器接入后按同一标注集评测（提取正确率/漏检/捏造/费用）。

    证据池构造：默认"官方库收录了该公告"（uri+hash+归属都匹配）——让核验走完
    全部规则；错误实体用例则给**真实归属方**的条目（归属不符→拒收）；
    缺证据用例的来源（股吧）不在官方库。"""
    anno = json.loads(ANNO_PATH.read_text(encoding="utf-8"))
    assert len(anno["cases"]) >= 8, "标注集六类场景 + 补充用例"
    ext = DeterministicExtractor()
    for case in anno["cases"]:
        cid, doc, expect = case["id"], dict(case["doc"]), case["expect"]
        claims = ext.extract(doc)
        if expect.get("verification") == "rejected":
            assert len(claims) == 1, cid
            claim = claims[0]
            if "引用不存在" in expect.get("reason_contains", ""):
                if cid.startswith("AN-01"):
                    # 错误实体：官方库收录的是真实归属方（688123）的公告
                    pool = [{"uri": "巨潮公告", "hash": "real_filing_hash",
                             "security_id": "688123"}]
                else:
                    # 缺证据：股吧来源不在官方库
                    pool = [{"uri": "巨潮公告", "hash": None, "security_id": None},
                            {"uri": "东方财富", "hash": None, "security_id": None}]
                with pytest.raises(ClaimVerificationError, match=expect["reason_contains"]):
                    verify_claim(claim, evidence_pool=pool)
            else:
                # 未来日期/单位：引用先放行（官方库有此条），走后续规则拦截
                pool = [{"uri": claim.citation_uri, "hash": claim.citation_hash,
                         "security_id": claim.security_id or None}]
                with pytest.raises(ClaimVerificationError, match=expect["reason_contains"]):
                    verify_claim(claim, evidence_pool=pool)
            continue
        assert claims, f"{cid} 应产出 claim"
        c = claims[0]
        if expect.get("relation_neutral"):
            assert c.relation is ClaimRelation.NEUTRAL, \
                f"{cid} 上下游未定必须中性（禁止自动 SUPPORTS）"
        if expect.get("no_action_leak"):
            # 注入文本：核验结果只由结构化字段决定，statement 保持公告标题（指令未进入）
            pool = [{"uri": c.citation_uri, "hash": c.citation_hash,
                     "security_id": c.security_id or None}]
            v = verify_claim(c, evidence_pool=pool)
            assert "满仓" not in v.claim.statement and "目标价" not in v.claim.statement, \
                f"{cid} 注入指令泄漏进 statement"
            assert "立即" not in v.claim.statement


def test_old_news_resubmission_dedup():
    """AN-02 旧闻重炒场景的完整路径：同事件第二次推送 → 谱系去重。"""
    first = DeterministicExtractor().extract(
        {"title": "公司获大额订单", "content": "…", "date": "2025-06-01",
         "source": "东方财富", "security_id": "600519", "event_type": "order"})
    resub = DeterministicExtractor().extract(
        {"title": "公司获大额订单", "content": "…", "date": "2025-06-01",
         "source": "东方财富", "security_id": "600519", "event_type": "order"})
    out, dropped = dedup_claims(first + resub)
    assert dropped == 1 and len(out) == 1, "旧闻重炒不重复计票"


def test_correction_case_not_deduped_from_annotation():
    """AN-03 财报更正：revision=2 的更正与原版并存（指纹不同）。"""
    anno = json.loads(ANNO_PATH.read_text(encoding="utf-8"))
    doc = next(c for c in anno["cases"] if c["id"] == "AN-03-财报更正")["doc"]
    original = _claim(event_type="forecast", value=1.0, unit="元",
                      citation_uri="巨潮公告", citation_hash="old")
    ext = DeterministicExtractor()
    correction = ext.extract(doc)
    out, dropped = dedup_claims([original] + correction)
    assert dropped == 0 and len(out) == 2, "更正公告不被去重吞掉"


# ── 7. F6 对抗审查修复回归锁 ─────────────────────────────

def test_naive_datetime_claim_rejected_at_construction():
    """P1-3 回归锁：naive datetime 构造期即拒（不炸未来日期比较）。"""
    with pytest.raises(ValidationError, match="时区"):
        _claim(published_at=datetime(2026, 9, 1))  # naive
    with pytest.raises(ValidationError, match="时区"):
        _claim(occurred_at=datetime(2026, 9, 1))


def test_wangu_unit_allowed():
    """P1-4 回归锁：万股/亿股 是合法单位（伪 token '股-万股-亿股' 已拆）。"""
    assert verify_claim(_claim(unit="万股"), evidence_pool=EVIDENCE_POOL).claim.unit == "万股"
    with pytest.raises(ClaimVerificationError, match="单位不合法"):
        verify_claim(_claim(unit="股-万股-亿股"), evidence_pool=EVIDENCE_POOL)


def test_malformed_inputs_downgrade_not_crash():
    """P1-5 回归锁：畸形日期/非法 relation/非数值 value → 降级产出（不炸不硬凑）。"""
    ext = DeterministicExtractor()
    claims = ext.extract({"title": "公告", "content": "x", "date": "2026/09/01",
                          "source": "巨潮公告", "security_id": "600519",
                          "relation": "利好", "value": "abc", "unit": "亿元"})
    assert len(claims) == 1, "畸形日期仍应产出 claim（日期降级 None）"
    assert claims[0].published_at is None, "解析不出的日期降级 None"
    assert claims[0].relation is ClaimRelation.SUPPORTS, "中文标签利好→SUPPORTS"
    assert claims[0].value is None, "非数值 value 降级 None"
    claims2 = ext.extract({"title": "公告", "content": "x", "date": "2026-09-01",
                           "source": "巨潮公告", "security_id": "600519",
                           "relation": "BULLISH_NOT_A_TAG"})
    assert claims2[0].relation is ClaimRelation.NEUTRAL, "非法 relation 降级 NEUTRAL"


def test_cross_source_repost_clustered():
    """P1-1 回归锁：改写转载（正文加了转载标记→指纹不同）在第二阶段按
    （主体/类型/发生日/归一值）聚类——同一经济事件不计两票。"""
    from src.core.claim_extraction import event_key
    original = _claim()
    repost = _claim(statement="【转载】公司公告获得 5 亿元订单——某媒体快讯")
    assert repost.content_fingerprint() != original.content_fingerprint(), "改写转载指纹不同"
    assert event_key(repost) == event_key(original), "聚类键一致（同主体/类型/发生日/归一值）"
    out, dropped = dedup_claims([original, repost])
    assert dropped == 1 and len(out) == 1, "改写转载不重复计票"


def test_correction_with_same_value_locked_by_revision():
    """P2 回归锁：更正公告改文字不改数字（revision=2 其余全同）——指纹含 revision
    不被第一阶段吞（若指纹丢 revision 此测试必红）。"""
    orig = _claim()
    correction = _claim(revision=2)
    assert correction.content_fingerprint() != orig.content_fingerprint()
    out, dropped = dedup_claims([orig, correction])
    assert dropped == 0 and len(out) == 2


def test_higher_revision_wins_second_stage():
    """P1-1 配套：第二阶段同 event_key 冲突时保留 revision 更高版本（更正优先）。"""
    older = _claim(revision=1)
    newer = _claim(revision=2, published_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    out, dropped = dedup_claims([older, newer])
    assert dropped == 0 and len(out) == 2, "更正版本保留可审计（第二阶段跳过 revision!=1）"


def test_weak_pool_entity_gap_documented():
    """P1-2 诚实边界：uri-only 弱验证池下伪造归属可穿透——本测试**锁定**该已知缺口
    （文档级池是强验证条件；接入 F7/F8 前必须换文档级证据库，见账本未完成条款）。"""
    weak_pool = [{"uri": "巨潮公告", "hash": None, "security_id": "600519"}]
    forged = _claim()  # 实为别家订单，但引用 uri 与 600519 的来源名相同
    v = verify_claim(forged, evidence_pool=weak_pool)  # 穿透（已知缺口）
    assert v.claim.security_id == "600519"
    # 强验证（文档级 hash+归属）下同 claim 被拒
    strong_pool = [{"uri": "巨潮公告", "hash": "real_filing_hash", "security_id": "688123"}]
    with pytest.raises(ClaimVerificationError, match="引用不存在"):
        verify_claim(forged, evidence_pool=strong_pool)


def test_value_unit_normalization_in_cluster_key():
    """P2 回归锁：5亿元 与 50000万元 归一后同 event_key（不因单位分裂事件）。"""
    a = _claim(value=5.0, unit="亿元")
    b = _claim(value=50000.0, unit="万元")
    from src.core.claim_extraction import event_key
    assert event_key(a) == event_key(b)
    out, dropped = dedup_claims([a, b])
    assert dropped == 1


def test_value_none_not_clustered():
    """P2 回归锁：两笔值缺失但内容不同的同日事件不误并（值 None 不参与聚类键）；
    完全相同的重发仍被第一阶段去重。"""
    a = _claim(statement="订单事件甲（金额未披露）", value=None, unit="")
    b = _claim(statement="订单事件乙（金额未披露）", value=None, unit="")
    out, dropped = dedup_claims([a, b])
    assert dropped == 0 and len(out) == 2, "值缺失不参与第二阶段（防误并）"
    same = _claim(statement="订单事件甲（金额未披露）", value=None, unit="")
    out2, dropped2 = dedup_claims([a, same])
    assert dropped2 == 1, "同文档重发仍被第一阶段去重"


# ── R2：分级核验协议（cv2，DATA_TRUST §4）──────────────────

def test_verify_claim_without_pool_does_not_claim_resolves():
    """探针 P3 回归：无 evidence_pool 时不产生 citation_resolves（未执行的来源解析
    不得记为通过）。"""
    v = verify_claim(_claim())
    assert "citation_resolves" not in v.checks
    v2 = verify_claim(_claim(), evidence_pool=EVIDENCE_POOL)
    assert "citation_resolves" in v2.checks, "带池行为不变（E5 兼容）"


def test_tiered_rejects_contradictory_claim_with_quote():
    """探针 P2 回归：正文「no new order」与正面主张 → REJECTED（结构核验≠内容核验）。"""
    from src.core.claim_extraction import SourceDocument, VerificationLevel, verify_claim_tiered
    body = "Company reports no new order."
    doc = SourceDocument(canonical_uri="probe://doc",
                         content_hash=SourceDocument.body_hash(body),
                         security_ids=["600000"], body=body,
                         published_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    c = _claim(security_id="600000", subject="600000", citation_uri="probe://doc",
               citation_hash=doc.content_hash,
               statement="Company won a new order worth 9000000 yuan.",
               value=9000000, unit="元",
               published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
               quote_text="no new order")
    r = verify_claim_tiered(c, [doc], as_of=datetime(2024, 6, 1, tzinfo=timezone.utc))
    assert r.level is VerificationLevel.REJECTED
    assert any("摘录正文相反" in f for f in r.failures)


def test_tiered_title_never_counts_as_body():
    """SourceDocument 内容 hash 只对正文——标题不冒充已读全文；无正文时内容核验不可达。"""
    from src.core.claim_extraction import SourceDocument, VerificationLevel, verify_claim_tiered
    body = "正文内容：与某客户签订 900 万元销售订单。"
    doc = SourceDocument(canonical_uri="cninfo://x", content_hash=SourceDocument.body_hash(body),
                         security_ids=["600519"], body=body,
                         published_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    c = _claim(citation_uri="cninfo://x", citation_hash=doc.content_hash,
               quote_text="900 万元销售订单", value=900, unit="万元")
    r = verify_claim_tiered(c, [doc], as_of=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert r.level is VerificationLevel.FACT_CHECKED
    # 空 body（doc 侧正文缺失）→ NEEDS_REVIEW（TASKS 暂停点：正文不可得保留人工通道）
    bare = doc.model_copy(update={"body": ""})
    r2 = verify_claim_tiered(c, [bare], as_of=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert r2.level is VerificationLevel.NEEDS_REVIEW
    assert any("无正文" in f for f in r2.failures)


def test_tiered_replay_as_of_explicit():
    """验收3：核验必须显式 as_of（回放截止）——截止前后同一主张资格不同。"""
    from src.core.claim_extraction import SourceDocument, VerificationLevel, verify_claim_tiered
    body = "正文：签订 900 万元订单。"
    doc = SourceDocument(canonical_uri="cninfo://x",
                         content_hash=SourceDocument.body_hash(body),
                         security_ids=["600519"],
                         published_at=datetime(2026, 9, 1, 23, 59, tzinfo=timezone.utc),
                         body=body)
    c = _claim(citation_uri="cninfo://x", citation_hash=doc.content_hash,
               quote_text="900 万元订单", value=900, unit="万元",
               published_at=datetime(2026, 9, 1, 23, 59, tzinfo=timezone.utc))
    before = verify_claim_tiered(c, [doc], as_of=datetime(2026, 8, 1, tzinfo=timezone.utc))
    after = verify_claim_tiered(c, [doc], as_of=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert before.level is VerificationLevel.PARSED
    assert after.level is VerificationLevel.FACT_CHECKED


def test_deterministic_claim_reaches_excerpt_grounded():
    """确定性通道产出带 quote_text 的 claim——配原文文档可达摘录级（无 AI 的核验阶梯可用）。
    池文档经 from_source_doc 构造（hash 约定与提取器一致——uri+hash 双匹配）。"""
    from src.core.claim_extraction import SourceDocument, VerificationLevel, verify_claim_tiered
    content = "公司公告：与某客户签订销售订单，订单金额 900 万元。"
    source = {"title": "订单公告", "content": content, "date": "2026-09-01",
              "source": "cninfo://ann/x", "security_id": "600519",
              "event_type": "order", "value": 900.0, "unit": "万元"}
    ext = DeterministicExtractor()
    claims = ext.extract(source)
    doc = SourceDocument.from_source_doc(source)
    r = verify_claim_tiered(claims[0], [doc], as_of=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert r.level is VerificationLevel.FACT_CHECKED, \
        f"确定性转写（数值可定位）应走完全阶梯: {r.failures}"
    assert "excerpt_grounded" in r.checks and "numeric_consistent" in r.checks


def test_extractor_cache_key_versioned_by_schema_and_prompt():
    """R2 缓存版本化：键含 schema 版本 + prompt hash——换提示词=换键（旧缓存不冒充新语义）。"""
    import src.core.claim_llm_extractor as llm_mod
    doc = {"title": "t", "content": "c"}
    base = ClaimExtractor()
    k1 = base._cache_key(doc)
    assert k1.startswith("f6.v1|base|")
    fake = object()
    e1 = llm_mod.LLMClaimExtractor(client=fake, model="test-model")
    e2 = llm_mod.LLMClaimExtractor(client=fake, model="test-model")
    k_before = e1._cache_key(doc)  # 补丁前取值（行内重比较会在补丁后重求值——测试逻辑坑）
    assert k_before == e2._cache_key(doc)
    old_prompt = llm_mod.SYSTEM_PROMPT
    try:
        llm_mod.SYSTEM_PROMPT = old_prompt + "（改动提示词）"
        e3 = llm_mod.LLMClaimExtractor(client=fake, model="test-model")
        assert e3._cache_key(doc) != k_before, "prompt 变更必须换缓存键"
    finally:
        llm_mod.SYSTEM_PROMPT = old_prompt
    assert e1._cache_key(doc) == k_before, "恢复后键回到原值（不残留污染）"


def test_llm_extractor_failure_and_truncation_accounted():
    """R2 成本留痕：API 失败/JSON 非法计 failures；finish_reason=length 计 truncations
    （失败/拒识/截断入分母——评测报告不只成功数）。"""
    import src.core.claim_llm_extractor as llm_mod
    from src.core.claim_llm_extractor import LLMClaimExtractor

    class _Resp:
        def __init__(self, content, finish_reason="stop"):
            self.choices = [SimpleNamespace(message=SimpleNamespace(content=content),
                                            finish_reason=finish_reason)]
            self.usage = SimpleNamespace(total_tokens=100)

    class _Client:
        def __init__(self, content, finish_reason="stop", boom=False):
            self._content, self._fr, self._boom = content, finish_reason, boom

        def error(self):
            raise RuntimeError("api down")

        @property
        def chat(self):
            if self._boom:
                raise RuntimeError("api down")
            return SimpleNamespace(completions=SimpleNamespace(
                create=lambda **kw: _Resp(self._content, self._fr)))

    e = LLMClaimExtractor(client=_Client("bad json {", "stop"), model="t")
    assert e.extract({"title": "t", "content": "c"}) == []
    assert e.failures == 1 and e.calls == 1 and e.last_usage_tokens == 100
    e.extract({"title": "t", "content": "c"})
    assert e.calls == 1, "同输入缓存命中不重复付费"

    e2 = LLMClaimExtractor(client=_Client("", "stop", boom=True), model="t2")
    assert e2.extract({"title": "t", "content": "c"}) == []
    assert e2.failures == 1

    e3 = LLMClaimExtractor(client=_Client('{"claims": []}', "length"), model="t3")
    assert e3.extract({"title": "t", "content": "c"}) == []
    assert e3.truncations == 1, "截断留痕（拒识/截断成本进评测分母）"


def test_legacy_pool_entry_adapter():
    """旧 dict 池条目 → SourceDocument 适配；无 body 的弱引用只能到 SOURCE_RESOLVED。"""
    from src.core.claim_extraction import SourceDocument, VerificationLevel, verify_claim_tiered
    c = _claim(citation_uri="巨潮公告", quote_text="x")
    legacy = {"uri": "巨潮公告", "hash": "abc123", "security_id": "600519"}
    r = verify_claim_tiered(c, [legacy], as_of=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert r.level is VerificationLevel.NEEDS_REVIEW
    assert any("无正文" in f for f in r.failures), "旧弱引用无 body → 摘录/内容核验不可达（人工通道）"
    adapted = SourceDocument.from_legacy_pool_entry(legacy)
    assert adapted.canonical_uri == "巨潮公告" and adapted.security_ids == ["600519"]


def test_negation_boilerplate_in_body_does_not_kill_real_claim():
    """监督员 P1 回归：否定检查只在摘录内判定——正文其他部分的披露套话
    （「不存在应披露未披露事项」）不得误杀真实主张（REJECTED 是对用户的错误指控）。"""
    from src.core.claim_extraction import SourceDocument, VerificationLevel, verify_claim_tiered
    body = ("公司公告：与某客户签订销售订单，订单金额 900 万元。"
            "本公司目前不存在应披露而未披露的重大事项。")
    doc = SourceDocument(canonical_uri="cninfo://x", content_hash=SourceDocument.body_hash(body),
                         security_ids=["600519"],
                         published_at=datetime(2026, 9, 1, 23, 59, tzinfo=timezone.utc),
                         body=body)
    c = _claim(citation_uri="cninfo://x", citation_hash=doc.content_hash,
               quote_text="订单金额 900 万元", value=900, unit="万元")
    r = verify_claim_tiered(c, [doc], as_of=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert r.level is VerificationLevel.FACT_CHECKED, f"套话误杀: {r.failures}"


def test_hash_conflict_with_uri_match_rejected():
    """监督员 P1 回归：uri 命中但 citation_hash 属另一版本（更正前后/篡改）→ REJECTED
    （SOURCE_RESOLVED 的「hash 均匹配」承诺不弱于旧函数）。"""
    from src.core.claim_extraction import SourceDocument, VerificationLevel, verify_claim_tiered
    body = "更正后订单金额 900 万元。"
    doc = SourceDocument(canonical_uri="cninfo://same", content_hash=SourceDocument.body_hash(body),
                         security_ids=["600519"],
                         published_at=datetime(2026, 9, 5, 23, 59, tzinfo=timezone.utc),
                         body=body)
    c = _claim(citation_uri="cninfo://same", citation_hash="hash_of_old_version",
               quote_text="订单金额 900 万元", value=900, unit="万元")
    r = verify_claim_tiered(c, [doc], as_of=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert r.level is VerificationLevel.REJECTED
    assert any("hash" in f for f in r.failures)


def test_naive_as_of_rejected_at_entry():
    """监督员 P1 回归：as_of naive → 构造期 ClaimVerificationError（不在比较处 TypeError 崩）。"""
    from src.core.claim_extraction import verify_claim_tiered
    c = _claim()
    with pytest.raises(ClaimVerificationError, match="时区"):
        verify_claim_tiered(c, [], as_of=datetime(2026, 9, 15))  # naive
    with pytest.raises(ClaimVerificationError, match="时区"):
        verify_claim(c, evidence_pool=EVIDENCE_POOL, as_of=datetime(2026, 9, 15))  # legacy 同口径


def test_no_time_does_not_claim_no_future_date():
    """监督员 P1 回归：published_at=None → 记 no_future_date_unchecked（无时间不声称已证实，
    DATA_TRUST §4 明文）。"""
    v = verify_claim(_claim(published_at=None), evidence_pool=EVIDENCE_POOL)
    assert "no_future_date_unchecked" in v.checks
    assert "no_future_date" not in v.checks
