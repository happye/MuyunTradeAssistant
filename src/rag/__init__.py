"""RAG子系统 - 检索增强生成 (v0.8.1)

核心原则：RAG是信息增强组件，不参与交易决策。

架构：
  用户查询/新闻/事件
        ↓
   Embedding模块（BGE中文模型）
        ↓
   FAISS向量索引
        ↓
   混合检索（关键词+语义）
        ↓
   上下文构建
        ↓
   注入AI Prompt（增强分析质量）

集成点：
  - Chat Agent: search_knowledge工具
  - Event Layer: 事件关键词→策略知识检索
  - AI Modifier: 新闻分析→策略知识增强
"""

from src.rag.service import RAGService
from src.rag.models import RAGDocument, RetrievalResult

__all__ = ["RAGService", "RAGDocument", "RetrievalResult"]
