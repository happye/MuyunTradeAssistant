# -*- coding: utf-8 -*-
"""G 批次修复回归测试（G区块裁决 2026-08-30，ISS-074）

- G01: portfolio.yaml 损坏 → 拒绝覆盖写入（不再 fail-open 清空）+ 原子写 + 备份
- G04: YAML 数字键规范化（int 600519 → str "600519"）
- G03/G06: 技能内聚合改加权得分制（强 SELL 不再被弱 BUY 数量折叠；WATCH 不抬票）
- G05: SELL 强弱死分支合并（行为不变，文档诚实）
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import yaml
import pytest

from src.data.portfolio import PortfolioManager
from src.core.skill_engine import YAMLBasedSkill
from src.data.models import StockData, SignalType


def _write_pf(path, data):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)


def _mk_positions(n):
    return {"positions": {
        f"60000{i}": {"stock_name": f"股{i}", "entry_price": 10.0,
                      "current_ratio": 0.1, "lifecycle": "HOLD"}
        for i in range(1, n + 1)
    }}


# ── G01 ──────────────────────────────────────────────────────────

def test_g01_corrupted_file_blocks_save(tmp_path):
    """损坏文件加载后必须拒绝覆盖写入——原实现 fail-open 清空，
    下一次 add_position 就把真实持仓从文件里抹掉（G01 🔴）"""
    pf = str(tmp_path / "portfolio.yaml")
    with open(pf, "w", encoding="utf-8") as f:
        f.write("positions:\n  600519: [ 未闭合\n    broken: :::{{{{")  # 语法损坏
    pm = PortfolioManager(portfolio_path=pf)
    assert len(pm._data.get("positions", {})) == 0
    assert getattr(pm, "_corrupted", False) is True, "损坏后应置 _corrupted 标志"

    pm.add_position("000001", stock_name="x", entry_price=10.0, ratio=0.1)
    with open(pf, encoding="utf-8") as f:
        content = f.read()
    assert "000001" not in content, "损坏保护下 _save 必须拒绝写入"
    assert "未闭合" in content, "原损坏文件内容必须原样保留（供人工修复）"


def test_g01_atomic_save_and_backup(tmp_path):
    pf = str(tmp_path / "portfolio.yaml")
    _write_pf(pf, _mk_positions(2))
    pm = PortfolioManager(portfolio_path=pf)
    pm.add_position("000009", stock_name="新", entry_price=10.0, ratio=0.1)
    with open(pf, encoding="utf-8") as f:
        back = yaml.safe_load(f)
    assert "000009" in back["positions"] and len(back["positions"]) == 3
    bak = pf + ".bak"
    assert os.path.exists(bak), "保存后应保留 .bak 单份滚动备份"
    with open(bak, encoding="utf-8") as f:
        bak_data = yaml.safe_load(f)
    assert len(bak_data["positions"]) == 2, ".bak 应是保存前的旧状态"


def test_g01_bulk_removal_keeps_snapshot_backup(tmp_path):
    """单次 save 持仓数减半以上（内存被异常清空/批量误操作）：
    主文件放行（合法删仓不被阻断），但保存前必须留时间戳快照备份"""
    pf = str(tmp_path / "portfolio.yaml")
    _write_pf(pf, _mk_positions(4))
    pm = PortfolioManager(portfolio_path=pf)
    # 模拟内存态一次减半（如代码 bug 清空 _data 后只余 1 只）再 _save
    keep = pm._data["positions"]["600004"]
    pm._data["positions"] = {"600004": keep}
    pm._save()
    with open(pf, encoding="utf-8") as f:
        back = yaml.safe_load(f)
    assert len(back["positions"]) == 1, "减半保存应放行（不阻断合法操作）"
    snaps = [f for f in os.listdir(tmp_path) if f.startswith("portfolio.yaml.before_")]
    assert snaps, "减半以上保存前应留时间戳快照备份"
    with open(os.path.join(tmp_path, snaps[0]), encoding="utf-8") as f:
        snap_data = yaml.safe_load(f)
    assert len(snap_data["positions"]) == 4, "快照应是保存前的完整 4 只状态"


# ── G04 ──────────────────────────────────────────────────────────

def test_g04_int_keys_normalized(tmp_path):
    """手改文件的无引号数字键（YAML 解析为 int）必须规范化为 str，
    否则 has_position 查不到但总仓位照算 = 隐形持仓"""
    pf = str(tmp_path / "portfolio.yaml")
    with open(pf, "w", encoding="utf-8") as f:
        f.write("positions:\n  600519:\n    stock_name: 茅台\n    current_ratio: 0.3\n")
    pm = PortfolioManager(portfolio_path=pf)
    assert pm.has_position("600519"), "int 键规范化后应能按字符串查到"
    assert "600519" in pm._data["positions"]
    assert all(isinstance(k, str) for k in pm._data["positions"])


# ── G03/G06 ──────────────────────────────────────────────────────

def _skill(rules):
    from pathlib import Path
    s = YAMLBasedSkill(name="test", config={"rules": rules},
                       yaml_path=Path("."), skill_type="base")
    sd = StockData(stock_code="x", stock_name="x", price=10.0, volume=1000,
                   rsi_6=55, rsi_12=55, rsi_24=45)
    return s.execute(sd)


def test_g03_strong_sell_not_folded_by_weak_buys():
    """1 强SELL(0.95,w1.5) + 1 弱BUY(0.3,w0.3) 命中：加权得分制下 SELL 应胜出
    （原数量表决 1:1 打平 → HOLD conf=0.84，SELL 证据被折叠）"""
    sig = _skill([
        {"condition": {"rsi_6_above_50": {}}, "signal": "SELL", "confidence": 0.95, "weight": 1.5},
        {"condition": {"rsi_12_above_50": {}}, "signal": "BUY", "confidence": 0.3, "weight": 0.3},
    ])
    assert sig.signal == SignalType.SELL, f"强 SELL 应胜出，实际 {sig.signal}"
    assert sig.confidence >= 0.9


def test_g06_watch_rules_do_not_inflate_buy():
    """1BUY(0.3)+2WATCH(0.5)：BUY 置信度应为自身桶 0.3（原实现被 WATCH 抬到 0.4）"""
    sig = _skill([
        {"condition": {"rsi_6_above_50": {}}, "signal": "BUY", "confidence": 0.3, "weight": 0.5},
        {"condition": {"rsi_12_above_50": {}}, "signal": "WATCH", "confidence": 0.5, "weight": 0.5},
        {"condition": {"rsi_24_above_50": {}}, "signal": "WATCH", "confidence": 0.5, "weight": 0.5},
    ])
    assert sig.signal == SignalType.BUY
    assert sig.confidence == 0.3, f"BUY 置信度应为自身桶值 0.3，实际 {sig.confidence}"


def test_g03_count_and_weight_agree_unchanged():
    """权重与数量同向时行为不变（1BUY 命中、无 SELL → BUY）"""
    sig = _skill([
        {"condition": {"rsi_6_above_50": {}}, "signal": "BUY", "confidence": 0.75, "weight": 1.0},
    ])
    assert sig.signal == SignalType.BUY and sig.confidence == 0.75


# ── G05 ──────────────────────────────────────────────────────────

def test_g05_sell_branch_behavior_stable():
    """G05 合并死分支后行为必须不变：强弱卖出都是 REDUCE 留 65%"""
    from src.core.decision_engine import DecisionEngine
    from src.data.models import MarketState, PositionAction
    eng = DecisionEngine()
    ws = {SignalType.BUY: 0.1, SignalType.SELL: 0.7, SignalType.HOLD: 0.1, SignalType.WATCH: 0.1}
    a = eng._calculate_position(SignalType.SELL, MarketState.TRANSITION,
                                {**ws, SignalType.SELL: 0.6}, [], current_position_ratio=0.5)
    b = eng._calculate_position(SignalType.SELL, MarketState.TRANSITION,
                                {**ws, SignalType.SELL: 0.2}, [], current_position_ratio=0.5)
    assert a == b == (PositionAction.REDUCE, 0.325)
