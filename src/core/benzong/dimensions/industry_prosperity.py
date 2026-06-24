"""维度 1：行业景气度（最复杂 — AI + 新闻 + RAG）

笨总教学 1 + 教学 3：现象级事件四要素 + 三类拐点 + 高景气筛选标准

输入：股票所属行业 + 30 天新闻 + RAG 笨总教学锚点
AI 任务：综合判定行业景气度 0-100
"""

import logging
from typing import Optional
from src.core.benzong.dimensions import _missing_data_result, _ai_failed_result, _call_ai_for_score

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是笨总「超景气价值投机」体系的行业景气度评分员。

评分维度：行业景气度（仅看二级市场景气程度，非实际行业市场）

笨总评分参考（教学 1 + 教学 3）：
- 100 分：现象级事件驱动爆单（如 BB 机爆炸 → 海能达） / AI 大模型出现（ChatGPT）/ 政策反转直接利好
- 90 分：正在持续突破（自主可控芯片半导体），ING 状态最性感
- 70 分：高景气但已被市场充分定价（如已涨多年的龙头）
- 50 分：行业稳定但无显著景气拐点
- 30 分：景气度边际下行（如新能源车产能过剩阶段）
- 0 分：景气度归零（如中免般的反面案例）

四要素一票否决（教学 3）：
- 真实性：新闻是否经得起交叉验证？假消息直接 0 分
- 传播性：是否出圈全民讨论
- 规模性：行业体量是否足够大
- 时效性：是否第一次（重复消息打折）

输出 JSON：{"score": 0-100, "confidence": 0-1, "reasoning": "评分依据..."}
温度低，不要发挥，只看证据。
"""


def score(code: str, name: str, *, data_summary: dict,
          ai_client=None, ai_model: Optional[str] = None,
          rag_service=None) -> dict:
    """行业景气度评分（0-100）。

    v0.8.6.3：去掉 RAG 依赖（用户决定 bz 不依赖 RAG）。笨总教学评分标准
    已内嵌在 SYSTEM_PROMPT，行业+新闻足够 AI 判定，无需 RAG 锚点。
    rag_service 参数保留（签名统一）但不使用。
    """
    industry = data_summary.get("industry") or {}
    industry_name = industry.get("industry_name", "")
    announcements = data_summary.get("announcements") or []

    if not industry_name and not announcements:
        return _missing_data_result(
            "无法获取所属行业 + 最近 30 天新闻数据",
            dim_name="行业景气度",
        )

    # 拼接新闻文本（最多 10 条）
    news_text = ""
    for a in announcements[:10]:
        title = a.get("title", "")
        date = a.get("date", "")
        if title:
            news_text += f"- [{date}] {title}\n"
    if not news_text:
        news_text = "（最近 30 天无相关新闻）"

    user_prompt = f"""请评估 {name}（{code}）的行业景气度。

所属行业：{industry_name or "未知"}

最近 30 天相关新闻（{len(announcements)} 条）：
{news_text}

请按笨总「超景气价值投机」体系给行业景气度打分（0-100）。评分标准见系统提示。
JSON 输出：{{"score": 数字, "confidence": 0-1, "reasoning": "评分依据..."}}
"""

    if ai_client is None:
        return _ai_failed_result("ai_client 未提供", dim_name="行业景气度")

    ai_result = _call_ai_for_score(ai_client, SYSTEM_PROMPT, user_prompt,
                                    dim_name="行业景气度", model=ai_model)
    if ai_result is None:
        return _ai_failed_result("AI 调用/解析失败", dim_name="行业景气度")

    sources = []
    if industry_name:
        sources.append(f"行业: {industry_name}")
    if announcements:
        sources.append(f"新闻 {len(announcements)} 条")

    return {
        "score": ai_result["score"],
        "confidence": ai_result["confidence"],
        "sources": sources,
        "reasoning": ai_result["reasoning"],
        "data_freshness": "实时",
        "warnings": [],
    }
