"""J3 研究应用服务回归测试（plan/fusion iteration3，DELIVERY_PLAN J3 验收）

锁死语义：
1. 研究链全程真实服务跑通：归档读取→合格因子→评估持久化→草稿入计划库→
   plan2 找回/接受；重启（新服务实例）同输入续跑——已归档季度零重复抓取
2. 因子输入由合格快照派生（SUSPECT 隔离后计算）+ 绑定同一 snapshot——
   raw dict 旁路在应用层闭合（无「未绑定合格快照」缺口）
3. 修订草稿：已有计划 revision+1、accepted_at 置空（不沿用旧接受）、
   supersedes_ref 留沿革；接受记录保留可审计
4. --json 与 CLI 渲染同数据源（ResearchRunResult）；
   chat/Web/TUI 显式未支持（UNSUPPORTED_ENTRYPOINTS 锁定）

纯内存+临时文件测试，零网络零 AI，不读写真实 HOME。
跑法：pytest tests/core/test_j3_research_application.py -q
"""
import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.research_application import (
    UNSUPPORTED_ENTRYPOINTS,
    ResearchApplicationService,
    ResearchRunResult,
)
from src.data.research_store import AssessmentStore, ResearchStore

AS_OF = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)


def _seed_quarter(store: ResearchStore, sec: str, y: int, q: int,
                  metrics: dict, pub: str):
    """按 capture 的落盘形态造一季归档（raw 留样 + 观测索引）。"""
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


def _make_app(tmp_path, fetch_metrics=None):
    """应用服务 + 注入采集（fetch_metrics: {quarter_idx: metrics}——首采后即归档）。"""
    store = ResearchStore(tmp_path / "research")
    calls = {"n": 0}

    def fetch_fn(sec, y, q):
        calls["n"] += 1
        m = (fetch_metrics or {}).get((y, q))
        if m is None:
            return [], []
        _seed_quarter(store, sec, y, q, m, pub=f"{y}-0{min(q * 3, 9)}-27")
        return [], []

    app = ResearchApplicationService(
        store=store, plans_store=_plans_store(tmp_path),
        assessment_store=AssessmentStore(tmp_path / "research"),
        fetch_fn=fetch_fn)
    return app, store, calls


def _plans_store(tmp_path):
    from src.data.horizon_plans import HorizonPlanStore
    return HorizonPlanStore(tmp_path / "plans.json")


def _metrics(cfo=0.9, roe=0.12, lta=0.55, cur=2.1, quick=1.8):
    # currentRatio/quickRatio 使 balance_risk_v1 可算；liabilityToAsset 默认值
    # 落在映射范围外报告期（2025+）→ 会被 J1 范围门 SUSPECT 隔离——因子不含它
    return {"CFOToNP": cfo, "roeAvg": roe, "liabilityToAsset": lta,
            "currentRatio": cur, "quickRatio": quick, "netProfit": 1e9}


# ── 1. 研究链全程 + 断点续跑 ───────────────────────────────

def test_full_chain_persists_drafts_and_assessments(tmp_path):
    """研究链真实服务跑通：评估持久化 + 草稿入计划库 + plan2 找回。"""
    app, store, calls = _make_app(tmp_path, fetch_metrics={
        (2026, 2): _metrics(), (2026, 1): _metrics(),
        (2025, 4): _metrics(), (2025, 3): _metrics()})
    result = app.run("600519", as_of=AS_OF, capture=True)
    assert isinstance(result, ResearchRunResult)
    assert result.steps["documents"]["quarters_fetched"] == 4
    assert result.steps["documents"]["quarters_archived_skipped"] == 0
    # 评估唯一真值已持久化（J0b）
    assert all(aid.startswith("asm_") for aid in result.assessment_ids.values())
    for aid in result.assessment_ids.values():
        assert store.load_raw is not None  # store 存活
        from src.data.research_store import AssessmentStore
        assert AssessmentStore(tmp_path / "research").load(aid) is not None
    # 草稿已存 HorizonPlanStore（plan2 找回语义）
    for h, pid in result.draft_plan_ids.items():
        plan = _plans_store(tmp_path).get("600519", h)
        assert plan is not None and plan.plan_id == pid
        assert plan.assessment_id == result.assessment_ids[h]
        assert plan.accepted_at is None, "系统草稿未激活"


def test_restart_skips_fetch_for_archived_quarters(tmp_path):
    """重启（新服务实例）同输入续跑：已归档季度零重复抓取（documents 步断点）；
    归档证据重建出同样的评估输入（run_id 一致=同输入确定性重放）。"""
    app, store, calls = _make_app(tmp_path, fetch_metrics={
        (2026, 2): _metrics(), (2026, 1): _metrics(),
        (2025, 4): _metrics(), (2025, 3): _metrics()})
    r1 = app.run("600519", as_of=AS_OF, capture=True)
    assert calls["n"] == 4
    app2, _, _ = _make_app(tmp_path)  # 全新实例（模拟重启）——无 fetch 能力
    result2 = app2.run("600519", as_of=AS_OF, capture=False)
    assert result2.steps["documents"]["quarters_archived_skipped"] == 4
    assert result2.steps["documents"]["quarters_fetched"] == 0
    assert result2.snapshot_id
    assert result2.run_id == r1.run_id, "同输入（同归档证据）→ 同 run_id（确定性重放）"


def test_offline_without_archive_gaps_not_fake(tmp_path):
    """无归档不联网 → 如实「缺失 N 季」缺口，不假装已读资料。"""
    app, _, _ = _make_app(tmp_path)
    result = app.run("600519", as_of=AS_OF, capture=False)
    assert result.steps["documents"]["quarters_missing"] == 4
    assert result.steps["documents"]["evidence_records"] == 0


# ── 2. 因子合格快照派生（旁路闭合）────────────────────────

def test_factors_from_qualified_snapshot_with_binding(tmp_path):
    """因子由合格快照派生并绑定同一 snapshot——无「未绑定合格快照」缺口；
    映射范围外的 liabilityToAsset（SUSPECT）不进因子输入。"""
    app, store, calls = _make_app(tmp_path, fetch_metrics={
        (2026, 2): _metrics(), (2026, 1): _metrics(),
        (2025, 4): _metrics(), (2025, 3): _metrics()})
    result = app.run("600519", as_of=AS_OF, capture=True)
    assert result.steps["verified"]["factors"]["balance_risk_v1"] == "OK", result.steps
    assert result.steps["verified"]["suspect_isolated"] >= 1, \
        "范围外负债率被 J1 范围门隔离（不进因子）"
    assert not any("未绑定合格快照" in g for g in result.gaps), result.gaps


def test_run_result_json_serializable(tmp_path):
    """--json 与 CLI 渲染同数据源：ResearchRunResult 可序列化。"""
    app, _, _ = _make_app(tmp_path, fetch_metrics={(2026, 2): _metrics()})
    result = app.run("600519", as_of=AS_OF, capture=True)
    payload = json.loads(json.dumps(result.model_dump(mode="json"), default=str))
    assert payload["security_id"] == "600519"
    assert "MID" in payload["assessments"]


# ── 3. 修订草稿与接受 ─────────────────────────────────────

def test_draft_revision_and_accept_semantics(tmp_path):
    """已有计划 → revision+1 修订草稿（accepted_at 置空——不沿用旧接受）；
    接受账目保留旧版本可审计；再研究再 +1。"""
    app, _, _ = _make_app(tmp_path, fetch_metrics={(2026, 2): _metrics()})
    r1 = app.run("600519", as_of=AS_OF, capture=True)
    ps = _plans_store(tmp_path)
    plan1 = ps.get("600519", "MID")
    assert plan1.revision == 1
    # 用户接受（MID/LONG 双计划并存——显式指定 horizon）
    ok, msg = ps.accept("600519", "MID")
    assert ok, msg
    accepted_plan = _plans_store(tmp_path).get("600519", "MID")  # 接受时点版本
    assert ps.is_accepted_version(accepted_plan)
    # 新一轮研究（输入变化 → 新 run）→ 修订草稿不沿用旧接受（fresh 实例读盘防缓存）
    app2, _, _ = _make_app(tmp_path, fetch_metrics={(2025, 4): _metrics(cfo=0.8)})
    r2 = app2.run("600519", as_of=AS_OF, capture=True)
    plan2 = _plans_store(tmp_path).get("600519", "MID")  # 新实例=重启读盘口径
    # accept 与草稿保存各写一次、各递增一次（store.save 统一管理 revision）
    assert plan2.revision == plan1.revision + 2, \
        f"accept(+1) 与草稿保存(+1) 恰各递增一次: {plan2.revision}"
    assert plan2.accepted_at is None, "新事实 → 修订草稿，不沿用旧接受"
    assert plan2.supersedes_ref == accepted_plan.content_hash(), "版本沿革指向紧邻前版"
    ref = _plans_store(tmp_path)._load()["accepted_refs"]["600519:MID"]
    assert ref["content_hash"] == accepted_plan.content_hash(), "接受记录与绑定版本一致"


# ── 4. 入口支持面（显式未支持）────────────────────────────

def test_unsupported_entrypoints_declared():
    """chat/Web/TUI 未适配研究入口——显式声明（路由测试锁定，不冒充已支持）。"""
    assert set(UNSUPPORTED_ENTRYPOINTS) == {"chat", "web", "tui"}
