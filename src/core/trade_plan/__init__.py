"""TradePlan 子系统（v0.8.5）

建仓时一次性定下交易计划（七要素），之后按计划执行，避免日常波动牵着走。

模块组成：
- generator.TradePlanGenerator：建仓时根据当时技术面/AI/RAG 生成 plan 草稿
- adjuster.TradePlanAdjuster：每日 scan 时输出"建议调整止损/持有天数"
- （PlanGuard 在 src/core/plan_guard.py，因为它是 Orchestrator 的一层）

设计原则：
1. 规则版优先：止损价用 entry - 2×ATR(14)（策略库 ch48 标准方法），
   max_hold_days 按 fundamental_outlook 分档，无 AI 也能跑通
2. AI 增强为可选：thesis 文本由 AI 生成草稿，用户可改可不用
3. 用户最终决策：所有调整需 source=user 才真正生效
"""

from src.core.trade_plan.generator import TradePlanGenerator, generate_plan_draft
from src.core.trade_plan.adjuster import TradePlanAdjuster

__all__ = ["TradePlanGenerator", "generate_plan_draft", "TradePlanAdjuster"]
