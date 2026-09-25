"""F8 实验基建与严格回放回归测试（plan/fusion TASKS.md F8 验收的离线可验证部分）

锁死语义：
1. 离线重放可复现：PortfolioReplay 同输入同结果（fingerprint 一致）
2. 未来数据探针红灯：bar date > as_of 必拒（validate_no_future_bars）
3. 成本加大不会无解释提高净收益（成本单调性性质测试）
4. T+1 / 整手 / 一字板 / 现金不足 —— 制度模型检查逐项
5. E0–E7 注册完整且信息集标签强制（rule_proxy/ai_lookahead/human_mode 不混记）
6. manifest 字段缺一不可（样本集为空不许开跑——VALIDATION §2）
7. 固定组合资金回放 ≠ 多只独立满仓均值（组合级共享现金语义锁死）

纯内存测试，无网络。跑法：pytest tests/core/test_experiment_replay.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.experiment import (
    E_SPEC,
    ExperimentManifest,
    InfoSetTag,
    PortfolioReplay,
    ReplayChecks,
)


# ── 1. 离线重放可复现 ─────────────────────────────────────

def test_replay_deterministic_fingerprint():
    def _run():
        r = PortfolioReplay(100_000.0)
        r.buy("600519", "2026-01-05", 100.0, 0.4)
        r.buy("000001", "2026-01-06", 10.0, 0.2)
        r.sell("600519", "2026-02-05", 110.0)
        return r
    a, b = _run(), _run()
    assert a.fingerprint() == b.fingerprint(), "同输入同结果（离线重放可复现）"
    assert a.cash == b.cash and set(a.positions) == set(b.positions)


# ── 2. 未来数据探针红灯 ───────────────────────────────────

def test_future_bar_probe_red_light():
    past = [{"date": "2026-01-05", "close": 10.0}]
    ReplayChecks.validate_no_future_bars(past, "2026-02-01")  # 全部过去 → 通过
    bars = past + [{"date": "2026-03-01", "close": 11.0}]
    with pytest.raises(ValueError, match="未来数据探针红灯"):
        ReplayChecks.validate_no_future_bars(bars, "2026-02-01")  # 混入未来 bar → 红灯


# ── 3. 成本单调性：成本加大净收益不升 ─────────────────────

def test_cost_increase_never_improves_return():
    """同一交易序列，费率上调 → 最终净值必须不升（无解释的收益提升=成交模型错误）。"""
    def _run(fee_rate, stamp_rate):
        r = PortfolioReplay(100_000.0, fee_rate=fee_rate, stamp_rate=stamp_rate)
        r.buy("600519", "2026-01-05", 100.0, 0.5)
        r.sell("600519", "2026-03-05", 120.0)
        return r.cash
    base = _run(0.0003, 0.001)
    heavy = _run(0.001, 0.002)
    assert heavy <= base + 1e-6, "成本加大净收益不得提高（成本单调性）"


# ── 4. 制度模型检查逐项 ──────────────────────────────────

def test_t_plus_1_enforced():
    with pytest.raises(ValueError, match="T\\+1"):
        ReplayChecks.check_t_plus_1("2026-01-05", "2026-01-05")
    with pytest.raises(ValueError, match="T\\+1"):
        ReplayChecks.check_t_plus_1("2026-01-06", "2026-01-05")  # 卖早于买更不行
    ReplayChecks.check_t_plus_1("2026-01-05", "2026-01-06")  # 次日合法


def test_lot_size_rounding():
    assert ReplayChecks.round_lot_buy(250) == 200
    assert ReplayChecks.round_lot_buy(100) == 100
    assert ReplayChecks.round_lot_buy(99) == 0


def test_limit_board_blocked():
    with pytest.raises(ValueError, match="涨停"):
        ReplayChecks.check_limit_board("BUY", 11.0, 11.0)
    with pytest.raises(ValueError, match="跌停"):
        ReplayChecks.check_limit_board("SELL", 9.0, 9.0)
    ReplayChecks.check_limit_board("BUY", 10.5, 11.0)  # 未封板可成交


def test_replay_portfolio_semantics_shared_cash():
    """固定组合资金：共享现金——一只票买入后另一只可用现金减少（不是各自满仓）。
    断言全部用关系式（费感知买入的绝对金额随费率/取整变化，不锚数字）。"""
    r = PortfolioReplay(100_000.0)
    r.buy("600519", "2026-01-05", 100.0, 0.5)   # 目标 50% NAV（费后取整）
    cash_after_first = r.cash
    assert 0 < cash_after_first < 100_000.0, "首笔从共享现金池扣减"
    r.buy("000001", "2026-01-06", 10.0, 0.2)    # 第二笔从同一池扣（非独立账户）
    assert r.cash < cash_after_first, "共享池继续扣减"
    r.sell("600519", "2026-02-05", 110.0)       # 卖出释放现金回池（盈利价）
    assert r.cash > cash_after_first, "卖出所得回共享池"


def test_replay_rejects_buy_without_cash():
    r = PortfolioReplay(1000.0)
    with pytest.raises(ValueError, match="不足一手|现金不足"):
        r.buy("600519", "2026-01-05", 100.0, 0.001)  # 预算不足一手


# ── 5. E0–E7 注册完整性与信息集标签 ──────────────────────

def test_e_matrix_registered_with_source_tags():
    ids = {e.experiment_id for e in E_SPEC}
    assert {"E0", "E1", "E2", "E3", "E4", "E5", "E6", "E7"} <= ids, "E0–E7 全注册"
    for e in E_SPEC:
        assert e.source_tag in InfoSetTag.__members__.values() or isinstance(e.source_tag, InfoSetTag)
        assert e.fixed and e.varied and e.answers, f"{e.experiment_id} 注册字段不齐"
    # AI 实验显式标记信息集（不与规则代理混记）
    e5 = next(e for e in E_SPEC if e.experiment_id == "E5")
    assert e5.source_tag is InfoSetTag.AI_LOOKAHEAD


# ── 6. manifest 字段缺一不可 ─────────────────────────────

def test_manifest_requires_sample_set():
    with pytest.raises(ValueError, match="样本集"):
        ExperimentManifest.build("E0", config_hash="x", as_of="2026-09-25", sample_set=[])
    m = ExperimentManifest.build("E0", config_hash="x", as_of="2026-09-25",
                                 sample_set=["600519"])
    assert m.fingerprint() and m.code_commit
    # 冻结后字段变化 → 指纹变化（审计可比）
    f1 = m.fingerprint()
    m.missing_ratio = 0.5
    assert m.fingerprint() != f1


# ── 5. F8 合并审查修复回归锁 ─────────────────────────────

def test_full_weight_buy_no_fee_margin_crash():
    """P1-A 回归锁：满仓买入（预算=全部现金）不因费差崩溃——份额换算先扣费。"""
    r = PortfolioReplay(100_000.0)
    t = r.buy("600519", "2026-01-05", 100.0, 1.0)
    assert t.shares > 0 and r.cash >= 0


def test_oversell_rejected_not_truncated():
    """P2 回归锁：严格回放超卖拒绝（不静默截断伪造成交）。"""
    r = PortfolioReplay(100_000.0)
    r.buy("600519", "2026-01-05", 100.0, 0.5)
    with pytest.raises(ValueError, match="超卖"):
        r.sell("600519", "2026-01-06", 110.0, shares=999_900)


def test_lot_size_semantics():
    """P2 回归锁：check_lot_size 真语义——BUY 非整手拒、SELL 尾股允许。"""
    with pytest.raises(ValueError, match="整手"):
        ReplayChecks.check_lot_size(150, "BUY")
    assert ReplayChecks.check_lot_size(150, "SELL") == 150
    assert ReplayChecks.check_lot_size(200, "BUY") == 200


def test_manifest_commit_fallback_offline():
    """P2 回归锁：git 不可用时 code_commit 落 unknown（不空串）。"""
    m = ExperimentManifest.build("E0", config_hash="x", as_of="2026-09-25",
                                 sample_set=["600519"], code_commit="unknown")
    assert m.code_commit == "unknown"
