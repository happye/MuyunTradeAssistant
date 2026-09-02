"""ISS-078 自举产业链字符串字段归一化回归（2026-09-03）

锁死语义：
1. save_auto_chain 入库时把 str 型 cycle_anchors/analysis_notes 归一为 [str]
   （端侧AI 链实测：字符串被 format_chain_graph 按 list join 成逐字碎片）
2. format_chain_graph 对历史已落盘的 str 字段容错渲染（不再逐字符碎裂）

跑法：pytest tests/core/test_industry_chain_normalization.py
"""
import os
import sys

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from src.data import industry_data as ind


@pytest.fixture
def auto_graph(tmp_path, monkeypatch):
    p = tmp_path / "industry_chains_auto.yaml"
    monkeypatch.setattr(ind, "AUTO_GRAPH_PATH", str(p))
    ind._graph_cache = None
    ind._graph_sources = {}
    yield p
    ind._graph_cache = None
    ind._graph_sources = {}


def test_save_auto_chain_normalizes_string_fields(auto_graph):
    ind.save_auto_chain("测试链", {
        "sections": {"上游": [{"环节": "芯片", "代表公司": ["000001 x股"]}]},
        "cycle_anchors": "锚点：订单驱动，无商品价格锚",
        "analysis_notes": "要点一；要点二",
    })
    data = yaml.safe_load(open(auto_graph, encoding="utf-8"))
    cfg = data["chains"]["测试链"]
    assert isinstance(cfg["cycle_anchors"], list), "cycle_anchors 必须归一为列表"
    assert cfg["cycle_anchors"] == ["锚点：订单驱动，无商品价格锚"]
    assert isinstance(cfg["analysis_notes"], list), "analysis_notes 必须归一为列表"


def test_format_chain_graph_tolerates_legacy_string():
    cfg = {"sections": {}, "cycle_anchors": "端侧AI是订单驱动，无商品价格锚",
           "analysis_notes": "要点一"}
    text = ind.format_chain_graph("测试", cfg)
    assert "周期位置锚点: 端侧AI是订单驱动，无商品价格锚" in text
    assert "端；侧" not in text, "字符串被逐字符 join = 渲染碎裂"


def test_format_chain_graph_list_still_works():
    cfg = {"sections": {}, "cycle_anchors": ["锚A", "锚B"], "analysis_notes": ["注A"]}
    text = ind.format_chain_graph("测试", cfg)
    assert "锚A；锚B" in text and "注A" in text
