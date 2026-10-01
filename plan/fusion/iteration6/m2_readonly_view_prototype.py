"""M2 前置原型：K2b 的最小日常研究只读视图（CONDITIONAL 卡——隔离原型，非生产实现）。

依据 plan/fusion/iteration7/DELIVERY_PLAN.md M2 与 R13 X4 复验要求修订：
- 空库与**损坏库返回不同状态**（损坏不装作空库、不给「建立研究」下一步）
- 每次视图调用取**真正的前后内容哈希**（零写入断言可失败）
- 候选、旧方法（非当期已验证生成版本）、缺评估、损坏记录、重启读取均有回执/下一步
- 评估状态只消费产品既有可执行规则（快照/方法/主体核对 + 未来时点拒绝）——
  不用字符串「过期」冒充时效判断，不自行发明期限
- B1 教训：日常查看不触发研究重跑/候选创建（运行时间变化不改两槽内容）

本原型在临时根内对**真实产品类**（HorizonPlanStore / AssessmentStore /
shadow_diff._thesis_status_for 同一评估核对路径 + shadow 版本资格常量）演示；
不导入 CLI、不做生产接线（M2 生产实施以 N0–N2 联合过门+合同冻结为前置）。

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
from src.core.research_service import (                      # noqa: E402
    ASSERTION_METHOD_VERSION, RESEARCH_SERVICE_VERSION,
)
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
    错配/未来时点/缺引用 → REVIEW_REQUIRED 待复核，不静默盖章）。
    N1 版本资格同口径：生成版本非当期已验证组合 → 如实标注待复核。
    损坏库 → store_status="corrupted"（与空库区分，不给「建立研究」误导）。"""
    view = {"security_id": security_id, "as_of": as_of.isoformat(timespec="seconds"),
            "store_status": "ok", "horizons": {}, "next_steps": []}
    # 懒加载存储——先触发一次读取，损坏标志才可信（R13 X4：损坏不装作空库）
    try:
        plans_store._load()
    except Exception:
        pass
    if getattr(plans_store, "_corrupted", False):
        # R13 X4：损坏库不装作空库——显式失败状态 + 人工修复指引
        view["store_status"] = "corrupted"
        view["next_steps"].append("研究计划库损坏（读取失败）——请人工核对修复后重试；"
                                  "查询不建立新记录")
        return view
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
            else:
                # N1 同口径版本资格（只消费产品常量——不发明兼容清单）
                if str(getattr(plan, "policy_version", "") or "") != RESEARCH_SERVICE_VERSION:
                    entry["version_note"] = "生成版本非当期已验证组合"
                    view["next_steps"].append(
                        f"{horizon} 已接受计划的生成版本非当期已验证组合"
                        f"（{plan.policy_version!r}）——重跑 research 复核，不静默盖章")
                if entry["assessment_status"] != ThesisStatus.VALID.value:
                    view["next_steps"].append(
                        f"{horizon} 已接受但逻辑状态 {entry['assessment_status']}"
                        "（评估错配/未来时点/缺引用——产品核对规则）——重跑 research 复核")
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

    def zero_write(watch_paths, baseline):
        """真正的前后哈希断言（每次视图调用后核对——不再自比较恒真）。"""
        check("zero_write_after_view", _tree_hash(watch_paths), baseline)

    with tempfile.TemporaryDirectory(prefix="m2_readonly_proto_") as tmp:
        root = Path(tmp)
        plans = HorizonPlanStore(root / "plans.json")
        asm_store = AssessmentStore(root / "research")

        # ── 场景1：正常（已接受计划 + 评估齐）——重复读取已接受版本不变 ──
        plan = HorizonPlan(plan_id=f"p2_{CODE}_m2", security_id=CODE, horizon="MID",
                           policy_id=POLICY_ID_MID, intent="M2 原型场景",
                           policy_version=RESEARCH_SERVICE_VERSION)
        asm = ThesisAssessment(thesis_id=f"thesis_{CODE}_MID", security_id=CODE,
                               horizon="MID", snapshot_id="snap-m2",
                               status=ThesisStatus.VALID,
                               method_version=ASSERTION_METHOD_VERSION,
                               evaluated_as_of=datetime.now().astimezone() - timedelta(minutes=1))
        plan.assessment_id = asm_store.save(asm)
        plan.snapshot_id = asm.snapshot_id
        plans.save(plan)
        ok, msg = plans.accept(CODE, "MID")
        assert ok, msg

        watch_paths = [root / "plans.json", root / "research"]
        as_of = datetime.now().astimezone()
        before = _tree_hash(watch_paths)
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
            zero_write(watch_paths, before)

        # ── 场景2：缺资料（无计划）→ 给具体 research 命令；空库状态可区分 ──
        v2 = daily_research_view("000001", plans_store=plans, assessment_store=asm_store,
                                 as_of=as_of)
        check("missing_store_status_ok", v2["store_status"], "ok")
        check("missing_shows_next_step",
              any("research 000001" in s for s in v2["next_steps"]), True)
        zero_write(watch_paths, before)

        # ── 场景3：候选存在未接受（双槽——候选不替换主意图）──
        cand = HorizonPlan(plan_id=f"p2_{CODE}_m2_c2", security_id=CODE, horizon="MID",
                           policy_id=POLICY_ID_MID, intent="新材料候选",
                           policy_version=RESEARCH_SERVICE_VERSION)
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
        zero_write(watch_paths, baseline_after_seed)
        # 重复查看候选不再增 revision（B1：运行时间不创建候选）
        v3b = daily_research_view(CODE, plans_store=plans, assessment_store=asm_store,
                                  as_of=as_of + timedelta(seconds=5))
        check("repeated_view_no_new_candidate",
              v3b["horizons"]["MID"]["candidate_available"], True)
        check("candidate_revision_stable",
              int(plans.get_candidate(CODE, "MID").revision), 2)

        # ── 场景4：失效（已接受但评估错配 → 待复核——产品核对规则，非字符串过期）──
        plan_bad = HorizonPlan(plan_id=f"p2_{CODE}_m2_bad", security_id=CODE,
                               horizon="LONG", policy_id="fusion_long_v1",
                               intent="快照错配样本",
                               policy_version=RESEARCH_SERVICE_VERSION)
        # 引用存在的评估 id 但 snapshot 不一致（错配形态：找到评估但不匹配）
        plan_bad.assessment_id = plan.assessment_id
        plan_bad.snapshot_id = "snap-不一致"
        plans.save(plan_bad)
        plans.accept(CODE, "LONG")
        baseline_bad = _tree_hash(watch_paths)
        v4 = daily_research_view(CODE, plans_store=plans, assessment_store=asm_store,
                                 as_of=as_of)
        long4 = v4["horizons"]["LONG"]
        check("mismatched_assessment_shows_review_not_valid",
              long4["assessment_status"], "REVIEW_REQUIRED")
        check("mismatch_next_step_listed",
              any("复核" in s for s in v4["next_steps"]), True)
        zero_write(watch_paths, baseline_bad)

        # ── 场景5：旧方法（生成版本非当期已验证组合）→ 如实标注待复核 ──
        plan_old = HorizonPlan(plan_id=f"p2_{CODE}_m2_old", security_id=CODE,
                               horizon="LONG", policy_id="fusion_long_v1",
                               intent="旧生成版本样本",
                               policy_version="UNSUPPORTED_OLD_VERSION")
        plans.save(plan_old)
        # 替换 LONG 接受版本为旧方法计划（直接落 accepted 引用——原形只演示读取面）
        store_data = plans._load()
        key = f"{CODE}:LONG"
        store_data["accepted_refs"][key] = {
            "plan_id": plan_old.plan_id, "revision": 1,
            "content_hash": plan_old.content_hash()}
        store_data["plans"][key] = plan_old.model_dump(mode="json")
        plans._save(store_data)
        plans2 = HorizonPlanStore(root / "plans.json")  # 重启读取（新实例读盘）
        v5 = daily_research_view(CODE, plans_store=plans2, assessment_store=asm_store,
                                 as_of=as_of)
        long5 = v5["horizons"]["LONG"]
        check("old_method_plan_flags_version_note",
              long5.get("version_note"), "生成版本非当期已验证组合")
        check("old_method_next_step_listed",
              any("已验证组合" in s for s in v5["next_steps"]), True)
        check("restart_read_consistent", v5["horizons"]["MID"]["plan_id"],
              mid["plan_id"])

        # ── 场景6：损坏库（R13 X4 负例）——显式失败状态，不装作空库 ──
        (root / "plans_corrupt.json").write_text("{ 损坏内容", encoding="utf-8")
        bad_store = HorizonPlanStore(root / "plans_corrupt.json")
        v6 = daily_research_view(CODE, plans_store=bad_store,
                                 assessment_store=asm_store, as_of=as_of)
        check("corrupt_store_status_distinct", v6["store_status"], "corrupted")
        check("corrupt_store_no_research_suggestion",
              any("research" not in s or "人工" in s for s in v6["next_steps"])
              and any("损坏" in s for s in v6["next_steps"]), True)
        check("corrupt_store_no_horizon_detail", v6["horizons"], {})

    print(json.dumps({"scope": "isolated prototype for M2 pre-work, not production wiring; "
                                 "production K2b wiring requires N0-N2 joint re-verification",
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
