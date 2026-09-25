"""排名层（Ranking Layer） — v0.8.0 Phase 4

在深度分析之后、CLI展示之前，对候选股按四维评分排序。

架构位置：
  Scanner.quick_scan() → Scanner.deep_analyze() → RankingLayer.rank() → CLI展示

四维评分：
  技术面(40%) + AI情绪(20%) + 流动性(20%) + 波动性(20%)
  输出：0-100分综合评分

权重调整：
  AI禁用时 sentiment 权重归零，剩余三维按比例重分配
"""

import math
import logging
from typing import Optional
from copy import deepcopy

from src.data.models import (
    ScanCandidate,
    DecisionResult,
    AIModifierResult,
    DimensionScore,
    RankingResult,
)

logger = logging.getLogger(__name__)


class RankingLayer:
    """排名计算引擎

    对 Scanner.deep_analyze() 的结果列表执行四维评分和排名，
    每个结果 dict 增加 "ranking" 字段（RankingResult），并按综合分降序排序。
    """

    DEFAULT_CONFIG = {
        "enabled": True,
        "weights": {
            "technical": 0.40,
            "sentiment": 0.20,
            "liquidity": 0.20,
            "volatility": 0.20,
        },
        "liquidity": {
            "amount_weight": 0.60,
            "turnover_weight": 0.40,
        },
        "volatility": {
            "optimal_range": [2.0, 5.0],
        },
        "display": {
            "top_n": 10,
            "show_dimensions": True,
        },
    }

    def __init__(self, config: Optional[dict] = None):
        self.config = self._merge_config(config or {})
        self.weights = self.config["weights"]
        self.liquidity_config = self.config.get("liquidity", {})
        self.volatility_config = self.config.get("volatility", {})
        self.display_config = self.config.get("display", {})

    def rank(
        self,
        results: list[dict],
        ai_enabled: bool = True,
    ) -> list[dict]:
        """对深度分析结果执行排名

        Args:
            results: deep_analyze() 的返回列表，每项为 dict
            ai_enabled: 是否启用AI（影响 sentiment 维度权重）

        Returns:
            按排名排序后的 results，每项增加 "ranking" 字段（RankingResult）
        """
        # 1. 归一化权重
        effective_weights = self._normalize_weights(self.weights, ai_enabled)

        # 2. 逐只计算评分
        for r in results:
            if not r.get("success"):
                r["ranking"] = RankingResult(
                    stock_code=r["stock_code"],
                    stock_name=r.get("stock_name", ""),
                    total_score=0.0,
                    rank=999,
                    decision="FAIL",
                    ai_enabled=ai_enabled,
                )
                continue

            candidate = r.get("candidate")
            dr = r.get("decision_result")
            ai_r = r.get("ai_result")

            # 计算各维度评分
            tech_score, tech_detail = self._calc_technical_score(dr)
            sent_score, sent_detail = self._calc_sentiment_score(ai_r)
            liq_score, liq_detail = self._calc_liquidity_score(candidate)
            vol_score, vol_detail = self._calc_volatility_score(candidate)

            # v0.8.7.6 审查裁决 B19：AI 失败的个股"信息缺失不参与 AI 分项"——
            # 该股 sentiment 权重归零并按比例重分配到其余维度（复用 AI 禁用口径），
            # 不再伪造中性 50 分（原实现里 AI 失败股 50×0.2=10 分反超 AI 判空股 ~1 分）。
            ai_ok = ai_r is not None and getattr(ai_r, "adjusted", False)
            stock_weights = (
                effective_weights if ai_ok
                else self._normalize_weights(self.weights, ai_enabled=False)
            )

            dimensions = []
            for dim_name, score, detail in [
                ("technical", tech_score, tech_detail),
                ("sentiment", sent_score, sent_detail),
                ("liquidity", liq_score, liq_detail),
                ("volatility", vol_score, vol_detail),
            ]:
                weight = stock_weights.get(dim_name, 0)
                contribution = score * weight
                dimensions.append(DimensionScore(
                    name=dim_name,
                    score=round(score, 1),
                    weight=round(weight, 4),
                    contribution=round(contribution, 2),
                    detail=detail,
                ))

            total = sum(d.contribution for d in dimensions)
            decision = dr.decision.value if dr else "?"

            r["ranking"] = RankingResult(
                stock_code=r["stock_code"],
                stock_name=r.get("stock_name", ""),
                total_score=round(total, 1),
                rank=0,  # 排序后填充
                dimensions=dimensions,
                decision=decision,
                ai_enabled=ai_enabled,
            )

        # 3. 排序并填充排名（同分并列）
        success_results = [r for r in results if r.get("success")]
        success_results.sort(key=lambda r: r["ranking"].total_score, reverse=True)

        # 并列排名：相同分数的股票获得相同名次
        if success_results:
            success_results[0]["ranking"].rank = 1
            for i in range(1, len(success_results)):
                prev_score = success_results[i - 1]["ranking"].total_score
                curr_score = success_results[i]["ranking"].total_score
                if curr_score == prev_score:
                    # 同分并列，与上一名相同排名
                    success_results[i]["ranking"].rank = success_results[i - 1]["ranking"].rank
                else:
                    # 不同分，排名=在列表中的位置+1（跳过并列占位）
                    success_results[i]["ranking"].rank = i + 1

        # 4. 重组 results（成功的按排名排前面，失败的排后面）
        ranked = success_results + [r for r in results if not r.get("success")]

        ranked_count = len(success_results)
        if ranked_count > 0:
            top = success_results[0]["ranking"]
            logger.info(
                f"RankingLayer: {ranked_count}只排名完成, "
                f"TOP1={top.stock_code}({top.total_score}分)"
            )

        return ranked

    # =====================================================================
    # 维度评分方法
    # =====================================================================

    def _calc_technical_score(
        self, dr: Optional[DecisionResult]
    ) -> tuple[float, str]:
        """技术面评分

        直接映射 DecisionResult.score (0-1) → 0-100。
        score 已经是 base信号投票 + regulator修正 + AI/事件调节后的综合分数。

        F2（plan/fusion TASKS F2 验收"SELL强度不再被当买入吸引力"）：排名层只服务
        scan 买入候选榜——SELL 决策的 score 是卖出强度（探针 D：SELL .9→90 分被当
        买入排序依据的病根），记 0 分不入排序，原始强度留 detail 供诊断。

        Returns:
            (score, detail)
        """
        if dr is None:
            return 50.0, "无决策数据"

        # F2：SELL 强度不是买入吸引力
        if dr.decision.value == "SELL":
            return 0.0, f"SELL(score={dr.score:.2f})——卖出强度不计入买入技术分"

        score = self._clip(dr.score * 100, 0, 100)
        decision = dr.decision.value
        detail = f"{decision}(score={dr.score:.2f})"
        return score, detail

    def _calc_sentiment_score(
        self, ai_r: Optional[AIModifierResult]
    ) -> tuple[float, str]:
        """AI情绪评分

        以50分为中性基准，看多向100偏移，看空向0偏移。
        偏移幅度由置信度驱动：置信度越高，偏离中性越远。
        风险等级会压低分数（对看多信号施加折扣）。

        公式：score = 50 + sentiment_direction × confidence × 50 + risk_penalty
        - bullish: direction = +1, score ∈ [50, 100]（减去风险惩罚后最低25）
        - neutral:  direction =  0, score = 50 附近
        - bearish:  direction = -1, score ∈ [0, 50]（加上风险惩罚后最高75）

        无AI数据时返回50分（中性）——v0.8.7.6 B19：该分项权重同时归零重分配，
        50 分仅作展示占位，不再计入加权总分。

        Returns:
            (score, detail)
        """
        if ai_r is None or not ai_r.adjusted:
            return 50.0, "无AI数据(分项不计入,权重已重分配)"

        # 情绪方向映射
        direction = {
            "bullish": 1,
            "neutral": 0,
            "bearish": -1,
        }.get(ai_r.sentiment, 0)

        # 核心公式：50分基准 + 方向×置信度×50分偏移
        # 置信度0→50分, 置信度1→看多100/看空0
        score = 50 + direction * ai_r.confidence * 50

        # neutral但adjusted=True时，轻微偏移（基于score_adjustment）
        if ai_r.sentiment == "neutral" and ai_r.score_adjustment != 0:
            score += ai_r.score_adjustment * 25  # 微调，最多±12.5分

        # 风险等级惩罚：仅对看多/中性信号生效（看空信号风险已反映在方向中）
        risk_penalty = 0
        if direction >= 0:  # bullish 或 neutral
            risk_penalty = {"low": 0, "medium": -10, "high": -25}.get(
                ai_r.risk_level, 0
            )
        else:  # bearish
            # 看空+高风险：风险确认了判断方向，轻微加分
            risk_penalty = {"low": 0, "medium": -5, "high": -5}.get(
                ai_r.risk_level, 0
            )
        score += risk_penalty

        score = self._clip(score, 0, 100)

        # 可解释性
        sentiment_cn = {"bullish": "看多", "neutral": "中性", "bearish": "看空"}.get(
            ai_r.sentiment, "?"
        )
        risk_cn = {"low": "", "medium": "中风险", "high": "高风险"}.get(
            ai_r.risk_level, ""
        )
        detail_parts = [f"{sentiment_cn}({ai_r.confidence:.0%})"]
        if risk_cn:
            detail_parts.append(risk_cn)
        detail = "+".join(detail_parts)

        return score, detail

    def _calc_liquidity_score(
        self, candidate: Optional[ScanCandidate]
    ) -> tuple[float, str]:
        """流动性评分

        双因子加权：成交额(对数映射) 60% + 换手率(倒U形) 40%
        无 ScanCandidate 数据时返回50分。

        成交额对数映射（amount单位为元）：
          1亿(1e8)→20分, 3亿→39分, 10亿→60分, 50亿→88分, 100亿→100分

        换手率倒U形：
          <1%→0-30分, 1-3%→30-60分, 3-7%→60-90分(最优), 7-15%→90-60分, >15%→60-0分

        Returns:
            (score, detail)
        """
        if candidate is None:
            return 50.0, "无候选数据"

        amount = candidate.amount
        turnover = candidate.turnover_rate

        # 成交额评分
        if amount and amount > 0:
            log_val = math.log10(amount)
            # log10范围: 7.5(约3千万) ~ 10(100亿) → 映射到 0~100
            amount_score = self._clip(
                (log_val - 7.5) / (10 - 7.5) * 100, 0, 100
            )
        else:
            amount_score = 50.0

        # 换手率评分（倒U形）
        if turnover is not None and turnover > 0:
            if turnover < 1:
                turnover_score = turnover * 30
            elif turnover <= 3:
                turnover_score = 30 + (turnover - 1) / 2 * 30
            elif turnover <= 7:
                turnover_score = 60 + (turnover - 3) / 4 * 30
            elif turnover <= 15:
                turnover_score = 90 - (turnover - 7) / 8 * 30
            else:
                turnover_score = max(0, 60 - (turnover - 15) * 2)
        else:
            turnover_score = 50.0

        aw = self.liquidity_config.get("amount_weight", 0.6)
        tw = self.liquidity_config.get("turnover_weight", 0.4)
        score = self._clip(amount_score * aw + turnover_score * tw, 0, 100)

        # 可解释性
        amount_yi = f"{amount / 1e8:.1f}亿" if amount and amount > 0 else "?"
        detail = f"额{amount_yi}({amount_score:.0f})×{aw:.0%}+换{turnover or 0:.1f}%({turnover_score:.0f})×{tw:.0%}"
        return score, detail

    def _calc_volatility_score(
        self, candidate: Optional[ScanCandidate]
    ) -> tuple[float, str]:
        """波动性评分

        倒U形曲线：适度波动最好（有交易机会但风险可控），
        过低（僵尸股）和过高（投机）都扣分。

        基于振幅(amplitude%)：
          <1%→0-30分(僵尸), 1-3%→30-70分, 3-5%→70-100分(最优),
          5-8%→100-60分, >8%→60-0分(投机)

        无数据时返回50分。

        Returns:
            (score, detail)
        """
        if candidate is None:
            return 50.0, "无候选数据"

        amplitude = candidate.amplitude

        if amplitude is None:
            return 50.0, "无振幅数据"

        # 振幅评分（倒U形）
        if amplitude < 1:
            score = amplitude * 30
        elif amplitude <= 3:
            score = 30 + (amplitude - 1) / 2 * 40
        elif amplitude <= 5:
            score = 70 + (amplitude - 3) / 2 * 30
        elif amplitude <= 8:
            score = 100 - (amplitude - 5) / 3 * 40
        else:
            score = max(0, 60 - (amplitude - 8) * 8)

        score = self._clip(score, 0, 100)

        # 可解释性
        if amplitude < 1:
            vol_label = "低波动"
        elif amplitude <= 5:
            vol_label = "适度"
        elif amplitude <= 8:
            vol_label = "偏高"
        else:
            vol_label = "高波动"
        detail = f"振幅{amplitude:.1f}%({vol_label})"

        return score, detail

    # =====================================================================
    # 工具方法
    # =====================================================================

    @staticmethod
    def _normalize_weights(weights: dict, ai_enabled: bool) -> dict:
        """归一化权重，AI禁用时 sentiment 权重归零并按比例重分配

        Args:
            weights: 原始权重字典
            ai_enabled: AI是否启用

        Returns:
            归一化后的权重字典（总和=1.0）
        """
        w = weights.copy()

        if not ai_enabled:
            w["sentiment"] = 0.0

        # 按比例归一化
        total = sum(w.values())
        if total > 0:
            w = {k: round(v / total, 4) for k, v in w.items()}

        return w

    @staticmethod
    def _merge_config(user_config: dict) -> dict:
        """合并用户配置与默认配置（浅层合并）"""
        result = deepcopy(RankingLayer.DEFAULT_CONFIG)
        for key, value in user_config.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key].update(value)
            else:
                result[key] = value
        return result

    @staticmethod
    def _clip(value: float, min_val: float, max_val: float) -> float:
        """裁剪到[min_val, max_val]范围"""
        return max(min_val, min(max_val, value))
