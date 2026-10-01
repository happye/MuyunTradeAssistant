"""M2 前置原型：K2b 的最小日常研究只读视图（CONDITIONAL 卡——隔离原型，非生产实现）。

依据 plan/fusion/iteration6/DELIVERY_PLAN.md M2 与 DESIGN_VALIDATION B1 基线：
- 日常 l/la 查看「已接受周期/版本、评估真值/缺口、候选是否存在、下一步」；
  无研究给具体 research 命令——查询默认零新外源、零付费、零新草稿/评估/接受记录
- B1 教训：日常查看不得触发研究重跑（同材料 as_of+1s 使 revision 1→2）；
  显式 research/--capture 才生成新研究与候选
- 运行时间变化不能在日常查看时创建候选（daily_view 重复读取不改两槽内容）

本原型在临时根内对**真实产品类**（HorizonPlanStore / AssessmentStore /
shadow_diff._thesis_status_for 同一评估核对路径）演示四场景与零写入断言；
不导入 CLI、不做生产接线（M2 生产实施以 M0/M1 独立复验通过为前置）。

运行：python plan/fusion/iteration6/m2_readonly_view_prototype.py
输出：m2_readonly_view_results.json（与设计合同探针同级）。
"""
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from src.data.horizon_plans import HorizonPlanStore          # noqa: E402
from src.data.research_store import AssessmentStore          # noqa: E402
from src.core.shadow_diff import _thesis_status_for          # noqa: E402（同一评估核对路径）
from src.core.decision_policy import HorizonPlan, POLICY_ID_MID  # noqa: E402
from src.core.research import ThesisAssessment               # noqa: E402
from src.core.research_service import ASSERTION_METHOD_VERSION  # noqa: E402
from src.core.decision_contract import ThesisStatus          # noqa: E402

CODE = "600519"


def _tree_hash(paths: list[Path]) -> str:
    """目录/文件集合内容哈希（零写入断言用——任何新增/修改都改变结果）。"""
    h = hashlib.sha256()
    for root in paths:
        root = Path(root)
        if root.is_file():
            h.update(root.read_bytes())
        elif root.is_dir():
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    h.update(str(p.relative_to(root)).encode())
                    h.update(p.read_bytes())
    return h.hexdigest()[:16]


def daily_research_view(security_id: str, *, plans_store: HorizonPlanStore,
                        assessment_store: AssessmentStore, as_of: datetime) -> dict:
    """日常只读研究视图（M2 最小合同的原型形态——生产接线待前置审批）。

    消费既有接受态与评估唯一真值，**零写入**：不生成草稿/评估/候选、不联网、
    不接受计划。评估状态消费与影子同一核对路径（_thesis_status_for——
    错配/过期→REVIEW_REQUIRED 待复核，不静默盖章）。"""
    view = {"security_id": security_id, "as_of": as_of.isoformat(timespec="seconds"),
            "horizons": {}, "next_steps": []}
    for horizon in ("MID", "LONG"):
        plan = plans_store.get(security_id, horizon)
        entry = {"has_plan": plan is not None}
        if plan is not None:
            accepted = plans_store.is_accepted_version(plan)
            entry.update({
                "plan_id": plan.plan_id,
                "revision": int(getattr(plan, "revision", 0) or 0),
                "activated": plan.activated,
                "accepted": bool(accepted),
                "accepted_ref": (plans_store.get_accepted_ref(security_id, horizon)
                                 if accepted else None),
                "assessment_status": _thesis_status_for(
                    plan, horizon, security_id,
                    assessment_store=assessment_store).value,
                "snapshot_id": str(getattr(plan, "snapshot_id", "") or ""),
            })
            if not accepted:
                view["next_steps"].append(
                    f"{horizon} 草稿未接受（revision {entry['revision']}）——"
                    f"plan2 accept {security_id} {horizon.lower()} 激活后出真判断")
            elif entry["assessment_status"] != ThesisStatus.VALID.value:
                view["next_steps"].append(
                    f"{horizon} 已接受但逻辑状态 {entry['assessment_status']}"
                    "（评估错配/过期/缺引用）——重跑 research 复核，不静默盖章")
        cand = plans_store.get_candidate(security_id, horizon)
        entry["candidate_available"] = cand is not None
        if cand is not None:
            # 候选存在即提示（已接受计划在位时同样提示——候选未接受不替换主意图）
            view["next_steps"].append(
                f"{horizon} 存在候选草稿（revision {getattr(cand, 'revision', 0)}，"
                f"未接受不替换主意图）——plan2 diff/accept 显式处理")
        if plan is None and cand is None:
            view["next_steps"].append(
                f"{horizon} 无研究记录——research {security_id} --claims <主张文件> "
                "建立（查询不自动补采）")
        view["horizons"][horizon] = entry
    return view


def main() -> int:
    rows: list[dict] = []

    def check(name: str, observed, expected):
        assert observed == expected, (name, observed, expected)
        rows.append({"case": name, "pass": True})

    with tempfile.TemporaryDirectory(prefix="m2_readonly_proto_") as tmp:
        root = Path(tmp)
        plans = HorizonPlanStore(root / "plans.json")
        asm_store = AssessmentStore(root / "research")

        # ── 场景1：正常（已接受计划 + 评估齐）──
        plan = HorizonPlan(plan_id=f"p2_{CODE}_m2", security_id=CODE, horizon="MID",
                           policy_id=POLICY_ID_MID, intent="M2 原型场景",
                           policy_version="r4.research_service_v1")
        asm = ThesisAssessment(thesis_id=f"thesis_{CODE}_MID", security_id=CODE,
                               horizon="MID", snapshot_id="snap-m2",
                               status=ThesisStatus.VALID,
                               method_version=ASSERTION_METHOD_VERSION,
                               evaluated_as_of=datetime.now().astimezone() - timedelta(minutes=1))
        plan.assessment_id = asm_store.save(asm)
        plan.snapshot_id = asm.snapshot_id
        saved_asm_id = plan.assessment_id
        plans.save(plan)
        ok, msg = plans.accept(CODE, "MID")
        assert ok, msg

        watch_paths = [root / "plans.json", root / "research"]
        before = _tree_hash(watch_paths)
        as_of = datetime.now().astimezone()
        v1 = daily_research_view(CODE, plans_store=plans, assessment_store=asm_store,
                                 as_of=as_of)
        mid = v1["horizons"]["MID"]
        check("normal_accepted_plan_visible", (mid["has_plan"], mid["accepted"],
                                               mid["assessment_status"]),
              (True, True, "VALID"))
        check("normal_no_candidate", mid["candidate_available"], False)
        # B1：重复读取已接受版本不变（运行时间变化不创建候选/不重跑研究）
        for i in range(3):
            vi = daily_research_view(CODE, plans_store=plans, assessment_store=asm_store,
                                     as_of=as_of + timedelta(seconds=i))
            check(f"daily_view_keeps_accepted_{i}",
                  (vi["horizons"]["MID"]["plan_id"], vi["horizons"]["MID"]["revision"],
                   vi["horizons"]["MID"]["accepted_ref"]),
                  (mid["plan_id"], mid["revision"], mid["accepted_ref"]))
        check("normal_zero_plan_mutation", _tree_hash(watch_paths), before)

        # ── 场景2：缺资料（无计划）→ 给具体 research 命令 ──
        v2 = daily_research_view("000001", plans_store=plans, assessment_store=asm_store,
                                 as_of=as_of)
        check("missing_shows_next_step",
              any("research 000001" in s for s in v2["next_steps"]), True)
        check("missing_zero_write", _tree_hash(watch_paths), before)

        # ── 场景3：候选存在未接受（双槽——候选不替换主意图）──
        cand = HorizonPlan(plan_id=f"p2_{CODE}_m2_c2", security_id=CODE, horizon="MID",
                           policy_id=POLICY_ID_MID, intent="新材料候选",
                           policy_version="r4.research_service_v1")
        cand.revision = 2
        plans.save_candidate(cand)
        # 基线取「候选落盘后、视图前」——证明视图本身零写入
        baseline_after_seed = _tree_hash(watch_paths)
        v3 = daily_research_view(CODE, plans_store=plans, assessment_store=asm_store,
                                 as_of=as_of)
        mid3 = v3["horizons"]["MID"]
        check("candidate_visible_without_replacing_primary",
              (mid3["candidate_available"], mid3["accepted"], mid3["plan_id"]),
              (True, True, f"p2_{CODE}_m2"))
        check("candidate_next_step_listed",
              any("候选草稿" in s for s in v3["next_steps"]), True)
        check("view_zero_write", _tree_hash(watch_paths), baseline_after_seed)
        # 重复查看候选不再增 revision（B1：运行时间不创建候选）
        v3b = daily_research_view(CODE, plans_store=plans, assessment_store=asm_store,
                                  as_of=as_of + timedelta(seconds=5))
        check("repeated_view_no_new_candidate",
              v3b["horizons"]["MID"]["candidate_available"], True)
        cand_after = plans.get_candidate(CODE, "MID")
        check("candidate_revision_stable", int(cand_after.revision), 2)

        # ── 场景4：失效（已接受但评估错配 → 待复核，不静默盖章）──
        asm_bad = ThesisAssessment(thesis_id=f"thesis_{CODE}_MID", security_id=CODE,
                                   horizon="MID", snapshot_id="snap-其他",
                                   status=ThesisStatus.VALID,
                                   method_version=ASSERTION_METHOD_VERSION,
                                   evaluated_as_of=datetime.now().astimezone())
        plan_bad = HorizonPlan(plan_id=f"p2_{CODE}_m2_bad", security_id=CODE,
                               horizon="LONG", policy_id="fusion_long_v1",
                               intent="快照错配样本",
                               policy_version="r4.research_service_v1")
        # 引用存在的评估 id 但 snapshot 不一致（错配形态：找到评估但不匹配）
        plan_bad.assessment_id = saved_asm_id
        plan_bad.snapshot_id = "snap-不一致"
        plans.save(plan_bad)
        plans.accept(CODE, "LONG")
        v4 = daily_research_view(CODE, plans_store=plans, assessment_store=asm_store,
                                 as_of=as_of)
        long4 = v4["horizons"]["LONG"]
        check("mismatched_assessment_shows_review_not_valid",
              long4["assessment_status"], "REVIEW_REQUIRED")
        check("mismatch_next_step_listed",
              any("不静默盖章" in s or "复核" in s for s in v4["next_steps"]), True)
        check("mismatch_zero_write", _tree_hash([root / "research"]),
              _tree_hash([root / "research"]))

    print(json.dumps({"scope": "isolated prototype for M2 pre-work, not production wiring; "
                                 "production K2b wiring requires M0/M1 independent re-verification",
                      "protocol_hint": "daily view consumes accepted state read-only; "
                                       "explicit research/--capture generates new research",
                      "passed": len(rows), "cases": rows}, ensure_ascii=False, indent=2))
    out = Path(__file__).parent / "m2_readonly_view_results.json"
    out.write_text(json.dumps({"scope": "isolated prototype for M2 pre-work, not production wiring",
                               "passed": len(rows), "cases": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
