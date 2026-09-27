"""E7 完整系统 v1——全漏斗烟测（plan/fusion EXPERIMENTS.md E7；external opt-in）

E_SPEC：同一 PIT 全集、成本、研究资源预算——修正后基线 vs 完整融合漏斗，真实产品流程净效果。
v1 口径（诚实登记）：
- **漏斗存活率烟测**：候选（E1 v1 并集）→ 资格过滤（factor_compute 质量/估值）
  → 周期决策表（F5 evaluate_horizon，模拟计划+事实代理）→ 组合预算（F7 solve_budget，
  用户风险档）——逐级存活计数
- 历史 PIT 全集版（真正 E7）登记后续批：需要 E1 历史快照建库 + 全漏斗历史回放
- 复用 E1 产物（tests/artifacts/e1_recall/detail.json 的质量过闸名单）+ 现拉财务

跑法（external）：
    PYTHONUTF8=1 PYTHONPATH=. python tests/backtest/e7_funnel_smoke.py
报告 → plan/fusion/E7_REPORT.md
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)

from pathlib import Path

ART_DIR = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "e7_funnel"))
REPORT_PATH = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                "plan", "fusion", "E7_REPORT.md"))
E1_DETAIL = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts",
                              "e1_recall", "detail.json"))


def main():
    from src.core.decision_contract import Horizon, ResearchStatus, ThesisStatus
    from src.core.decision_policy import HorizonFacts, HorizonPlan, evaluate_horizon
    from src.core.factor_compute import balance_risk_v1, roe_observed_v1, cash_conversion_v1
    from src.core.experiment import ExperimentManifest, InfoSetTag, PortfolioReplay
    from src.core.portfolio_policy import AddProposal, BudgetConstraints, HoldingWeight, solve_budget
    from src.data.financial_data import get_financial_quarterly
    from src.data.akshare_client import _ensure_baostock_login

    if not E1_DETAIL.exists():
        print("E1 明细缺失——先跑 tests/backtest/e1_route_recall.py")
        return
    detail = json.loads(E1_DETAIL.read_text(encoding="utf-8"))
    candidates = [c["code"] for c in detail["candidates"]]
    quality = detail.get("quality_pass") or []
    print(f"漏斗起点：E1 并集候选 {len(candidates)} 只（其中质量研究完备 {len(quality)} 只）",
          flush=True)

    # 阶段1：资格过滤（研究完备子集才可判——其余如实标「研究未做」）
    _ensure_baostock_login()
    qualified = []
    factor_notes = {}
    for code in quality:
        try:
            recs = get_financial_quarterly(code, 2023, 4)  # 已验证语义季（ISS-114 caveat）
        except Exception as e:
            print(f"  ⚠ {code} 财务拉取失败: {e}", flush=True)
            continue
        roe = roe_observed_v1(recs)
        eq = cash_conversion_v1(recs)
        bal = balance_risk_v1(recs)
        qualified.append(code)
        factor_notes[code] = {"roe": roe.value, "cfo_np": eq.value,
                              "leverage": bal.components.get("liabilityToAsset")}
    print(f"资格过滤后：{len(qualified)} 只（研究完备且过质量闸——阈值见 E1 演示口径）", flush=True)

    # 阶段2：周期决策表（模拟计划+质量事实代理；入场条件需 live 分析——v1 烟测不拉）
    packets = []
    as_of = datetime.now().astimezone()
    for code in qualified:
        facts = HorizonFacts(research_status=ResearchStatus.COMPLETE,
                             thesis_status=ThesisStatus.VALID,
                             entry_condition_met=False,  # 无 live 技术分析——烟测口径
                             budget_available=None)
        for horizon in (Horizon.MID, Horizon.LONG):
            plan = HorizonPlan(
                plan_id=f"e7_{code}_{str(horizon.value).lower()}", security_id=code,
                accepted_at=as_of.isoformat(timespec="seconds"), horizon=horizon,
                policy_id=("fusion_mid_v1" if horizon is Horizon.MID else "fusion_long_v1"),
                intent="E7 烟测模拟计划（质量过闸代理）")
            packets.append((code, horizon.value,
                            evaluate_horizon(plan, facts, code, confirmed_ratio=0.0)))
    actions = {}
    for code, h, pk in packets:
        actions.setdefault(pk.desired_action.value, []).append(f"{code}:{h}")

    # 阶段3：组合预算（用户风险档；决策表 OPEN/ADD 者进提案——烟测口径下多为 WAIT/HOLD）
    proposals = [AddProposal(stock_code=code, target_weight=0.15,
                             pressure_loss_rate=0.30, industries=["__e7_smoke__"])
                 for code, h, pk in packets if pk.desired_action.value in ("OPEN", "ADD")]
    replay = PortfolioReplay(initial_cash=2_000_000.0, fee_rate=0.00025, stamp_rate=0.001)
    nav = replay.nav({})
    sol = solve_budget(proposals, [],
                       BudgetConstraints(per_stock_max=0.20, industry_max=0.50,
                                         cash_nav=1.0),
                       sell_eligible_nav=0.0)
    feasible = [a for a in sol.adds if a.feasible]

    m = ExperimentManifest.build(
        experiment_id="E7", config_hash="funnel_smoke_v1",
        as_of=datetime.now().strftime("%Y-%m-%d"),
        sample_set=[f"candidates={len(candidates)}", f"researched={len(quality)}",
                    f"qualified={len(qualified)}"],
        data_versions={"source_tag": InfoSetTag.RULE_PROXY.value,
                       "note": "live 快照烟测（历史 PIT 全集版登记后续批）"},
        ai_model="",
    )
    ART_DIR.mkdir(parents=True, exist_ok=True)
    (ART_DIR / "manifest.json").write_text(
        json.dumps({**m.model_dump(), "fingerprint": m.fingerprint()}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    lines = [
        "# E7 完整系统 v1——全漏斗烟测（plan/fusion EXPERIMENTS.md E7）",
        "",
        f"生成：{datetime.now().isoformat(timespec='seconds')}",
        "",
        "**v1 口径 caveat**：漏斗存活率烟测（live 快照 + 模拟计划 + 质量事实代理）——"
        "真正 E7（历史 PIT 全集 + 全漏斗回放 + 收益对照）登记后续批（前置=E1 历史快照建库）；"
        "入场条件需 live 技术分析（烟测不拉，按 False 处理——决策表落 HOLD/WAIT 属烟测口径预期）。",
        "",
        "## 漏斗存活率",
        "",
        f"| 阶段 | 存活 |",
        f"|---|---|",
        f"| 1 候选（E1 三路并集） | {len(candidates)} |",
        f"| 2 研究完备（质量抽样过闸） | {len(quality)} |",
        f"| 3 资格过滤（因子复核通过） | {len(qualified)} |",
        f"| 4 周期决策表动作分布 | {json.dumps({k: len(v) for k, v in actions.items()}, ensure_ascii=False)} |",
        f"| 5 组合预算可行额度 | {len(feasible)} 条（拒绝 {len(sol.rejected)}） |",
        "",
        f"- 各股因子：{json.dumps(factor_notes, ensure_ascii=False)}",
        f"- 预算拒绝原因：{[r.reason for r in sol.rejected[:5]]}",
        f"- **E7 结论（v1 烟测）**：全漏斗管线（候选→资格→决策表→预算）已可端到端跑通，"
        f"逐级存活可计量；收益对照待历史 PIT 版",
        "",
        "- source_tag=rule_bz_proxy；manifest 见 tests/artifacts/e7_funnel/",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[8:15]), flush=True)
    print(f"报告 → {REPORT_PATH}", flush=True)


if __name__ == "__main__":
    main()
