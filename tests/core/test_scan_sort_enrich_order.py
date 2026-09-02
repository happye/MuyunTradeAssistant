"""M-G 回归：sort_by 列数据源缺失时，先富集再排序再截断（2026-09-03）

锁死语义：healthy_pullback 声明 sort_by=change_60d，但新浪/efinance 都没有该列。
旧行为 head(30) 截断发生在 _enrich_trend_data 填 60d 之前 →「按60日涨幅Top30」
实为任意30只。候选量 ≤ 富集上限(50) 时必须先富集、后排序、再截断。

跑法：pytest tests/core/test_scan_sort_enrich_order.py
"""
import os
import sys
import tempfile

import pandas as pd
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.scanner.scanner_engine import ScannerEngine


class _FakeCache:
    def get_all_stocks(self):
        n = 40
        return pd.DataFrame({
            "代码": [f"6000{i:03d}" for i in range(n)],
            "名称": [f"股{i}" for i in range(n)],
            "最新价": [10.0] * n,
            "成交量": [1000] * n,
        })

    def get_cache_status(self):
        return {}


def _rules_file(tmp_path):
    rules = {
        "global_exclude": [],
        "rules": {
            "test_sort": {
                "name": "排序回归",
                "filters": [{"field": "price", "op": "gte", "value": 1.0}],
                "sort_by": "change_60d",
                "sort_desc": True,
                "max_candidates": 30,
            },
        },
    }
    p = tmp_path / "scan_rules_test.yaml"
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(rules, f, allow_unicode=True)
    return str(p)


def test_change_60d_sort_applies_after_enrich(tmp_path, monkeypatch):
    eng = ScannerEngine(rules_path=_rules_file(tmp_path))
    eng.market_cache = _FakeCache()

    def _fake_enrich(candidates):
        # 与候选顺序无关的确定性伪随机值——旧代码 head 先截断时，
        # 取到的 30 个值必然乱序，才能区分「先截断后富集」与「先富集后排序」
        for c in candidates:
            c.change_60d = float(int(c.stock_code) % 17) + 0.5
        return candidates

    monkeypatch.setattr(eng, "_enrich_trend_data", _fake_enrich)
    candidates, info = eng.quick_scan(rule_name="test_sort")

    assert len(candidates) == 30, "应截断到 max_candidates"
    vals = [c.change_60d for c in candidates]
    assert vals == sorted(vals, reverse=True), \
        "60d 排序必须在富集之后生效（旧行为：head 截断先于富集=任意30只）"
    assert vals[0] > vals[-1], "取的应是 60d 最高的一批"


def test_change_60d_none_sinks_to_tail_on_desc(tmp_path, monkeypatch):
    """监督审查 P1 回归：富集失败（change_60d=None）的候选必须沉底，不得挤占 TopN。

    实测过中间版 bug：元组 (is None, ...) 在 reverse=True 时把 None 顶到最前，
    次新/停牌股反而霸占「按60日涨幅Top30」头部。
    """
    eng = ScannerEngine(rules_path=_rules_file(tmp_path))
    eng.market_cache = _FakeCache()

    def _fake_enrich(candidates):
        # 20 只有值 + 20 只 None：max=30 时 None 必然进榜，验证其只能沉在尾部
        for c in candidates:
            c.change_60d = None if int(c.stock_code) % 100 < 20 else 5.0
        return candidates

    monkeypatch.setattr(eng, "_enrich_trend_data", _fake_enrich)
    candidates, info = eng.quick_scan(rule_name="test_sort")

    assert len(candidates) == 30
    head_vals = [c.change_60d for c in candidates[:20]]
    assert all(v == 5.0 for v in head_vals), "有值的候选应排在 None 之前"
    tail_vals = [c.change_60d for c in candidates[20:]]
    assert all(v is None for v in tail_vals), \
        f"None（富集失败）应沉底，实际第21-30名: {tail_vals}"
