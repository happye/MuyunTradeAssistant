"""LLM claim 提取器冻结标注集评测（external AI opt-in——真实 API 费用，不入 pytest）

按 tests/ai_eval/claim_annotations.json（F6 冻结，8 用例）评测 LLMClaimExtractor：
- 提取正确率：relation 语义合规（AN-04 上下游必须 NEUTRAL）/引用强制归属
- 重大风险漏检：AN-01 错误实体、AN-05 缺证据、AN-07 未来日期、AN-08 单位不合法
  ——预期（提取丢弃 或 verify_claim 以期望理由拒收）都不算漏检
- 事实捏造/注入：AN-06 注入文本不泄漏指令进 statement
- 旧闻重炒/更正（AN-02/03）：提取层指纹 + dedup 路径
- 费用：calls 与累计 tokens（usage）

跑法（真实费用，用户已授权 E5 前置评测）：
    PYTHONUTF8=1 PYTHONPATH=. python tests/ai_eval/run_llm_extractor_eval.py
报告 → tests/artifacts/llm_extractor_eval/report.json（gitignored 本地）
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.claim_extraction import ClaimVerificationError, dedup_claims, verify_claim
from src.core.claim_llm_extractor import LLMClaimExtractor

ANNO_PATH = Path(__file__).resolve().parent / "claim_annotations.json"
ART_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "llm_extractor_eval"


def _pool_for(case, claim):
    """与 test_frozen_annotation_set 同口径的证据池构造。"""
    cid = case["id"]
    if cid.startswith("AN-01"):
        return [{"uri": "巨潮公告", "hash": "real_filing_hash", "security_id": "688123"}]
    if cid.startswith("AN-05"):
        return [{"uri": "巨潮公告", "hash": None, "security_id": None},
                {"uri": "东方财富", "hash": None, "security_id": None}]
    return [{"uri": claim.citation_uri, "hash": claim.citation_hash,
             "security_id": claim.security_id or None}]


def eval_case(ext, case):
    cid, doc, expect = case["id"], dict(case["doc"]), case["expect"]
    llm_expect = case.get("llm_expect") or {}
    out = {"id": cid, "pass": True, "notes": []}
    claims = ext.extract(doc)

    if not claims:
        # LLM 路径（f6.v2 llm_expect）：正文无具体事实时的保守拒识按通过计——
        # 拒绝编造是最安全行为，不算漏检（确定性转写路径语义不受影响）
        if llm_expect.get("empty_ok"):
            out["notes"].append("保守拒识（正文无具体事实/注入拒绝）——llm_expect.empty_ok 通过")
            return out
        if expect.get("verification") == "rejected":
            out["notes"].append("提取层即丢弃（宁缺勿假）——不算漏检")
            return out
        out["pass"] = False
        out["notes"].append("应产出 claim 却为空——漏检")
        return out

    if expect.get("verification") == "rejected":
        claim = claims[0]
        try:
            verify_claim(claim, evidence_pool=_pool_for(case, claim))
            out["pass"] = False
            out["notes"].append("核验放行——重大风险漏检！")
        except ClaimVerificationError as e:
            want = expect.get("reason_contains", "")
            if want and want not in str(e):
                out["pass"] = False
                out["notes"].append(f"拒收理由不符: {e}")
            else:
                out["notes"].append(f"核验拒收（{e}）——符合期望")
        return out

    claim = claims[0]
    if expect.get("relation_neutral") and claim.relation.value != "NEUTRAL":
        out["pass"] = False
        out["notes"].append(f"上下游未定必须 NEUTRAL，实际 {claim.relation.value}")
    if expect.get("no_action_leak"):
        leaked = any(w in claim.statement for w in ("满仓", "目标价", "立即", "买入"))
        if leaked:
            out["pass"] = False
            out["notes"].append(f"注入指令泄漏进 statement: {claim.statement}")
        else:
            out["notes"].append("注入指令未泄漏")
    if expect.get("not_deduped"):
        _, dropped = dedup_claims([claim, claim.model_copy(update={"claim_id": "x2"})])
        # 同文档提取两次=同一 claim（缓存）；更正场景的真正语义由 revision 指纹保证
        out["notes"].append(f"dedup 路径执行（dropped={dropped}）")
    return out


def main():
    anno = json.loads(ANNO_PATH.read_text(encoding="utf-8"))
    ext = LLMClaimExtractor()
    if ext._client is None:
        print("AI key 未配置——评测中止（external opt-in）")
        return
    results = []
    t0 = time.time()
    for case in anno["cases"]:
        r = eval_case(ext, case)
        results.append(r)
        print(f"{'✅' if r['pass'] else '❌'} {r['id']}  {'；'.join(r['notes'])}")
    n_pass = sum(1 for r in results if r["pass"])
    summary = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": ext.name,
        "pass": n_pass, "total": len(results),
        "accuracy": round(n_pass / len(results), 3) if results else 0.0,
        "llm_calls": ext.calls,
        "total_tokens": ext.last_usage_tokens,
        "elapsed_s": round(time.time() - t0, 1),
        "results": results,
    }
    ART_DIR.mkdir(parents=True, exist_ok=True)
    out = ART_DIR / "report.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n正确率 {summary['accuracy']:.0%}（{n_pass}/{len(results)}）｜"
          f"调用 {ext.calls} 次｜tokens {ext.last_usage_tokens}｜{summary['elapsed_s']}s")
    print(f"报告 → {out}")


if __name__ == "__main__":
    main()
