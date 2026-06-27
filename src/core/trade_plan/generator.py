"""TradePlan 生成器（v0.8.5 阶段 1.2）

建仓时根据当下信息生成交易计划草稿。

设计：
- **规则版（必有）**：纯技术面 + 数学公式，无外部依赖，离线可跑
  · 止损价：entry - 2 × ATR(14)（策略库 ch48 标准方法）
  · max_hold_days：按 Weinstein 阶段 + 信号强度分档
  · 止盈分档：entry × (1 + 10%) / (1 + 20%)
  · 失效条件文本：模板化生成
- **AI 增强（可选）**：调 RAG + AI Modifier 生成 thesis 文本
  · API Key 不可用时降级为规则模板，不报错
  · AI 失败时 plan 仍可用，只是 thesis 简短

依据：
- 策略库 投资策略（持续更新）/望周知—系统化交易大纲.txt:322-324（七要素）
- 策略库 投资策略（持续更新）/第48章 止损位设置.txt:30-31（2×ATR）
- 策略库 投资策略（持续更新）/第48章:53-55（止损单向上移）
"""

import logging
from datetime import datetime
from typing import Optional, Tuple

from src.data.models import TradePlan, StockData

logger = logging.getLogger(__name__)


# ATR 倍数（策略库 ch48 标准）
ATR_MULTIPLIER = 2.0

# 默认止盈分档（entry 的百分比，与策略层 take_profit_trim 对齐）
DEFAULT_TAKE_PROFIT_PCTS = [0.10, 0.20]

# max_hold_days 分档（按 Weinstein 阶段 + outlook）
MAX_HOLD_DAYS_BY_STAGE = {
    "S2": 90,   # 上升期 — 中线持有
    "S1": 60,   # 筑底 — 等突破
    "S3": 30,   # 顶部 — 短线
    "S4": 15,   # 下跌 — 不该建仓，给短窗口逼出场
    "?":  60,   # 未知 — 中性默认
}

# fundamental_outlook 对 max_hold_days 的乘数
OUTLOOK_HOLD_MULTIPLIER = {
    "bullish": 1.5,    # 看好则延长
    "neutral": 1.0,
    "bearish": 0.5,    # 看空则缩短（其实不该建仓）
}


# 笨总 grade → 笨总模式映射（跳法A 阶段1：建仓时笨总评分一次定 mode）
# A 级且行业景气>0 → 气宗(长期格局,持有期长,宽止损,无技术失效条件)
# B 级 → 剑宗(一波流,持有期短,紧止损,保留技术失效条件)
# C/D/F/None → 不设模式(向后兼容,PlanGuard 仅压 weak_sell)
QIZONG_MIN_HOLD_DAYS = 180
JIANZONG_MAX_HOLD_DAYS = 30
JIANZONG_ATR_MULTIPLIER = 1.0


def _mode_from_grade(grade: Optional[str], industry_prosperity: Optional[float],
                     market_state: Optional[str] = None) -> Optional[str]:
    """由笨总等级推断交易模式。

    行业景气度=0 时模型失效（笨总大前提），即使 A 级也不设气宗。
    跳法A阶段4（批次2回测发现）：气宗是牛市武器，调整/熊市死扛下跌有害。
    market_state 作前置闸门——仅 RISK_ON(牛市)允许气宗；TRANSITION/RISK_OFF/PANIC
    一律不定气宗（A 级降为剑宗，B 级保持剑宗），让调整年不被气宗压住该跑的跌。
    market_state=None 时退回原逻辑（向后兼容，如未传大盘状态）。
    """
    # 牛市闸门：非牛市环境，气宗降级为剑宗（不死扛，但仍保留纪律）
    bull_only = market_state is not None and market_state != "RISK_ON"
    if grade == "A":
        if industry_prosperity is not None and industry_prosperity <= 0:
            return None  # 大前提失效，不强加气宗纪律
        return "jianzong" if bull_only else "qizong"
    if grade == "B":
        return "jianzong"
    return None


def _detect_weinstein_stage(stock: StockData) -> str:
    """从 StockData 推断 Weinstein 阶段（与 cli/main.py:_weinstein_stage 同口径）"""
    ma20 = stock.ma20
    ma60 = stock.ma60
    price = stock.price
    if ma20 is None or ma60 is None or price is None:
        return "?"
    if ma20 > ma60:
        if price > ma20:
            return "S2"  # 上升
        else:
            return "S3"  # 顶部
    else:
        if price > ma20:
            return "S1"  # 筑底
        else:
            return "S4"  # 下跌


def _calculate_initial_stop(stock: StockData, entry_price: float, mode: Optional[str] = None) -> float:
    """计算初始止损价（策略库 ch48 标准：entry - 2×ATR(14)）。

    跳法A 阶段1：剑宗一波流用紧凑 1×ATR 止损，气宗/默认用 2×ATR 宽松止损。
    ATR 缺失时退化为固定止损（剑宗 5% / 其他 8%）。
    """
    multiplier = JIANZONG_ATR_MULTIPLIER if mode == "jianzong" else ATR_MULTIPLIER
    if stock.atr_14 and stock.atr_14 > 0:
        stop = entry_price - multiplier * stock.atr_14
        # 防御：止损不能低于 entry 的 70%（极端波动股的下限）
        return max(stop, entry_price * 0.70)
    # 兜底：剑宗 5% / 其他 8% 固定止损
    return entry_price * (0.95 if mode == "jianzong" else 0.92)


def _calculate_take_profit_targets(entry_price: float) -> list[float]:
    """计算止盈分档目标价（与策略层 tier1_pct=10% / tier2_pct=20% 对齐）"""
    return [round(entry_price * (1 + p), 2) for p in DEFAULT_TAKE_PROFIT_PCTS]


def _calculate_max_hold_days(stage: str, outlook: str) -> int:
    """按 Weinstein 阶段 + outlook 计算最长持有天数"""
    base = MAX_HOLD_DAYS_BY_STAGE.get(stage, 60)
    multiplier = OUTLOOK_HOLD_MULTIPLIER.get(outlook, 1.0)
    return int(round(base * multiplier))


def _infer_outlook_from_stage(stage: str, change_pct: Optional[float]) -> str:
    """规则版的 outlook 推断（无 AI 时用）"""
    if stage == "S2":
        return "bullish"
    if stage in ("S4",):
        return "bearish"
    if stage == "S1":
        return "neutral"  # 筑底待确认
    if stage == "S3":
        # 顶部区，看回调幅度
        if change_pct is not None and change_pct < -2.0:
            return "bearish"
        return "neutral"
    return "neutral"


def _generate_why_buy_template(stock: StockData, stage: str, outlook: str) -> str:
    """规则版 thesis 模板（AI 不可用时的兜底）"""
    parts = []

    # 阶段判断
    stage_descriptions = {
        "S1": "Weinstein S1 筑底阶段（MA20<MA60 但价在 MA20 上方）",
        "S2": "Weinstein S2 上升阶段（MA20>MA60 且价在 MA20 上方）",
        "S3": "Weinstein S3 顶部阶段（MA20>MA60 但价在 MA20 下方）",
        "S4": "Weinstein S4 下跌阶段（MA20<MA60 且价在 MA20 下方）",
        "?":  "技术阶段数据不足",
    }
    parts.append(stage_descriptions.get(stage, "未知阶段"))

    # 趋势细节
    if stock.ma20 and stock.ma60:
        if stock.ma20 > stock.ma60:
            parts.append(f"MA20({stock.ma20:.2f})>MA60({stock.ma60:.2f}) 多头排列")
        else:
            parts.append(f"MA20({stock.ma20:.2f})<MA60({stock.ma60:.2f}) 空头排列")

    # MACD / RSI 增量信息
    if stock.macd_dif is not None and stock.macd_dea is not None:
        if stock.macd_dif > stock.macd_dea:
            parts.append("MACD 多头")
        else:
            parts.append("MACD 空头")

    parts.append(f"基本面前景判定：{outlook}（规则版从技术阶段推断，AI 增强可改）")

    return "；".join(parts)


def _generate_invalidate_conditions(stage: str, stock: StockData) -> list[str]:
    """生成失效条件文本列表（用户可改）"""
    conds = []

    # 共通失效条件
    conds.append("跌破初始止损价（致命）")

    # 阶段化失效
    if stage in ("S2", "S1"):
        if stock.ma60:
            conds.append(f"跌破 MA60(¥{stock.ma60:.2f}) + 成交量异常放大")
    if stage == "S2":
        conds.append("MA20 死叉 MA60（上升趋势结构破坏）")
    if stage == "S3":
        conds.append("从近期高点回撤 >15%（顶部确认）")

    # 外部突变
    conds.append("出现重大利空（如政策转向、财务造假、行业系统性风险）")

    return conds


def generate_plan_draft(
    stock_code: str,
    stock_name: str,
    entry_price: float,
    ratio: float,
    stock_data: Optional[StockData] = None,
    rag_context: Optional[str] = None,
    ai_thesis: Optional[str] = None,
    today: Optional[str] = None,
    benzong_grade: Optional[str] = None,
    industry_prosperity: Optional[float] = None,
    market_state: Optional[str] = None,
) -> TradePlan:
    """生成 TradePlan 草稿（规则版必有，AI 增强可选）

    Args:
        stock_code: 股票代码
        stock_name: 股票名称
        entry_price: 入场价
        ratio: 目标仓位比例
        stock_data: 当前股票技术指标（含 atr_14, ma20, ma60, macd 等）；缺失时走最低兜底
        rag_context: RAG 检索的策略章节文本（用于 thesis_sources 锚点）
        ai_thesis: AI 生成的 thesis 文本；为 None 时用规则模板
        today: 建仓日期 YYYY-MM-DD；缺失用今天
        benzong_grade: 笨总评分等级 A/B/C/D/F；用于定 mode（跳法A 阶段1）
        industry_prosperity: 笨总行业景气度维分；=0 时气宗大前提失效
        market_state: 大盘状态 RISK_ON/TRANSITION/RISK_OFF/PANIC；非牛市时气宗降级剑宗（跳法A阶段4）

    Returns:
        TradePlan: 完整的计划草稿，用户可逐字段编辑
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    plan_id = f"{stock_code}_{today}"

    # 笨总模式（跳法A 阶段1：建仓时一次定 mode，之后靠硬规则锁持有）
    mode = _mode_from_grade(benzong_grade, industry_prosperity, market_state)

    # 阶段判断 + outlook
    if stock_data:
        stage = _detect_weinstein_stage(stock_data)
        outlook = _infer_outlook_from_stage(stage, stock_data.change_pct)
    else:
        stage = "?"
        outlook = "neutral"

    # 数值计算
    if stock_data:
        initial_stop = _calculate_initial_stop(stock_data, entry_price, mode=mode)
    else:
        initial_stop = entry_price * 0.92  # 缺数据时 8% 兜底

    targets = _calculate_take_profit_targets(entry_price)
    max_hold = _calculate_max_hold_days(stage, outlook)
    # 气宗持有期拉长、剑宗收紧（覆盖按阶段算的 max_hold）
    if mode == "qizong":
        max_hold = max(max_hold, QIZONG_MIN_HOLD_DAYS)
    elif mode == "jianzong":
        max_hold = min(max_hold, JIANZONG_MAX_HOLD_DAYS)

    # thesis：AI 优先，规则模板兜底
    if ai_thesis and ai_thesis.strip():
        why_buy = ai_thesis.strip()
    elif stock_data:
        why_buy = _generate_why_buy_template(stock_data, stage, outlook)
    else:
        why_buy = f"建仓 {stock_name}（{stock_code}），仓位 {ratio:.0%}（数据不足，建议补充技术面后修订计划）"

    # when_buy 描述
    when_buy = f"建仓价 ¥{entry_price:.2f}"
    if stock_data:
        when_buy += f"，{stage} 阶段"
        if stock_data.ma20:
            relation = "上方" if stock_data.price > stock_data.ma20 else "下方"
            when_buy += f"，价格在 MA20({stock_data.ma20:.2f}){relation}"

    # 失效条件
    # 气宗：不靠技术失效条件（回调时跌破 MA 正是该拿住的时刻），仅留致命止损
    if mode == "qizong":
        invalidate = ["跌破初始止损价（致命）", "高位止盈3维度触发（宏观/板块/个股大顶信号）"]
    elif stock_data:
        invalidate = _generate_invalidate_conditions(stage, stock_data)
    else:
        invalidate = ["跌破初始止损价（致命）", "出现重大利空"]

    # 锚点
    sources = [
        "ch48-止损位设置：基于 2×ATR(14) 的标准方法",
        f"望周知大纲 ch40-41：交易计划七要素（建仓时定，按计划执行）",
    ]
    if stock_data and stage != "?":
        sources.append(f"Weinstein {stage} 阶段判定（MA20/MA60 关系 + 价格位置）")
    if rag_context:
        # 摘要 RAG 上下文头部 80 字作为锚点
        rag_snippet = rag_context.strip().replace("\n", " ")[:80]
        sources.append(f"RAG: {rag_snippet}...")

    plan = TradePlan(
        plan_id=plan_id,
        opened_at=today,
        why_buy=why_buy,
        when_buy=when_buy,
        how_much=ratio,
        when_sell_targets=targets,
        when_sell_invalidate=invalidate,
        locked_initial_stop=round(initial_stop, 2),
        current_stop=round(initial_stop, 2),
        max_hold_days=max_hold,
        fundamental_outlook=outlook,
        mode=mode,
        thesis_sources=sources,
        adjustments=[],
    )
    return plan


class TradePlanGenerator:
    """交易计划生成器（高层封装，串联 RAG + AI Modifier 增强）

    使用：
        gen = TradePlanGenerator(rag_service=rag, ai_modifier=ai)
        plan = gen.generate(stock_code, stock_name, entry_price, ratio, stock_data)

    rag_service / ai_modifier 都可为 None，缺失时走规则版兜底。
    """

    def __init__(self, rag_service=None, ai_modifier=None):
        self.rag = rag_service
        self.ai = ai_modifier

    def generate(
        self,
        stock_code: str,
        stock_name: str,
        entry_price: float,
        ratio: float,
        stock_data: Optional[StockData] = None,
        benzong_grade: Optional[str] = None,
        industry_prosperity: Optional[float] = None,
        market_state: Optional[str] = None,
    ) -> Tuple[TradePlan, dict]:
        """生成 TradePlan + 元数据（哪些来源生效、哪些降级）

        Args:
            benzong_grade: 笨总评分等级 A/B/C/D/F；用于定 mode（跳法A 阶段1）
            industry_prosperity: 笨总行业景气度维分；=0 时气宗大前提失效
            market_state: 大盘状态；非牛市时气宗降级剑宗（跳法A阶段4）

        Returns:
            (plan, meta)
            meta = {
                "rag_used": bool,
                "ai_thesis_used": bool,
                "weinstein_stage": str,
                "benzong_grade": str | None,
                "mode": str | None,
                "fallback_reasons": list[str],
            }
        """
        meta = {
            "rag_used": False,
            "ai_thesis_used": False,
            "weinstein_stage": "?",
            "benzong_grade": benzong_grade,
            "mode": _mode_from_grade(benzong_grade, industry_prosperity, market_state),
            "fallback_reasons": [],
        }

        # 步骤 1：RAG 检索（可选）
        rag_context = None
        if self.rag is not None:
            try:
                stage = _detect_weinstein_stage(stock_data) if stock_data else "?"
                meta["weinstein_stage"] = stage
                query = f"{stock_name} 建仓 止损 {stage} 阶段"
                rag_context = self.rag.get_context(query, target="modifier", max_length=600)
                if rag_context:
                    meta["rag_used"] = True
            except Exception as e:
                meta["fallback_reasons"].append(f"RAG 检索失败: {type(e).__name__}")
                logger.warning(f"TradePlanGenerator RAG 检索失败: {e}")

        # 步骤 2：AI thesis 生成（可选，本阶段先占位）
        # AI 增强留到阶段 1.4 与 adjuster 一起做（避免一次铺太大），
        # 这里先用规则模板，预留 ai_thesis 接口
        ai_thesis = None
        # TODO 阶段 1.4 接入：调 self.ai 让它读 RAG + 技术面写 thesis
        if self.ai is None:
            meta["fallback_reasons"].append("AI Modifier 未配置 — thesis 走规则模板")

        # 步骤 3：组装 plan
        plan = generate_plan_draft(
            stock_code=stock_code,
            stock_name=stock_name,
            entry_price=entry_price,
            ratio=ratio,
            stock_data=stock_data,
            rag_context=rag_context,
            ai_thesis=ai_thesis,
            benzong_grade=benzong_grade,
            industry_prosperity=industry_prosperity,
            market_state=market_state,
        )

        return plan, meta
