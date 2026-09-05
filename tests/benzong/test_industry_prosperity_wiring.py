"""ISS-083 景气度接线回归测试（v0.8.8.7，方案A prompt 注入；监督审查后强化版）

锁死语义：
1. industry_prosperity 收到 data_summary["industry_metrics"] 时，user_prompt 注入
   【客观行业数据】块（含本期可用度 ✓/✗ 标注），sources 只给真实可用的节
2. 兜底变严（验收门槛④）：行业名+新闻+industry_metrics 通道三者全空才 50 兜底；
   metrics 命中（哪怕行业名/新闻缺）必须走 AI
3. AI 失败 + metrics 在 → 50/conf0 诚实降级（不假装有判断）
4. 未命中（metrics=None）→ prompt 与旧版一致（不含客观块），未命中股行为不变
5. data_provider.get_industry_metrics 返回 (metrics|None, status) 三态
   ("matched"/"not_matched"/"failed")；L1 只认手写链；L2 只在手写链子集内匹配
   （auto 链别名不得遮蔽）；L3 商品关键词；节级异常转缺失标记不拖垮整包
6. 回测隔离：行为断言（回测形态 data_summary 跑 rule_score 绝不触碰 live 拉数）
   + 源码字符串断言双保险

跑法：pytest tests/benzong/test_industry_prosperity_wiring.py
"""
import contextlib
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

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
    assert "可用度" in cap["user"], "客观块必须带本期可用度标注（监督审查 P2-2）"
    assert "不得据此扣分或脑补" in cap["system"], "SYSTEM_PROMPT 必须声明缺失项不得脑补"
    assert "confidence 不得超过 0.5" in cap["system"], "SYSTEM_PROMPT 必须有分级退路"
    assert any("商品锚" in s for s in r["sources"]), f"sources 应带客观标签: {r['sources']}"
    assert r["score"] == 66


def test_unavailable_sections_no_source_label():
    """缺口占位符节不得宣称来源（监督审查 P2 诚实性），可用度标 ✗。"""
    m = _metrics(commodity="[数据缺失]（该行业无商品期货价格绑定…）",
                 demand="[数据缺失]（该行业无免费需求端接口…）")
    with _capture_ai() as cap:
        r = ip.score("688981", "中芯国际", data_summary=_ds(metrics=m), ai_client=object())
    assert "商品锚✗" in cap["user"] and "需求端✗" in cap["user"] and "宏观✓" in cap["user"]
    srcs = ";".join(r["sources"])
    assert "商品锚" not in srcs and "需求端" not in srcs, f"缺口节不得进来源: {srcs}"


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


# ── 5. get_industry_metrics 三级桥接（返回 (metrics, status) 三态）──

_MANUAL_CHAINS = {
    "锂电": {"aliases": ["锂电", "锂电池"],
             "sections": {"中游": [{"环节": "电池", "代表公司": ["300750 宁德时代(动力电池)"]}]}},
    "假自举链": {"aliases": ["某主题"],
                "sections": {"上": [{"环节": "x", "代表公司": ["300750 某公司"]}]}},
}
_SOURCES = {"锂电": "manual", "假自举链": "auto"}


def _patch_bridge(match_chain=None, match_commodity=None,
                  chains=None, sources=None, commodity="商品锚文本",
                  demand="需求文本", macro="PMI 49.5", keep_match_chain=False):
    import src.data.industry_data as ind_mod
    patches = [
        mock.patch.object(ind_mod, "load_chains", lambda: chains if chains is not None else {}),
        mock.patch.object(ind_mod, "_graph_sources", sources or {}),
        mock.patch.object(ind_mod, "_company_codes",
                          lambda cfg: {"300750": ("中游", "宁德")}
                          if "300750 宁德时代" in str(cfg.get("sections")) else {}),
        mock.patch.object(ind_mod, "match_commodity",
                          match_commodity if match_commodity else (lambda q: None)),
        mock.patch.object(ind_mod, "get_commodity_section", lambda cfg: commodity),
        mock.patch.object(ind_mod, "get_demand_section", lambda cfg, cache_key=None: demand),
        mock.patch.object(ind_mod, "get_generic_commodity_section", lambda q: commodity),
        mock.patch.object(ind_mod, "format_macro", lambda: macro),
    ]
    if not keep_match_chain:
        patches.insert(3, mock.patch.object(
            ind_mod, "match_chain",
            match_chain if match_chain else (lambda q, chains=None: None)))
    return patches


def test_bridge_l1_manual_chain_only():
    from src.core.benzong.data_provider import get_industry_metrics
    with contextlib.ExitStack() as st:
        for p in _patch_bridge(chains=_MANUAL_CHAINS, sources=_SOURCES):
            st.enter_context(p)
        r, status = get_industry_metrics("300750", "C38电气机械")
    assert status == "matched"
    assert r["chain"] == "锂电", f"L1 只认手写链，自举链不得参与: {r}"
    assert r["matched_via"] == "company_code"
    assert r["macro"], "宏观底色必须带上"


def test_bridge_l2_within_manual_subset_only():
    """L2 只在手写链子集内匹配（监督审查 P2-4）：auto 链别名不得遮蔽/不得命中；
    matched_via 必须标 chain_name（此前误标 company_code 的回归锁）。"""
    from src.core.benzong.data_provider import get_industry_metrics
    with contextlib.ExitStack() as st:
        # 不 patch match_chain——走真实实现 + 手写链子集
        for p in _patch_bridge(chains=_MANUAL_CHAINS, sources=_SOURCES, keep_match_chain=True):
            st.enter_context(p)
        r, status = get_industry_metrics("300750X", "锂电池产业链")  # 非链内代码→走 L2
    assert status == "matched" and r["chain"] == "锂电"
    assert r["matched_via"] == "chain_name", f"L2 命中必须标 chain_name: {r}"

    # 仅 auto 链别名命中 → 不得当作 L2 命中
    chains_auto_only = {"某主题链": {"aliases": ["某主题"], "sections": {}}}
    sources_auto = {"某主题链": "auto"}
    with contextlib.ExitStack() as st:
        for p in _patch_bridge(chains=chains_auto_only, sources=sources_auto):
            st.enter_context(p)
        r, status = get_industry_metrics("600519", "某主题")
    assert r is None and status == "not_matched", "auto 链别名不得充当桥接命中"


def test_bridge_l3_commodity_keyword():
    from src.core.benzong.data_provider import get_industry_metrics

    def _cm(q):
        return ("有色链", [("CU", "铜")]) if "有色" in q else None

    with contextlib.ExitStack() as st:
        for p in _patch_bridge(match_commodity=_cm, commodity="铜价分位文本"):
            st.enter_context(p)
        r, status = get_industry_metrics("601899", "B09有色金属矿采选业")
    assert status == "matched"
    assert r["chain"] == "商品:有色链" and r["matched_via"] == "commodity_keyword"
    assert r["demand"] == "" and r["commodity"] == "铜价分位文本"


def test_bridge_full_miss_returns_not_matched():
    from src.core.benzong.data_provider import get_industry_metrics
    with contextlib.ExitStack() as st:
        for p in _patch_bridge(chains={}, sources={}):
            st.enter_context(p)
        r, status = get_industry_metrics("600519", "C15酒饮料")
    assert r is None and status == "not_matched", "全不命中=设计内未命中（非故障）"


def test_bridge_fail_soft_section_survives():
    """节级异常转缺失标记，好节照常存活（industry_data._cached 双保险之外的一层）。"""
    from src.core.benzong.data_provider import get_industry_metrics
    import src.data.industry_data as ind_mod
    patches = _patch_bridge(chains=_MANUAL_CHAINS, sources=_SOURCES)
    patches[-1] = mock.patch.object(
        ind_mod, "format_macro",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        r, status = get_industry_metrics("300750", "C38")
    assert status == "matched" and r is not None, "坏节不得拖垮整包"
    assert r["commodity"] == "商品锚文本", "好节必须存活"
    assert r["macro"].startswith("[数据缺失]"), "坏节转缺失标记"


def test_l1_against_real_manual_yaml():
    """用真实 configs/industry_chains.yaml 走 L1（防 mock 与真实解析脱节，监督审查 P1-2）。"""
    from src.core.benzong.data_provider import get_industry_metrics
    assert os.path.exists("configs/industry_chains.yaml"), "手写图谱文件缺失"
    with contextlib.ExitStack() as st:
        import src.data.industry_data as ind_mod
        for p in (mock.patch.object(ind_mod, "get_commodity_section", lambda cfg: "锚"),
                  mock.patch.object(ind_mod, "get_demand_section", lambda cfg, cache_key=None: "需求"),
                  mock.patch.object(ind_mod, "format_macro", lambda: "PMI")):
            st.enter_context(p)
        r, status = get_industry_metrics("300750", "C38电气机械和器材制造业")
    assert status == "matched" and r["chain"] == "锂电", f"真实 YAML L1 直配失败: {r}"
    assert r["matched_via"] == "company_code"


def test_three_state_fetch_status():
    """三态 fetch_status（监督审查 P2-1）：未命中标 not_matched（truthy 不告警），失败标 False。"""
    from src.core.benzong import data_provider as dp
    cases = [((None, "not_matched"), "not_matched"),
             ((None, "failed"), False),
             ((_metrics(), "matched"), True)]
    for (metrics, status), expect in cases:
        with contextlib.ExitStack() as st:
            for p in (mock.patch.object(dp, "get_business_introduction", lambda c: None),
                      mock.patch.object(dp, "get_recent_announcements", lambda c, days=30: []),
                      mock.patch.object(dp, "get_industry_info", lambda c: {"industry_name": "x"}),
                      mock.patch.object(dp, "get_industry_metrics", lambda c, n: (metrics, status)),
                      mock.patch.object(dp, "get_recent_kline", lambda c: None),
                      mock.patch.object(dp, "get_market_turnover", lambda: None)):
                st.enter_context(p)
            summary = dp.get_data_summary("600519")
        assert summary["fetch_status"]["industry_metrics"] == expect, f"status={status}"
        assert summary["industry_metrics"] == metrics


# ── 6. 回测隔离（行为断言 + 源码断言双保险）──

def test_rule_score_backtest_shape_never_touches_live_fetch():
    """行为断言（监督审查 P2-3）：回测形态 data_summary 跑 rule_score，
    活体拉数函数一旦被触碰立即爆炸——字符串断言抓不住的隐蔽耦合这层能抓住。"""
    import pandas as pd
    from src.core.benzong import data_provider as dp
    from src.core.benzong.rule_scorer import rule_score

    def _boom(*a, **k):
        raise AssertionError("回测路径不得调用 live 拉数（get_data_summary/get_industry_metrics）")

    kline = pd.DataFrame({"收盘": [10.0 + i * 0.1 for i in range(30)]})
    ds = {"business_intro": None, "announcements": [], "industry": None,
          "kline": kline, "market_turnover": None,
          "fetch_status": {"kline": True}}  # backtest_engine.py:290-297 同款形态
    with contextlib.ExitStack() as st:
        for p in (mock.patch.object(dp, "get_data_summary", _boom),
                  mock.patch.object(dp, "get_industry_metrics", _boom),
                  mock.patch.object(dp, "get_recent_kline", _boom)):
            st.enter_context(p)
        dims = rule_score("600000", data_summary=ds, spot=None)
    assert "grade" in dims, "回测形态评分必须正常产出（隔离成立）"


def test_backtest_engine_has_no_industry_metrics():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    src = open(os.path.join(root, "src", "core", "backtest_engine.py"), encoding="utf-8").read()
    assert "industry_metrics" not in src, \
        "回测引擎不得引用 industry_metrics——回测显式构造 data_summary（禁网/前瞻隔离）"
    rule_src = open(os.path.join(root, "src", "core", "benzong", "rule_scorer.py"),
                    encoding="utf-8").read()
    assert "industry_metrics" not in rule_src, "回测规则版景气度本批不接客观数据（无 point-in-time）"
