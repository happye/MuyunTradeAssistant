"""维度评分器统一接口（v0.8.6.2）

每个维度评分器（dimensions/*.py）应提供 score() 函数，签名：
    def score(code: str, name: str, *, data_summary: dict,
              ai_client=None, ai_model: Optional[str] = None,
              rag_service=None) -> dict

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
                       dim_name: str, model: Optional[str] = None,
                       max_tokens: int = 1500) -> Optional[dict]:
    """共用 AI 调用 helper：发 prompt → 拿 JSON → 解析 score/confidence/reasoning。

    v0.8.6.3 修复：max_tokens 800→1500（防 reasoning 超长截断致 JSONDecodeError）+
    finish_reason==length 时自动重试（要求 reasoning 精简）+ JSON 解析容错截断。
    v0.8.6.4：max_tokens 改为参数，业务纯度等长 reasoning 维度可提到 2000。

    Returns:
        dict {score, confidence, reasoning} 或 None（调用/解析失败）
    """
    if ai_client is None:
        logger.warning(f"{dim_name}: ai_client 未提供，跳过 AI 调用")
        return None

    if model is None:
        logger.warning(
            f"{dim_name}: ai_model 未透传，回退默认 deepseek-chat "
            f"（kimi/moonshot provider 下会 404，请检查 auto_scorer ai_model 透传链路）"
        )

    # 最多重试 2 次：第1次正常，第2次因截断重试时要求 reasoning 精简
    for attempt in range(2):
        try:
            sys_p = system_prompt
            user_p = user_prompt
            if attempt == 1:
                # 重试：要求极简 reasoning 避免再截断
                sys_p = system_prompt + "\n\n注意：reasoning 字段必须 ≤80字，避免输出被截断。"
            response = ai_client.chat.completions.create(
                model=model or "deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": sys_p},
                    {"role": "user", "content": user_p},
                ],
                temperature=0.2,  # 评分类任务低温
                max_tokens=max_tokens,
                **({"extra_body": {"thinking": {"type": "disabled"}}} if str(model or "deepseek-v4-flash").startswith("deepseek") else {}),
            )
            text = response.choices[0].message.content or ""
            finish_reason = getattr(response.choices[0], "finish_reason", None)

            result = _parse_score_json(text, dim_name)
            if result is not None:
                return result

            # 解析失败：若是 length 截断导致，重试（attempt==0 时进重试）
            if finish_reason == "length" and attempt == 0:
                logger.warning(f"{dim_name}: AI 输出被截断(finish_reason=length)，重试要求精简 reasoning")
                continue
            # 非截断的解析失败，或已重试过仍失败
            logger.warning(f"{dim_name}: AI 返回 JSON 解析失败: {text[:120]}")
            return None
        except Exception as e:
            logger.error(f"{dim_name}: AI 调用/解析失败: {type(e).__name__}: {str(e)[:100]}")
            return None
    return None


def _parse_score_json(text: str, dim_name: str) -> Optional[dict]:
    """从 AI 文本解析 {score,confidence,reasoning} JSON。容错 markdown/截断。

    v0.8.6.3：用 raw_decode 从首个 { 起解析，容错尾部截断（解析到哪算哪，
    只要 score 字段在已解析部分就算成功）。
    """
    if not text:
        return None
    # 去 markdown 代码块
    cleaned = text.strip()
    if "```" in cleaned:
        m = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(1).strip()

    # 找第一个 { 起始
    start = cleaned.find("{")
    if start < 0:
        return None

    # 先尝试标准 json.loads（完整 JSON）
    try:
        data = json.loads(cleaned[start:])
        return _validate_score_dict(data)
    except json.JSONDecodeError:
        pass

    # 截断容错：用 raw_decode 逐步解析，遇到不完整则回退到能解析的最小完整前缀
    decoder = json.JSONDecoder()
    idx = start
    best = None
    while idx < len(cleaned):
        try:
            obj, end = decoder.raw_decode(cleaned, idx)
            if isinstance(obj, dict) and "score" in obj:
                best = obj  # 记住最后一个含 score 的完整对象
                idx = end
            else:
                idx += 1
        except json.JSONDecodeError:
            idx += 1
    if best is not None:
        return _validate_score_dict(best)

    # 最后退化：正则提取 score 数字（截断到只剩 score:80 片段时）。
    # confidence 降到 0.3——截断残片只能信 score 大致值，confidence/reasoning 都丢了。
    m = re.search(r'"score"\s*:\s*([0-9.]+)', cleaned)
    if m:
        logger.warning(f"{dim_name}: JSON 严重截断，仅提取 score={m.group(1)}（confidence/reasoning 丢失，conf 置 0.3）")
        return _validate_score_dict({"score": float(m.group(1)), "confidence": 0.3, "reasoning": "(AI 输出截断，仅提取到 score)"})
    return None


def _validate_score_dict(data: dict) -> Optional[dict]:
    """校验并标准化 score dict。"""
    if not isinstance(data, dict) or "score" not in data:
        return None
    try:
        score_val = max(0.0, min(100.0, float(data["score"])))
    except (TypeError, ValueError):
        return None
    return {
        "score": score_val,
        "confidence": float(data.get("confidence", 0.7)),
        "reasoning": str(data.get("reasoning", ""))[:300],
        "raw_ai_response": str(data)[:500],
    }


# 维度懒加载（避免某个文件未建好时 __init__ 整体崩）
def _lazy_load_dimensions():
    from src.core.benzong.dimensions.valuation_position import score as vp_score
    from src.core.benzong.dimensions.business_purity import score as bp_score
    from src.core.benzong.dimensions.industry_prosperity import score as ip_score
    from src.core.benzong.dimensions.industry_leader import score as il_score
    from src.core.benzong.dimensions.market_recognition import score as mr_score
    from src.core.benzong.dimensions.risk_deduction import score as rd_score
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
