"""维度 2：业务纯度（AI 读主营介绍）

笨总教学 2 标准：
- 主营业务占比越高越好（≥90% 满分，海外业务 ≥50% 打 8 折）
"""

import logging
from src.core.benzhong.dimensions import _missing_data_result, _ai_failed_result, _call_ai_for_score

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是笨总「超景气价值投机」体系的业务纯度评分员。

评分标准（教学 2）：
- 100 分：主营业务占总营收 ≥90%（如茅台 99% 主营白酒）
- 80 分：主营 ≥70%；或海外业务 ≥50%（说明海外布局成熟）
- 60 分：主营 ≥50%
- 40 分：主营 30-50%（业务杂乱）
- 20 分：主营 <30%（多元化失焦）
- 0 分：所谓主营是 PPT 概念（如蹭脑机接口/核聚变的子公司）

警惕「画饼」：投了一点钱给热门概念子公司不算主营，看真实营收占比。

输出 JSON：{"score": 0-100, "confidence": 0-1, "reasoning": "评分依据..."}
"""


def score(code: str, name: str, *, data_summary: dict,
          ai_client=None, rag_service=None) -> dict:
    business_intro = data_summary.get("business_intro")

    if not business_intro:
        return _missing_data_result(
            "无法获取公司主营业务介绍（akshare stock_zyjs_ths 失败）",
            dim_name="业务纯度",
        )

    user_prompt = f"""请评估 {name}（{code}）的业务纯度。

主营业务介绍：
{business_intro}

按笨总教学 2 标准打分（主营占比越高越好，海外 ≥50% 打 8 折）。
注意区分真主营 vs 蹭概念。

JSON 输出：{{"score": 数字, "confidence": 0-1, "reasoning": "..."}}
"""

    if ai_client is None:
        return _ai_failed_result("ai_client 未提供", dim_name="业务纯度")

    ai_result = _call_ai_for_score(ai_client, SYSTEM_PROMPT, user_prompt,
                                    dim_name="业务纯度")
    if ai_result is None:
        return _ai_failed_result("AI 调用失败", dim_name="业务纯度")

    return {
        "score": ai_result["score"],
        "confidence": ai_result["confidence"],
        "sources": [f"主营介绍 ({len(business_intro)} 字)"],
        "reasoning": ai_result["reasoning"],
        "data_freshness": "实时",
        "warnings": [],
    }
