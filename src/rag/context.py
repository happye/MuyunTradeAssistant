"""RAG上下文构建 - 检索结果→结构化上下文 (v0.8.1)

将检索到的策略文档格式化为AI可用的上下文文本，
注入到不同接入点的prompt中。
"""

import logging
from typing import Optional

from src.rag.models import RetrievalResult, RAGDocument

logger = logging.getLogger(__name__)


def build_chat_context(retrieval_result: RetrievalResult, max_length: int = 2000) -> str:
    """构建Chat Agent用的策略知识上下文

    格式化为自然语言段落，适合Chat AI理解。

    Args:
        retrieval_result: 检索结果
        max_length: 最大上下文长度

    Returns:
        格式化的上下文文本
    """
    return retrieval_result.format_context(max_length)


def build_event_context(
    retrieval_result: RetrievalResult,
    event_type: str = "",
    max_length: int = 1500,
) -> str:
    """构建Event Layer用的策略知识上下文

    格式化为简洁的事件相关策略参考。

    Args:
        retrieval_result: 检索结果
        event_type: 事件类型
        max_length: 最大上下文长度

    Returns:
        格式化的上下文文本
    """
    if not retrieval_result.documents:
        return ""

    parts = []
    total_len = 0

    event_cn = {
        "policy": "政策变化", "war": "地缘冲突", "earnings": "财报",
        "macro": "宏观", "black_swan": "黑天鹅", "market_crash": "暴跌",
    }
    event_label = event_cn.get(event_type, event_type)

    for i, doc in enumerate(retrieval_result.documents):
        chapter = doc.chapter or "策略参考"
        snippet = doc.content[:200]

        entry = f"- [{chapter}] {snippet}"
        entry_len = len(entry)

        if total_len + entry_len > max_length:
            break

        parts.append(entry)
        total_len += entry_len

    if not parts:
        return ""

    header = f"以下是关于{event_label}的策略知识参考：\n"
    return header + "\n".join(parts)


def build_modifier_context(
    retrieval_result: RetrievalResult,
    stock_name: str = "",
    max_length: int = 1500,
) -> str:
    """构建AI Modifier用的策略知识上下文

    格式化为分析参考，注入AI新闻分析的system prompt。

    Args:
        retrieval_result: 检索结果
        stock_name: 股票名称
        max_length: 最大上下文长度

    Returns:
        格式化的上下文文本
    """
    if not retrieval_result.documents:
        return ""

    parts = []
    total_len = 0

    for i, doc in enumerate(retrieval_result.documents):
        chapter = doc.chapter or "策略参考"
        snippet = doc.content[:250]

        entry = f"【{chapter}】{snippet}"
        entry_len = len(entry)

        if total_len + entry_len > max_length:
            break

        parts.append(entry)
        total_len += entry_len

    if not parts:
        return ""

    stock_label = f"关于{stock_name}" if stock_name else "关于当前分析"
    header = f"以下是{stock_label}的策略知识参考：\n\n"
    return header + "\n\n---\n\n".join(parts)
