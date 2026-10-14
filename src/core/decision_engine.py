"""决策引擎 - 聚合技能信号，生成最终决策"""

import logging
import math
from typing import Optional

from src.data.models import (
    StockData, SkillSignal, DecisionResult, DecisionTrace, SignalType, MarketState,
    PositionAction
)

logger = logging.getLogger(__name__)


def _eval_time_qualified(evaluation_as_of, quote_as_of) -> tuple[bool, str]:
    """S2a-X 评估时点资格（base/regulator **共用**，防只修一个入口）。

    Returns: (合格?, 拒因)
    - 评估截止严格 parse_source_time（缺失/非法/垃圾尾巴→不合格，不切前 10 位）
    - 行情时点严格解析（缺失/非法→不合格）；解析成功才用 source_time_in_future
      （坏值返回 False 不能当合格）
    - **截止为日精度 → 按日期比较**（同日无论时刻一律放行——不虚构午夜精度把
      live 当天带时刻的实时行情全拒掉，guard P0 实证；口径与 Q 已验收 exit 门
      「分析日期是日精度，不做盘中时点对比」一致）；截止带时刻→按实际时刻比较
    - 行情晚于截止 → 不合格（未来数据）；同日/更早合法
    """
    from src.core.source_time import parse_source_time, source_time_in_future
    if not quote_as_of:
        return False, "行情时点缺失"
    _qp = parse_source_time(quote_as_of)
    if _qp is None:
        return False, f"行情时点非法({quote_as_of!r})"
    if not evaluation_as_of:
        return False, "评估时点缺失"
    _cp = parse_source_time(evaluation_as_of)
    if _cp is None:
        return False, f"评估时点非法({evaluation_as_of!r})"
    if _cp[1] is False:
        if _qp[0].date() > _cp[0].date():
            return False, f"行情时点({quote_as_of})晚于分析截止({evaluation_as_of})"
    else:
        if source_time_in_future(quote_as_of, _cp[0]):
            return False, f"行情时点({quote_as_of})晚于分析截止({evaluation_as_of})"
    return True, ""


def _eval_market_input(evaluation_as_of, data):
    ok, reason = _eval_time_qualified(evaluation_as_of, getattr(data, "quote_as_of", None))
    if not ok:
        return ok, reason
    price = getattr(data, "price", None)
    if (not isinstance(price, (int, float)) or isinstance(price, bool)
            or not math.isfinite(price) or price <= 0):
        return False, "行情价格缺失/非法→UNKNOWN"
    return True, ""


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

    # S2a 聚合协议版本（DecisionResult.aggregation_protocol）
    PROTOCOL_V1 = "s2a-v1"
    PROTOCOL_NO_ROSTER = "legacy-no-roster"
    PROTOCOL_UNKNOWN_ROSTER = "v1-unknown-roster"

    def make_decision(
        self,
        data: StockData,
        signals: list[SkillSignal],
        state: Optional[MarketState] = None,
        current_position_ratio: float = 0.0,
        expected_base: Optional[dict[str, float]] = None,
        rule_versions: Optional[dict[str, str]] = None,
        evaluation_as_of: Optional[str] = None,
        expected_regulators: Optional[dict[str, float]] = None,
        regulator_rule_versions: Optional[dict[str, str]] = None,
        evidence_resolver=None,
    ) -> DecisionResult:
        """信号聚合（v0.7.2: 不再是最终决策，而是信号聚合结果）

        ISS-117 S2a（v0.8.29.7）：分开**意见占比 P** 与**动作强度 A**——
        一张 1% 把握的买入票不再被按方向和归一化放大成 100%；普通投票的动作
        门/仓位/确认/强卖只消费 A；P 仅展示；C（有效响应覆盖率）独立记录。
        固定分母 E 由调用方从技能注册表预先传入（expected_base），缺失/失败的
        启用槽仍占 W——不以本次成功返回的信号反推配置全集。

        Args:
            expected_base: 启用 base 技能权重表 E（S2_DESIGN 冻结公式 §1）。
                None 或空 = 调用方无法提供注册表 → 显式降级（普通投票不产方向，
                action 覆盖照常），绝不按成功票缩分母。
            rule_versions: 各槽规则版本（skill_engine 内容指纹）。
            evaluation_as_of: **独立分析截止**——backtest=调用方 bar 日期，live=当天；
                不得用待核行情自身时点（data.quote_as_of）自我背书。

        ISS-001: 分层决策 + 信号去重；ISS-007: 决策追溯
        """
        trace: list[DecisionTrace] = []
        slot_notes = []

        if state is None:
            state = StateMachine.determine_state(data)

        trace.append(DecisionTrace(
            step="市场状态判定",
            description=f"当前市场状态: {state.value}",
            data={"state": state.value, "index_trend": data.index_trend}
        ))

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

        # ── S2a：固定分母 + 一槽一票 + A/P/C（冻结公式 §1-§5） ──
        # 坏注册表（权重非正有限或总和为 0）与空注册表同等对待：显式降级，
        # 不受控 ZeroDivision（guard P2）
        if expected_base:
            _bad_roster = any(
                not isinstance(w, (int, float)) or isinstance(w, bool)
                or not math.isfinite(w) or w <= 0
                for w in expected_base.values())
            if _bad_roster or sum(expected_base.values()) <= 0:
                expected_base = None
        data_gaps: list[str] = []
        degraded = not expected_base  # None 或空注册表都视为不可用（显式降级）
        if degraded:
            protocol = (self.PROTOCOL_NO_ROSTER if expected_base is None
                        else self.PROTOCOL_UNKNOWN_ROSTER)
            degraded_reason = ("普通投票不可用：无技能注册表 E（旧直调路径或空注册表）——"
                               "不按成功票缩分母，普通票不产方向；止损/止盈等独立纪律不受影响")
            action_strength = None
            opinion_share = None
            coverage_ratio = None
            weighted_scores = {st: 0.0 for st in SignalType}  # 全零输入：门槛按状态给 HOLD/WATCH（与冻结矩阵「全缺失」一致）
            trace.append(DecisionTrace(
                step="普通投票降级",
                description=degraded_reason,
                data={"protocol": protocol, "expected_base": None},
            ))
        else:
            protocol = self.PROTOCOL_V1
            degraded_reason = ""
            weighted_scores, coverage_ratio, slot_notes = self._aggregate_votes(
                base_signals, expected_base, rule_versions, evaluation_as_of, data, evidence_resolver, diagnostic_gaps=data_gaps)
            trace.append(DecisionTrace(
                step="基础信号投票（s2a-v1）",
                description=f"固定分母 W={sum(expected_base.values()):.2f}，覆盖率 C={coverage_ratio:.2f}",
                data={
                    "raw_U": {k.value: round(v, 4) for k, v in weighted_scores.items()},
                    "coverage": round(coverage_ratio, 4),
                    "slots": slot_notes,
                    "expected_base": {k: round(v, 3) for k, v in expected_base.items()},
                }
            ))

        # 第二步：上下文调节器（S2a：幅度按调节器自身置信度缩放；零/坏置信度不参与）
        regulator_modifiers, regulator_notes = self._apply_regulators(
            data, regulator_signals, weighted_scores,
            expected_regulators=expected_regulators,
            regulator_rule_versions=regulator_rule_versions,
            evaluation_as_of=evaluation_as_of, diagnostic_gaps=data_gaps)
        regulator_details = [f"{k.value}: ×{v:.3f}" for k, v in regulator_modifiers.items()]

        for sig_type, modifier in regulator_modifiers.items():
            weighted_scores[sig_type] *= modifier

        if not degraded:
            # A = clip(M·U/W)；P = M·U / Σ(M·U)（仅展示）。不按方向和归一化。
            W = sum(expected_base.values())
            action_strength = {st: max(0.0, min(1.0, weighted_scores[st] / W))
                               for st in SignalType}
            total_mu = sum(weighted_scores.values())
            opinion_share = ({st: (weighted_scores[st] / total_mu if total_mu > 0 else 0.0)
                             for st in SignalType} if total_mu > 0
                             else {st: 0.0 for st in SignalType})
        else:
            action_strength = None
            opinion_share = None

        trace.append(DecisionTrace(
            step="调节器修正",
            description="regulator修改base得分（S2a：幅度×调节器自身置信度，槽资格核验）",
            data={
                "modifiers": regulator_details,
                "adjusted_U": {k.value: round(v, 4) for k, v in weighted_scores.items()},
                "regulator_notes": regulator_notes,
            }
        ))

        # 第三步：确定最终决策（门槛沿用既有绝对值；输入=动作强度 A）
        pre_action_signal = self._determine_final_signal(
            action_strength if action_strength is not None else weighted_scores, state)

        trace.append(DecisionTrace(
            step="市场状态影响",
            description=f"状态={state.value}, 决策前信号={pre_action_signal.value}",
            data={
                "state": state.value,
                # S2a：final_scores 兼容值 = 动作强度 A（协议版本随行）
                "final_scores": ({k.value: round(v, 4) for k, v in action_strength.items()}
                                 if action_strength is not None
                                 else {k.value: 0.0 for k in SignalType}),
                "final_scores_protocol": self.PROTOCOL_V1 if action_strength is not None else "unavailable",
                "signal_before_action": pre_action_signal.value,
            }
        ))

        # 第四步：动作信号覆盖（止损/止盈优先级）——独立纪律，不受覆盖率影响
        final_signal, action_reason, override_conf, override_signal_id = self._apply_action_signals(
            action_signals, pre_action_signal, data,
            current_position_ratio=current_position_ratio,
        )
        # override_conf = 触发本次覆盖判定的信号置信度（_apply_action_signals 返回；
        # 空仓止损压制 BUY→WATCH / 止盈压制 BUY→HOLD 也如实记录）。**方向未翻转
        # 也保留**（S2a 补修 B 组裁决：「没有翻转方向」不等于「没有独立保护」——
        # 止损一票否决与 base 同向时若清掉来源，确认门会拿弱普通 A 把止损 SELL
        # 拖成 HOLD 延迟一天）。它与普通投票 A 分通道存放，消费端 _gate_score
        # 取两者较大者：强普通票不被较弱 action 拉低，硬止损也不被弱普通票拖延。
        overridden = final_signal != pre_action_signal
        trace.append(DecisionTrace(
            step="动作信号覆盖",
            description=f"止损/止盈优先级检查{'→ 覆盖!' if overridden else '→ 未覆盖'}",
            data={
                "signal_before": pre_action_signal.value,
                "signal_after": final_signal.value,
                "overridden": overridden,
                "action_reason": action_reason,
                "override_confidence": override_conf,
                "override_signal_id": override_signal_id,
            }
        ))

        # 综合评分 = 动作强度 A[final]（S2a：action 覆盖的兜底强度改走
        # action_override_confidence 明确通道，不再反灌普通投票分数——D03 的
        # 下游门已改为读该通道）
        score = (action_strength or weighted_scores).get(final_signal, 0.0)

        reasons = self._generate_reasons(data, base_signals, regulator_signals, action_signals, final_signal)
        if action_reason:
            reasons.insert(0, action_reason)

        warnings = self._generate_warnings(
            data, signals, state, adopted_signal_id=override_signal_id,
            adopted_confidence=override_conf, final_signal=final_signal,
            current_position_ratio=current_position_ratio,
        )
        warnings.extend(n for n in (slot_notes + regulator_notes)
                        if any(tag in n for tag in ("UNKNOWN", "未注册", "非法", "缺", "错", "弃权")))
        if degraded_reason:
            warnings.append(degraded_reason)
            data_gaps.append(degraded_reason)

        position_action, position_ratio = self._calculate_position(
            final_signal, state,
            action_strength if action_strength is not None else weighted_scores,
            action_signals,
            current_position_ratio=current_position_ratio,
            inhibit_risk_increase=(override_signal_id == "take_profit"
                                   and final_signal == SignalType.HOLD),
        )

        return DecisionResult(
            stock=data,
            state=state,
            decision=final_signal,
            score=round(score, 2),
            signals=signals,
            reason=reasons,
            warnings=warnings,
            data_gaps=list(dict.fromkeys(data_gaps)),
            trace=trace,
            position_action=position_action,
            position_ratio=position_ratio,
            # 存原值不舍入（guard P2）：引擎门槛与 strategy 门同源，展示层负责格式化
            action_strength=({st.value: v for st, v in action_strength.items()}
                             if action_strength is not None else None),
            opinion_share=({st.value: v for st, v in opinion_share.items()}
                           if opinion_share is not None else None),
            coverage_ratio=(round(coverage_ratio, 4) if coverage_ratio is not None else None),
            aggregation_protocol=protocol,
            signal_authority_protocol="iss117-s3-v1",
            aggregation_degraded_reason=degraded_reason,
            action_override_confidence=override_conf,
            action_override_signal_id=override_signal_id,
        )

    # ── S2a: 一槽一票聚合（冻结公式 §1-§3、§7 同槽去重） ──

    def _aggregate_votes(
        self,
        base_signals: list[SkillSignal],
        expected_base: dict[str, float],
        rule_versions: Optional[dict[str, str]],
        evaluation_as_of: Optional[str],
        data: StockData,
        evidence_resolver=None,
        *,
        diagnostic_gaps: Optional[list[str]] = None,
    ) -> tuple[dict[SignalType, float], float, list[str]]:
        """按固定分母聚合格票，返回 (U_d, C, 槽诊断)。

        槽 = (skill_id, security, evaluation_as_of, rule_version)。身份绑定规则
        （S2a 补修 A 组）：证券/评估时点/规则版本由**注册上下文**建立——证券可用
        本次分析标的补绑定（本地技能不要求自报）；**评估时点与规则版本缺失不得
        由票自报补齐**（缺 context 不能靠信号自报填成合格）；票自带身份与注册
        不一致（错股/错时点/错版本）→ 该票弃权：不投票不计覆盖，W 保留（同槽其余合格票不受污染）。
        完全相同票合一；同槽冲突/任一 confidence 非有限越界 → 同上。
        不在 E 的槽不参与。c=0 的合格响应计覆盖、贡献 0。
        """
        W = float(sum(expected_base.values()))
        grouped: dict[str, list[tuple[SignalType, float, SkillSignal]]] = {}
        notes: list[str] = []
        def reject(note):
            notes.append(note)
            if diagnostic_gaps is not None:
                diagnostic_gaps.append(note)
        if diagnostic_gaps is not None:
            responding_slots = {sig.skill_name for sig in base_signals}
            for name in expected_base.keys() - responding_slots:
                reject(f"{name}: 注册槽无返回票，资格待核，W保留")
        # S2a-X：评估时点资格（共用 _eval_time_qualified——cutoff 日精度按日期
        # 比较，同日带时刻的实时行情合法；guard P0 修复）
        _ok, _quote_note = _eval_market_input(evaluation_as_of, data)
        if not _ok:
            reject(f"评估时点资格不足：{_quote_note}→全部槽 UNKNOWN")
        for sig in base_signals:
            if sig.skill_name not in expected_base:
                reject(f"{sig.skill_name}: 未注册（不参与投票与分母）")
                continue
            if _quote_note:
                reject(f"{sig.skill_name}: {_quote_note}→槽 UNKNOWN")
                continue
            sec = getattr(data, "stock_code", None)
            reg_rv = (rule_versions or {}).get(sig.skill_name)
            slot_key = (sig.skill_name, sec, evaluation_as_of, reg_rv)
            # 槽资格——上下文缺失/错配一律 UNKNOWN（票自报不能补缺）
            if not sec:
                reject(f"{sig.skill_name}: 缺证券上下文→槽 UNKNOWN")
                continue  # 弃权即不投不计覆盖（独立记录，不污染合法槽组）
            if not evaluation_as_of:
                reject(f"{sig.skill_name}: 缺独立评估时点→槽 UNKNOWN")
                continue  # 弃权即不投不计覆盖（独立记录，不污染合法槽组）
            if not reg_rv or reg_rv == "unavailable":
                reject(f"{sig.skill_name}: 缺注册规则版本→槽 UNKNOWN")
                continue  # 弃权即不投不计覆盖（独立记录，不污染合法槽组）
            if sig.security and sig.security != sec:
                reject(f"{sig.skill_name}: 错股({sig.security})→槽 UNKNOWN")
                continue  # 弃权即不投不计覆盖（独立记录，不污染合法槽组）
            if sig.evaluation_as_of and sig.evaluation_as_of != evaluation_as_of:
                reject(f"{sig.skill_name}: 错时点({sig.evaluation_as_of})→槽 UNKNOWN")
                continue  # 弃权即不投不计覆盖（独立记录，不污染合法槽组）
            if sig.rule_version and sig.rule_version != reg_rv:
                reject(f"{sig.skill_name}: 错版本({sig.rule_version}≠注册{reg_rv})→槽 UNKNOWN")
                continue  # 弃权即不投不计覆盖（独立记录，不污染合法槽组）
            grouped.setdefault(str(slot_key), []).append((sig.signal, sig.confidence, sig))

        U = {st: 0.0 for st in SignalType}
        covered_w = 0.0
        contributions = {}
        for key, rows in grouped.items():
            slot_name = rows[0][2].skill_name
            w = float(expected_base[slot_name])
            # 槽资格：任一票方向未知/置信度非有限/越界，或同槽 rows 不完全一致 → 弃权
            eligible = True
            directions = set()
            for d, c, _sig in rows:
                if d is None or not isinstance(c, (int, float)) or isinstance(c, bool)                         or not math.isfinite(c) or not (0.0 <= c <= 1.0):
                    eligible = False
                directions.add(d)
            if len(rows) > 1 and len({(d, round(float(c), 10)) for d, c, _ in rows}) > 1:
                eligible = False  # 同槽不同票（方向或置信度不一致）→ 冲突
            if len(rows) > 1 and any(row[2].source_kind != rows[0][2].source_kind
                    or row[2].evidence_binding != rows[0][2].evidence_binding for row in rows[1:]):
                eligible = False
            if not eligible or (len(rows) > 1 and len(directions) != 1):
                reject(f"{slot_name}: 槽 UNKNOWN（冲突/非法值）→弃权，W 保留")
                continue
            direction, confidence, _sig = rows[0]
            binding = _sig.evidence_binding
            if _sig.source_kind == "external" or binding is not None:
                required = ("claim_id", "evidence_id", "revision", "security", "as_of")
                resolved = None
                if isinstance(binding, dict) and all(isinstance(binding.get(k), str) and binding[k].strip() for k in required) and callable(evidence_resolver):
                    try:
                        resolved = evidence_resolver(binding["evidence_id"])
                    except Exception as exc:
                        reject(f"{slot_name}: 证据解析失败({type(exc).__name__})")
                if (not isinstance(resolved, dict) or resolved.get("verified") is not True
                        or resolved.get("data_quality") != "OK" or resolved.get("revoked") is True
                        or any(resolved.get(k) != binding[k] for k in required)
                        or binding["security"] != data.stock_code
                        or not _eval_time_qualified(evaluation_as_of, binding["as_of"])[0]):
                    reject(f"{slot_name}: 外部命题无可解析合格证据→UNKNOWN，W保留")
                    continue
                budget_key = tuple(binding[k] for k in required)
            else:
                budget_key = ("technical", slot_name)  # Distinct predicates retain distinct budgets.
            covered_w += w
            contributions.setdefault(budget_key, []).append((direction, confidence, w, slot_name))
            if confidence == 0:
                notes.append(f"{slot_name}: 置信度0（计覆盖、贡献0）")
        for budget_key, votes in contributions.items():
            if budget_key[0] != "technical" and len({v[0] for v in votes}) > 1:
                covered_w -= sum(v[2] for v in votes)
                reject("同一命题/证据修订意见冲突→组UNKNOWN，待回核验")
                continue
            scale = max(v[2] for v in votes) / sum(v[2] for v in votes)
            for direction, confidence, weight, name in votes:
                U[direction] += weight * confidence * scale
                if len(votes) > 1:
                    notes.append(f"{name}: 同源命题预算缩放={scale:g}，同材料不同命题不合票")
        coverage = covered_w / W if W > 0 else 0.0
        return U, coverage, notes

    def _apply_regulators(
        self,
        data: StockData,
        regulator_signals: list[SkillSignal],
        base_scores: dict[SignalType, float],
        expected_regulators: Optional[dict[str, float]] = None,
        regulator_rule_versions: Optional[dict[str, str]] = None,
        evaluation_as_of: Optional[str] = None,
        diagnostic_gaps: Optional[list[str]] = None,
    ) -> tuple[dict[SignalType, float], list[str]]:
        """应用上下文调节器，返回(各信号类型修正系数, 槽诊断)。

        S2a 补修 A 组：regulator 不是免检渠道——槽资格与去重同 base（启用身份/
        规则版本/证券/时点核验、同槽重复合一、冲突/错股/错时点/坏权重弃权并留
        拒因）；零/非有限置信度不参与（不贡献不稀释）。合法有限正权重的既有幅度
        公式与几何平均保留（不另选强度）。未提供注册表（None/空）→ 全部弃权
        （与 base 降级同语义）。调节器不能凭空创造 U_d=0 的方向证据（几何平均
        只乘不改方向基准，U=0 时乘积仍 0）。

        调节器逻辑（幅度沿用）：
        - regulator 输出 SELL → 降低BUY得分，提高SELL得分
        - regulator 输出 BUY → 提高BUY得分，降低SELL得分
        - regulator 输出 WATCH → 轻微降低BUY和SELL得分
        - regulator 输出 HOLD → 不调整
        - 多个regulator叠加时取几何平均（幅度已按各自置信度缩放）
        """
        modifiers = {
            SignalType.BUY: [],
            SignalType.SELL: [],
            SignalType.HOLD: [],
            SignalType.WATCH: [],
        }
        notes: list[str] = []
        def reject(note):
            notes.append(note)
            if diagnostic_gaps is not None:
                diagnostic_gaps.append(note)
        if not expected_regulators:
            if regulator_signals:
                reject("regulator 全部弃权：无注册表（降级）")
            return {st: 1.0 for st in SignalType}, notes

        # S2a-X：与 base 共用同一时点资格（_eval_time_qualified）
        _ok, _quote_note = _eval_market_input(evaluation_as_of, data)
        if not _ok:
            reject(f"regulator 全部弃权：{_quote_note}")
            return {st: 1.0 for st in SignalType}, notes
        grouped: dict[str, list[tuple[SignalType, float, float, SkillSignal]]] = {}
        sec = getattr(data, "stock_code", None)
        for sig in regulator_signals:
            if sig.skill_name not in expected_regulators:
                reject(f"regulator {sig.skill_name}: 未注册→弃权")
                continue
            reg_rv = (regulator_rule_versions or {}).get(sig.skill_name)
            if not sec or not evaluation_as_of:
                reject(f"regulator {sig.skill_name}: 缺证券/时点上下文→弃权")
                continue
            if _quote_note:
                reject(f"regulator {sig.skill_name}: {_quote_note}→弃权")
                continue
            if not reg_rv or reg_rv == "unavailable":
                reject(f"regulator {sig.skill_name}: 缺注册规则版本→弃权")
                continue
            if sig.security and sig.security != sec:
                reject(f"regulator {sig.skill_name}: 错股({sig.security})→弃权，不扩大当前证券资格")
                continue
            if sig.evaluation_as_of and sig.evaluation_as_of != evaluation_as_of:
                reject(f"regulator {sig.skill_name}: 错时点({sig.evaluation_as_of})→弃权")
                continue
            if sig.rule_version and sig.rule_version != reg_rv:
                reject(f"regulator {sig.skill_name}: 错版本({sig.rule_version}≠注册{reg_rv})→弃权")
                continue
            weight = self.weights.get(sig.skill_name, 1.0)
            if (not isinstance(weight, (int, float)) or isinstance(weight, bool)
                    or not math.isfinite(weight) or weight <= 0):
                # 负/NaN 权重不得翻转调节方向或被 clip 洗成合法——弃权留拒因
                reject(f"regulator {sig.skill_name}: 非法权重({weight!r})→弃权")
                continue
            c = sig.confidence
            if not isinstance(c, (int, float)) or isinstance(c, bool) \
                    or not math.isfinite(c) or c <= 0:
                notes.append(f"regulator {sig.skill_name}: 零/坏置信度→不参与（不贡献不稀释）")
                if diagnostic_gaps is not None and c != 0:
                    diagnostic_gaps.append(f"regulator {sig.skill_name}: 非法置信度，资格待核")
                continue
            slot = str((sig.skill_name, sec, evaluation_as_of, sig.rule_version or reg_rv))
            grouped.setdefault(slot, []).append((sig.signal, c, weight, sig))

        # 逐槽：完全相同票合一；同槽冲突（方向或置信度不一致）弃权
        qualified = []
        for key, rows in grouped.items():
            name = rows[0][3].skill_name
            if len(rows) > 1 and len({(d, round(float(c), 10)) for d, c, _w, _s in rows}) > 1:
                reject(f"regulator {name}: 同槽冲突→弃权")
                continue
            qualified.append(rows[0])

        for sig_signal, c, weight, sig in qualified:
            # 权重越高，调节力度越大（既有幅度与上限沿用，不另选强度）
            impact = 0.5 + (weight - 1.0) * 0.2  # 基础0.5，权重1.5时impact=0.6
            impact = min(impact, 0.8)  # 最大0.8
            # S2a（冻结公式 §4）：m_i = 1 + c_i*(m_legacy - 1)——幅度按调节器
            # 自身置信度缩放（c 已过资格核验）
            if sig_signal == SignalType.SELL:
                modifiers[SignalType.BUY].append(1.0 - c * impact)    # 降低BUY
                modifiers[SignalType.SELL].append(1.0 + c * impact)   # 提高SELL
            elif sig_signal == SignalType.BUY:
                modifiers[SignalType.BUY].append(1.0 + c * impact)    # 提高BUY
                modifiers[SignalType.SELL].append(1.0 - c * impact)   # 降低SELL
            elif sig_signal == SignalType.WATCH:
                modifiers[SignalType.BUY].append(1.0 - c * impact * 0.5)  # 轻微降低BUY
                modifiers[SignalType.SELL].append(1.0 - c * impact * 0.5) # 轻微降低SELL
            # HOLD 不调整

        # 计算最终修正系数（取几何平均）
        result = {}
        for sig_type in [SignalType.BUY, SignalType.SELL, SignalType.HOLD, SignalType.WATCH]:
            mods = modifiers[sig_type]
            if mods:
                product = 1.0
                for m in mods:
                    product *= m
                result[sig_type] = product ** (1.0 / len(mods))
            else:
                result[sig_type] = 1.0

        return result, notes



    def _apply_action_signals(
        self,
        action_signals: list[SkillSignal],
        current_decision: SignalType,
        data: StockData,
        current_position_ratio: float = 0.0,
    ) -> tuple[SignalType, Optional[str], Optional[float], Optional[str]]:
        """应用动作信号（止损/止盈），返回(最终决策, 覆盖原因, 触发信票置信度, 选中技能身份)

        层级逻辑（ISS-016 方案A：止损仓位感知）：
        - 有仓位时：止损SELL confidence≥0.8 → 直接覆盖为SELL（一票否决）
        - 空仓时：止损SELL → 降级为风险提示，压制BUY为WATCH，不否决HOLD
        - 止盈SELL confidence≥0.75 → 压制BUY为HOLD，确认HOLD为SELL
        - 止损HOLD → 不覆盖

        第三元 = 触发本次覆盖判定的信号置信度（S2a：明确来源通道，
        strategy._gate_score 优先消费；未触发覆盖时 None）。
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
                    return (SignalType.SELL,
                            f"⚠️ 止损信号覆盖：{', '.join(sig.reason[:2])}",
                            sig.confidence, sig.skill_name)
                else:
                    # 空仓：止损只是风险提示，压制BUY为WATCH（观望而非卖出）
                    if current_decision == SignalType.BUY:
                        return (SignalType.WATCH,
                                f"⚠️ 止损风险提示（空仓观望）：{', '.join(sig.reason[:2])}",
                                sig.confidence, sig.skill_name)
                    # 空仓+HOLD/SELL/WATCH → 不变，止损不放大空仓的卖出信号
                    return current_decision, None, None, None

        # 止盈次之
        for sig in take_profit_signals:
            if sig.signal == SignalType.SELL and sig.confidence >= 0.75:
                if current_decision == SignalType.BUY:
                    return (SignalType.HOLD,
                            f"止盈信号压制买入：{', '.join(sig.reason[:2])}",
                            sig.confidence, sig.skill_name)
                elif current_decision == SignalType.HOLD:
                    return (SignalType.SELL,
                            f"止盈信号确认卖出：{', '.join(sig.reason[:2])}",
                            sig.confidence, sig.skill_name)

        return current_decision, None, None, None

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

    @staticmethod
    def buy_gate(a_buy: float, a_sell: float, state: MarketState) -> bool:
        """同市场状态的既有 BUY 判据（S2a 冻结：新增 OPEN/ADD 的唯一资格门）。

        从 _determine_final_signal 的 BUY 分支抽出为唯一实现——HOLD 不得靠
        相对票分优势（BUY>1.5*SELL）绕过此门获得 OPEN/ADD；阈值沿用：
        强 .4、弱 .25（RISK_ON 弱买需 >SELL），PANIC 无 BUY 门。
        """
        if state == MarketState.RISK_ON:
            return a_buy >= 0.4 or (a_buy >= 0.25 and a_buy > a_sell)
        if state in (MarketState.TRANSITION, MarketState.RISK_OFF):
            return a_buy >= 0.4 and a_buy > a_sell * 1.5
        return False  # PANIC：无 BUY 门（现状只产 SELL/WATCH）

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
        S2a（v0.8.29.7）：输入语义 = 动作强度 A（绝对值，不按方向和归一化）；
        阈值与分支优先级沿用。BUY 判据统一走 buy_gate（与 Strategy 的
        OPEN/ADD 资格门同一实现）。
        """

        buy_score = weighted_scores[SignalType.BUY]
        sell_score = weighted_scores[SignalType.SELL]
        hold_score = weighted_scores[SignalType.HOLD]

        # SELL分层门槛（方案C核心）
        sell_threshold = self.SELL_THRESHOLDS.get(state, 0.30)

        # 市场状态影响决策
        if state == MarketState.PANIC:
            # PANIC状态：SELL门槛最低（0.20），但仍然需要sell>hold
            if sell_score >= sell_threshold and sell_score > hold_score:
                return SignalType.SELL
            return SignalType.WATCH

        if self.buy_gate(buy_score, sell_score, state):
            return SignalType.BUY

        if state == MarketState.RISK_ON:
            # 牛市：SELL偏向保守
            if sell_score >= sell_threshold and sell_score > buy_score * self.STRONG_SELL_RATIO:
                # 牛市SELL：需要更高门槛(0.35) + 明显强于BUY(1.2倍)
                return SignalType.SELL
            elif sell_score >= sell_threshold and sell_score > buy_score:
                # SELL达标但不够强 → 降为WATCH（牛市不轻易卖）
                return SignalType.WATCH
            else:
                return SignalType.HOLD

        elif state == MarketState.TRANSITION:
            # 过渡状态：多空分歧，买卖都需要强信号
            if sell_score >= sell_threshold and sell_score > buy_score * 1.5:
                # 震荡市SELL：门槛0.30，需1.5倍优势（原逻辑保留）
                return SignalType.SELL
            else:
                return SignalType.WATCH

        else:  # RISK_OFF
            # 熊市：SELL偏向灵敏（门槛0.25），只需sell>buy（灵敏卖出）
            #（BUY 判据已在上方 buy_gate 统一处理；两条件数学互斥，顺序无关）
            if sell_score >= sell_threshold and sell_score > buy_score:
                return SignalType.SELL
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
                reasons.extend(sig.reason)

        # 基础信号理由
        for sig in base_signals:
            if sig.signal == final_signal and sig.reason:
                reasons.extend(sig.reason)

        # 调节器理由（标注为上下文）
        for sig in regulator_signals:
            if sig.reason:
                for r in sig.reason:
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

        return unique_reasons

    def _generate_warnings(
        self,
        data: StockData,
        signals: list[SkillSignal],
        state: MarketState,
        *,
        adopted_signal_id: Optional[str] = None,
        adopted_confidence: Optional[float] = None,
        final_signal: Optional[SignalType] = None,
        current_position_ratio: Optional[float] = 0.0,
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
                adopted = (sig.skill_type == "action" and adopted_signal_id == "stop_loss"
                           and adopted_confidence == sig.confidence and sig.confidence >= .8)
                if adopted and final_signal == SignalType.SELL and (current_position_ratio is None or current_position_ratio > 0):
                    warnings.append("已采用止损退出保护，止损只上移不下移，别让希望绑架决策")
                elif adopted and final_signal == SignalType.WATCH:
                    warnings.append("已采用止损风险保护：空仓抑制新增，保持观望")
                else:
                    warnings.append("止损技能线索：未采纳为独立退出保护，请核对技能类型、置信度和适用持仓")

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
                adopted = (sig.skill_type == "action" and adopted_signal_id == "take_profit"
                           and adopted_confidence == sig.confidence and sig.confidence >= .75)
                if adopted and final_signal == SignalType.HOLD:
                    warnings.append("已采用止盈保护：抑制新增，不据此清仓")
                elif adopted and final_signal == SignalType.SELL and (current_position_ratio is None or current_position_ratio > 0):
                    warnings.append("已采用止盈退出保护，具体减仓动作由策略层核定")
                else:
                    warnings.append("止盈技能线索：未采纳为独立退出保护，请核对技能类型、置信度和适用持仓")

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
        *,
        inhibit_risk_increase: bool = False,
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
                return (PositionAction.ADD if current_position_ratio is not None and current_position_ratio > 0
                        else PositionAction.OPEN), target
            elif buy_score >= 0.25:
                # 中等买入：试探建仓
                if current_position_ratio is None:
                    return PositionAction.HOLD_POSITION, 0.0  # L1：权重未知阻精确新增
                target = min(self.OPEN_RATIO, cap)
                return (PositionAction.ADD if current_position_ratio is not None and current_position_ratio > 0
                        else PositionAction.OPEN), target
            else:
                # 弱买入：极轻仓
                if current_position_ratio is None:
                    return PositionAction.HOLD_POSITION, 0.0  # L1：权重未知阻精确新增
                target = min(self.OPEN_RATIO * 0.5, cap)
                return (PositionAction.ADD if current_position_ratio is not None and current_position_ratio > 0
                        else PositionAction.OPEN), target

        elif final_signal == SignalType.HOLD:
            if inhibit_risk_increase:
                return PositionAction.HOLD_POSITION, current_position_ratio
            # HOLD：维持当前仓位。S2a：相对优势（buy>sell*1.5）不再足以 ADD——
            # 新增须先过同状态既有 BUY 判据（buy_gate），实际仓位不增加时保持 HOLD
            if self.buy_gate(buy_score, sell_score, state):
                # 偏多且过门：建议加仓
                if current_position_ratio is None:
                    return PositionAction.HOLD_POSITION, 0.0  # L1：权重未知阻精确新增
                target = min(self.OPEN_RATIO + self.ADD_RATIO, cap)
                return PositionAction.ADD, target
            else:
                return PositionAction.HOLD_POSITION, 0.0

        else:  # WATCH
            # 观望：空仓等待
            return PositionAction.STAY_OUT, 0.0
