"""维度元数据注册表测试（ADR-07，v0.8.17）：registry 与 scorer 权重键集合一致。

ADR-07 口径：唯一公式在 scorer.py，registry 只做元数据集中——两者漂移
（registry 有 scorer 没有的维度，或反之）会让展示与公式脱节，测试锁死。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.benzong.registry import DIM_CN, DIM_ORDER


def test_registry_covers_all_scorer_weighted_dims():
    """scorer.py 的全部加权维度必须在 registry 有中文名（展示不缺项）。"""
    from src.core.benzong import scorer
    weighted = {k for k in vars(scorer) if k.startswith("WEIGHT_")}
    # WEIGHT_X 的维度名 = X 小写
    dims_from_weights = {w[len("WEIGHT_"):].lower() for w in weighted}
    assert dims_from_weights == set(DIM_CN.keys()), \
        f"registry 与 scorer 权重维度漂移: {dims_from_weights ^ set(DIM_CN.keys())}"


def test_registry_order_and_cn_consistency():
    """DIM_ORDER 与 DIM_CN 键集合一致（顺序表不漏维度）。"""
    assert set(DIM_ORDER) == set(DIM_CN.keys())
    assert len(DIM_ORDER) == 6


def test_registry_cache_key_matches_cache_implementation():
    """CACHE_KEY_FIELDS 与 cache.py 实际键签名一致（ADR-07 元数据不漂移）。"""
    import inspect
    from src.core.benzong import cache
    from src.core.benzong.registry import CACHE_KEY_FIELDS
    params = list(inspect.signature(cache.get).parameters.keys())
    assert params[:3] == CACHE_KEY_FIELDS, \
        f"registry 声明的缓存键 {CACHE_KEY_FIELDS} 与 cache.get 实际签名 {params[:3]} 漂移"
