"""F4 候选池谱系与因子登记回归测试（plan/fusion TASKS.md F4 验收条款）

锁死语义（DESIGN §5.3 / RESEARCH §2.1）：
1. 多渠道同股只计算一次（并集去重：同股一个 Candidate，contributions 记全部来源）
2. 候选来源配额不会因输入顺序变（路内 (code,...) 确定序截断，G09 排列不变性）
3. 长期候选不受当天回调必要条件误杀（LONG_QUALITY 路独立准入）
4. 快照缺量比的候选标待验证（volume_ratio_missing），不宣称缩量
5. 不新增龙头偏向、不按市值一刀切（候选池层无规模歧视，只有配额）
6. 因子登记表完整自检（字段缺一不可；needs_data_probe 因子未探查前只登记不计算）
7. 截断可追溯（excluded_by_quota 记录 code/route/原因）；fingerprint 排列无关

纯内存测试，无网络。跑法：pytest tests/core/test_candidate_lineage.py -q
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.candidate_pool import (
    CandidateRoute,
    build_candidate_set,
    from_scan_candidates,
    from_theme_results,
)
from src.core.factor_registry import FACTOR_SPECS, get_spec, validate_registry


def _scan_item(code, name=""):
    """模拟扫描输出（新浪源形态：无量比/无 60 日涨幅）。"""
    return {"stock_code": code, "stock_name": name or f"股{code}",
            "price": 10.0, "change_pct": -2.0, "volume_ratio": None,
            "amount": 1.5e8}


# ── 1. 并集去重：多渠道同股只算一次 ───────────────────────

def test_union_dedup_same_stock_once():
    tech = from_scan_candidates([_scan_item("600519"), _scan_item("000001")],
                                "healthy_pullback", "v1")
    ind = from_theme_results(["600519", "300750"], "氮化镓", "bz.v1")
    pool = build_candidate_set({CandidateRoute.TECHNICAL: tech,
                                CandidateRoute.INDUSTRY: ind})
    codes = [c.stock_code for c in pool.candidates]
    assert len(codes) == len(set(codes)) == 3, "三只票（600519 双路去重）"
    c = pool.get("600519")
    assert set(c.routes) == {CandidateRoute.TECHNICAL, CandidateRoute.INDUSTRY}
    assert pool.dedup_count == 1, "跨路去重合并记一次"


# ── 2. 配额排列不变 ──────────────────────────────────────

def _three_tech():
    return from_scan_candidates([_scan_item(f"00000{i}") for i in range(1, 6)],
                                "steady_advance", "v1")


def test_quota_stable_under_input_permutation():
    quotas = {CandidateRoute.TECHNICAL: 3}
    base = build_candidate_set({CandidateRoute.TECHNICAL: _three_tech()}, quotas)
    # 倒序重排输入（同内容）
    shuffled = list(reversed(_three_tech()))
    perm = build_candidate_set({CandidateRoute.TECHNICAL: shuffled}, quotas)
    assert base.fingerprint() == perm.fingerprint(), "输入排列变化不改变结果（G09）"
    assert len(base.candidates) == 3
    assert len(base.excluded_by_quota) == 2, "截断可追溯"
    assert base.excluded_by_quota[0]["reason"].startswith("TECHNICAL")


def test_quota_per_route_independent():
    """长期质量路配额独立：技术路满员不挤占长期路（互不误杀）。"""
    tech = from_scan_candidates([_scan_item(f"00000{i}") for i in range(1, 4)],
                                "healthy_pullback", "v1")
    quality = [{"stock_code": "688001", "stock_name": "质优股", "rule_name": "long_quality",
                "rule_version": "f4.v1", "applied_filters": ["long_quality"]}]
    pool = build_candidate_set(
        {CandidateRoute.TECHNICAL: tech, CandidateRoute.LONG_QUALITY: quality},
        {CandidateRoute.TECHNICAL: 2, CandidateRoute.LONG_QUALITY: 5})
    assert pool.get("688001") is not None, "长期候选不受技术路配额影响"
    assert len(pool.by_route(CandidateRoute.TECHNICAL)) == 2


def test_long_quality_admitted_without_technical_conditions():
    """TASKS F4 验收原文：长期候选不受当天回调必要条件误杀——
    当天涨幅+5%（不满足任何回调条件）仍经 LONG_QUALITY 路入池。"""
    quality = [{"stock_code": "600519", "stock_name": "贵州茅台",
                "rule_name": "long_quality_watch", "rule_version": "f4.v1",
                "applied_filters": ["long_quality_watch"],
                "missing_filters": []}]
    pool = build_candidate_set({CandidateRoute.LONG_QUALITY: quality})
    assert pool.get("600519") is not None, "长期质量路独立准入（当日技术形态无关）"


# ── 3. 量比缺失标待验证 ──────────────────────────────────

def test_missing_volume_ratio_marked_not_claimed():
    items = from_scan_candidates([_scan_item("000001")], "shrink_pullback", "v1")
    assert items[0]["missing_filters"] == ["volume_ratio_missing"]
    pool = build_candidate_set({CandidateRoute.TECHNICAL: items})
    c = pool.get("000001")
    assert "volume_ratio_missing" in c.missing_filters
    # 谱系里没有"缩量确认"语义——missing ≠ applied
    assert "shrink_pullback" in c.contributions[0].applied_filters
    assert all("缩量" not in f for f in c.contributions[0].applied_filters)


def test_scan_adapter_marks_missing_amount_too():
    item = _scan_item("000001")
    item["amount"] = None
    items = from_scan_candidates([item], "healthy_pullback", "v1")
    assert "amount_missing" in items[0]["missing_filters"]


# ── 4. 无规模歧视 ────────────────────────────────────────

def test_no_size_or_leader_discrimination():
    """候选池层只有配额，无市值/龙头过滤——小盘/大盘同序公平竞争。"""
    items = [{"stock_code": c, "stock_name": f"股{c}", "total_mv": mv,
              "rule_name": "steady_advance", "rule_version": "v1"}
             for c, mv in [("000001", 30e8), ("600519", 20000e8), ("300001", 15e8)]]
    pool = build_candidate_set({CandidateRoute.TECHNICAL: items})
    assert len(pool.candidates) == 3, "市值差异不导致入池差异（不新增龙头偏向）"
    assert all(not any("mv" in f or "市值" in f or "龙头" in f
                       for f in c.contributions[0].applied_filters)
               for c in pool.candidates)


# ── 5. 因子登记表 ────────────────────────────────────────

def test_factor_registry_complete_and_valid():
    assert validate_registry() == [], f"登记表自检失败: {validate_registry()}"
    ids = {s.factor_id for s in FACTOR_SPECS}
    # DESIGN §5.3 首批 8 因子全部登记
    assert {"business_exposure_v1", "demand_change_v1", "earnings_quality_v1",
            "capital_return_v1", "balance_risk_v1", "valuation_range_v1",
            "relative_trend_v1", "trading_capacity_v1"} <= ids


def test_unprobed_factors_registered_not_computed():
    """needs_data_probe=True 的因子（上游未验证）只登记不计算——真锁：登记模块除
    查询/自检两个函数外不含任何计算函数；不填通用数字强行纳入（DESIGN §5.3）。"""
    import src.core.factor_registry as reg
    import inspect
    funcs = {n for n, o in inspect.getmembers(reg, inspect.isfunction)
             if o.__module__ == reg.__name__}
    assert funcs == {"get_spec", "validate_registry"}, \
        f"登记模块出现计算类函数（未探查因子不得计算）: {funcs - {'get_spec', 'validate_registry'}}"
    # 探查已通过（2026-09-26，tests/data_sources/probe_fin_pubdate.py：财务季频 pubDate
    # 实证 + 申万指数日线可用）→ 翻转 False；接线批前仍只登记
    for fid in ("earnings_quality_v1", "capital_return_v1", "balance_risk_v1",
                "valuation_range_v1", "relative_trend_v1"):
        assert get_spec(fid).needs_data_probe is False, f"{fid} 探查已通过仍标未验证"
    # 上游仍未取得：分部营收（business_exposure）无自动接口、行业量价库存（demand_change）未探查
    for fid in ("business_exposure_v1", "demand_change_v1"):
        assert get_spec(fid).needs_data_probe is True, f"{fid} 上游未验证不得翻转"
    # 行情天然 PIT 且输入已接线的因子可先计算（停牌状态分量 UNKNOWN 不阻比值）
    assert get_spec("trading_capacity_v1").needs_data_probe is False


def test_no_composite_score_in_specs():
    """DESIGN 红线：不求覆盖所有用途的总分——缺失政策必须显式 UNKNOWN/NOT_APPLICABLE
    （禁止兜底 0/50 语义混入）。"""
    for s in FACTOR_SPECS:
        assert "UNKNOWN" in s.missing_policy or "NOT_APPLICABLE" in s.missing_policy, \
            f"{s.factor_id} 缺失政策必须显式 UNKNOWN/NOT_APPLICABLE（禁止兜底分）"


# ── 6. F4 对抗审查修复回归锁 ─────────────────────────────

def test_theme_results_accepts_bz_fac_c_shape():
    """P0 回归锁：bz 法C 真实输出 {"code","name","term","why"}（theme_locator.py）
    必须正确入池——不再静默清零。"""
    fac_c = [{"code": "600519", "name": "贵州茅台", "term": "氮化镓", "why": "上游衬底"},
             {"code": "300750", "name": "宁德时代", "term": "氮化镓", "why": "应用端"}]
    items = from_theme_results(fac_c, "氮化镓", "bz.v1")
    pool = build_candidate_set({CandidateRoute.INDUSTRY: items})
    assert {c.stock_code for c in pool.candidates} == {"600519", "300750"}, \
        "法C 形态必须全部入池（P0：原实现静默清零）"
    assert pool.get("600519").stock_name == "贵州茅台"
    assert pool.skipped_invalid == 0


def test_invalid_codes_counted_not_silent():
    """P0 放大器回归锁：空/形态不匹配的代码计数留痕（skipped_invalid），不静默丢弃。"""
    items = [{"code": None, "name": "坏数据"}, {"code": "600519", "name": "正常"}]
    pool = build_candidate_set({CandidateRoute.INDUSTRY: from_theme_results(items, "x", "v1")})
    assert pool.skipped_invalid == 1 and len(pool.candidates) == 1


def test_quota_respects_source_rank_not_code_order():
    """P1 回归锁：配额按 rank_in_source 截断（来源排序权威），不按代码字典序
    （代码序=板块序，按代码截断会系统性牺牲科创板）。"""
    items = [{"stock_code": "688001", "rank_in_source": 1, "rule_name": "steady_advance"},
             {"stock_code": "000001", "rank_in_source": 4, "rule_name": "steady_advance"}]
    pool = build_candidate_set({CandidateRoute.TECHNICAL: items},
                               {CandidateRoute.TECHNICAL: 1})
    kept = {c.stock_code for c in pool.candidates}
    assert kept == {"688001"}, f"rank1 必须保留（不按代码序）: {kept}"


def test_source_truncation_reason_preserved():
    """P1 回归锁：来源侧截断原因进谱系（RouteContribution.source_truncation_reason）。"""
    items = from_scan_candidates([_scan_item("000001")], "healthy_pullback", "v1",
                                 rank_complete=False, truncation_reason="scan max_candidates=30 截断")
    pool = build_candidate_set({CandidateRoute.TECHNICAL: items})
    assert pool.get("000001").contributions[0].source_truncation_reason == "scan max_candidates=30 截断"
    assert pool.get("000001").contributions[0].rank_complete is False


def test_same_route_multi_rule_lineage_kept():
    """P2 回归锁：同股同路多规则命中——追加谱系（只计算一次≠只记录一次）。"""
    a = from_scan_candidates([_scan_item("000001")], "healthy_pullback", "v1")
    b = from_scan_candidates([_scan_item("000001")], "shrink_pullback", "v1")
    pool = build_candidate_set({CandidateRoute.TECHNICAL: a + b})
    c = pool.get("000001")
    assert {x.rule_name for x in c.contributions} == {"healthy_pullback", "shrink_pullback"}
    assert pool.dedup_count == 0, "同路多规则不算跨路去重"


def test_route_input_dict_order_invariant():
    """P2 回归锁：route_inputs dict 传入序不影响结果（实现靠枚举序，测试锁死）。"""
    tech = from_scan_candidates([_scan_item("000001")], "steady_advance", "v1")
    ind = from_theme_results(["600519"], "氮化镓", "bz.v1")
    p1 = build_candidate_set({CandidateRoute.TECHNICAL: tech, CandidateRoute.INDUSTRY: ind})
    p2 = build_candidate_set({CandidateRoute.INDUSTRY: ind, CandidateRoute.TECHNICAL: tech})
    assert p1.fingerprint() == p2.fingerprint()


def test_fingerprint_invariant_to_filter_order():
    """P2 回归锁：applied/missing filters 顺序不同 → 构造时排序 → fingerprint 稳定。"""
    i1 = [{"stock_code": "600519", "rule_name": "r", "applied_filters": ["a", "b"]}]
    i2 = [{"stock_code": "600519", "rule_name": "r", "applied_filters": ["b", "a"]}]
    p1 = build_candidate_set({CandidateRoute.TECHNICAL: i1})
    p2 = build_candidate_set({CandidateRoute.TECHNICAL: i2})
    assert p1.fingerprint() == p2.fingerprint()
