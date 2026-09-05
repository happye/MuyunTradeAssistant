"""ISS-083 景气度接线回归测试（v0.8.8.7，方案A prompt 注入）

锁死语义：
1. industry_prosperity 收到 data_summary["industry_metrics"] 时，user_prompt 注入
   【客观行业数据】块（商品锚/需求/宏观），sources 带客观来源标签
2. 兜底变严（验收门槛④）：行业名+新闻+industry_metrics 三者全空才 50 兜底；
   metrics 在（哪怕行业名/新闻缺）必须走 AI
3. AI 失败 + metrics 在 → 50/conf0 诚实降级（不假装有判断）
4. 未命中（metrics=None）→ prompt 与旧版一致（不含客观块），未命中股行为不变
5. data_provider.get_industry_metrics：三级桥接（L1 代码直配只认手写链，自举链
   不参与）、全不命中→None、内部异常 fail-soft→None
6. 回测隔离（结构断言）：backtest_engine 源码不含 industry_metrics——回测显式
   构造 data_summary，接线结构性不触网不前瞻

跑法：pytest tests/benzong/test_industry_prosperity_wiring.py
"""
import contextlib
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.benzong.dimensions import industry_prosperity as ip


def _metrics(**over):
    m = {
        "chain": "锂电",
        "matched_via": "company_code",
        "commodity": "- 期货LC: 现价 71000，90日分位 12%（近底）\n- LC仓单(库存代理): 30日 -8%（去库）",
        "demand": "NEV渗透率 52.3%（最新月）；乘用车产量同比 +12%",
        "macro": "制造业PMI 49.5（收缩区间）；PPI 同比 -1.2%",
    }
    m.update(over)
    return m


def _ds(metrics=Ellipsis, industry_name="C32有色金属冶炼", news=True):
    return {
        "industry": {"industry_name": industry_name},
        "announcements": [{"title": "碳酸锂期货反弹", "date": "2026-09-01"}] if news else [],
        "industry_metrics": _metrics() if metrics is Ellipsis else metrics,
    }


@contextlib.contextmanager
def _capture_ai(score=66, conf=0.7, fail=False):
    captured = {}

    def _fake(client, system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return None if fail else {"score": score, "confidence": conf, "reasoning": "测试"}

    with mock.patch.object(ip, "_call_ai_for_score", _fake):
        yield captured


# ── 1. 注入 ──

def test_metrics_injected_into_prompt_and_sources():
    with _capture_ai() as cap:
        r = ip.score("300750", "宁德时代", data_summary=_ds(), ai_client=object())
    assert "客观行业数据" in cap["user"], "user_prompt 必须注入客观块"
    assert "90日分位" in cap["user"] and "PMI" in cap["user"]
    assert "不得脑补" in cap["system"] or "不得据此扣分" in cap["system"], \
        "SYSTEM_PROMPT 必须声明缺失项不得脑补"
    assert any("商品锚" in s for s in r["sources"]), f"sources 应带客观标签: {r['sources']}"
    assert r["score"] == 66


# ── 2. 兜底变严（验收门槛④）──

def test_missing_requires_all_three_sources_empty():
    with _capture_ai() as cap:
        r = ip.score("300750", "宁德时代", data_summary=_ds(industry_name="", news=False),
                     ai_client=object())
    assert r["score"] == 66, "有 industry_metrics 时不得走 50 兜底（必须走 AI）"
    assert cap.get("user"), "AI 必须被调用"


def test_all_empty_still_falls_back():
    r = ip.score("300750", "宁德时代",
                 data_summary=_ds(metrics=None, industry_name="", news=False),
                 ai_client=object())
    assert r["score"] == 50 and r["confidence"] == 0.0, "三者全空才允许 50 兜底"
    assert r.get("warnings"), "兜底必须带警告"


# ── 3. AI 失败诚实降级 ──

def test_ai_fail_with_metrics_is_honest_50():
    with _capture_ai(fail=True):
        r = ip.score("300750", "宁德时代", data_summary=_ds(), ai_client=object())
    assert r["score"] == 50 and r["confidence"] == 0.0
    assert r.get("warnings"), "AI 失败必须带警告（不假装有客观判断）"


# ── 4. 未命中行为不变 ──

def test_no_metrics_keeps_legacy_prompt():
    with _capture_ai() as cap:
        ip.score("600519", "贵州茅台", data_summary=_ds(metrics=None), ai_client=object())
    assert "客观行业数据" not in cap["user"], "未命中股 prompt 不得出现客观块"


# ── 5. get_industry_metrics 三级桥接 ──

def _patch_ind(ind, chains=None, sources=None, commodity="商品锚文本", demand="需求文本", macro="PMI 49.5"):
    import src.data.industry_data as ind_mod
    patches = [
        mock.patch.object(ind_mod, "load_chains", lambda: chains or {}),
        mock.patch.object(ind_mod, "_graph_sources", sources or {}),
        mock.patch.object(ind_mod, "_company_codes", lambda cfg: cfg.get("_codes", {})),
        mock.patch.object(ind_mod, "match_chain", lambda q: None),
        mock.patch.object(ind_mod, "match_commodity", lambda q: None),
        mock.patch.object(ind_mod, "get_commodity_section", lambda cfg: commodity),
        mock.patch.object(ind_mod, "get_demand_section", lambda cfg, cache_key=None: demand),
        mock.patch.object(ind_mod, "get_generic_commodity_section", lambda q: commodity),
        mock.patch.object(ind_mod, "generic_demand_section", lambda q: demand),
        mock.patch.object(ind_mod, "format_macro", lambda: macro),
    ]
    return patches


def test_bridge_l1_manual_chain_only():
    from src.core.benzong.data_provider import get_industry_metrics
    chains = {
        "锂电": {"_codes": {"300750": ("中游·电池", "宁德时代")}},
        "假自举链": {"_codes": {"300750": ("某环节", "某公司")}},
    }
    sources = {"锂电": "manual", "假自举链": "auto"}
    with contextlib.ExitStack() as st:
        for p in _patch_ind(None, chains=chains, sources=sources):
            st.enter_context(p)
        r = get_industry_metrics("300750", "C38电气机械")
    assert r is not None and r["chain"] == "锂电", \
        f"L1 只认手写链，自举链不得参与: {r}"
    assert r["matched_via"] == "company_code"
    assert r["macro"], "宏观底色必须带上"


def test_bridge_full_miss_returns_none():
    from src.core.benzong.data_provider import get_industry_metrics
    with contextlib.ExitStack() as st:
        for p in _patch_ind(None, chains={}, sources={}):
            st.enter_context(p)
        assert get_industry_metrics("600519", "C15酒饮料") is None, "全不命中→None（走原新闻路径）"


def test_bridge_fail_soft():
    from src.core.benzong.data_provider import get_industry_metrics
    chains = {"锂电": {"_codes": {"300750": ("中游", "宁德")}}}
    patches = _patch_ind(None, chains=chains, sources={"锂电": "manual"})
    # format_macro 抛异常 → 整体 fail-soft 返回 None（不拖垮 6 维评分）
    patches[-1] = mock.patch.object(
        __import__("src.data.industry_data", fromlist=["x"]), "format_macro",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        r = get_industry_metrics("300750", "C38")
    assert r is None, "内部异常必须 fail-soft 返回 None"


# ── 6. 回测隔离（结构断言）──

def test_backtest_engine_has_no_industry_metrics():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    src = open(os.path.join(root, "src", "core", "backtest_engine.py"), encoding="utf-8").read()
    assert "industry_metrics" not in src, \
        "回测引擎不得引用 industry_metrics——回测显式构造 data_summary（禁网/前瞻隔离）"
    rule_src = open(os.path.join(root, "src", "core", "benzong", "rule_scorer.py"),
                    encoding="utf-8").read()
    assert "industry_metrics" not in rule_src, "回测规则版景气度本批不接客观数据（无 point-in-time）"
