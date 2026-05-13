import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def test_apply_concept_filter_keeps_only_concept_members():
    engine = ScannerEngine()
    engine.market_cache = _FakeMarketCache()
    df = pd.DataFrame([
        {"代码": "000001", "名称": "A"},
        {"代码": "000002", "名称": "B"},
        {"代码": "000003", "名称": "C"},
    ])

    filtered = engine._apply_concept_filter(df, ["AI算力"])
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

    after_industry = engine._apply_industry_filter(df, ["半导体"])
    filtered = engine._apply_concept_filter(after_industry, ["AI算力"])
    codes = filtered["代码"].astype(str).tolist()

    assert codes == ["000001"]


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

    filtered = engine._apply_concept_filter(df, ["AI"])
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

    filtered = engine._apply_industry_filter(df, ["半导"])
    codes = filtered["代码"].astype(str).tolist()

    assert codes == ["000001", "000003"]


if __name__ == "__main__":
    test_apply_concept_filter_keeps_only_concept_members()
    test_industry_and_concept_filter_can_be_combined_as_intersection()
    test_get_concept_list_reads_board_names_and_change_pct()
    test_concept_filter_supports_fuzzy_matching()
    test_industry_filter_supports_fuzzy_matching()
    print("ALL SCANNER CONCEPT FILTER TESTS PASSED")