import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.scanner.scanner_engine import ScannerEngine


class _FakeMarketCache:
    def __init__(self):
        self._concept_map = {
            "AI算力": ["000001", "000002"],
            "机器人": ["000002", "000003"],
        }
        self._industry_map = {
            "半导体": ["000001", "000003"],
        }

    def get_industry_boards(self):
        return pd.DataFrame([
            {"板块名称": "半导体", "涨跌幅": 2.1, "领涨股票": "000001"},
            {"板块名称": "电力设备", "涨跌幅": 1.4, "领涨股票": "000003"},
        ])

    def get_stocks_by_concept(self, concept_name: str):
        return self._concept_map.get(concept_name, [])

    def get_stocks_by_industry(self, industry_name: str):
        return self._industry_map.get(industry_name, [])

    def get_concept_boards(self):
        return pd.DataFrame([
            {"板块名称": "AI算力", "涨跌幅": 3.2, "领涨股": "000001"},
            {"板块名称": "机器人", "涨跌幅": 1.8, "领涨股": "000003"},
        ])


class _BrokenBoardListMarketCache(_FakeMarketCache):
    def get_industry_boards(self):
        return pd.DataFrame()

    def get_concept_boards(self):
        return pd.DataFrame()


def test_apply_concept_filter_keeps_only_concept_members():
    engine = ScannerEngine()
    engine.market_cache = _FakeMarketCache()
    df = pd.DataFrame([
        {"代码": "000001", "名称": "A"},
        {"代码": "000002", "名称": "B"},
        {"代码": "000003", "名称": "C"},
    ])

    filtered = engine._apply_market_theme_filter(df, [], ["AI算力"])
    codes = filtered["代码"].astype(str).tolist()

    assert codes == ["000001", "000002"]


def test_industry_and_concept_filter_can_be_combined_as_intersection():
    engine = ScannerEngine()
    engine.market_cache = _FakeMarketCache()
    df = pd.DataFrame([
        {"代码": "000001", "名称": "A"},
        {"代码": "000002", "名称": "B"},
        {"代码": "000003", "名称": "C"},
    ])

    filtered = engine._apply_market_theme_filter(df, ["半导体"], ["AI算力"])
    codes = filtered["代码"].astype(str).tolist()

    assert codes == ["000001", "000002", "000003"]


def test_get_concept_list_reads_board_names_and_change_pct():
    engine = ScannerEngine()
    engine.market_cache = _FakeMarketCache()

    concepts = engine.get_concept_list(keyword="AI")

    assert len(concepts) == 1
    assert concepts[0]["name"] == "AI算力"
    assert concepts[0]["change_pct"] == 3.2


def test_concept_filter_supports_fuzzy_matching():
    engine = ScannerEngine()
    engine.market_cache = _FakeMarketCache()
    df = pd.DataFrame([
        {"代码": "000001", "名称": "A"},
        {"代码": "000002", "名称": "B"},
        {"代码": "000003", "名称": "C"},
    ])

    filtered = engine._apply_market_theme_filter(df, [], ["AI"])
    codes = filtered["代码"].astype(str).tolist()

    assert codes == ["000001", "000002"]


def test_industry_filter_supports_fuzzy_matching():
    engine = ScannerEngine()
    engine.market_cache = _FakeMarketCache()
    df = pd.DataFrame([
        {"代码": "000001", "名称": "A"},
        {"代码": "000002", "名称": "B"},
        {"代码": "000003", "名称": "C"},
    ])

    filtered = engine._apply_market_theme_filter(df, ["半导"], [])
    codes = filtered["代码"].astype(str).tolist()

    assert codes == ["000001", "000003"]


def test_resolve_market_theme_can_match_concepts_without_explicit_type():
    engine = ScannerEngine(ai_config={"enabled": False})
    engine.market_cache = _FakeMarketCache()

    result = engine.resolve_market_theme("AI")

    assert result["industries"] == []
    assert result["concepts"] == ["AI算力"]
    assert result["source"] == "local"


def test_market_theme_filter_uses_union_not_intersection():
    engine = ScannerEngine(ai_config={"enabled": False})
    engine.market_cache = _FakeMarketCache()
    df = pd.DataFrame([
        {"代码": "000001", "名称": "A"},
        {"代码": "000002", "名称": "B"},
        {"代码": "000003", "名称": "C"},
    ])

    filtered = engine._apply_market_theme_filter(df, ["半导体"], ["AI算力"])
    codes = filtered["代码"].astype(str).tolist()

    assert codes == ["000001", "000002", "000003"]


def test_market_query_supports_multiple_terms_split_by_comma():
    engine = ScannerEngine(ai_config={"enabled": False})
    engine.market_cache = _FakeMarketCache()

    result = engine.resolve_market_theme("AI,半导体")

    assert result["industries"] == ["半导体"]
    assert result["concepts"] == ["AI算力"]


def test_market_query_falls_back_to_direct_constituent_probe_when_board_lists_fail():
    engine = ScannerEngine(ai_config={"enabled": False})
    engine.market_cache = _BrokenBoardListMarketCache()

    result = engine.resolve_market_theme("半导体,机器人")

    assert result["industries"] == ["半导体"]
    assert result["concepts"] == ["机器人"]
    assert result["source"] == "direct"


def test_market_query_without_matches_should_not_raise_scan_error():
    engine = ScannerEngine(ai_config={"enabled": False})

    class _QuickScanCache(_BrokenBoardListMarketCache):
        def get_all_stocks(self, force_refresh: bool = False):
            return pd.DataFrame([
                {"代码": "000001", "名称": "A", "最新价": 10, "涨跌幅": 1.0, "换手率": 2.0, "振幅": 3.0, "成交额": 2e8},
                {"代码": "000002", "名称": "B", "最新价": 11, "涨跌幅": 2.0, "换手率": 3.0, "振幅": 4.0, "成交额": 3e8},
            ])

        def get_cache_status(self):
            return {"stocks": {"cached": True, "expired": False, "count": 2, "age_seconds": 0}}

    engine.market_cache = _QuickScanCache()
    candidates, scan_info = engine.quick_scan(rule_name="healthy_pullback", market_query="AI")

    assert "error" not in scan_info
    assert scan_info["fallback_unfiltered"] is True
    assert scan_info["market_query"] == "AI"
    assert isinstance(candidates, list)


if __name__ == "__main__":
    test_apply_concept_filter_keeps_only_concept_members()
    test_industry_and_concept_filter_can_be_combined_as_intersection()
    test_get_concept_list_reads_board_names_and_change_pct()
    test_concept_filter_supports_fuzzy_matching()
    test_industry_filter_supports_fuzzy_matching()
    test_resolve_market_theme_can_match_concepts_without_explicit_type()
    test_market_theme_filter_uses_union_not_intersection()
    test_market_query_supports_multiple_terms_split_by_comma()
    test_market_query_falls_back_to_direct_constituent_probe_when_board_lists_fail()
    test_market_query_without_matches_should_not_raise_scan_error()
    print("ALL SCANNER CONCEPT FILTER TESTS PASSED")