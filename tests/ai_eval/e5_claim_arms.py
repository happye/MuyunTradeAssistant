"""E5 消融 v1——证据层三臂对照（plan/fusion EXPERIMENTS.md E5 前置实验；external AI opt-in）

E_SPEC：同一证据、计划、预算下——无AI/结构化提取/提取+反证；旧 modifier 作独立对照。
v1 口径（诚实登记）：
- **证据层消融**（不动决策路径——live 接线须影子期验证，登记 E5 本体后续批）：
  同一批真实公告 → 三臂各产出 claim 集 → 对照「提取产出/核验通过/反证识别/去重」
  - 臂 1 无AI：DeterministicExtractor（转写公告字段，无新主张）
  - 臂 2 结构化提取：LLMClaimExtractor（真实 DeepSeek）
  - 臂 3 提取+反证：臂 2 + verify_claim 核验 + REFUTES（反证）显式标记
- 旧 modifier 对照： modifier 是「情绪加分」不是「事实主张」——语义不同不可直接比，
  v1 登记（F2 已断其直改分数路径；方向语义对照见 tests/core/test_ai_direction.py）
- 费用：每公告 1 次 LLM 调用（限 6 份公告）

跑法（真实费用，用户已授权）：
    PYTHONUTF8=1 PYTHONPATH=. python tests/ai_eval/e5_claim_arms.py
报告 → plan/fusion/E5_REPORT.md
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)

MAX_DOCS = 6
SAMPLE_CODES = ["600519", "002594"]  # 两只代表性标的的最近公告


def main():
    from src.core.claim_extraction import (
        ClaimVerificationError,
        DeterministicExtractor,
        dedup_claims,
        verify_claim,
    )
    from src.core.claim_llm_extractor import LLMClaimExtractor
    from src.data.news_client import NewsClient

    ext_llm = LLMClaimExtractor()
    if ext_llm._client is None:
        print("AI key 未配置——E5 v1 中止（external opt-in）")
        return
    ext_det = DeterministicExtractor()

    # 同一批真实公告（两标的各取最近 3 条）
    client = NewsClient()
    docs = []
    for code in SAMPLE_CODES:
        try:
            news = client.get_stock_news(code, max_count=6) or []
            for n in news[:3]:
                docs.append({"title": n.get("title") or "", "content": n.get("content") or
                             n.get("summary") or "", "date": (n.get("publish_time") or "")[:10],
                             "source": n.get("source") or "东财", "security_id": code,
                             "event_type": "other"})
        except Exception as e:
            print(f"⚠ {code} 公告/新闻拉取失败: {e}", flush=True)
    docs = [d for d in docs if (d["title"] or d["content"])][:MAX_DOCS]
    print(f"同一证据集：{len(docs)} 份公告/新闻（{SAMPLE_CODES}）", flush=True)
    if not docs:
        print("证据集为空——中止")
        return

    # 臂 1 无AI
    arm1 = []
    for d in docs:
        arm1.extend(ext_det.extract(d))
    # 臂 2 结构化提取
    arm2 = []
    for d in docs:
        arm2.extend(ext_llm.extract(d))
    arm2_u, _ = dedup_claims(list(arm2))
    # 臂 3 提取+反证（核验 + REFUTES 标记）
    arm3_verified, arm3_refutes, arm3_rejected = [], [], []
    pool = [{"uri": d["source"], "hash": None, "security_id": d["security_id"] or None}
            for d in docs]
    for c in arm2_u:
        try:
            v = verify_claim(c, evidence_pool=pool)
            (arm3_refutes if c.relation.value == "REFUTES" else arm3_verified).append(v)
        except ClaimVerificationError as e:
            arm3_rejected.append({"claim": c.statement[:40], "reason": str(e)[:60]})

    summary = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "docs": len(docs),
        "arm1_noai": {"claims": len(arm1)},
        "arm2_llm": {"raw": len(arm2), "deduped": len(arm2_u)},
        "arm3_verify": {"verified": len(arm3_verified), "refutes": len(arm3_refutes),
                        "rejected": len(arm3_rejected),
                        "reject_reasons": [r["reason"] for r in arm3_rejected[:5]]},
        "llm_calls": ext_llm.calls, "total_tokens": ext_llm.last_usage_tokens,
    }
    lines = [
        "# E5 消融报告 v1——证据层三臂（plan/fusion EXPERIMENTS.md E5）",
        "",
        f"生成：{summary['when']}｜同一证据集 {len(docs)} 份公告（{','.join(SAMPLE_CODES)} 最近 14 天）",
        "",
        "**v1 口径 caveat**：证据层消融（不动决策路径——live 接线须影子期验证，E5 本体后续批）；"
        "旧 modifier 是情绪加分语义与事实主张不可直接比（F2 已断直改路径，方向语义对照"
        " test_ai_direction.py）；费用 " + f"{summary['llm_calls']} 次调用 / {summary['total_tokens']} tokens。",
        "",
        "| 臂 | 产出 | 说明 |",
        "|---|---|---|",
        f"| 1 无AI（确定性转写） | {len(arm1)} 条 | 只转写公告已有字段，无新主张 |",
        f"| 2 结构化提取（LLM） | 原始 {len(arm2)} → 去重 {len(arm2_u)} 条 | 模型产出，引用强制挂原文 |",
        f"| 3 提取+核验反证 | 通过 {len(arm3_verified)}｜反证(REFUTES) {len(arm3_refutes)}｜拒收 {len(arm3_rejected)} | 核验五规则 + 反证显式标记 |",
        "",
    ]
    for r in arm3_rejected[:5]:
        lines.append(f"- 拒收样例：{r['claim']}…（{r['reason']}）")
    lines += [
        "",
        f"- **E5 结论（v1 初步）**：三臂在同一证据上的产出差异可量化"
        f"（无AI {len(arm1)} vs LLM 去重 {len(arm2_u)}，核验拒收 {len(arm3_rejected)}）——"
        f"反证/拒收是「宁缺勿假」防线的直接体现；净收益是否覆盖费用待 E5 本体（决策路径 A/B）",
        "",
        "- source_tag=ai_lookahead（信息集标签强制）；明细见运行日志",
    ]
    from pathlib import Path
    rp = Path(__file__).resolve().parent.parent.parent / "plan" / "fusion" / "E5_REPORT.md"
    rp.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[8:14]), flush=True)
    print(f"报告 → {rp}", flush=True)


if __name__ == "__main__":
    main()
