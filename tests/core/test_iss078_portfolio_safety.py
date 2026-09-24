"""ISS-078 持仓数据安全回归测试（第三轮对抗审查 2026-09-03）

锁死语义：
1. update_from_strategy_decision 回写必须保留既有 trade_plan（P0：chat 每分析一次
   持仓股就把计划抹掉的病根；连带 overweight_executed「只出手一次」铁律标志）
2. add_position / update_position_fields 拒绝非法值：负数/NaN/Inf 仓位、非正数开仓价
   （AI 供参的 manage_portfolio 是入口，数据层必须兜底）
3. portfolio.yaml 中残缺 trade_plan 加载不崩，且原始数据在保存后原样保留（防静默抹除）
4. 会话外修改 portfolio.yaml 后 _save 必须拒绝写入并回滚内存（M5 内容指纹，
   升级原 mtime 告警；内容未变的 touch 不算冲突）

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


# ── 4. M5 并发写保护（plan/TECHNICAL_HANDOFF §6，升级 ISS-078 的 mtime 检测）──

def test_save_rejects_when_disk_modified_externally(caplog):
    """两实例同版本起步：A 先保存，B 后保存必须被拒绝——不得覆盖较新的用户操作。

    M5 核心场景（任务卡验收）：内容指纹检测外部修改 → 拒绝写入 + 内存回滚磁盘版。
    （原实现只告警仍覆盖，mtime 检测形同虚设。）
    """
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.1, "strategy_state": {},
    }}) as path:
        a = PortfolioManager(path)
        b = PortfolioManager(path)      # 与 A 同一磁盘版本起步
        assert a.add_position("000001", stock_name="A的新仓", ratio=0.1) is True
        # B 基于旧快照改：必须冲突拒绝，磁盘上 A 的改动不能被覆盖
        with caplog.at_level(logging.WARNING, logger="src.data.portfolio"):
            r = b.add_position("600036", stock_name="B的新仓", ratio=0.1)
        assert r is False, "旧快照保存必须被拒绝（防覆盖另一会话的较新改动）"
        assert any("已被其他会话" in rec.message for rec in caplog.records), \
            "冲突拒绝必须发人话告警"
        with open(path, encoding="utf-8") as f:
            disk = yaml.safe_load(f)
        assert "000001" in disk["positions"], "A 的较新改动必须完好"
        assert "600036" not in disk["positions"], "B 的旧快照改动不得落盘"
        # 内存回滚：B 已重新加载磁盘真值（A 的改动可见，B 自己的被丢弃）
        assert b.get_position("600036") is None
        assert b.get_position("000001") is not None


def test_save_tolerates_touch_with_same_content(caplog):
    """外部只改 mtime 未改内容（编辑器 touch/复制文件）→ 不算冲突，正常保存。

    内容指纹比 mtime 更少误报：mtime 变了但字节相同 = 没有外部改动可覆盖。
    """
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.1, "strategy_state": {},
    }}) as path:
        pm = PortfolioManager(path)
        st = os.stat(path)
        os.utime(path, (st.st_atime, st.st_mtime + 100))
        with caplog.at_level(logging.WARNING, logger="src.data.portfolio"):
            r = pm.add_position("000001", stock_name="新仓", ratio=0.1)
        assert r is True, "内容未变的 touch 不算冲突，必须正常保存"
        assert not any("已被其他会话" in rec.message for rec in caplog.records)


def test_add_position_without_prior_file_saves():
    """首次建仓（加载时无文件）→ 无冲突语义，正常保存返回 True。"""
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="iss078_m5_")
    os.close(fd)
    os.remove(path)   # 确保文件不存在
    try:
        pm = PortfolioManager(path)
        assert pm.add_position("600519", stock_name="首仓", entry_price=100.0, ratio=0.2) is True
        pm2 = PortfolioManager(path)
        assert pm2.get_position("600519") is not None
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_update_position_fields_propagates_save_failure():
    """_save 失败 → update_position_fields 返回 False，不报假成功（M5）。

    回归锁：原实现忽略 _save 返回值，保存失败仍返回 True（chat 会向用户谎报成功）。
    """
    from src.data.portfolio import SaveResult
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.1, "strategy_state": {},
    }}) as path:
        pm = PortfolioManager(path)
        pm._save = lambda: SaveResult(False, False)
        assert pm.update_position_fields("600519", current_ratio=0.2) is False


def test_attach_plan_propagates_save_failure():
    """_save 失败 → attach_plan 返回 False，不报假成功（M5）。"""
    from src.data.portfolio import SaveResult
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.1, "strategy_state": {},
    }}) as path:
        pm = PortfolioManager(path)
        pm._save = lambda: SaveResult(False, False)
        assert pm.attach_plan("600519", TradePlan(**_plan_dump())) is False


def test_update_from_strategy_decision_propagates_save_failure():
    """_save 失败 → update_from_strategy_decision 返回 False（M5 起传播 bool）。"""
    from src.data.portfolio import SaveResult
    with _tmp_portfolio({"600519": {
        "stock_name": "贵州茅台", "current_ratio": 0.1, "strategy_state": {},
    }}) as path:
        pm = PortfolioManager(path)
        pm._save = lambda: SaveResult(False, False)
        fake_sd = _fake_decision()
        fake_stock = type("S", (), {"stock_name": "贵州茅台"})()
        assert pm.update_from_strategy_decision(
            "600519", "贵州茅台", fake_sd, fake_stock, price=1500.0) is False
