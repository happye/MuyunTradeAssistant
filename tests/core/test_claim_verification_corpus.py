"""R2 分级核验冻结语料运行器（plan/fusion iteration2 TASKS R2 验收2/5）

锁死语义（DATA_TRUST §4，当前协议 cv6；原cv3文件另有SHA保持断言）：
1. 冻结语料（tests/ai_eval/claim_verification_corpus_cv6.json）全部用例按期望等级/原因通过
   ——先冻结后运行；改期望=改语料=新版本，不得原地改后宣称同一测试通过
2. 运行器输出报告（precision 同意率、各等级覆盖、失败原因分布、关键事实错误数），
   落 tests/artifacts/claim_verification_report_cv6.json——不只一个通过数
3. 显式 as_of：同一主张（CV-11a/11b）截止前后资格不同，不依赖机器当前日期
4. 注入免疫：正文指令是数据（CV-08）——确定性检查路径无解释无执行

纯内存测试，无网络无 AI。跑法：pytest tests/core/test_claim_verification_corpus.py -q
"""
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.claim_extraction import (
    CLAIM_VERIFICATION_PROTOCOL_VERSION,
    SourceDocument,
    VerificationLevel,
    ClaimRecord,
    verify_claim_tiered,
)

CORPUS_PATH = Path(__file__).resolve().parents[1] / "ai_eval" / "claim_verification_corpus_cv6.json"
ARTIFACT = Path(__file__).resolve().parents[2] / "tests" / "artifacts" / "claim_verification_report_cv6.json"


def _load_corpus() -> dict:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert corpus["_meta"]["schema"] == CLAIM_VERIFICATION_PROTOCOL_VERSION == "cv6"
    assert corpus["_meta"]["frozen_at"], "语料必须带冻结时间戳"
    return corpus


def test_cv3_frozen_original_is_preserved():
    original = CORPUS_PATH.with_name("claim_verification_corpus.json")
    assert hashlib.sha256(original.read_bytes()).hexdigest() == "ba76fa52ac5fa7440abc1bb8378607ca04a9d76d6af43af2a4d74d6341a09847"


def _build_claim(c: dict) -> ClaimRecord:
    kw = dict(c["claim"])
    for k in ("occurred_at", "published_at"):
        if kw.get(k):
            kw[k] = datetime.fromisoformat(kw[k])
    return ClaimRecord(**kw)


def _build_documents(corpus: dict, c: dict) -> list[SourceDocument]:
    doc = c.get("document")
    if not doc:
        return []
    raw = dict(corpus["documents"][doc])
    raw.update(c.get("document_overrides") or {})
    return [SourceDocument(
        canonical_uri=raw["canonical_uri"],
        content_hash=SourceDocument.body_hash(raw["body"]),
        security_ids=raw.get("security_ids") or [],
        published_at=datetime.fromisoformat(raw["published_at"]) if raw.get("published_at") else None,
        body=raw.get("body") or "",
        body_locator=raw.get("body_locator") or "",
        title=raw.get("title") or "",
        is_correction=raw.get("is_correction", False),
    )]


def test_frozen_corpus_all_cases_agree():
    """冻结语料全用例按期望等级与原因通过；报告先落盘再断言（失败也有 artifact 留档）。"""
    corpus = _load_corpus()
    results = []
    all_ok = True
    for case in corpus["cases"]:
        claim = _build_claim(case)
        docs = _build_documents(corpus, case)
        as_of = datetime.fromisoformat(case["as_of"])
        result = verify_claim_tiered(claim, docs, as_of=as_of)
        expect = case["expect"]
        ok = result.level.value == expect["level"]
        frag_ok = all(any(frag in f for f in result.failures)
                      for frag in expect.get("failures_contain", [])) \
            and all(chk in result.checks for chk in expect.get("checks_contain", []))
        all_ok = all_ok and ok and frag_ok
        results.append({
            "case_id": case["id"], "expected": expect["level"],
            "actual": result.level.value, "agree": ok and frag_ok,
            "failures": result.failures,
            "checks": result.checks,
        })

    # 报告：等级覆盖 + 失败原因分布 + 一致率 + 成本口径（验收5：不只一个通过数）
    by_level = {}
    for r in results:
        by_level[r["actual"]] = by_level.get(r["actual"], 0) + 1
    all_failures = [f for r in results for f in r["failures"]]
    rejected_ids = [r["case_id"] for r in results if r["actual"] == "REJECTED"]
    report = {
        "protocol": corpus["_meta"]["protocol"],
        "corpus_frozen_at": corpus["_meta"]["frozen_at"],
        "total_cases": len(results),
        "agree_count": sum(1 for r in results if r["agree"]),
        "agreement": (sum(1 for r in results if r["agree"]) / len(results)) if results else 0.0,
        "level_coverage": by_level,
        "failure_reasons": sorted({f.split("（")[0] for f in all_failures}),
        "rejected_cases": rejected_ids,
        "content_rejected_non_structural": [c for c in rejected_ids if "CV-02" not in c],
        "cost": {"deterministic_verify_calls": len(results), "deterministic_cost": 0,
                 "llm_calls": 0, "llm_tokens": 0, "llm_status": "NOT_RUN（AI opt-in，R7 承接）"},
        "cases": results,
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    assert report["agree_count"] == report["total_cases"]
    # 关键等级必须都有覆盖（否则语料失去判别力——不能全是 PASS 型用例）
    for required in ("FACT_CHECKED", "NEEDS_REVIEW", "REJECTED", "PARSED", "SOURCE_RESOLVED"):
        assert by_level.get(required, 0) >= 1, f"语料缺 {required} 等级覆盖"


def test_as_of_replay_independent_of_machine_clock():
    """验收3：同一主张资格随显式 as_of 翻转，不依赖机器当前日期——
    把机器时钟概念排除：两次调用只差 as_of 参数，结论必须确定。"""
    corpus = _load_corpus()
    case = next(c for c in corpus["cases"] if c["id"] == "CV-11a-截止前主张不可得")
    claim = _build_claim(case)
    docs = _build_documents(corpus, case)
    early = verify_claim_tiered(claim, docs, as_of=datetime.fromisoformat(case["as_of"]))
    late = verify_claim_tiered(
        _build_claim(next(c for c in corpus["cases"] if c["id"] == "CV-11b-截止后同一主张可核验")),
        docs, as_of=datetime.fromisoformat("2026-09-15T08:00:00+00:00"))
    assert early.level is VerificationLevel.PARSED
    assert late.level is VerificationLevel.FACT_CHECKED


def test_injection_is_data_not_instruction():
    """验收2 注入项：正文指令不改变任何核验结果（确定性检查不做文本解释/执行）。"""
    corpus = _load_corpus()
    case = next(c for c in corpus["cases"] if c["id"] == "CV-08-注入指令不影响核验")
    claim = _build_claim(case)
    result = verify_claim_tiered(claim, _build_documents(corpus, case),
                                 as_of=datetime.fromisoformat(case["as_of"]))
    assert result.level is VerificationLevel.FACT_CHECKED
    # Compare the real no-injection baseline, not just different relations.
    baseline_docs = _build_documents(corpus, case)
    body = "公司签订900万元订单。"
    baseline_docs[0] = baseline_docs[0].model_copy(update={
        "body": body, "content_hash": SourceDocument.body_hash(body)})
    baseline = verify_claim_tiered(claim, baseline_docs,
                                  as_of=datetime.fromisoformat(case["as_of"]))
    assert baseline.level == result.level and baseline.checks == result.checks
    # relation 是研究推论属性——换 relation 结论不变（真断言：核验不消费 relation）
    for rel in ("SUPPORTS", "REFUTES", "NEUTRAL"):
        r2 = verify_claim_tiered(_build_claim(case).model_copy(update={"relation": rel}),
                                 _build_documents(corpus, case),
                                 as_of=datetime.fromisoformat(case["as_of"]))
        assert r2.level == result.level and r2.checks == result.checks, \
            f"relation={rel} 不得影响核验结论"
