"""维度 4：细分行业龙头（AI 判断）

笨总教学 1：
- 全球龙头/国内龙头/A 股最强选手 = 100/90/80
- 不是龙头 = 0
"""

import logging
from typing import Optional
from src.core.benzong.dimensions import _missing_data_result, _ai_failed_result, _call_ai_for_score

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是笨总「超景气价值投机」体系的细分行业龙头评分员。

评分标准（教学 1）：
- 100 分：全球龙头（在全球该细分领域排第一）
- 90 分：国内龙头（A 股或港股最大）
- 80 分：A 股上市最强选手（行业老大若未上市，A 股最强者）
- 50 分：A 股 Top 3
- 20 分：A 股 Top 10
- 0 分：不是龙头

注意：技术含量高 + 卡位（卡脖子环节）= 加分项；最上游卖铲子 = 加分项。

输出 JSON：{"score": 0-100, "confidence": 0-1, "reasoning": "..."}
"""


def score(code: str, name: str, *, data_summary: dict,
          ai_client=None, ai_model: Optional[str] = None,
          rag_service=None) -> dict:
    industry = data_summary.get("industry") or {}
    industry_name = industry.get("industry_name", "")
    business_intro = data_summary.get("business_intro", "")
    market_cap = industry.get("total_market_cap", "")

    if not industry_name and not business_intro:
        return _missing_data_result(
            "无法获取行业归属 + 主营介绍",
            dim_name="细分行业龙头",
        )

    user_prompt = f"""请评估 {name}（{code}）在其细分行业内的龙头地位。

所属行业：{industry_name or "未知"}
总市值：{market_cap or "未知"}
主营介绍：
{business_intro[:500] if business_intro else "（未获取）"}

按笨总教学 1 标准（全球龙头 100/国内龙头 90/A 股最强 80/Top3 50/不是龙头 0）打分。

JSON 输出：{{"score": 数字, "confidence": 0-1, "reasoning": "..."}}
"""

    if ai_client is None:
        return _ai_failed_result("ai_client 未提供", dim_name="细分行业龙头")

    ai_result = _call_ai_for_score(ai_client, SYSTEM_PROMPT, user_prompt,
                                    dim_name="细分行业龙头", model=ai_model)
    if ai_result is None:
        return _ai_failed_result("AI 调用失败", dim_name="细分行业龙头")

    sources = []
    if industry_name:
        sources.append(f"行业: {industry_name}")
    if market_cap:
        sources.append(f"市值: {market_cap}")

    return {
        "score": ai_result["score"],
        "confidence": ai_result["confidence"],
        "sources": sources,
        "reasoning": ai_result["reasoning"],
        "data_freshness": "实时",
        "warnings": [],
    }
