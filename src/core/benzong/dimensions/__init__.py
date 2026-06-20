"""维度评分器统一接口（v0.8.6.2）

每个维度评分器（dimensions/*.py）应提供 score() 函数，签名：
    def score(code: str, name: str, *, data_summary: dict,
              ai_client=None, rag_service=None) -> dict

返回 dict 必须含：
    score: 0-100
    confidence: 0-1
    sources: list[str]    # 引用的数据源
    reasoning: str         # 评分依据（人话）
    data_freshness: str    # 数据时效，如 "2026-06-20" 或 "缓存 1 天"
    warnings: list[str]    # 数据缺失等警告

约定：
- 数据缺失 → confidence=0，score=50（中性）+ warning
- AI 异常 → confidence=0，score=50 + warning（不假装算出来）
- 红线（如风险维度的"叛国/违禁"）→ score=100 (扣分)，invalidate=True
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def _missing_data_result(reason: str, dim_name: str = "") -> dict:
    """生成"数据缺失"标准返回"""
    return {
        "score": 50,
        "confidence": 0.0,
        "sources": [],
        "reasoning": f"{reason}",
        "data_freshness": "N/A",
        "warnings": [f"{dim_name} 数据缺失，评分不可信" if dim_name else "数据缺失"],
    }


def _ai_failed_result(reason: str, dim_name: str = "") -> dict:
    """生成"AI 调用失败"标准返回"""
    return {
        "score": 50,
        "confidence": 0.0,
        "sources": [],
        "reasoning": f"AI 调用失败: {reason}",
        "data_freshness": "N/A",
        "warnings": [f"{dim_name} AI 调用失败，评分不可信" if dim_name else "AI 调用失败"],
    }


def _call_ai_for_score(ai_client, system_prompt: str, user_prompt: str,
                       dim_name: str, model: Optional[str] = None) -> Optional[dict]:
    """共用 AI 调用 helper：发 prompt → 拿 JSON → 解析 score/confidence/reasoning。

    Returns:
        dict {score, confidence, reasoning} 或 None（调用/解析失败）
    """
    if ai_client is None:
        logger.warning(f"{dim_name}: ai_client 未提供，跳过 AI 调用")
        return None

    try:
        # AI 调用（参考 ai_modifier.py:278 风格）
        api_params = {
            "model": model or "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,  # 评分类任务低温
            "max_tokens": 800,
        }
        response = ai_client.chat.completions.create(**api_params)
        text = response.choices[0].message.content or ""

        # 解析 JSON（容错）
        json_match = re.search(r'\{[^{}]*"score"[^{}]*\}', text, re.DOTALL)
        if not json_match:
            # 退化：尝试整个 text
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if not json_match:
            logger.warning(f"{dim_name}: AI 返回不含 JSON: {text[:100]}")
            return None
        result = json.loads(json_match.group(0))

        # 校验关键字段
        if "score" not in result:
            logger.warning(f"{dim_name}: AI JSON 缺 score 字段: {result}")
            return None

        score_val = float(result["score"])
        score_val = max(0.0, min(100.0, score_val))  # 钳制 0-100

        return {
            "score": score_val,
            "confidence": float(result.get("confidence", 0.7)),
            "reasoning": str(result.get("reasoning", ""))[:300],
            "raw_ai_response": text[:500],
        }
    except Exception as e:
        logger.error(f"{dim_name}: AI 调用/解析失败: {type(e).__name__}: {str(e)[:100]}")
        return None


# 维度懒加载（避免某个文件未建好时 __init__ 整体崩）
def _lazy_load_dimensions():
    from src.core.benzhong.dimensions.valuation_position import score as vp_score
    from src.core.benzhong.dimensions.business_purity import score as bp_score
    from src.core.benzhong.dimensions.industry_prosperity import score as ip_score
    from src.core.benzhong.dimensions.industry_leader import score as il_score
    from src.core.benzhong.dimensions.market_recognition import score as mr_score
    from src.core.benzhong.dimensions.risk_deduction import score as rd_score
    return {
        "industry_prosperity": ip_score,
        "business_purity": bp_score,
        "valuation_position": vp_score,
        "industry_leader": il_score,
        "market_recognition": mr_score,
        "risk_deduction": rd_score,
    }


__all__ = [
    "_missing_data_result",
    "_ai_failed_result",
    "_call_ai_for_score",
    "_lazy_load_dimensions",
]
