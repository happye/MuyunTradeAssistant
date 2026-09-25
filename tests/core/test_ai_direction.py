"""F2 AI 分数方向语义回归测试（plan/fusion TASKS F2：明确 direction/strength）

锁死语义（RESEARCH §3.1 探针 C/D 的病根修复）：
1. 探针 C 病根：bearish 统一负调节作用于 SELL 决策时，把卖出强度 0.8 削到 0.56，
   再被策略层闸门降级成 HOLD——看空反而软化卖出。修复后：SELL + 负向调节 →
   跳过分数调节（方向感知），BUY/HOLD 行为不变
2. 开关 ai.direction_aware_adjustment=false → 回 legacy 统一加减口径（A/B 对照）
3. 探针 D 病根：排名技术分 score×100 把 SELL 强度当买入吸引力——修复后 SELL 记 0 分
4. 接线哨兵：orchestrator 三处合并点（AI/事件合并/事件独立）都必须走方向感知 helper
   （防"helper 写好但合并点没接"的 no-op 回归——LRN-20260925-016 同类教训）

纯内存测试，无网络。跑法：pytest tests/core/test_ai_direction.py -q
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.orchestrator import Orchestrator
from src.data.models import SignalType

ORCH_PATH = Path(__file__).resolve().parents[2] / "src" / "core" / "orchestrator.py"


def _dr(decision: str, score: float, reasons=None):
    """鸭子 DecisionResult：helper 只访问 decision/score/reason。"""
    return SimpleNamespace(decision=SignalType(decision), score=score,
                           reason=list(reasons or []))


def _orch(direction_aware: bool = True) -> Orchestrator:
    """绕过 __init__（不装技能引擎），只测 _apply_sentiment_to_decision。"""
    o = Orchestrator.__new__(Orchestrator)
    o.direction_aware_ai = direction_aware
    return o


# ── 1. 方向感知：看空不削弱卖出 ───────────────────────────

def test_bearish_does_not_weaken_sell():
    dr = _dr("SELL", 0.8)
    _orch(True)._apply_sentiment_to_decision(dr, -0.24, "AI Modifier")
    assert dr.score == 0.8, f"SELL 强度不得被看空调节削弱，实际 {dr.score}"
    assert any("方向感知" in r for r in dr.reason), "跳过调节必须留痕"


def test_bearish_still_suppresses_buy():
    """BUY 决策行为不变：看空调节照常压制买入。"""
    dr = _dr("BUY", 0.8)
    _orch(True)._apply_sentiment_to_decision(dr, -0.24, "AI Modifier")
    assert abs(dr.score - 0.56) < 1e-9, "看空调节照常压制买入强度"


def test_bullish_still_strengthens_sell():
    """正向调节在 SELL 上照常应用（看多消息加强卖出强度=合理的同向增强）。"""
    dr = _dr("SELL", 0.8)
    _orch(True)._apply_sentiment_to_decision(dr, 0.1, "AI Modifier")
    assert abs(dr.score - 0.9) < 1e-9


def test_score_clamped_to_unit_range():
    dr = _dr("BUY", 0.95)
    _orch(True)._apply_sentiment_to_decision(dr, 0.3, "AI Modifier")
    assert dr.score == 1.0, "分数仍钳制在 [0,1]"
    dr2 = _dr("BUY", 0.1)
    _orch(True)._apply_sentiment_to_decision(dr2, -0.5, "AI Modifier")
    assert dr2.score == 0.0


def test_legacy_switch_restores_old_behavior():
    """开关关闭 = legacy 口径：SELL 也被负调节削弱（A/B 对照基线）。"""
    dr = _dr("SELL", 0.8)
    _orch(False)._apply_sentiment_to_decision(dr, -0.24, "AI Modifier")
    assert abs(dr.score - 0.56) < 1e-9, "开关关=legacy 统一加减口径"
    assert not any("方向感知" in r for r in dr.reason)


def test_black_swan_does_not_weaken_sell():
    """black_swan -0.5 同样不得削弱 SELL（仓位上限/强制状态另行应用，不受影响）。"""
    dr = _dr("SELL", 0.85)
    _orch(True)._apply_sentiment_to_decision(dr, -0.5, "AI Modifier")
    assert dr.score == 0.85


# ── 2. 接线哨兵：三处合并点必须走 helper ──────────────────

def test_orchestrator_merge_sites_use_direction_aware_helper():
    """三处（AI 分支/事件合并增量/事件独立）都调用 _apply_sentiment_to_decision。"""
    src = ORCH_PATH.read_text(encoding="utf-8")
    calls = src.count("self._apply_sentiment_to_decision(")
    assert calls >= 3, f"orchestrator 合并点接线数 {calls} < 3——存在未接线的直接加减路径"
    # 旧的裸加法模式不得再出现在合并点（三处全部改造）
    assert "decision_result.score + ai_result.score_adjustment" not in src, \
        "存在绕过方向感知的裸分数加法（AI/事件合并点）"
    assert "decision_result.score + event_ai_result.score_adjustment" not in src, \
        "存在绕过方向感知的裸分数加法（事件合并点）"


def test_ranking_sell_strength_not_buy_attractiveness():
    """探针 D 回归：SELL .9 不得折算 90 分买入技术分；BUY 照常 score×100。"""
    from src.core.ranking_layer import RankingLayer
    rank = RankingLayer.__new__(RankingLayer)  # 只测单维计算，不装配全层
    rank._clip = lambda v, lo, hi: max(lo, min(hi, v))
    sell = rank._calc_technical_score(SimpleNamespace(
        score=0.9, decision=SimpleNamespace(value="SELL")))
    buy = rank._calc_technical_score(SimpleNamespace(
        score=0.6, decision=SimpleNamespace(value="BUY")))
    assert sell[0] == 0.0, f"SELL 强度不得计入买入技术分，实际 {sell[0]}"
    assert "不计入" in sell[1]
    assert buy[0] == 60.0, "BUY 照常 score×100"
