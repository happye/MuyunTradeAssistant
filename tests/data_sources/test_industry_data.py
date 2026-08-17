"""测试行业数据层（ISS-061）：图谱匹配/格式化/持仓定位/降级/落盘。

不依赖真实网络：网络函数用 industry_data 内部注入点（_cached 的 fn 参数不可注入，
故直接 monkeypatch 模块级 get_commodity_section / get_demand_section / format_macro）。
"""
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

# 确保项目根在 path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data import industry_data as ind


# ── 图谱匹配 ──────────────────────────────────────────────

def test_match_chain_aliases():
    """别名匹配：锂矿/碳酸锂/光模块/金价 各命中正确链。"""
    assert ind.match_chain("锂矿")[0] == "锂电"
    assert ind.match_chain("碳酸锂")[0] == "锂电"
    assert ind.match_chain("光模块")[0] == "AI算力"
    assert ind.match_chain("金价")[0] == "黄金"
    assert ind.match_chain("动力煤")[0] == "煤炭"
    assert ind.match_chain("光伏组件怎么看")[0] == "光伏"
    assert ind.match_chain("量子计算") is None


def test_match_chain_longest_alias_wins():
    """最长别名优先：'碳酸锂' 应命中别名'碳酸锂'而非单字'锂'。"""
    name, cfg = ind.match_chain("碳酸锂")
    assert name == "锂电"
    assert "碳酸锂" in cfg.get("aliases", [])


# ── 纯格式化 ──────────────────────────────────────────────

def _daily_df(last_date: str, days: int = 300):
    dates = pd.date_range(end=last_date, periods=days).strftime("%Y-%m-%d")
    base = 100000
    closes = [base + i * 100 for i in range(days)]
    return pd.DataFrame({"date": dates, "close": closes})


def test_format_daily_fresh():
    """新鲜数据：输出含最新价/分位，无过期标注。"""
    today = datetime.now().strftime("%Y-%m-%d")
    out = ind.format_daily(_daily_df(today), "LC")
    assert "最新收盘" in out and "分位" in out
    assert "数据过期" not in out


def test_format_daily_stale_marks_expired():
    """过期数据（休眠合约如ZC0停在2022）：必须标注过期且不给分位分析。"""
    stale = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    out = ind.format_daily(_daily_df(stale), "ZC")
    assert "数据过期" in out
    assert "分位" not in out, "过期数据不得给出分位等分析"


def test_format_spot_and_inventory():
    spot = pd.DataFrame([{
        "symbol": "LC", "date": "20260814", "spot_price": 151000.0,
        "dominant_contract_price": 155240.0, "dom_basis": 4240.0,
    }])
    out = ind.format_spot(spot, ["LC"])
    assert "现货" in out and "升水" in out

    spot2 = spot.copy()
    spot2["dom_basis"] = -2000.0
    assert "贴水" in ind.format_spot(spot2, ["LC"])

    inv = pd.DataFrame({"日期": ["2026-08-14"], "库存": [36098.0], "增减": [590.0]})
    out2 = ind.format_inventory(inv)
    assert "仓单" in out2


def test_format_demand_nev_and_gap():
    """需求格式化：NEV行+锂电缺口标注；无数据时给缺口提示。"""
    dfs = {
        "nev": pd.DataFrame({"月份": ["2026-6月", "2026-7月"], "NEV": [62.9, 64.2], "ICE": [37.1, 35.8]}),
    }
    out = ind.format_demand(dfs)
    assert "64.2" in out and "渗透率" in out
    # 完全无数据
    assert "数据缺失" in ind.format_demand({})


# ── 持仓交叉定位 ──────────────────────────────────────────

def test_format_chain_graph_holdings_mapping():
    """图谱+持仓交叉定位：融捷002192应被定位到锂电上游环节。"""
    chains = ind.load_chains()
    cfg = chains["锂电"]
    positions = [
        {"stock_code": "002192", "current_ratio": 0.27},
        {"stock_code": "002594", "current_ratio": 0.13},
        {"stock_code": "600000", "current_ratio": 0.05},  # 不在链内
    ]
    out = ind.format_chain_graph("锂电", cfg, positions)
    assert "用户持仓在链条中的位置" in out
    assert "002192" in out and "002594" in out
    assert "600000" not in out, "链外持仓不应出现在定位段"


# ── 组装与降级 ────────────────────────────────────────────

def test_build_report_unknown_industry_fails_marked():
    """未识别行业：返回[工具失败]开头并列出支持范围。"""
    out = ind.build_industry_report("区块链", [])
    assert out.startswith("[工具失败]")
    assert "锂电" in out


def test_build_report_degrades_per_section(monkeypatch=None):
    """单项数据源失败：该节降级为[数据缺失]，整体报告不崩。"""
    chains = ind.load_chains()
    cfg = chains["锂电"]
    # 直接调 build_industry_report 会走网络；monkeypatch 三个网络节
    orig_com, orig_dem, orig_macro = (
        ind.get_commodity_section, ind.get_demand_section, ind.format_macro)
    ind.get_commodity_section = lambda c: "[数据缺失]（模拟超时）"
    ind.get_demand_section = lambda c: "需求正常文本"
    ind.format_macro = lambda: "宏观正常文本"
    try:
        out = ind.build_industry_report("锂矿", [{"stock_code": "002192", "current_ratio": 0.27}])
    finally:
        ind.get_commodity_section, ind.get_demand_section, ind.format_macro = (
            orig_com, orig_dem, orig_macro)
    assert "[数据缺失]（模拟超时）" in out
    assert "需求正常文本" in out and "宏观正常文本" in out
    assert "【锂电产业链结构】" in out
    assert "002192" in out


# ── 全行业通用引擎（v2）──────────────────────────────────

def test_match_commodity_mapping():
    """商品链映射：生猪/钢铁/白酒 各自命中或明确未命中。"""
    m = ind.match_commodity("生猪养殖怎么样")
    assert m is not None and m[0] == "生猪养殖链"
    assert any(v == "LH" for v, _ in m[1])
    m2 = ind.match_commodity("钢铁板块")
    assert m2 is not None and m2[0] == "钢铁链"
    assert ind.match_commodity("白酒") is None, "白酒无商品锚，应返回None"


def test_clean_query_strips_common_words():
    """提问清洗：剔掉'板块/行业/怎么样'等非行业词。"""
    assert ind._clean_query("白酒板块怎么样") == "白酒"
    assert ind._clean_query("生猪养殖行业前景") == "生猪养殖"


def test_generic_report_unknown_all_fails():
    """板块和商品锚都未命中 -> [工具失败] 并列出精链支持范围。"""
    out = ind.build_industry_report("不存在的量子行业", [], scanner_engine=None)
    assert out.startswith("[工具失败]")


def test_generic_report_with_fake_board():
    """通用引擎：命中商品锚（无板块）也能出报告，含价格锚+宏观+指引。"""
    # 不给 scanner（板块解析跳过），只靠商品锚
    out = ind.build_industry_report("生猪养殖", [], scanner_engine=None)
    assert "生猪" in out
    assert "商品价格锚" in out
    assert "分析指引" in out


def test_generic_report_board_positions_crossref():
    """通用引擎：成分股与持仓交叉定位（mock scanner）。"""
    class FakeSnap:
        def __init__(self, df):
            import pandas as pd
            self._df = df
            self.columns = list(df.columns)
        def copy(self):
            return self._df
        def __getitem__(self, k):
            return self._df[k]
        def sort_values(self, k, ascending=False):
            return self._df.sort_values(k, ascending=ascending)
        def head(self, n):
            return self._df.head(n)
        def iterrows(self):
            return self._df.iterrows()

    import pandas as pd
    df = pd.DataFrame({
        "代码": ["002714", "300498", "600519"],
        "名称": ["牧原股份", "温氏股份", "贵州茅台"],
        "总市值": [2e11, 1.5e11, 3e12],
    })

    class FakeMarketCache:
        def get_all_stocks(self):
            return FakeSnap(df)
        def get_stocks_by_industry(self, name):
            return ["002714", "300498"]
        def get_stocks_by_concept(self, name):
            return []

    class FakeScanner:
        market_cache = FakeMarketCache()
        def get_industry_list(self, keyword=None):
            return [{"name": "养殖业"}]
        def get_concept_list(self, keyword=None):
            return []

    orig_resolve = ind._resolve_board
    orig_business = ind.fetch_main_business
    ind._resolve_board = lambda q, se: ("养殖业", "行业", ["002714", "300498"])
    ind.fetch_main_business = lambda c: f"{c} 主营构成mock"
    try:
        out = ind.build_generic_report(
            "生猪养殖", [{"stock_code": "002714", "current_ratio": 0.1}],
            scanner_engine=FakeScanner())
    finally:
        ind._resolve_board = orig_resolve
        ind.fetch_main_business = orig_business
    assert "同花顺行业板块「养殖业」" in out
    assert "牧原股份" in out and "总市值Top8" in out
    assert "002714 仓位10%" in out, "持仓交叉定位应出现"
    assert "主营构成mock" in out
    assert "生猪" in out, "商品锚（LH）应命中"


# ── 图谱自举（v4）───────────────────────────────────────

_AUTO_GRAPH_SAMPLE = """
sections:
  上游:
    - 环节: 军工电子元器件
      代表公司: ["002049 紫光国微(特种IC)"]
  下游:
    - 环节: 主机厂
      代表公司: ["600760 中航沈飞(战机)"]
aliases: [军工, 国防]
"""


def _with_auto_graph(name="军工测试链"):
    """在临时自举图谱文件环境下执行 fn（用完恢复）。"""
    import src.data.industry_data as mod
    orig_path = mod.AUTO_GRAPH_PATH
    mod.AUTO_GRAPH_PATH = "./configs/_test_auto_graph.yaml"
    mod._graph_cache, mod._graph_sources = None, {}
    try:
        import yaml
        graph = yaml.safe_load(_AUTO_GRAPH_SAMPLE)
        mod.save_auto_chain(name, graph)
        yield mod
    finally:
        import os
        if os.path.exists(mod.AUTO_GRAPH_PATH):
            os.remove(mod.AUTO_GRAPH_PATH)
        mod.AUTO_GRAPH_PATH = orig_path
        mod._graph_cache, mod._graph_sources = None, {}
        mod.load_chains()


def test_save_auto_chain_and_match():
    """自举图谱：保存后可被 match_chain 命中，来源标记 auto。"""
    for mod in _with_auto_graph():
        m = mod.match_chain("军工测试链怎么看")
        assert m is not None and m[0] == "军工测试链"
        assert mod.graph_source("军工测试链") == "auto"


def test_manual_graph_wins_over_auto_same_name():
    """手写图谱优先：自举同名链不覆盖手写（锂电）。"""
    for mod in _with_auto_graph(name="锂电"):
        m = mod.match_chain("锂矿")
        assert m[0] == "锂电"
        assert mod.graph_source("锂电") == "manual"


def test_save_auto_chain_rejects_bad_schema():
    """schema 校验：无 sections 拒绝。"""
    import pytest
    from src.data.industry_data import save_auto_chain
    with pytest.raises(ValueError):
        save_auto_chain("坏链", {"aliases": ["x"]})


def test_premium_path_falls_back_to_commodity_map():
    """图谱路径数据兜底：自举链（无 commodity 绑定）仍拿到关键词映射的商品锚。"""
    for mod in _with_auto_graph():
        # 锂电是手写链有绑定；这里验证兜底分支：把一个自举链塞进报告
        # 用"生猪"别名建自举链（无 commodity 字段），报告应含 LH 价格锚
        import yaml
        graph = yaml.safe_load("""
sections:
  上游:
    - 环节: 养殖
      代表公司: ["002714 牧原股份(生猪)"]
aliases: [生猪, 猪]
""")
        mod.save_auto_chain("生猪自举链", graph)
        orig_com = mod.get_commodity_section
        orig_gcom = mod.get_generic_commodity_section
        orig_dem = mod.get_demand_section
        orig_gdem = mod.generic_demand_section
        orig_macro = mod.format_macro
        mod.get_commodity_section = lambda c: "SHOULD_NOT_APPEAR"
        mod.get_generic_commodity_section = lambda q: "生猪价格锚(mock LH)"
        mod.get_demand_section = lambda c, cache_key=None: "需求mock"
        mod.generic_demand_section = lambda q: "需求mock"
        mod.format_macro = lambda: "宏观mock"
        try:
            out = mod.build_industry_report("生猪", [])
        finally:
            mod.get_commodity_section = orig_com
            mod.get_generic_commodity_section = orig_gcom
            mod.get_demand_section = orig_dem
            mod.generic_demand_section = orig_gdem
            mod.format_macro = orig_macro
        assert "生猪自举链" in out
        assert "生猪价格锚(mock LH)" in out, "无绑定的自举链必须走商品映射兜底"
        assert "SHOULD_NOT_APPEAR" not in out
        assert "AI自举图谱" in out, "自举图谱必须标注未经人工复核"


def test_board_section_temperature_stats():
    """板块温度：成分股涨跌中位数/涨跌家数/PE中位数。"""
    import pandas as pd

    class FakeSnap:
        def __init__(self, df):
            self._df = df
            self.columns = list(df.columns)
        def copy(self):
            return self._df
        def __getitem__(self, k):
            return self._df[k]
        def isin(self, codes):
            return self._df["代码"].isin(codes)
        def sort_values(self, k, ascending=False):
            return self._df.sort_values(k, ascending=ascending)
        def head(self, n):
            return self._df.head(n)
        def iterrows(self):
            return self._df.iterrows()

    df = pd.DataFrame({
        "代码": ["600519", "000858"],
        "名称": ["贵州茅台", "五粮液"],
        "总市值": [3e12, 2.8e11],
        "涨跌幅": [1.5, -0.5],
        "市盈率-动态": [25.0, 15.0],
    })

    class FakeMC:
        def get_all_stocks(self):
            return FakeSnap(df)
        def get_stocks_by_industry(self, name):
            return ["600519", "000858"]
        def get_stocks_by_concept(self, name):
            return []

    class FakeScanner:
        market_cache = FakeMC()
        def get_industry_list(self, keyword=None):
            return [{"name": "白酒"}]
        def get_concept_list(self, keyword=None):
            return []

    orig = ind._resolve_board
    ind._resolve_board = lambda q, se: ("白酒", "行业", ["600519", "000858"])
    try:
        text, top = ind._board_section("白酒", [], FakeScanner())
    finally:
        ind._resolve_board = orig
    assert "板块温度" in text
    assert "涨1/跌1" in text
    assert "PE中位数" in text
    assert top and top[0][0] == "600519"


# ── chat 落盘 ─────────────────────────────────────────────

def test_persist_turn_writes_daily_file(tmp_path=None):
    """chat落盘：追加写 分析报告/chat/YYYY-MM-DD.md，失败不抛异常。"""
    from src.chat.agent import ChatAgent
    agent = object.__new__(ChatAgent)
    import src.chat.agent as agent_mod
    orig_dir = agent_mod.CHAT_REPORT_DIR
    agent_mod.CHAT_REPORT_DIR = "./分析报告/chat_test_tmp"
    try:
        agent._persist_turn("测试问题", "测试回答")
        agent._persist_turn("第二个问题", "第二个回答")
        path = os.path.join(agent_mod.CHAT_REPORT_DIR,
                            datetime.now().strftime("%Y-%m-%d") + ".md")
        assert os.path.exists(path)
        content = open(path, encoding="utf-8").read()
        assert "测试问题" in content and "第二个回答" in content
        os.remove(path)
        os.rmdir(agent_mod.CHAT_REPORT_DIR)
    finally:
        agent_mod.CHAT_REPORT_DIR = orig_dir


if __name__ == "__main__":
    import traceback

    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
