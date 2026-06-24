"""维度 5：市场辨识度（AI 判断）

笨总教学 1：「提到产品/服务，第一反应是它」= 满分
"""

import logging
from typing import Optional
from src.core.benzong.dimensions import _missing_data_result, _ai_failed_result, _call_ai_for_score

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是笨总「超景气价值投机」体系的市场辨识度评分员。

评分标准（教学 1）：
- 100 分：辨识度爆表 — 提到该产品/服务，第一反应就是它（如对讲机=海能达）
   或 A股+港股 仅此一家纯正标的（独苗效应）
- 90 分：行业内前三的强品牌
- 70 分：行业内有一定知名度
- 50 分：知道，但有平替
- 30 分：圈内人才知道
- 0 分：完全没听过

辨识度有多重维度：
1. 名字自带流量（如要做 AI 眼镜，股票名带"眼镜"）
2. 历史名人效应（被某著名投资人长期操盘）
3. 行业唯一性（A 股仅此一家）

输出 JSON：{"score": 0-100, "confidence": 0-1, "reasoning": "..."}
"""


def score(code: str, name: str, *, data_summary: dict,
          ai_client=None, ai_model: Optional[str] = None,
          rag_service=None) -> dict:
    industry = data_summary.get("industry") or {}
    industry_name = industry.get("industry_name", "")
    business_intro = data_summary.get("business_intro", "")

    if not name and not business_intro:
        return _missing_data_result(
            "无股票名称 + 业务介绍",
            dim_name="市场辨识度",
        )

    user_prompt = f"""请评估 {name}（{code}）在 A 股市场的辨识度。

行业：{industry_name or "未知"}
主营介绍：
{business_intro[:400] if business_intro else "（未获取）"}

按笨总教学 1 标准评分：「提到产品第一反应是它」=100 / 行业前三强品牌=90 / 一般知名度=70 等。

JSON 输出：{{"score": 数字, "confidence": 0-1, "reasoning": "..."}}
"""

    if ai_client is None:
        return _ai_failed_result("ai_client 未提供", dim_name="市场辨识度")

    ai_result = _call_ai_for_score(ai_client, SYSTEM_PROMPT, user_prompt,
                                    dim_name="市场辨识度", model=ai_model)
    if ai_result is None:
        return _ai_failed_result("AI 调用失败", dim_name="市场辨识度")

    return {
        "score": ai_result["score"],
        "confidence": ai_result["confidence"],
        "sources": [f"股票名 + 主营介绍"],
        "reasoning": ai_result["reasoning"],
        "data_freshness": "实时",
        "warnings": [],
    }
