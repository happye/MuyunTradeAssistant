# -*- coding: utf-8 -*-
"""H 批次修复回归测试（H区块裁决 2026-08-30）

- H01: 涨跌停判定按代码前缀分板——创业板/科创板 20cm 内正常波动不得误封
- H03: RAG store load 必须校验 index.ntotal == len(docs)，错配拒绝加载
- H02: TradePlan.entry_price 字段 + adjuster trailing 优先用真入场价
- H05: chat 工具代码规范化（带前缀/空格输入可匹配）
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np
import pandas as pd
import pytest

from src.core.execution_layer import ExecutionLayer
from src.data.models import StockData, PositionAction, ExecutionConstraint


def _sd(code, pct, price=100.0):
    return StockData(stock_code=code, stock_name="测试", price=price, volume=2e6,
                     avg_volume_20=1e6, change_pct=pct,
                     high_60d=price * 1.2, low_60d=price * 0.8)


# ── H01 ──────────────────────────────────────────────────────────

@pytest.fixture()
def el():
    return ExecutionLayer(ExecutionConstraint())


def test_h01_chinext_normal_surge_not_blocked(el):
    """创业板 +12%（20cm 板内合法波动）不得被判涨停封板"""
    ev = el.evaluate(_sd("300502", 12.0), PositionAction.OPEN)
    assert not ev.blocked, f"创业板 +12% 被误封: {ev.block_reason}"


def test_h01_chinext_normal_crash_sell_not_blocked(el):
    """创业板 -12% 当日止损 SELL 不得被判跌停封板"""
    ev = el.evaluate(_sd("300502", -12.0), PositionAction.CLOSE_ALL)
    assert not ev.blocked, f"创业板 -12% 止损被误封: {ev.block_reason}"


def test_h01_main_board_limit_still_blocked(el):
    """主板 +9.9%（真涨停区）仍必须封锁 BUY"""
    ev = el.evaluate(_sd("600519", 9.9), PositionAction.OPEN)
    assert ev.blocked and "涨停" in ev.block_reason


def test_h01_main_board_limit_down_still_blocked(el):
    """主板 -9.9%（真跌停区）仍必须封锁 SELL"""
    ev = el.evaluate(_sd("600519", -9.9), PositionAction.CLOSE_ALL)
    assert ev.blocked and "跌停" in ev.block_reason


def test_h01_star_market_limit_blocked(el):
    """科创板 +19.9%（真涨停）必须封锁 BUY"""
    ev = el.evaluate(_sd("688981", 19.9), PositionAction.OPEN)
    assert ev.blocked


def test_h01_star_normal_move_not_blocked(el):
    """科创板 +12%（未到 19.5%）不得封"""
    ev = el.evaluate(_sd("688981", 12.0), PositionAction.OPEN)
    assert not ev.blocked


# ── H03 ──────────────────────────────────────────────────────────

def test_h03_store_load_rejects_mismatch(tmp_path):
    """index.faiss 与 docs.json 数量错配时 load 必须拒绝（返回 False 触发重建），
    原实现静默加载 → 检索结果截断/张冠李戴"""
    from src.rag.store import FAISSVectorStore
    from src.rag.models import RAGDocument
    import faiss

    s = FAISSVectorStore()
    docs = [RAGDocument(id="d1", content="内容" * 40, source="a.txt"),
            RAGDocument(id="d2", content="内容二" * 40, source="b.txt")]
    s.add(docs, np.array([[1.0, 0], [0, 1.0]], dtype=np.float32))
    s.save(str(tmp_path))
    # 模拟中断：向 index 追加同维向量后重写（ntotal=3 ≠ docs=2）
    s._index.add(np.array([[0.5, 0.5]], dtype=np.float32))
    import faiss
    faiss.write_index(s._index, str(tmp_path / "index.faiss"))

    s2 = FAISSVectorStore()
    assert s2.load(str(tmp_path)) is False, "数量错配必须拒绝加载"
    # 修复后文档应保持干净，可重建
    assert s2.size == 0


def test_h03_store_atomic_save_roundtrip(tmp_path):
    from src.rag.store import FAISSVectorStore
    from src.rag.models import RAGDocument
    s = FAISSVectorStore()
    docs = [RAGDocument(id="d1", content="内容" * 40, source="a.txt")]
    s.add(docs, np.array([[1.0, 0]], dtype=np.float32))
    s.save(str(tmp_path))
    assert not list(tmp_path.glob("*.tmp")), "保存后不应残留 tmp 文件"
    s2 = FAISSVectorStore()
    assert s2.load(str(tmp_path)) is True
    assert s2.size == 1


# ── H02 ──────────────────────────────────────────────────────────

def test_h02_trade_plan_carries_entry_price():
    from src.core.trade_plan.generator import generate_plan_draft
    plan = generate_plan_draft("600519", "贵州茅台", entry_price=100.0, ratio=0.2,
                               benzong_grade="B")
    assert plan.entry_price == 100.0, "TradePlan 应直接携带真入场价"
    # 序列化往返（持久化兼容）
    d = plan.model_dump()
    from src.data.models import TradePlan
    plan2 = TradePlan(**d)
    assert plan2.entry_price == 100.0


def test_h02_adjuster_trailing_prefers_real_entry():
    """trailing 浮盈门槛优先用 plan.entry_price：
    entry=100、high=104（+4%）→ 未到 5% 门槛，不应给 trailing 建议
    （旧反推 entry=targets[0]/1.10=90.9 会把 +4% 算成 +14.4% 误触发）"""
    from src.core.trade_plan.generator import generate_plan_draft
    from src.core.trade_plan.adjuster import TradePlanAdjuster
    plan = generate_plan_draft("600519", "贵州茅台", entry_price=100.0, ratio=0.2,
                               benzong_grade="B")
    sd = StockData(stock_code="600519", stock_name="x", price=103.0, volume=1000,
                   atr_14=0.5, high=104.0)
    sug = TradePlanAdjuster().suggest(plan, sd, high_since_entry=104.0)
    trailing = [s for s in sug if s["trigger"] == "auto_trailing"]
    assert not trailing, f"入场价 100、最高 104（+4%<5%）不应触发 trailing: {trailing}"


# ── H05 ──────────────────────────────────────────────────────────

def test_h05_code_normalization_helper():
    from src.chat.tools import _normalize_code
    assert _normalize_code("sh.600519") == "600519"
    assert _normalize_code(" 600519 ") == "600519"
    assert _normalize_code("600519") == "600519"
    assert _normalize_code("SZ.000001") == "000001"
