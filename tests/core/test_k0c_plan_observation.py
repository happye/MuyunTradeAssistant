"""K0c 计划生命周期与观察消费回归测试（plan/fusion iteration4 K0c，R10 A1–A4）

锁死语义（反例先红后绿）：
1. A1 研究计划双槽：同内容重跑幂等（不写计划、不增 revision、不撤接受）；
   新材料只写候选草稿（已接受版本与接受引用原样保留）；显式 accept 原子切换
2. A2 评估快照资格：计划与评估 snapshot **两侧非空且相等**才可消费；
   任一侧为空/错配 → 待复核（thesis）/诊断（观察）——空值不作通配
3. A3 影子去重：同分钟不再「同股即重复」——输入或输出不同必须留痕
   （同分钟计划/账户版本变更不丢）
4. A4 去重按完整输入+输出：同输入指纹但决策结果变化（WAIT→EXIT）跨分钟必须留痕

跑法：pytest tests/core/test_k0c_plan_observation.py -q
"""
import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.research_application import ResearchApplicationService
from src.core.shadow_diff import ShadowDiffRecord, _append_record
from src.data.horizon_plans import HorizonPlanStore
from src.data.research_store import AssessmentStore, ResearchStore

AS_OF = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)


# ── A1：研究计划双槽（真实应用服务链）──────────────────────

def _seed_quarter(store: ResearchStore, sec: str, y: int, q: int, metrics: dict, pub: str):
    period_end = f"{y:04d}-{q * 3:02d}-" + {1: "31", 2: "30", 3: "30", 4: "31"}[q]
    for metric, value in metrics.items():
        fin = {"metric": metric, "security_id": sec, "value": value,
               "unit": "倍" if metric != "netProfit" else "元",
               "period_kind": "cumulative", "period_end": period_end,
               "published_at": pub, "source_uri": f"baostock.q(s={sec},y={y},q={q})",
               "source_version": "baostock_financial_v1"}
        digest, _ = store.archive_raw(fin, metadata={"metric": metric,
                                                     "period_end": period_end})
        store.append_observation({
            "kind": "financial_quarterly", "security_id": sec, "metric": metric,
            "period_end": period_end, "value": value, "unit": fin["unit"],
            "raw_sha256": digest, "source_version": "baostock_financial_v1",
        })


def _metrics(cfo=0.9, roe=0.12, lta=0.55, cur=2.1, quick=1.8):
    return {"CFOToNP": cfo, "roeAvg": roe, "liabilityToAsset": lta,
            "currentRatio": cur, "quickRatio": quick}


def _make_app(tmp_path, fetch_metrics=None):
    store = ResearchStore(tmp_path / "research")

    def fetch_fn(sec, y, q):
        m = (fetch_metrics or {}).get((y, q))
        if m is None:
            return [], []
        _seed_quarter(store, sec, y, q, m, pub=f"{y}-0{min(q * 3, 9)}-27")
        return [], []

    app = ResearchApplicationService(
        store=store, plans_store=HorizonPlanStore(tmp_path / "plans.json"),
        assessment_store=AssessmentStore(tmp_path / "research"),
        fetch_fn=fetch_fn)
    return app


def _plans(tmp_path):
    return HorizonPlanStore(tmp_path / "plans.json")


def _isolation_assert(tmp_path):
    """隔离断言：计划/评估/归档全部在临时根。"""
    root = str(tmp_path.resolve())
    assert str((tmp_path / "plans.json").resolve()).startswith(root)
    assert str((tmp_path / "research").resolve()).startswith(root)


def test_a1_same_input_rerun_is_idempotent_and_keeps_acceptance(tmp_path):
    """A1 主反例：接受后同输入重跑 research——revision 不增、接受态保持 active
    （旧实现 rev+1 且 active=False 撤销接受）。"""
    _isolation_assert(tmp_path)
    app = _make_app(tmp_path, fetch_metrics={(2026, 2): _metrics()})
    r1 = app.run("600519", as_of=AS_OF, capture=True)
    ps = _plans(tmp_path)
    plan1 = ps.get("600519", "MID")
    assert plan1 is not None and plan1.revision == 1
    ok, msg = ps.accept("600519", "MID")
    assert ok, msg
    accepted = _plans(tmp_path).get("600519", "MID")
    assert ps.is_accepted_version(accepted)
    # 同输入重跑（新服务实例=重启口径）
    app_same = _make_app(tmp_path, fetch_metrics={(2026, 2): _metrics()})
    r2 = app_same.run("600519", as_of=AS_OF, capture=True)
    plan2 = _plans(tmp_path).get("600519", "MID")
    assert plan2.revision == accepted.revision, \
        f"同内容重跑 revision 不得递增: {accepted.revision}->{plan2.revision}"
    assert plan2.accepted_at == accepted.accepted_at, "同内容重跑不得改变接受态"
    assert ps.is_accepted_version(plan2), "同内容重跑不得撤接受（A1）"
    assert ps.get_candidate("600519", "MID") is None, "同内容重跑不产生候选"
    assert r2.draft_kinds.get("MID") == "idempotent"


def test_a1_new_material_writes_candidate_not_overwrite_accepted(tmp_path):
    """A1：新材料研究 → 候选草稿（主槽已接受版本原样+接受引用不动）；
    显式 accept 原子切换（主槽=候选版本、accepted_refs 指向新版本、候选清空）。"""
    _isolation_assert(tmp_path)
    app = _make_app(tmp_path, fetch_metrics={(2026, 2): _metrics()})
    app.run("600519", as_of=AS_OF, capture=True)
    ps = _plans(tmp_path)
    ps.accept("600519", "MID")
    accepted = _plans(tmp_path).get("600519", "MID")
    accepted_hash = accepted.content_hash()
    ref_before = ps._load()["accepted_refs"]["600519:MID"]
    # 新材料（多一季 2025Q4 归档）
    app2 = _make_app(tmp_path, fetch_metrics={(2025, 4): _metrics(cfo=0.8)})
    r2 = app2.run("600519", as_of=AS_OF, capture=True)
    # 写后用新实例读盘（HorizonPlanStore 实例缓存 _data——J3 同款「重启读盘口径」）
    ps2 = _plans(tmp_path)
    plan_after = ps2.get("600519", "MID")
    assert plan_after.content_hash() == accepted_hash, "新材料不得覆盖已接受版本"
    assert ps2.is_accepted_version(plan_after), "新材料研究不得撤接受"
    assert ps2._load()["accepted_refs"]["600519:MID"] == ref_before, "接受引用不动"
    cand = ps2.get_candidate("600519", "MID")
    assert cand is not None, "新材料必须产生候选草稿（不能静默丢弃）"
    assert cand.content_hash() != accepted_hash
    assert r2.draft_kinds.get("MID") == "candidate"
    # 显式 accept → 原子切换
    ok, msg = ps2.accept("600519", "MID")
    assert ok, msg
    plan3 = _plans(tmp_path).get("600519", "MID")
    expected_hash = cand.model_copy(update={"revision": cand.revision + 1}).content_hash()
    assert plan3.content_hash() == expected_hash, "接受后主槽=候选内容（revision 递增后）"
    assert ps2.is_accepted_version(plan3), "接受引用指向新版本"
    assert _plans(tmp_path).get_candidate("600519", "MID") is None, "切换后候选清空"


def test_a1_draft_rerun_same_content_no_revision_bump(tmp_path):
    """未接受的草稿 + 同内容重跑 → 同样幂等（不写不增 revision）。"""
    _isolation_assert(tmp_path)
    app = _make_app(tmp_path, fetch_metrics={(2026, 2): _metrics()})
    app.run("600519", as_of=AS_OF, capture=True)
    ps = _plans(tmp_path)
    plan1 = ps.get("600519", "MID")
    assert plan1.accepted_at is None
    app_same = _make_app(tmp_path, fetch_metrics={(2026, 2): _metrics()})
    app_same.run("600519", as_of=AS_OF, capture=True)
    plan2 = ps.get("600519", "MID")
    assert plan2.revision == plan1.revision, "同内容重跑不增 revision（草稿同理）"
    assert plan2.content_hash() == plan1.content_hash()


# ── A2：评估快照资格（两侧非空且相等）──────────────────────

def _mk_plan(tmp_path, plans, *, snapshot_id, assessment_id, accepted=True):
    from src.core.decision_policy import POLICY_ID_MID, HorizonPlan
    plan = HorizonPlan(plan_id="p_a2", security_id="600519", horizon="MID",
                       policy_id=POLICY_ID_MID, intent="A2 测试",
                       snapshot_id=snapshot_id, assessment_id=assessment_id)
    plans.save(plan)
    if accepted:
        plans.accept("600519", "MID")
    return plans.get("600519", "MID")


def test_a2_empty_assessment_snapshot_not_consumed(tmp_path):
    """A2 主反例：计划快照有值、评估 snapshot_id 为空字符串 → 不得消费 VALID
    （旧实现空值当通配跳过比较）——thesis 待复核、观察降诊断。"""
    from src.core.shadow_diff import _load_verified_assessment, _thesis_status_for
    from src.core.research import ThesisAssessment
    from src.core.research_service import ASSERTION_METHOD_VERSION
    from src.core.decision_contract import ThesisStatus
    plans = HorizonPlanStore(tmp_path / "plans.json")
    store = AssessmentStore(tmp_path / "research")
    aid = store.save(ThesisAssessment(thesis_id="t_a2", security_id="600519", horizon="MID",
                                      snapshot_id="", status=ThesisStatus.VALID,
                                      method_version=ASSERTION_METHOD_VERSION,
                                      evaluated_as_of=datetime.now().astimezone()))
    plan = _mk_plan(tmp_path, plans, snapshot_id="snap-expected", assessment_id=aid)
    asm = _load_verified_assessment(plan, "MID", "600519", store)
    assert asm is None, "评估缺快照不得通过核对（空值不作通配，A2）"
    assert _thesis_status_for(plan, "MID", "600519", store) is ThesisStatus.REVIEW_REQUIRED


def test_a2_plan_snapshot_empty_also_rejected(tmp_path, monkeypatch):
    """反方向：计划快照为空、评估快照有值 → 同样不消费（两侧非空）。"""
    from src.core.shadow_diff import _load_verified_assessment
    from src.core.research import ThesisAssessment
    from src.core.research_service import ASSERTION_METHOD_VERSION
    from src.core.decision_contract import ThesisStatus
    plans = HorizonPlanStore(tmp_path / "plans.json")
    store = AssessmentStore(tmp_path / "research")
    aid = store.save(ThesisAssessment(thesis_id="t2", security_id="600519", horizon="MID",
                                      snapshot_id="snap-real", status=ThesisStatus.VALID,
                                      method_version=ASSERTION_METHOD_VERSION,
                                      evaluated_as_of=datetime.now().astimezone()))
    plan = _mk_plan(tmp_path, plans, snapshot_id="", assessment_id=aid)
    assert _load_verified_assessment(plan, "MID", "600519", store) is None, \
        "计划缺快照同样不得消费（两侧非空）"


def test_a2_matching_nonempty_snapshots_still_consumed(tmp_path):
    """正例：两侧非空且相等 → 消费（原有合法路径保持）。"""
    from src.core.shadow_diff import _load_verified_assessment
    from src.core.research import ThesisAssessment
    from src.core.research_service import ASSERTION_METHOD_VERSION
    from src.core.decision_contract import ThesisStatus
    plans = HorizonPlanStore(tmp_path / "plans.json")
    store = AssessmentStore(tmp_path / "research")
    aid = store.save(ThesisAssessment(thesis_id="t3", security_id="600519", horizon="MID",
                                      snapshot_id="snap-both", status=ThesisStatus.VALID,
                                      method_version=ASSERTION_METHOD_VERSION,
                                      evaluated_as_of=datetime.now().astimezone()))
    plan = _mk_plan(tmp_path, plans, snapshot_id="snap-both", assessment_id=aid)
    asm = _load_verified_assessment(plan, "MID", "600519", store)
    assert asm is not None and asm.status is ThesisStatus.VALID


# ── A3/A4：影子去重按完整输入+输出 ─────────────────────────

def _rec(security_id="601318", as_of="2026-09-27T10:00:00+08:00", **kw):
    base = dict(legacy_action="HOLD_POSITION", legacy_desired="HOLD",
                fusion_mid_action="REVIEW", fusion_long_action="REVIEW")
    base.update(kw)
    return ShadowDiffRecord(security_id=security_id, as_of=as_of, **base)


def test_a3_same_minute_plan_version_change_is_saved(tmp_path):
    """A3：同分钟计划 v1→v2（输入指纹变化）→ 第二条必须落盘（不再被分钟去重吞）。"""
    store = tmp_path / "shadow.jsonl"
    r1 = _rec(input_fingerprint="fp_plan_v1", output_fingerprint="out_same")
    assert _append_record(store, r1) is True
    r2 = _rec(as_of="2026-09-27T10:00:30+08:00", input_fingerprint="fp_plan_v2",
              output_fingerprint="out_same")
    assert _append_record(store, r2) is True, "同分钟计划版本变更必须留痕（A3）"
    assert sum(1 for _ in open(store, encoding="utf-8")) == 2


def test_a3_same_minute_account_version_change_is_saved(tmp_path):
    """A3：同分钟账户版本 v1→v2 → 留痕（输入指纹含账户版本）。"""
    store = tmp_path / "shadow.jsonl"
    assert _append_record(store, _rec(input_fingerprint="acct_v1",
                                      output_fingerprint="out_x")) is True
    assert _append_record(store, _rec(as_of="2026-09-27T10:00:40+08:00",
                                      input_fingerprint="acct_v2",
                                      output_fingerprint="out_x")) is True, \
        "同分钟账户版本变更必须留痕"
    assert sum(1 for _ in open(store, encoding="utf-8")) == 2


def test_a4_cross_minute_output_change_is_saved(tmp_path):
    """A4：跨分钟、同输入指纹、决策结果变化（WAIT→EXIT/BLOCKED）→ 必须留痕
    （旧实现输出不在指纹内被吞）。"""
    store = tmp_path / "shadow.jsonl"
    assert _append_record(store, _rec(as_of="2026-09-27T10:00:00+08:00",
                                      input_fingerprint="fp_same",
                                      output_fingerprint="out_wait",
                                      fusion_mid_action="REVIEW",
                                      legacy_desired="HOLD")) is True
    assert _append_record(store, _rec(as_of="2026-09-27T10:02:00+08:00",
                                      input_fingerprint="fp_same",
                                      output_fingerprint="out_exit",
                                      fusion_mid_action="EXIT",
                                      legacy_desired="HOLD")) is True, \
        "同输入不同输出（决策结果变化）跨分钟必须留痕（A4）"
    assert sum(1 for _ in open(store, encoding="utf-8")) == 2


def test_a4_true_repeat_still_deduped_with_receipt(tmp_path):
    """真正重复（同输入+同输出）仍去重；回执可区分 saved/deduped。"""
    store = tmp_path / "shadow.jsonl"
    r1 = _rec(input_fingerprint="fp", output_fingerprint="out")
    assert _append_record(store, r1) is True
    r2 = _rec(as_of="2026-09-27T10:05:00+08:00", input_fingerprint="fp",
              output_fingerprint="out")
    assert _append_record(store, r2) is False, "同输入同输出同日重复=去重"
    assert sum(1 for _ in open(store, encoding="utf-8")) == 1


# ── K0c-5：精确接受引用与数量确认接线 ──────────────────────

def _plan_store_with(tmp_path, plans_spec):
    """plans_spec: [(plan_id, horizon)] → 每个一份计划（未接受）。"""
    from src.core.decision_policy import POLICY_ID_MID, POLICY_ID_LONG, HorizonPlan
    from src.core.decision_contract import Horizon
    ps = HorizonPlanStore(tmp_path / "plans.json")
    for plan_id, horizon in plans_spec:
        ps.save(HorizonPlan(plan_id=plan_id, security_id="600519",
                            horizon=Horizon[horizon],
                            policy_id=POLICY_ID_MID if horizon == "MID" else POLICY_ID_LONG,
                            intent="K0c-5 测试"))
    return ps


def test_k0c5_account_service_resolves_plan_ref_by_id(tmp_path):
    """K0c-5（监督审查裁决口径）：股数是事实——计划引用不存在/与主意图不符时
    **放行入账并标偏离**（不虚构「已按计划成交」，也不拒绝真实成交）。"""
    from src.data.account_service import AccountService, FillInput
    ps = _plan_store_with(tmp_path, [("p_mid_1", "MID"), ("p_long_1", "LONG")])
    svc = AccountService(tmp_path / "events.jsonl", plans_store=ps)
    payload = FillInput(security_id="600519", action="BUY", quantity=100, price=10.0,
                        trade_date="2026-09-20", plan_ref="p_mid_1")
    r1 = svc.confirm_fill("f1", "", payload)
    assert r1.status == "ACCEPTED" and r1.ok
    e1 = next(e for e in svc.log.events() if e.security_id == "600519")
    assert not e1.model_dump().get("deviation_from_proposal"), "引用一致不标偏离"
    # 引用不存在（过期/被删）→ 放行入账 + 标偏离（不拒绝真实成交）
    r2 = svc.confirm_fill("f2", "", FillInput(security_id="600519", action="BUY",
                                              quantity=100, price=10.0,
                                              trade_date="2026-09-21", plan_ref="p_wrong"))
    assert r2.status == "ACCEPTED" and r2.ok, "引用不存在放行真实成交（股数是事实）"
    e2 = next(e for e in svc.log.events() if e.event_id == "fill_f2")
    assert e2.model_dump().get("deviation_from_proposal") is True, "偏离必须标注"
    assert "偏离" in (e2.model_dump().get("fill_note") or "")
    # 主意图 active_ref 指向 MID——BUY 引用指向存在但非主意图的计划（LONG）
    # → 放行入账 + 标偏离（注记说明与主意图不符）
    ps.accept("600519", "MID")
    ps.set_active_ref("600519", ps.get("600519", "MID"))
    r3 = svc.confirm_fill("f3", "", FillInput(security_id="600519", action="BUY",
                                              quantity=100, price=10.0,
                                              trade_date="2026-09-22", plan_ref="p_long_1"))
    assert r3.status == "ACCEPTED" and r3.ok
    e3 = next(e for e in svc.log.events() if e.event_id == "fill_f3")
    assert e3.model_dump().get("deviation_from_proposal") is True, "与主意图不符标偏离"
    assert "主意图" in (e3.model_dump().get("fill_note") or "")


def test_k0c5_cli_confirm_records_active_ref_in_event(tmp_path):
    """CLI 数量确认连接计划库：主意图 active_ref 的 plan_id 随事件保留（plan_ref）。"""
    import io as _io
    from unittest.mock import patch as _patch
    from rich.console import Console as _Console
    import src.cli.main as cli_main
    import src.data.account_service as account_module
    import src.data.horizon_plans as horizon_plans_module
    from src.data.account_service import AccountService
    from src.data.portfolio import PortfolioManager
    from src.data.proposals import Proposal
    folder = tmp_path / "k0c5"
    folder.mkdir()
    pm = PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))
    pm.add_position("600519", stock_name="测试股", entry_price=10, ratio=0.1)
    pm._proposals.add_proposal(Proposal(stock_code="600519", stock_name="测试股",
                                        position_action="ADD", target_ratio=0.15,
                                        current_ratio=0.1))
    pm._proposals._save()
    ps = _plan_store_with(tmp_path, [("p_mid_9", "MID")])
    mid_plan = ps.get("600519", "MID")
    ps.accept("600519", "MID")
    ps.set_active_ref("600519", ps.get("600519", "MID"))
    ledger = folder / "events.jsonl"
    svc = AccountService(ledger)
    svc.opening_import(opening_cash=100000.0, trade_date="2026-09-01",
                       lots=[{"security_id": "600519", "quantity": 300,
                              "cost_price": 10.0, "acquired_at": "2026-09-01"}])
    output = _io.StringIO()
    with _patch.object(cli_main, "PortfolioManager", return_value=pm), \
         _patch.object(cli_main, "console", _Console(file=output, width=200)), \
         _patch.object(account_module, "DEFAULT_LEDGER_PATH", ledger), \
         _patch.object(horizon_plans_module, "PLANS_FILE", tmp_path / "plans.json"):
        cli_main.manage_positions("confirm", stock_code="600519", qty=100, price=15,
                                  trade_date="2026-09-10")
    ev = svc.log.events()[-1]
    assert ev.model_dump().get("plan_ref") == "p_mid_9", \
        f"主意图引用须随事件保留: {ev.model_dump().get('plan_ref')}"
    assert ev.event_type.value == "BUY"


def test_k0c2_assessment_store_content_address_integrity(tmp_path):
    """K0c-2：评估文件内容与内容寻址 id 不一致 → 按坏文件隔离（返回 None）——
    唯一真值不被损坏/篡改记录冒充。"""
    import json as _json
    from src.core.decision_contract import ThesisStatus
    from src.core.research import ThesisAssessment
    from src.core.research_service import ASSERTION_METHOD_VERSION
    store = AssessmentStore(tmp_path / "research")
    aid = store.save(ThesisAssessment(
        thesis_id="t_itg", security_id="600519", horizon="MID",
        snapshot_id="snap-itg", status=ThesisStatus.VALID,
        method_version=ASSERTION_METHOD_VERSION,
        evaluated_as_of=datetime.now().astimezone()))
    assert store.load(aid) is not None
    # 篡改内容（沿用旧文件名/旧登记 id）→ 重算指纹不符 → 隔离
    path = store.dir / "assessments" / f"{aid}.json"
    data = _json.loads(path.read_text(encoding="utf-8"))
    data["assessment"]["status"] = "INVALID"
    path.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert store.load(aid) is None, "内容与寻址 id 不一致=坏文件隔离（K0c-2）"
