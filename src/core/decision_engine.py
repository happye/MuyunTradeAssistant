"""决策引擎 - 聚合技能信号，生成最终决策"""

import logging
from typing import Optional

from src.data.models import (
    StockData, SkillSignal, DecisionResult, DecisionTrace, SignalType, MarketState,
    PositionAction
)

logger = logging.getLogger(__name__)


class StateMachine:
    """市场状态机 - 基于A股行业标准判断市场状态

    判定方法（A股行业共识）：
    - 年线（250日均线）= 牛熊分界线（百度百科定义，A股30年共识）
    - 20%涨跌幅法则 = 技术性牛熊市的量化定义（全球通用，A股机构采用）
    - MA50/MA200金叉死叉 = 机构常用趋势确认信号

    状态定义：
    - RISK_ON: 沪深300 > 年线 且 回撤 < 15%（牛市环境）
    - RISK_OFF: 沪深300 < 年线 且 回撤 > 20%（熊市环境）
    - TRANSITION: 介于两者之间（震荡/过渡）
    - PANIC: 个股当日暴跌>5%（或破MA60+放量暴跌>3%）——个股视角的极端保护

    ⚠️ G02 裁决（2026-08-30，方向A）：**第一优先 PANIC 判定使用的是个股自身的
    量价数据**——这是有意设计并维持现状：个股单日崩盘时，对该持仓股按恐慌市
    处理（SELL 门槛降到 0.20、策略层进入极端模式），属持仓保护性行为。
    原 docstring「不是个股」的表述与此矛盾，本次改为如实描述；
    大盘级牛熊（RISK_ON/OFF/TRANSITION）仍基于沪深300年线，数周到数月一变。
    """

    @staticmethod
    def determine_state(data: StockData) -> MarketState:
        """根据沪深300指数判断市场状态

        优先级：PANIC > 年线+回撤判断
        """

        # ===== 第一优先：PANIC（极端暴跌，不依赖长周期判断） =====
        if data.change_pct is not None and data.change_pct < -5.0:
            return MarketState.PANIC
        # 跌破MA60 + 放量暴跌(>2倍) -> PANIC
        if (data.ma60 and data.price < data.ma60
                and data.avg_volume_20 and data.volume > data.avg_volume_20 * 2
                and data.change_pct is not None and data.change_pct < -3.0):
            return MarketState.PANIC

        # ===== 第二优先：基于沪深300年线的牛熊判断（A股行业标准） =====
        # 需要沪深300收盘价和年线数据
        if data.index_close is not None and data.index_ma250 is not None:
            index_close = data.index_close
            index_ma250 = data.index_ma250

            # 计算回撤幅度（从近250日高点）
            drawdown = 0.0
            if data.index_high_250d and data.index_high_250d > 0:
                drawdown = (data.index_high_250d - index_close) / data.index_high_250d

            # 年线附近±3%视为"年线区域"（避免价格刚穿越年线就翻转）
            near_ma250 = abs(index_close - index_ma250) / index_ma250 < 0.03

            # 牛市：价格在年线上方 + 回撤小于15%
            if index_close > index_ma250 and not near_ma250 and drawdown < 0.15:
                return MarketState.RISK_ON

            # 熊市：价格在年线下方 + 回撤大于20%
            if index_close < index_ma250 and not near_ma250 and drawdown > 0.20:
                return MarketState.RISK_OFF

            # 年线附近或回撤在15%-20%之间：震荡/过渡
            return MarketState.TRANSITION

        # ===== 降级方案：无年线数据时用MA20/MA60趋势判断 =====
        # 保留部分旧逻辑作为降级，但去掉当日涨跌和当日成交量（避免日线抖动）
        if data.index_close is not None and data.index_ma20 is not None and data.index_ma60 is not None:
            if data.index_ma20 > data.index_ma60 and data.index_close > data.index_ma20:
                return MarketState.RISK_ON
            elif data.index_ma20 < data.index_ma60 and data.index_close < data.index_ma20:
                return MarketState.RISK_OFF
            else:
                return MarketState.TRANSITION

        # ===== 最终降级：仅有个股均线时 =====
        if data.ma20 and data.ma60:
            if data.ma20 > data.ma60 and data.price > data.ma20:
                return MarketState.RISK_ON
            elif data.ma20 < data.ma60 and data.price < data.ma20:
                return MarketState.RISK_OFF
            else:
                return MarketState.TRANSITION

        return MarketState.RISK_OFF


class DecisionEngine:
    """信号聚合器（v0.7.2 角色调整）

    v0.7.2 变更：
    - 从"最终决策者"变为"信号聚合器"
    - 保留职责：技能信号整合、市场状态判断、动作信号覆盖
    - 移除职责：最终交易动作决定、仓位管理（迁移到Strategy Layer）

    输出：DecisionResult（信号聚合结果，非最终交易决策）
    最终决策由 Strategy Layer + Execution Layer 产出
    """

    # 信号优先级
    SIGNAL_PRIORITY = {
        SignalType.BUY: 3,
        SignalType.HOLD: 2,
        SignalType.SELL: 1,
        SignalType.WATCH: 0,
    }

    # 信号权重
    DEFAULT_WEIGHTS = {
        "ma_trend": 1.0,
        "volume_price": 1.0,
        "support_resistance": 1.0,
        "stop_loss": 1.5,  # 止损权重更高
        "multi_timeframe": 1.2,
        "macd": 1.0,
        "rsi": 0.8,
        "boll": 0.9,
        "kdj": 0.8,
        # Phase 3新增技能
        "position_context": 1.3,  # 位置上下文（第41章）
        "market_context": 1.5,    # 大盘环境（第42章，环境决定形态有效性）
        "smart_money": 1.2,       # 主力行为（第46-47章）
        "take_profit": 1.3,       # 止盈管理（第49章）
    }

    def __init__(self, weights: Optional[dict[str, float]] = None):
        # v0.8.7.8 审计修复 D05（第四轮审查 ISS-072）：
        # 原写法 `weights or self.DEFAULT_WEIGHTS` 让所有实例**共享同一个类级 dict**，
        # 任一实例改权重会污染其它实例（实测 e1.weights[k]+=99 后 e2 也被改）；
        # 且传空 dict（falsy）会静默回落默认权重而非"空权重"。
        self.weights = dict(weights) if weights is not None else dict(self.DEFAULT_WEIGHTS)

    def make_decision(
        self,
        data: StockData,
        signals: list[SkillSignal],
        state: Optional[MarketState] = None,
        current_position_ratio: float = 0.0,
    ) -> DecisionResult:
        """信号聚合（v0.7.2: 不再是最终决策，而是信号聚合结果）

        v0.7.2 角色调整：
        - 保留：市场状态判定、基础投票、调节器修正、SELL分层门槛、动作信号覆盖
        - 移除：仓位管理（由Strategy Layer负责）
        - position_action/position_ratio字段仍填充，但作为参考，Strategy Layer会重新计算

        ISS-001: 分层决策 + 信号去重
        ISS-007: 决策追溯
        """

        trace: list[DecisionTrace] = []

        # 确定市场状态
        if state is None:
            state = StateMachine.determine_state(data)

        trace.append(DecisionTrace(
            step="市场状态判定",
            description=f"当前市场状态: {state.value}",
            data={"state": state.value, "index_trend": data.index_trend}
        ))

        # 按技能类型分组
        base_signals = [s for s in signals if s.skill_type == "base"]
        regulator_signals = [s for s in signals if s.skill_type == "regulator"]
        action_signals = [s for s in signals if s.skill_type == "action"]

        trace.append(DecisionTrace(
            step="技能分组",
            description=f"base={len(base_signals)}, regulator={len(regulator_signals)}, action={len(action_signals)}",
            data={
                "base": [f"{s.skill_alias}({s.signal.value}:{s.confidence:.0%})" for s in base_signals],
                "regulator": [f"{s.skill_alias}({s.signal.value}:{s.confidence:.0%})" for s in regulator_signals],
                "action": [f"{s.skill_alias}({s.signal.value}:{s.confidence:.0%})" for s in action_signals],
            }
        ))

        # 第一步：基础信号投票（只看base技能的独立信号）
        weighted_scores = {SignalType.BUY: 0.0, SignalType.SELL: 0.0, SignalType.HOLD: 0.0, SignalType.WATCH: 0.0}
        total_weight = 0.0
        vote_details = []

        for sig in base_signals:
            weight = self.weights.get(sig.skill_name, 1.0)
            contribution = sig.confidence * weight
            weighted_scores[sig.signal] += contribution
            total_weight += weight
            vote_details.append(f"{sig.skill_alias}: {sig.signal.value} ×{weight} ×{sig.confidence:.0%} = {contribution:.3f}")

        # 归一化
        raw_scores = {k: round(v, 4) for k, v in weighted_scores.items()}
        if total_weight > 0:
            for sig_type in weighted_scores:
                weighted_scores[sig_type] /= total_weight

        trace.append(DecisionTrace(
            step="基础信号投票",
            description=f"总权重={total_weight:.1f}",
            data={
                "raw_scores": {k.value: v for k, v in raw_scores.items()},
                "normalized_scores": {k.value: round(v, 4) for k, v in weighted_scores.items()},
                "vote_details": vote_details,
            }
        ))

        # 第二步：上下文调节器修正权重（不产出独立信号，修改base信号的得分）
        regulator_modifiers = self._apply_regulators(data, regulator_signals, weighted_scores)
        regulator_details = [f"{k.value}: ×{v:.3f}" for k, v in regulator_modifiers.items()]

        # 应用调节器修正
        for sig_type, modifier in regulator_modifiers.items():
            weighted_scores[sig_type] *= modifier

        # 重新归一化
        total = sum(weighted_scores.values())
        if total > 0:
            for sig_type in weighted_scores:
                weighted_scores[sig_type] /= total

        trace.append(DecisionTrace(
            step="调节器修正",
            description="regulator修改base得分",
            data={
                "modifiers": regulator_details,
                "adjusted_scores": {k.value: round(v, 4) for k, v in weighted_scores.items()},
            }
        ))

        # 第三步：确定最终决策（考虑市场状态）
        pre_action_signal = self._determine_final_signal(weighted_scores, state)

        trace.append(DecisionTrace(
            step="市场状态影响",
            description=f"状态={state.value}, 决策前信号={pre_action_signal.value}",
            data={
                "state": state.value,
                "final_scores": {k.value: round(v, 4) for k, v in weighted_scores.items()},
                "signal_before_action": pre_action_signal.value,
            }
        ))

        # 第四步：动作信号覆盖（止损/止盈优先级）
        # v0.6.0: 传入仓位比例，止损需要知道是否有仓位
        final_signal, action_reason = self._apply_action_signals(
            action_signals, pre_action_signal, data,
            current_position_ratio=current_position_ratio,
        )

        overridden = final_signal != pre_action_signal
        trace.append(DecisionTrace(
            step="动作信号覆盖",
            description=f"止损/止盈优先级检查{'→ 覆盖!' if overridden else '→ 未覆盖'}",
            data={
                "signal_before": pre_action_signal.value,
                "signal_after": final_signal.value,
                "overridden": overridden,
                "action_reason": action_reason,
            }
        ))

        # 计算综合评分
        # v0.8.7.8 审计修复 D04（第四轮审查 ISS-072）：
        # 原写法把 WATCH 决策的 score 取成 HOLD 桶（通常恒为 0），
        # 导致"观望"上报成 0 分，下游无法区分"弱信号"与"无数据"。
        score = weighted_scores.get(final_signal, 0.0)

        # v0.8.7.8 审计修复 D03（第四轮审查 ISS-072，🔴）：
        # final_signal 可能是动作信号（止损/止盈）一票否决的产物，它**不经过 base 投票**，
        # 因此 weighted_scores[SELL] 恒为 0.0。下游策略层有两道闸门挂在 score 上：
        #   - _apply_reverse_cost: score < 0.003 → SELL 降级为 HOLD
        #   - _needs_confirmation: score < 弱信号阈值 → 需确认 → SELL 降级为 HOLD
        # 结果是止损"一票否决"形同虚设；更糟的是降级后 HOLD 分支会走
        # `buy_score > sell_score * 1.5` 反手给出 ADD —— 在止损触发当日加仓。
        # 修法：用触发本次覆盖的动作信号置信度给 score 兜底（取较大者，不覆盖 base 票高分）。
        if final_signal == SignalType.SELL:
            veto_conf = max(
                (s.confidence for s in action_signals if s.signal == SignalType.SELL),
                default=0.0,
            )
            score = max(score, veto_conf)

        # 生成决策理由
        reasons = self._generate_reasons(data, base_signals, regulator_signals, action_signals, final_signal)
        if action_reason:
            reasons.insert(0, action_reason)

        # 生成风险提示
        warnings = self._generate_warnings(data, signals, state)

        # 第五步：计算仓位管理（ISS-014 根本解决）
        position_action, position_ratio = self._calculate_position(
            final_signal, state, weighted_scores, action_signals,
            current_position_ratio=current_position_ratio,
        )

        return DecisionResult(
            stock=data,
            state=state,
            decision=final_signal,
            score=round(score, 2),
            signals=signals,
            reason=reasons,
            warnings=warnings,
            trace=trace,
            position_action=position_action,
            position_ratio=position_ratio,
        )

    def _apply_regulators(
        self,
        data: StockData,
        regulator_signals: list[SkillSignal],
        base_scores: dict[SignalType, float]
    ) -> dict[SignalType, float]:
        """应用上下文调节器，返回各信号类型的修正系数

        调节器逻辑：
        - regulator 输出 SELL → 降低BUY得分(×0.6)，提高SELL得分(×1.3)
        - regulator 输出 BUY → 提高BUY得分(×1.3)，降低SELL得分(×0.6)
        - regulator 输出 WATCH → 降低BUY和SELL得分(×0.8)
        - regulator 输出 HOLD → 不调整(×1.0)
        - 多个regulator叠加时取几何平均
        """
        modifiers = {
            SignalType.BUY: [],
            SignalType.SELL: [],
            SignalType.HOLD: [],
            SignalType.WATCH: [],
        }

        for sig in regulator_signals:
            weight = self.weights.get(sig.skill_name, 1.0)
            # 权重越高，调节力度越大
            impact = 0.5 + (weight - 1.0) * 0.2  # 基础0.5，权重1.5时impact=0.6
            impact = min(impact, 0.8)  # 最大0.8

            if sig.signal == SignalType.SELL:
                modifiers[SignalType.BUY].append(1.0 - impact)    # 降低BUY
                modifiers[SignalType.SELL].append(1.0 + impact)   # 提高SELL
            elif sig.signal == SignalType.BUY:
                modifiers[SignalType.BUY].append(1.0 + impact)    # 提高BUY
                modifiers[SignalType.SELL].append(1.0 - impact)   # 降低SELL
            elif sig.signal == SignalType.WATCH:
                modifiers[SignalType.BUY].append(1.0 - impact * 0.5)  # 轻微降低BUY
                modifiers[SignalType.SELL].append(1.0 - impact * 0.5) # 轻微降低SELL
            # HOLD 不调整

        # 计算最终修正系数（取几何平均）
        result = {}
        for sig_type in [SignalType.BUY, SignalType.SELL, SignalType.HOLD, SignalType.WATCH]:
            mods = modifiers[sig_type]
            if mods:
                # 几何平均
                product = 1.0
                for m in mods:
                    product *= m
                result[sig_type] = product ** (1.0 / len(mods))
            else:
                result[sig_type] = 1.0

        return result

    def _apply_action_signals(
        self,
        action_signals: list[SkillSignal],
        current_decision: SignalType,
        data: StockData,
        current_position_ratio: float = 0.0,
    ) -> tuple[SignalType, Optional[str]]:
        """应用动作信号（止损/止盈），返回(最终决策, 覆盖原因)

        层级逻辑（ISS-016 方案A：止损仓位感知）：
        - 有仓位时：止损SELL confidence≥0.8 → 直接覆盖为SELL（一票否决）
        - 空仓时：止损SELL → 降级为风险提示，压制BUY为WATCH，不否决HOLD
        - 止盈SELL confidence≥0.75 → 压制BUY为HOLD，确认HOLD为SELL
        - 止损HOLD → 不覆盖
        """
        stop_loss_signals = [s for s in action_signals if s.skill_name == "stop_loss"]
        take_profit_signals = [s for s in action_signals if s.skill_name == "take_profit"]

        # 止损优先级最高
        # v0.5.2: 提高止损触发阈值从0.7→0.8
        # v0.6.0: 止损仓位感知——空仓时止损降级为WATCH（保护性观望）
        for sig in stop_loss_signals:
            if sig.signal == SignalType.SELL and sig.confidence >= 0.8:
                # L1（V3）：None=有仓（数量事实）但权重未知——按持仓处理（保护性卖出不吞）
                if current_position_ratio is None or current_position_ratio > 0:
                    # 有仓位：止损一票否决，卖出保护本金
                    return SignalType.SELL, f"⚠️ 止损信号覆盖：{', '.join(sig.reason[:2])}"
                else:
                    # 空仓：止损只是风险提示，压制BUY为WATCH（观望而非卖出）
                    if current_decision == SignalType.BUY:
                        return SignalType.WATCH, f"⚠️ 止损风险提示（空仓观望）：{', '.join(sig.reason[:2])}"
                    # 空仓+HOLD/SELL/WATCH → 不变，止损不放大空仓的卖出信号
                    return current_decision, None

        # 止盈次之
        for sig in take_profit_signals:
            if sig.signal == SignalType.SELL and sig.confidence >= 0.75:
                if current_decision == SignalType.BUY:
                    return SignalType.HOLD, f"止盈信号压制买入：{', '.join(sig.reason[:2])}"
                elif current_decision == SignalType.HOLD:
                    return SignalType.SELL, f"止盈信号确认卖出：{', '.join(sig.reason[:2])}"

        return current_decision, None

    # ===== SELL分层门槛（ISS-016 方案C） =====
    # 依赖方案D（年线牛熊判断）的稳定市场状态
    # 策略库思路："牛市做突破（不轻易卖），熊市做防守（该跑就跑）"
    SELL_THRESHOLDS = {
        MarketState.RISK_OFF: 0.25,    # 熊市：灵敏卖出（该跑就跑）
        MarketState.PANIC: 0.20,       # 恐慌：更灵敏（极端行情先跑再说）
        MarketState.TRANSITION: 0.30,  # 震荡：中等门槛（多空不明不急卖）
        MarketState.RISK_ON: 0.35,     # 牛市：保守卖出（避免震荡洗出）
    }

    # SELL信号必须明显强于BUY才触发（避免弱看空+regulator放大→SELL）
    STRONG_SELL_RATIO = 1.2  # sell_score > buy_score * 1.2

    def _determine_final_signal(
        self,
        weighted_scores: dict[SignalType, float],
        state: MarketState
    ) -> SignalType:
        """根据加权分数和市场状态确定最终信号

        v0.6.1: SELL分层门槛（方案C）
        - SELL必须达到市场状态对应的门槛（熊市0.25/震荡0.30/牛市0.35/恐慌0.20）
        - SELL必须明显强于BUY（>1.2倍），否则降为WATCH
        - 止损/止盈的action信号覆盖在第四步，不受此处门槛影响
        """

        buy_score = weighted_scores[SignalType.BUY]
        sell_score = weighted_scores[SignalType.SELL]
        hold_score = weighted_scores[SignalType.HOLD]

        # 阈值
        strong_threshold = 0.4
        weak_threshold = 0.25

        # SELL分层门槛（方案C核心）
        sell_threshold = self.SELL_THRESHOLDS.get(state, 0.30)

        # 市场状态影响决策
        if state == MarketState.PANIC:
            # PANIC状态：SELL门槛最低（0.20），但仍然需要sell>hold
            if sell_score >= sell_threshold and sell_score > hold_score:
                return SignalType.SELL
            return SignalType.WATCH

        elif state == MarketState.RISK_ON:
            # 牛市：BUY偏向灵敏，SELL偏向保守
            if buy_score >= strong_threshold:
                return SignalType.BUY
            elif buy_score >= weak_threshold and buy_score > sell_score:
                return SignalType.BUY
            elif sell_score >= sell_threshold and sell_score > buy_score * self.STRONG_SELL_RATIO:
                # 牛市SELL：需要更高门槛(0.35) + 明显强于BUY(1.2倍)
                return SignalType.SELL
            elif sell_score >= sell_threshold and sell_score > buy_score:
                # SELL达标但不够强 → 降为WATCH（牛市不轻易卖）
                return SignalType.WATCH
            else:
                return SignalType.HOLD

        elif state == MarketState.TRANSITION:
            # 过渡状态：多空分歧，买卖都需要强信号
            if buy_score >= strong_threshold and buy_score > sell_score * 1.5:
                return SignalType.BUY
            elif sell_score >= sell_threshold and sell_score > buy_score * 1.5:
                # 震荡市SELL：门槛0.30，需1.5倍优势（原逻辑保留）
                return SignalType.SELL
            else:
                return SignalType.WATCH

        else:  # RISK_OFF
            # 熊市：SELL偏向灵敏（门槛0.25），BUY需要更强信号
            if sell_score >= sell_threshold and sell_score > buy_score:
                # 熊市SELL：低门槛(0.25)，只需sell>buy（灵敏卖出）
                return SignalType.SELL
            elif buy_score >= strong_threshold and buy_score > sell_score * 1.5:
                return SignalType.BUY
            else:
                return SignalType.WATCH

    def _generate_reasons(
        self,
        data: StockData,
        base_signals: list[SkillSignal],
        regulator_signals: list[SkillSignal],
        action_signals: list[SkillSignal],
        final_signal: SignalType
    ) -> list[str]:
        """生成决策理由"""
        reasons = []

        # 动作信号理由优先展示
        for sig in action_signals:
            if sig.signal == final_signal and sig.reason:
                reasons.extend(sig.reason[:2])

        # 基础信号理由
        for sig in base_signals:
            if sig.signal == final_signal and sig.reason:
                reasons.extend(sig.reason[:2])

        # 调节器理由（标注为上下文）
        for sig in regulator_signals:
            if sig.reason:
                for r in sig.reason[:1]:
                    reasons.append(f"[上下文] {r}")

        if len(reasons) < 3 and data.ma20:
            if data.price > data.ma20:
                reasons.append(f"价格({data.price})在MA20({data.ma20})上方")
            else:
                reasons.append(f"价格({data.price})在MA20({data.ma20})下方")

        # 去重
        seen = set()
        unique_reasons = []
        for r in reasons:
            if r not in seen:
                seen.add(r)
                unique_reasons.append(r)

        return unique_reasons[:5]

    def _generate_warnings(
        self,
        data: StockData,
        signals: list[SkillSignal],
        state: MarketState
    ) -> list[str]:
        """生成风险提示（融入第41-55章策略智慧）"""
        warnings = []

        # 市场状态提示
        if state == MarketState.RISK_OFF:
            warnings.append("市场处于谨慎状态，建议降低仓位")
        elif state == MarketState.TRANSITION:
            warnings.append("市场多空分歧，趋势不明，建议轻仓观望")
        elif state == MarketState.PANIC:
            warnings.append("市场恐慌，但需区分恐慌性下跌和趋势性下跌")

        # 第42章：均线空头排列，熊市中突破不可信
        if data.ma5 and data.ma20 and data.ma60:
            if data.ma5 < data.ma20 < data.ma60:
                warnings.append("均线空头排列，中期趋势向下，突破信号不可靠")

        # 第44章：情绪亢奋特征 - RSI超买+高位
        if data.rsi_6 and data.rsi_6 > 80:
            warnings.append("RSI极度超买，市场情绪亢奋，技术指标可能失效")

        # 第41章：高位形态降权
        if data.high_60d and data.low_60d:
            price_range = data.high_60d - data.low_60d
            if price_range > 0:
                position = (data.price - data.low_60d) / price_range
                if position > 0.85:
                    warnings.append("价格处于60日区间高位，形态可靠性降低，警惕骗线")

        # 第46章：高位放量滞涨 - 出货信号
        if data.avg_volume_20 and data.volume > data.avg_volume_20 * 2:
            if data.high_60d and data.price > data.high_60d * 0.9:
                if data.change_pct is not None and data.change_pct < 1:
                    warnings.append("高位放量滞涨，疑似主力出货")

        # 第48/54章：止损纪律提醒
        stop_loss_signals = [s for s in signals if s.skill_name == "stop_loss"]
        for sig in stop_loss_signals:
            if sig.signal == SignalType.SELL:
                warnings.append("触发止损条件，止损只上移不下移，别让希望绑架决策")

        # 第52章：贪婪警告 - 不加杠杆
        buy_signals = [s for s in signals if s.signal == SignalType.BUY]
        if len(buy_signals) >= 4 and state == MarketState.RISK_ON:
            warnings.append("多个买入信号共振，但切忌因贪婪加杠杆")

        # 第53章：恐惧警告 - 不在恐慌中卖出
        sell_signals = [s for s in signals if s.signal == SignalType.SELL]
        if len(sell_signals) >= 4:
            warnings.append("多个卖出信号，但需区分趋势性下跌和恐慌性下跌，避免割在地板")

        # 第49章：止盈提醒
        take_profit_signals = [s for s in signals if s.skill_name == "take_profit"]
        for sig in take_profit_signals:
            if sig.signal == SignalType.SELL:
                warnings.append("触发止盈条件，分批止盈落袋为安")

        # 成交量极度萎缩
        if data.avg_volume_20 and data.volume < data.avg_volume_20 * 0.3:
            warnings.append("成交量极度萎缩，可能变盘")

        return warnings

    # ===== 仓位管理（ISS-014 根本解决） =====

    # 策略库仓位规则（第17/53/49章）：
    # - 第17章：初始仓位不超过20%，第一次加仓不超过初始仓位的50%，总仓位上限60%
    # - 第53章：先买两成仓位，跌了有钱补，涨了有底仓
    # - 第49章：分批止盈——分批卖出，移动止盈留底仓

    # 仓位上限表：市场状态 → 最大允许仓位
    POSITION_CAPS = {
        MarketState.RISK_ON: 0.60,     # 牛市：最高60%
        MarketState.TRANSITION: 0.30,   # 分歧：最高30%
        MarketState.RISK_OFF: 0.15,     # 熊市：最高15%（只允许极轻仓）
        MarketState.PANIC: 0.0,         # 恐慌：不允许持仓
    }

    # 初始建仓比例
    OPEN_RATIO = 0.20          # 试探建仓20%（第53章：先买两成）
    ADD_RATIO = 0.20           # 加仓到40%（趋势确认后加倍）

    # 减仓目标：减仓后的目标仓位 = 当前仓位 × 系数
    TAKE_PROFIT_KEEP = 0.60    # 止盈减仓：保留60%底仓（第49章：分批止盈留底仓，卖出1/3~1/2）
    NORMAL_REDUCE_KEEP = 0.65  # 普通减仓：保留65%（趋势走弱，温和减仓，避免减完仓位过低）

    # 低仓位直接清仓阈值
    LOW_POSITION_CLEAR = 0.05  # 仓位<5%时直接清仓（太小无意义，交易成本不划算）

    def _calculate_position(
        self,
        final_signal: SignalType,
        state: MarketState,
        weighted_scores: dict[SignalType, float],
        action_signals: list[SkillSignal],
        current_position_ratio: float = 0.0,
    ) -> tuple[PositionAction, float]:
        """根据最终决策、市场状态和信号强度，计算仓位动作和目标比例

        仓位管理核心规则（来自策略库）：
        - 第17章：初始仓位不超过20%，第一次加仓不超过初始仓位的50%，总仓位上限60%
        - 第53章：先买两成仓位，跌了有钱补，涨了有底仓
        - 第49章：分批止盈——分批卖出，移动止盈留底仓

        Args:
            final_signal: 最终信号
            state: 市场状态
            weighted_scores: 加权分数
            action_signals: 动作信号列表
            current_position_ratio: 当前仓位比例（0-1），用于计算减仓目标

        Returns:
            (PositionAction, target_ratio) - 仓位动作和目标仓位比例
            注意：REDUCE时target_ratio是减仓后的目标仓位（非0.0），
            回测引擎据此计算卖出数量
        """
        buy_score = weighted_scores[SignalType.BUY]
        sell_score = weighted_scores[SignalType.SELL]
        cap = self.POSITION_CAPS.get(state, 0.30)

        # 判断止损/止盈是否触发
        has_stop_loss = any(
            s.skill_name == "stop_loss" and s.signal == SignalType.SELL and s.confidence >= 0.7
            for s in action_signals
        )
        has_take_profit = any(
            s.skill_name == "take_profit" and s.signal == SignalType.SELL and s.confidence >= 0.7
            for s in action_signals
        )

        if final_signal == SignalType.SELL:
            if has_stop_loss:
                # 止损分级处理（v0.5.2）：
                # 深度止损（跌破MA60超2%）：趋势走坏，全部清仓
                # 浅层止损（跌破MA20超1%/死叉）：短期走弱，减仓留底仓
                has_deep_stop = any(
                    s.skill_name == "stop_loss" and s.signal == SignalType.SELL and s.confidence >= 0.85
                    for s in action_signals
                )
                if has_deep_stop:
                    # 深度止损：全部清仓（趋势走坏，避免越套越深）
                    return PositionAction.CLOSE_ALL, 0.0
                elif current_position_ratio is not None and \
                        0 < current_position_ratio < self.LOW_POSITION_CLEAR:
                    # 仓位已经很低（<10%），直接清仓
                    return PositionAction.CLOSE_ALL, 0.0
                elif current_position_ratio is None:
                    # L1（V3）：权重未知——减仓意图保留，不伪造精确目标
                    return PositionAction.REDUCE, None
                else:
                    # 浅层止损：减仓留65%底仓（短期走弱不等于趋势反转，温和减仓）
                    target = current_position_ratio * self.NORMAL_REDUCE_KEEP
                    return PositionAction.REDUCE, target
            elif current_position_ratio is not None and \
                    0 < current_position_ratio < self.LOW_POSITION_CLEAR:
                # 仓位已经很低（<10%），减仓没有意义，直接清仓
                return PositionAction.CLOSE_ALL, 0.0
            elif has_take_profit:
                # 止盈：减仓留50%底仓（分批止盈，第49章）
                if current_position_ratio is None:
                    return PositionAction.REDUCE, None  # L1：权重未知不伪造目标
                target = current_position_ratio * self.TAKE_PROFIT_KEEP
                return PositionAction.REDUCE, target
            else:
                # v0.8.7.8 裁决修复 G05（G区块）：原"强卖出(≥0.4)/弱卖出"两分支行为
                # 完全相同（都用 NORMAL_REDUCE_KEEP=0.65，0.4 是死分档）且注释「留50%」
                # 与常量矛盾。合并为单分支：减仓留 65%（趋势走坏温和减仓）。
                # 若未来想让强卖出真留 50%（TAKE_PROFIT_KEEP），属策略改动需 A/B 立项。
                if current_position_ratio is None:
                    return PositionAction.REDUCE, None  # L1：权重未知不伪造目标
                target = current_position_ratio * self.NORMAL_REDUCE_KEEP
                return PositionAction.REDUCE, target

        elif final_signal == SignalType.BUY:
            if buy_score >= 0.4:
                # 强买入：建仓到较高比例或加仓
                target = min(self.OPEN_RATIO + self.ADD_RATIO, cap)
                # 如果已有持仓，这是加仓信号
                # L1（V3）：None=有仓但权重未知——精确加仓被阻（不假增仓），持仓事实保留
                if current_position_ratio is None:
                    return PositionAction.HOLD_POSITION, 0.0
                if current_position_ratio > 0:
                    return PositionAction.ADD, target
                return PositionAction.OPEN, target
            elif buy_score >= 0.25:
                # 中等买入：试探建仓
                if current_position_ratio is None:
                    return PositionAction.HOLD_POSITION, 0.0  # L1：权重未知阻精确新增
                target = min(self.OPEN_RATIO, cap)
                return PositionAction.OPEN, target
            else:
                # 弱买入：极轻仓
                if current_position_ratio is None:
                    return PositionAction.HOLD_POSITION, 0.0  # L1：权重未知阻精确新增
                target = min(self.OPEN_RATIO * 0.5, cap)
                return PositionAction.OPEN, target

        elif final_signal == SignalType.HOLD:
            # HOLD：维持当前仓位（加仓信号，趋势确认）
            if buy_score > sell_score * 1.5:
                # 偏多：建议加仓
                if current_position_ratio is None:
                    return PositionAction.HOLD_POSITION, 0.0  # L1：权重未知阻精确新增
                target = min(self.OPEN_RATIO + self.ADD_RATIO, cap)
                return PositionAction.ADD, target
            else:
                return PositionAction.HOLD_POSITION, 0.0

        else:  # WATCH
            # 观望：空仓等待
            return PositionAction.STAY_OUT, 0.0
