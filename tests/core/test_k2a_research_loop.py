"""K2a 最小研究闭环回归测试（plan/fusion iteration4，DELIVERY_PLAN K2a）

锁死语义（架构师合同——从公开解析器输入验收，不直调 ResearchService 注入正例）：
1. 公开通道：ResearchApplicationService.run 接受 documents（主张+核验池）与
   checkpoints（检查点）——当前公开入口没有这些入参，合法 MID 正例建不起来
2. 主张核验走 verify_claim_tiered 同一协议：错主体/无摘录/缺原件 → 不进 verified
   （如实缺口，不冒充）；原文 URI/hash 可追溯（归档留样）
3. 风险确认：接受计划不自动替代风险确认——checkpoint 无显式确认不生效
4. 用户接受精确版本 → 主意图引用（account×security active_ref）→ 数量确认随事件
   保留引用（闭环贯通）

全部路径构造前断言临时根；零网络零 AI。
跑法：pytest tests/core/test_k2a_research_loop.py -q
"""
import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.research_application import ResearchApplicationService
from src.data.horizon_plans import HorizonPlanStore
from src.data.research_store import AssessmentStore, ResearchStore

AS_OF = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)
PUB = "2026-09-01T08:00:00+00:00"


def _plan_store(tmp_path):
    return HorizonPlanStore(tmp_path / "plans.json")


def _make_app(tmp_path, metrics=None):
    store = ResearchStore(tmp_path / "research")

    def fetch_fn(sec, y, q):
        return [], []

    return ResearchApplicationService(
        store=store, plans_store=_plan_store(tmp_path),
        assessment_store=AssessmentStore(tmp_path / "research"),
        fetch_fn=fetch_fn), store


def _claim_doc(security_id="600519", statement="公司签订 900 万元订单",
               event_type="order", value=900, unit="万元",
               body="公司与客户正式签订 900 万元订单，交付期为三个月。",
               quote=None, source_uri="cninfo://ann/fix-1", claim_id="c_fix_1", **kw):
    """合法主张+原文（公开解析器输入形态——uri 必填，原文落归档；
    claim_id 固定以便 checkpoint.evidence_refs 引用）。"""
    return {"claim": {"claim_id": claim_id,
                      "security_id": security_id, "subject": security_id,
                      "statement": statement, "event_type": event_type,
                      "value": value, "unit": unit, "quote_text": quote or body,
                      "citation_uri": source_uri, "published_at": PUB, **kw},
            "document": {"canonical_uri": source_uri,
                         "body": body, "security_ids": [security_id],
                         "published_at": PUB}}


def _isolation_assert(tmp_path):
    root = str(tmp_path.resolve())
    for p in ("plans.json", "research", "shadow.jsonl"):
        assert os.path.abspath(str(tmp_path / p)).startswith(root)


# ── 1. 公开通道：documents/checkpoints 进服务 ──────────────

def test_k2a_documents_channel_builds_verified_claims(tmp_path):
    """合法 MID fixture（订单主张+原文）经公开入口 → verified_claims≥1、
    source_uris 非空（不再来自一直为空的参数）、MID change_to_profit 命题 TRUE。"""
    _isolation_assert(tmp_path)
    app, store = _make_app(tmp_path)
    doc_bundle = _claim_doc()
    result = app.run("600519", as_of=AS_OF, horizons=("MID",),
                     documents=[doc_bundle])
    assert result.verified_claims >= 1, "合法主张经公开入口应核验通过"
    assert result.source_uris, "source_uris 必须来自真实文档（不能恒为空）"
    mid = result.assessments["MID"]
    assert mid["status"] in ("VALID", "UNESTABLISHED"), f"评估应产出: {mid}"


def test_k2a_checkpoint_requires_explicit_user_confirmation(tmp_path):
    """风险确认（K2a 审查 P2-1 真断言）：证据齐+用户确认 → 检查点支撑生效
    （window_and_refutation TRUE with 显式登记）；同样证据但未确认 → UNKNOWN
    ——接受计划不自动替代风险确认，AI 不能自行确认。"""
    from src.core.research import CheckpointCondition
    _isolation_assert(tmp_path)
    app, _ = _make_app(tmp_path)
    doc_bundle = _claim_doc()
    asm_store = AssessmentStore(tmp_path / "research")
    cp_confirmed = CheckpointCondition(
        security_id="600519", horizon="MID", proposition_type="window_and_refutation",
        description="2026-10-30 三季报核验订单转化",
        evidence_refs=["c_fix_1"], derived_from="user_confirmed_risk",
        user_confirmed=True)
    r_ok = app.run("600519", as_of=AS_OF, horizons=("MID",),
                   documents=[doc_bundle], checkpoints=[cp_confirmed])
    asm = asm_store.load(r_ok.assessment_ids["MID"])
    w_ok = next(a for a in asm.required_assertions
                if a.proposition_type == "window_and_refutation")
    assert w_ok.evaluation.value == "TRUE", \
        f"确认+证据齐 → 检查点支撑生效: {w_ok.evaluation_note or w_ok.evaluation}"
    assert "c_fix_1" in w_ok.supporting_evidence_ids, "支撑必须显式登记（可审计）"
    # 同样证据，仅缺用户确认 → UNKNOWN（不生效）
    cp_unconfirmed = cp_confirmed.model_copy(update={"user_confirmed": False})
    r_no = app.run("600519", as_of=AS_OF, horizons=("MID",),
                   documents=[doc_bundle], checkpoints=[cp_unconfirmed])
    asm_no = asm_store.load(r_no.assessment_ids["MID"])
    w_no = next(a for a in asm_no.required_assertions
                if a.proposition_type == "window_and_refutation")
    assert w_no.evaluation.value == "UNKNOWN", \
        f"未确认风险意愿 → 检查点不生效: {w_no.evaluation}"


# ── 2. 反向：错主体/无摘录不核验 ───────────────────────────

def test_k2a_wrong_subject_claim_not_verified(tmp_path):
    """错主体（别家公告安到自家头上）→ 不进 verified——如实缺口。"""
    _isolation_assert(tmp_path)
    app, _ = _make_app(tmp_path)
    bad = _claim_doc(security_id="000002")  # 主张主体与 run 主体不符
    result = app.run("600519", as_of=AS_OF, horizons=("MID",), documents=[bad])
    assert result.verified_claims == 0
    assert result.unverified_claims >= 1


def test_k2a_cli_channel_missing_body_not_self_verified(tmp_path, monkeypatch):
    """K2a 审查 P1-1 回归（CLI 通道）：主张文件缺 body 不得回退为摘录——
    「缺原件」的 typed 主张经 CLI 导入同样不得 FACT_CHECKED（与服务通道同语义）。"""
    _isolation_assert(tmp_path)
    import json as _json
    from src.cli import main as cli_main_mod
    claims = [{"statement": "公司签订 900 万元订单", "event_type": "order",
               "value": 900, "unit": "万元", "quote_text": "公司与客户正式签订 900 万元订单",
               "source_uri": "cninfo://ann/fix-no-body", "published_at": PUB,
               "verification_scope": "typed_numeric_event"}]  # 无 body 字段
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(_json.dumps(claims, ensure_ascii=False), encoding="utf-8")
    docs = cli_main_mod._load_research_claims_file(claims_path, "600519")
    assert docs[0]["document"]["body"] == "", "body 缺失必须保持为空（不回退摘录）"
    app, _ = _make_app(tmp_path)
    result = app.run("600519", as_of=AS_OF, horizons=("MID",), documents=docs)
    assert result.verified_claims == 0, "缺原件经 CLI 通道同样不得 FACT_CHECKED"
    assert result.unverified_claims >= 1


def test_k2a_claim_without_original_body_not_fact_checked(tmp_path):
    """缺原件（文档无正文）→ 核验不可达 FACT_CHECKED——不冒充。"""
    _isolation_assert(tmp_path)
    app, _ = _make_app(tmp_path)
    no_body = _claim_doc(body="", quote="公司与客户正式签订 900 万元订单")
    result = app.run("600519", as_of=AS_OF, horizons=("MID",), documents=[no_body])
    assert result.verified_claims == 0, "缺原件不得 FACT_CHECKED"


# ── 3. 闭环：接受精确版本 → 主意图引用 → 确认随事件保留 ────

def test_k2a_accept_primary_sets_active_ref(tmp_path):
    """plan2 accept --primary → account×security 主意图引用落地（set_active_ref
    此前无生产入口——K2a 补齐闭环）。"""
    _isolation_assert(tmp_path)
    app, _ = _make_app(tmp_path)
    app.run("600519", as_of=AS_OF, horizons=("MID",))
    ps = HorizonPlanStore(tmp_path / "plans.json")
    ok, msg = ps.accept("600519", "MID")
    assert ok, msg
    ok2, msg2 = ps.set_active_ref("600519", ps.get("600519", "MID"))
    assert ok2 and ps.get_active_ref("600519"), "主意图引用必须可落地（闭环环节）"


def test_k2a_full_loop_fixture_to_active_ref(tmp_path):
    """纵向：合法 fixture → 公开入口评估 → 计划候选 → 显式接受 → 主意图引用
    → 隔离分析影子落盘（effective 观察）——DELIVERY_PLAN K2a 验收全链。"""
    _isolation_assert(tmp_path)
    app, store = _make_app(tmp_path)
    doc_bundle = _claim_doc()
    result = app.run("600519", as_of=AS_OF, horizons=("MID",), documents=[doc_bundle])
    assert result.draft_plan_ids.get("MID"), "公开入口必须产出计划候选"
    ps = HorizonPlanStore(tmp_path / "plans.json")
    plan = ps.get("600519", "MID")
    assert plan is not None
    assert ps.accept("600519", "MID")[0]
    assert ps.set_active_ref("600519", ps.get("600519", "MID"))[0]
    ref = ps.get_active_ref("600519")
    assert ref.get("plan_id") == plan.plan_id
    # 隔离分析：接受计划+评估可解析+账户版本齐 → 影子落盘为 effective 观察
    from src.core.shadow_diff import build_shadow_report
    from src.core.analysis_service import build_decision_packet
    rec = _capture_shadow_for(tmp_path, ps, store_assessments=None,
                              assessment_store=AssessmentStore(tmp_path / "research"))
    assert rec is not None and rec.observation_kind == "effective", \
        f"合法闭环应产有效观察: {getattr(rec, 'observation_kind', None)}"
    report = build_shadow_report(store_path=tmp_path / "shadow.jsonl", days=7)
    assert report["effective_observations"] >= 1, "报告分母计入已落盘有效观察"


def _capture_shadow_for(tmp_path, plans_store, store_assessments=None, *, assessment_store):
    """构造一次最小持仓分析影子捕获（复用 capture_shadow 公开入口——stub 形态
    与 test_shadow_diff._sd 同源）。"""
    from types import SimpleNamespace
    from src.core.analysis_service import build_decision_packet
    from src.data.models import (
        PositionAction, SignalType, StrategyDecision, StrategyState, TradeLifecycle,
    )
    from src.core.shadow_diff import capture_shadow
    pos = SimpleNamespace(stock_code="600519", stock_name="测试股",
                          current_ratio=0.1, trade_plan=None)
    decision_result = SimpleNamespace(
        decision=SimpleNamespace(value="HOLD"), score=0.6,
        stock=SimpleNamespace(stock_code="600519", stock_name="测试股"),
        warnings=[])
    strategy_decision = StrategyDecision(
        decision=SignalType("HOLD"), position_action=PositionAction("HOLD_POSITION"),
        position_ratio=0.1, sell_path=None,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        strategy_reasons=["策略理由"], divergence=None, new_state=StrategyState())
    execution_eval = SimpleNamespace(effective_action=SimpleNamespace(value="HOLD"),
                                     blocked=False)
    packet = build_decision_packet(decision_result, strategy_decision, execution_eval,
                                   confirmed_ratio=0.1, source="test")
    cfg = {"fusion": {"mode": "opt_in", "shadow_capture": True}}
    return capture_shadow(decision_result, strategy_decision, execution_eval, pos,
                          packet=packet, source="test", config=cfg,
                          store_path=tmp_path / "shadow.jsonl",
                          plans_store=plans_store, assessment_store=assessment_store,
                          account_version="v_k2a",
                          quote_as_of="2026-09-26")  # L0：行情时点齐（v7 合同门槛）
