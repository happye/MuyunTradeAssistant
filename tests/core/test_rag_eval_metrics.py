"""RAG 评估器纯计算函数测试（C4，v0.8.17）。

tests/rag_eval/evaluator.py 的指标函数是纯计算（无网络无模型）——在此纳入
离线回归；真实检索评估（需 RAG 服务+知识库）仍为手动跑（见 tests/README.md）。
锁死：series_breakdown 的前缀分组与排序（C4 引用归属分组视图）+ 既有 IR 指标。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "rag_eval")))

import evaluator


def test_series_breakdown_groups_by_doc_id_prefix():
    results = [
        {"top_docs": ["rz_001", "rz_002", "bz_010", "jrz_005", "ot_009"]},
        {"top_docs": ["rz_003", "bz_011"]},
    ]
    bd = evaluator.series_breakdown(results, top_k=5)
    assert bd == {"rz": 3, "bz": 2, "jrz": 1, "ot": 1}   # 降序


def test_series_breakdown_caps_at_top_k_and_handles_empty():
    results = [{"top_docs": ["a_1"] * 8}]   # top_k=5 截断
    bd = evaluator.series_breakdown(results, top_k=5)
    assert sum(bd.values()) == 5
    assert evaluator.series_breakdown([], top_k=5) == {}
    assert evaluator.series_breakdown([{"top_docs": []}]) == {}


def test_series_breakdown_unparseable_prefix_goes_to_other():
    results = [{"top_docs": ["weird", "_lead", "ok_1"]}]
    bd = evaluator.series_breakdown(results, top_k=5)
    assert bd.get("other") == 2 and bd.get("ok") == 1


def test_existing_ir_metrics_known_values():
    """既有 IR 指标的已知输入输出锁（防改动漂移）。"""
    # recall = 命中的相关文档数 / 总相关文档数（与位置无关，只看 top-k 内命中比例）
    assert evaluator.recall_at_k([1, 1, 0, 0, 0], 5) == 1.0
    assert evaluator.recall_at_k([1, 0, 0, 0, 0], 5) == 1.0   # 1 个相关命中 1 个 = 1.0
    assert evaluator.recall_at_k([1, 0, 0, 0, 0], 5) != 0.5
    assert evaluator.recall_at_k([0, 1, 0, 0, 0], 5) == 1.0   # 命中在 top-k 内即可
    assert evaluator.recall_at_k([0, 0, 0, 0, 0], 5) == 0.0   # 无相关文档=0（分母保护）
    assert evaluator.mrr([0, 1, 0, 0, 0]) == 0.5
    assert evaluator.mrr([0, 0, 0, 0, 0]) == 0.0
    assert evaluator.dcg_at_k([3, 2, 0], 3) > evaluator.dcg_at_k([0, 2, 3], 3)
