"""RAG数据模型 - Pydantic定义 (v0.8.1)"""

from typing import Optional
from pydantic import BaseModel, Field
from enum import Enum

# ── 模型名常量（唯一出处；embedding/reranker/service 三处共用，模型换代只改这里）──
# 实际生效值仍以 configs/settings.yaml 的 rag.embedding.model / rag.reranker.model 为准，
# 本常量只作"配置缺失时的兜底"，避免同一字面量散落多个文件后漏改。
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"   # 512 维，中文，约 100MB
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-base"    # 交叉编码器，约 1GB（默认关闭，见 settings 注释）


class StrategyCategory(str, Enum):
    """策略分类"""
    TECHNICAL = "technical"              # 技术分析（第1-50章）
    PSYCHOLOGY = "psychology"            # 投资心理（第51-65章）
    RISK_MANAGEMENT = "risk_management"  # 风控管理（止损/止盈/仓位）
    MARKET_ANALYSIS = "market_analysis"  # 市场分析（大盘/板块/周期）


class Quantifiability(str, Enum):
    """可量化程度"""
    HIGH = "high"        # 可直接编码为规则（如ATR止损）
    MEDIUM = "medium"    # 部分可量化（如大盘环境分类）
    LOW = "low"          # 难以量化，适合RAG检索后AI参考（如损失厌恶心理）


class RAGDocument(BaseModel):
    """RAG文档 - 策略知识的最小检索单元"""
    id: str = Field(description="文档唯一ID，如 'ch48_p2'")
    content: str = Field(description="文档内容（300-800字段落）")
    metadata: dict = Field(default_factory=dict, description="元数据")
    source_file: str = Field(default="", description="源文件路径")

    # 便捷属性（从metadata中提取）
    @property
    def chapter(self) -> str:
        """章节号"""
        return self.metadata.get("chapter", "")

    @property
    def category(self) -> StrategyCategory:
        """策略分类"""
        cat = self.metadata.get("category", "technical")
        try:
            return StrategyCategory(cat)
        except ValueError:
            return StrategyCategory.TECHNICAL

    @property
    def quantifiability(self) -> Quantifiability:
        """可量化程度"""
        q = self.metadata.get("quantifiability", "medium")
        try:
            return Quantifiability(q)
        except ValueError:
            return Quantifiability.MEDIUM


class RetrievalResult(BaseModel):
    """检索结果"""
    query: str = Field(description="查询文本")
    documents: list[RAGDocument] = Field(default_factory=list, description="检索到的文档")
    scores: list[float] = Field(default_factory=list, description="相关性分数")
    method: str = Field(default="hybrid", description="检索方式：keyword/semantic/hybrid")
    total_found: int = Field(default=0, description="总匹配数")

    def format_context(self, max_length: int = 2000) -> str:
        """将检索结果格式化为AI可用的上下文文本

        Args:
            max_length: 最大上下文长度（字符数）

        Returns:
            格式化的策略知识上下文
        """
        if not self.documents:
            return ""

        parts = []
        total_len = 0

        for i, doc in enumerate(self.documents):
            score = self.scores[i] if i < len(self.scores) else 0.0
            chapter = doc.chapter or "未知章节"
            snippet = doc.content[:300]  # 每段最多300字

            entry = f"【{chapter}】(相关度:{score:.2f})\n{snippet}"
            entry_len = len(entry)

            if total_len + entry_len > max_length:
                break

            parts.append(entry)
            total_len += entry_len

        return "\n\n---\n\n".join(parts)
