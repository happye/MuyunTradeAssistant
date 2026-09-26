"""R4 候选漏斗机器核对 + 法C 分片预算 + 行业双时间轴回归测试

锁死语义（RESEARCH_LOOP §3 / VALIDATION E1b / DATA_TRUST §5）：
1. 漏斗报告：原始/路内配额/跨路去重/最终各阶段计数**可机器核对**（原始130→配额后35 不再人抄）；
   缺字段候选明确完成度，不作为成功执行全部过滤
2. 分片召回：预算硬顶（耗尽抛错不无限重试）；单分片失败保留已核实实体 + 覆盖缺口显式登记；
   截断分片进缺口；重试计入总预算
3. 行业成员双时间轴：当前值接口（known_from=今天）对历史 as_of 不 strict——不回填上市日

纯内存测试，无网络。跑法：pytest tests/core/test_r4_recall_and_funnel.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.candidate_pool import (
    CandidateRoute,
    ShardRecallBudgetExhausted,
    build_candidate_set,
    funnel_report,
    run_sharded_recall,
)
from src.data.research_store import IndustryMembership, industry_membership_from_current


def _route_input(n, route_rule="rule", *, start=0, ranked=True):
    return [{"stock_code": f"{600000 + start + i:06d}", "stock_name": f"股{i}",
             "rule_name": route_rule, "rule_version": "v1",
             "rank_in_source": (i if ranked else None)}
            for i in range(n)]


# ── 1. 漏斗机器核对 ────────────────────────────────────────

def test_funnel_report_machine_checkable():
    """R4 验收4：原始 130 → 配额后 35 这类差异逐阶段机器可核。"""
    route_inputs = {
        CandidateRoute.TECHNICAL: _route_input(50, "回调", start=0),
        CandidateRoute.INDUSTRY: _route_input(75, "半导体", start=50),
        CandidateRoute.LONG_QUALITY: _route_input(5, "质量", start=125),
    }
    cs = build_candidate_set(route_inputs, quotas={CandidateRoute.TECHNICAL: 20,
                                                   CandidateRoute.INDUSTRY: 10,
                                                   CandidateRoute.LONG_QUALITY: 5})
    rep = funnel_report(cs, route_inputs)
    assert rep["raw_total"] == 130
    assert rep["per_route"]["TECHNICAL"]["raw"] == 50
    assert rep["per_route"]["TECHNICAL"]["excluded_quota"] == 30
    assert rep["per_route"]["INDUSTRY"]["excluded_quota"] == 65
    assert rep["final_unique"] == len(cs.candidates) == 35
    assert rep["after_route_quota"] == 35
    # 一致性：报告数与集合内部记录互洽
    assert rep["union_dedup_merged"] == cs.dedup_count
    assert rep["invalid_skipped"] == cs.skipped_invalid


def test_funnel_report_flags_completeness_gaps():
    """缺字段/截断候选显式汇总——不作为「成功执行全部过滤」。"""
    items = [{"stock_code": "600001", "rule_name": "r", "missing_filters": ["volume_ratio_missing"],
              "rank_in_source": 1},
             {"stock_code": "600002", "rule_name": "r", "rank_in_source": 2,
              "rank_complete": False, "source_truncation_reason": "max_candidates=30"}]
    cs = build_candidate_set({CandidateRoute.TECHNICAL: items})
    rep = funnel_report(cs, {CandidateRoute.TECHNICAL: items})
    assert set(rep["candidates_with_gaps"]) == {"600001", "600002"}
    assert "不算全部过滤成功" in rep["completeness_note"]


# ── 2. 分片召回预算 ────────────────────────────────────────

def test_sharded_recall_success_and_truncation_gap():
    shards = [{"shard_id": "s1", "coverage": "上游衬底"}, {"shard_id": "s2", "coverage": "芯片设计"}]
    calls = []

    def call(shard):
        calls.append(shard["shard_id"])
        if shard["shard_id"] == "s2":
            return {"entities": [{"code": "688001"}], "truncated": True}
        return {"entities": [{"code": "600001"}, {"code": "600002"}]}

    out = run_sharded_recall(shards, call, max_requests=10)
    assert len(out["entities"]) == 3
    assert out["requests_used"] == 2 and out["retries_used"] == 0
    assert len(out["coverage_gaps"]) == 1 and out["coverage_gaps"][0]["shard_id"] == "s2"
    assert "截断" in out["coverage_gaps"][0]["error"]


def test_sharded_recall_failure_keeps_verified_entities_and_gap():
    """R4 验收5：单分片失败只丢该片——已核实实体保留 + 缺口显式登记。"""
    def call(shard):
        if shard["shard_id"] == "bad":
            raise RuntimeError("AI 输出结构非法")
        return {"entities": [{"code": "600001"}]}

    out = run_sharded_recall([{"shard_id": "good", "coverage": "A"},
                              {"shard_id": "bad", "coverage": "B"}], call, max_requests=10)
    assert [e["code"] for e in out["entities"]] == ["600001"]
    assert out["coverage_gaps"][0]["shard_id"] == "bad" and "结构非法" in out["coverage_gaps"][0]["error"]


def test_sharded_recall_budget_hard_cap_including_retries():
    """预算硬顶：重试计入总预算；耗尽抛错且不超限。"""
    calls = {"n": 0}

    def call(shard):
        calls["n"] += 1
        raise RuntimeError("always fails")

    with pytest.raises(ShardRecallBudgetExhausted) as ei:
        run_sharded_recall([{"shard_id": "s"} for _ in range(10)], call,
                           max_requests=3, max_retries_per_shard=2)
    assert calls["n"] == 3, "请求总数不超预算（重试计入）"
    assert "预算耗尽" in str(ei.value) and "保留" in str(ei.value)


def test_sharded_recall_retry_within_budget_recovers():
    flaky = {"n": 0}

    def call(shard):
        flaky["n"] += 1
        if flaky["n"] == 1:
            raise RuntimeError("transient")
        return {"entities": [{"code": "600001"}]}

    out = run_sharded_recall([{"shard_id": "s"}], call, max_requests=5, max_retries_per_shard=1)
    assert out["entities"] == [{"code": "600001"}]
    assert out["retries_used"] == 1 and out["requests_used"] == 2


# ── 3. 行业双时间轴 ────────────────────────────────────────

def test_current_only_membership_not_strict_for_history():
    """R4 验收2 后半：当前值接口（known_from=今天）对历史 as_of 不 strict——不回填。"""
    m = industry_membership_from_current("600519", "801950", known_from="2026-09-27",
                                         source_version="bz_industry")
    assert m.strict_eligible_at("2026-09-27") is True, "known_from 当日起可前瞻"
    assert m.strict_eligible_at("2020-01-01") is False, "历史 as_of 查不到——不能宣称 strict"
    assert "不回填" in m.note


def test_membership_with_effective_period_supports_history():
    """有生效期证明（effective/known 双区间覆盖 as_of）→ 严格历史可用（原始档案路径）。"""
    m = IndustryMembership(security_id="600519", industry_id="801950",
                           effective_from="2018-01-01", effective_to=None,
                           known_from="2018-01-05", source_version="archive")
    assert m.strict_eligible_at("2020-06-01") is True
    assert m.strict_eligible_at("2017-12-31") is False, "生效期之前"
    assert m.strict_eligible_at("2026-09-27") is True
