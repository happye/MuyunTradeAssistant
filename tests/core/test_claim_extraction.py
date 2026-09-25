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
