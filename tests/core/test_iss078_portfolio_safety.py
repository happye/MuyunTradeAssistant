"""ISS-078 持仓数据安全回归测试（第三轮对抗审查 2026-09-03）

锁死语义：
1. update_from_strategy_decision 回写必须保留既有 trade_plan（P0：chat 每分析一次
   持仓股就把计划抹掉的病根；连带 overweight_executed「只出手一次」铁律标志）
2. add_position / update_position_fields 拒绝非法值：负数/NaN/Inf 仓位、非正数开仓价
   （AI 供参的 manage_portfolio 是入口，数据层必须兜底）
3. portfolio.yaml 中残缺 trade_plan 加载不崩，且原始数据在保存后原样保留（防静默抹除）
4. 会话外修改 portfolio.yaml（mtime 变化）后 _save 必须发人话告警（防旧快照回滚无感知）

纯 mock/临时目录，无网络。跑法：pytest tests/core/test_iss078_portfolio_safety.py
"""
import contextlib
import logging
import os
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.portfolio import PortfolioManager
from src.data.models import TradePlan


# ── 基建 ──────────────────────────────────────────────────

def _plan_dump(**overrides) -> dict:
    plan = TradePlan(
        plan_id="600519_2026-09-01", opened_at="2026-09-01",
        why_buy="审查回归用thesis", when_buy="MA20上方", how_much=0.2,
        when_sell_targets=[120.0], when_sell_invalidate=["跌破 MA60(¥90) + 放量"],
        locked_initial_stop=90.0, current_stop=95.0,
        max_hold_days=30, fundamental_outlook="neutral", mode="jianzong",
        overweight_executed=False,
    )
    d = plan.model_dump()
    d.update(overrides)
    return d


def _write_portfolio(path: str, positions: dict):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"positions": positions}, f, allow_unicode=True)


@contextlib.contextmanager
def _tmp_portfolio(positions: dict):
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="iss078_test_")
    os.close(fd)
    _write_portfolio(path, positions)
    try:
        yield path
    finally:
        if os.path.exists(path):
            os.remove(path)


def _fake_decision(pos_action="HOLD_POSITION", ratio=0.0):
    """鸭子类型 StrategyDecision 替身（update_from_strategy_decision 只访问属性）。"""
    from types import SimpleNamespace
    state = SimpleNamespace(
        lifecycle=SimpleNamespace(value="HOLD"), cooldown_remaining=0,
        cooldown_reason=None, reverse_count=0, inertia_counter=1,
        last_decision=SimpleNamespace(value="HOLD"), recent_signals=["HOLD"],
        signal_stability_score=1.0, reduce_protection_remaining=0,
        min_hold_remaining=0, add_protection_remaining=0,
    )
    return SimpleNamespace(
        position_action=SimpleNamespace(value=pos_action),
        action_semantic="HOLD", sell_path=None, position_ratio=ratio,
        new_state=state, entry_exit=None,
    )


# ── 1. P0：回写保留 trade_plan ────────────────────────────

def test_update_from_strategy_decision_preserves_trade_plan():
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "entry_date": "2026-09-01", "entry_price": 1500.0,
        "current_ratio": 0.1, "last_action": "HOLD_POSITION", "lifecycle": "HOLD",
        "strategy_state": {}, "trade_plan": _plan_dump(),
    }}) as path:
        pm = PortfolioManager(path)
        assert pm.get_position("600519").trade_plan is not None, "前置：计划应已加载"
        pm.update_from_strategy_decision("600519", "贵州茅台", _fake_decision(), None, price=1500.0)

        rec = pm.get_position("600519")
        assert rec.trade_plan is not None, "回写后 trade_plan 被抹掉（P0 病根）"
        assert rec.trade_plan.current_stop == 95.0, "追踪止损价必须保住"
        assert rec.trade_plan.overweight_executed is False, "超配一次性标志必须保住"

        with open(path, encoding="utf-8") as f:
            assert "trade_plan" in f.read(), "磁盘上的 trade_plan 键被删除"


def test_update_wipes_when_no_plan_is_fine():
    """无计划的持仓回写后也不得凭空造计划。"""
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.1, "lifecycle": "HOLD",
        "strategy_state": {},
    }}) as path:
        pm = PortfolioManager(path)
        pm.update_from_strategy_decision("600519", "贵州茅台", _fake_decision(), None, price=1500.0)
        assert pm.get_position("600519").trade_plan is None


# ── 2. 值校验 ─────────────────────────────────────────────

def test_add_position_rejects_invalid_ratio():
    with _tmp_portfolio({}) as path:
        pm = PortfolioManager(path)
        for bad in (-0.5, float("nan"), float("inf"), 1.5):
            try:
                pm.add_position("000001", stock_name="x", ratio=bad)
            except ValueError:
                continue
            raise AssertionError(f"add_position(ratio={bad}) 应拒绝")


def test_add_position_rejects_invalid_price():
    with _tmp_portfolio({}) as path:
        pm = PortfolioManager(path)
        for bad in (-5.0, 0.0, float("nan"), float("inf")):
            try:
                pm.add_position("000001", stock_name="x", entry_price=bad, ratio=0.2)
            except ValueError:
                continue
            raise AssertionError(f"add_position(entry_price={bad}) 应拒绝")


def test_update_fields_rejects_nan_inf():
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.1, "entry_price": 1500.0,
        "strategy_state": {},
    }}) as path:
        pm = PortfolioManager(path)
        for kwargs in ({"current_ratio": float("nan")},
                       {"current_ratio": float("inf")},
                       {"entry_price": float("nan")},
                       {"entry_price": float("inf")}):
            try:
                pm.update_position_fields("600519", **kwargs)
            except ValueError:
                continue
            raise AssertionError(f"update_position_fields({kwargs}) 应拒绝 NaN/Inf")


# ── 3. 残缺 trade_plan 加载保护 ───────────────────────────

def test_corrupt_trade_plan_loads_and_preserves_raw():
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.1, "strategy_state": {},
        "trade_plan": {"plan_id": "残缺计划"},  # 缺 opened_at/why_buy 等必填字段
    }}) as path:
        pm = PortfolioManager(path)  # 不得抛 ValidationError
        rec = pm.get_position("600519")
        assert rec is not None, "残缺计划不应拖垮整个持仓加载"

        pm._save()  # 触发一次保存，原始计划数据必须原样保留
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "残缺计划" in content, "保存后残缺 trade_plan 原始数据被静默抹除"


def test_update_preserves_corrupt_plan_raw():
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.1, "strategy_state": {},
        "trade_plan": {"plan_id": "残缺计划"},
    }}) as path:
        pm = PortfolioManager(path)
        pm.update_from_strategy_decision("600519", "贵州茅台", _fake_decision(), None, price=1500.0)
        with open(path, encoding="utf-8") as f:
            assert "残缺计划" in f.read(), "回写后残缺计划原始数据被静默抹除"


# ── 4. 会话外修改告警 ─────────────────────────────────────

def test_save_warns_on_external_modification(caplog):
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.1, "strategy_state": {},
    }}) as path:
        pm = PortfolioManager(path)
        # 模拟会话外修改：改 mtime（内容语义上等于外部编辑器保存过）
        st = os.stat(path)
        os.utime(path, (st.st_atime, st.st_mtime + 100))
        with caplog.at_level(logging.WARNING, logger="src.data.portfolio"):
            pm.add_position("000001", stock_name="新仓", ratio=0.1)
        assert any("会话外" in r.message for r in caplog.records), \
            "检测到外部修改时必须发人话告警（防旧快照回滚无感知）"
