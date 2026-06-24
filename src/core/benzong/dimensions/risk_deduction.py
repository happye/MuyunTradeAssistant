"""维度 6：个股风险值（扣分项，0=最好）

笨总教学 1 + 课件：
- 短期定增/解禁/大股东减持 → 风险扣分
- 安全事件 / 黑天鹅 / 制裁 → 高风险
- 红线（叛国/违禁出口） → 一票否决，risk=100
"""

import logging
from typing import Optional
from src.core.benzong.dimensions import _missing_data_result, _ai_failed_result, _call_ai_for_score

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是笨总「超景气价值投机」体系的个股风险值评估员（扣分项）。

风险评分（0-100，分数越高表示风险越大；满分 100 表示一票否决）：

常规扣分项（每项 +20）：
- 短期定增/巨额解禁
- 大股东减持记录
- 悬而未决的官司
- 被海外制裁
- 矿难/安全事故
- 管理层经营不善（已 ST 或濒临 ST）
- 财务造假前科 / 审计保留意见

红线（一票否决，直接 100 + invalidate=true）：
- 叛国/违禁出口
- 财务造假被立案

风险释放原则：
- 黑天鹅事件已 1+ 年但未落地 → 风险已部分释放，扣 10 分而非 20

输出 JSON：{"score": 0-100, "confidence": 0-1, "reasoning": "...", "invalidate": true/false}
"""


def score(code: str, name: str, *, data_summary: dict,
          ai_client=None, ai_model: Optional[str] = None,
          rag_service=None) -> dict:
    announcements = data_summary.get("announcements") or []

    if not announcements:
        # 无新闻 ≠ 无风险，但也无证据风险存在 → 默认 0（最好）+ 低 confidence
        return {
            "score": 0,
            "confidence": 0.3,
            "sources": [],
            "reasoning": "近 30 天无公告/新闻数据，无明显风险信号（confidence 较低，需人工核对）",
            "data_freshness": "N/A",
            "warnings": ["近期公告/新闻拉取失败，风险评分仅为 fallback"],
        }

    news_text = ""
    for a in announcements[:15]:
        title = a.get("title", "")
        date = a.get("date", "")
        if title:
            news_text += f"- [{date}] {title}\n"

    user_prompt = f"""请评估 {name}（{code}）的个股风险值（扣分项 0-100）。

最近 30 天公告/新闻（{len(announcements)} 条）：
{news_text}

按笨总标准识别风险：
- 减持/定增/官司/制裁/事故/管理层 → 每项 +20
- 红线（叛国/违禁/造假立案）→ 100 + invalidate=true

JSON 输出：{{"score": 数字, "confidence": 0-1, "reasoning": "...", "invalidate": false}}
"""

    if ai_client is None:
        # AI 不可用时不能假装识别风险 → 返回 0 + 低 confidence + 警告
        return {
            "score": 0,
            "confidence": 0.0,
            "sources": [f"新闻 {len(announcements)} 条"],
            "reasoning": "AI 不可用，无法解析新闻识别风险",
            "data_freshness": "N/A",
            "warnings": ["AI 未启用，风险评分不可信"],
        }

    ai_result = _call_ai_for_score(ai_client, SYSTEM_PROMPT, user_prompt,
                                    dim_name="个股风险值", model=ai_model)
    if ai_result is None:
        return _ai_failed_result("AI 调用失败", dim_name="个股风险值")

    # 检查是否触发一票否决
    invalidate = False
    raw = ai_result.get("raw_ai_response", "")
    if "invalidate" in raw.lower() and "true" in raw.lower():
        invalidate = True

    warnings = []
    if invalidate or ai_result["score"] >= 80:
        warnings.append(f"⚠ 高风险标的（评分 {ai_result['score']}），建议放弃或人工复核")

    return {
        "score": ai_result["score"],
        "confidence": ai_result["confidence"],
        "sources": [f"近 30 天新闻 {len(announcements)} 条"],
        "reasoning": ai_result["reasoning"],
        "data_freshness": "实时",
        "warnings": warnings,
        "invalidate": invalidate,
    }
