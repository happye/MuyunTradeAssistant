"""RAG检索层 - 混合检索（关键词+语义）(v0.8.1)

检索策略：
1. 语义检索：向量相似度（BGE模型），对自然语言查询效果好
2. 关键词检索：BM25/jieba分词匹配，对专业术语精准
3. 混合检索：RRF(Reciprocal Rank Fusion)融合两种得分

权重配置：
- hybrid_weights.keyword: 关键词检索权重（默认0.4）
- hybrid_weights.semantic: 语义检索权重（默认0.6）
"""

import logging
from typing import Optional
from collections import defaultdict

import numpy as np
import jieba

from src.rag.models import RAGDocument, RetrievalResult
from src.rag.embedding import Embedder
from src.rag.store import VectorStore

logger = logging.getLogger(__name__)


class KeywordRetriever:
    """关键词检索器 - BM25风格

    使用jieba分词 + TF词频匹配。
    简化版BM25，适合小规模知识库。
    """

    def __init__(self):
        self._doc_tokens: list[list[str]] = []
        self._docs: list[RAGDocument] = []
        self._df: dict[str, int] = defaultdict(int)  # 文档频率
        self._avg_dl: float = 0.0  # 平均文档长度
        self._k1: float = 1.5  # BM25参数
        self._b: float = 0.75  # BM25参数

    def index(self, docs: list[RAGDocument]) -> None:
        """建立关键词索引

        Args:
            docs: 文档列表
        """
        self._docs = docs
        self._doc_tokens = []
        self._df = defaultdict(int)
        total_len = 0

        for doc in docs:
            tokens = list(jieba.cut(doc.content))
            # 过滤停用词和单字
            tokens = [t for t in tokens if len(t) > 1]
            self._doc_tokens.append(tokens)
            total_len += len(tokens)

            # 文档频率
            unique_tokens = set(tokens)
            for token in unique_tokens:
                self._df[token] += 1

        self._avg_dl = total_len / len(docs) if docs else 1.0
        logger.info(f"关键词索引建立: {len(docs)}个文档, 词典大小{len(self._df)}")

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        """关键词检索

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            [(doc_id, score), ...]
        """
        if not self._docs:
            return []

        query_tokens = list(jieba.cut(query))
        query_tokens = [t for t in query_tokens if len(t) > 1]

        if not query_tokens:
            return []

        n_docs = len(self._docs)
        scores = []

        for i, doc_tokens in enumerate(self._doc_tokens):
            score = self._bm25_score(
                query_tokens, doc_tokens, n_docs
            )
            scores.append((self._docs[i].id, score))

        # 按分数降序
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _bm25_score(
        self,
        query_tokens: list[str],
        doc_tokens: list[str],
        n_docs: int,
    ) -> float:
        """计算BM25得分"""
        score = 0.0
        dl = len(doc_tokens)
        tf_map = defaultdict(int)
        for t in doc_tokens:
            tf_map[t] += 1

        for qt in query_tokens:
            tf = tf_map.get(qt, 0)
            if tf == 0:
                continue

            # IDF
            df = self._df.get(qt, 0)
            idf = np.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)

            # TF归一化
            tf_norm = (tf * (self._k1 + 1)) / (
                tf + self._k1 * (1 - self._b + self._b * dl / self._avg_dl)
            )

            score += idf * tf_norm

        return score


class HybridRetriever:
    """混合检索器 - 语义+关键词融合

    使用RRF(Reciprocal Rank Fusion)融合两种检索结果：
    RRF_score = Σ (1 / (k + rank_i))  for each retriever i
    """

    def __init__(
        self,
        embedder: Embedder,
        store: VectorStore,
        keyword_weight: float = 0.4,
        semantic_weight: float = 0.6,
        rrf_k: int = 60,  # RRF常数，默认60
    ):
        """初始化

        Args:
            embedder: 嵌入器
            store: 向量存储
            keyword_weight: 关键词权重
            semantic_weight: 语义权重
            rrf_k: RRF常数
        """
        self.embedder = embedder
        self.store = store
        self.keyword_retriever = KeywordRetriever()
        self.keyword_weight = keyword_weight
        self.semantic_weight = semantic_weight
        self.rrf_k = rrf_k

    def index_documents(self, docs: list[RAGDocument]) -> None:
        """建立关键词索引（语义索引由store管理）

        Args:
            docs: 文档列表
        """
        self.keyword_retriever.index(docs)

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        method: str = "hybrid",
    ) -> RetrievalResult:
        """执行检索

        Args:
            query: 查询文本
            top_k: 返回数量
            method: 检索方式 "keyword"/"semantic"/"hybrid"

        Returns:
            RetrievalResult
        """
        if method == "keyword":
            return self._keyword_retrieve(query, top_k)
        elif method == "semantic":
            return self._semantic_retrieve(query, top_k)
        else:
            return self._hybrid_retrieve(query, top_k)

    def _keyword_retrieve(self, query: str, top_k: int) -> RetrievalResult:
        """纯关键词检索"""
        results = self.keyword_retriever.search(query, top_k * 2)

        docs = []
        scores = []
        for doc_id, score in results[:top_k]:
            doc = self.store.get_doc_by_id(doc_id) if hasattr(self.store, 'get_doc_by_id') else None
            if doc:
                docs.append(doc)
                scores.append(score)

        return RetrievalResult(
            query=query,
            documents=docs,
            scores=scores,
            method="keyword",
            total_found=len(results),
        )

    def _semantic_retrieve(self, query: str, top_k: int) -> RetrievalResult:
        """纯语义检索"""
        query_embedding = self.embedder.embed_query(query)
        results = self.store.query(query_embedding, top_k)

        docs = []
        scores = []
        for doc_id, score in results[:top_k]:
            doc = self.store.get_doc_by_id(doc_id) if hasattr(self.store, 'get_doc_by_id') else None
            if doc:
                docs.append(doc)
                scores.append(score)

        return RetrievalResult(
            query=query,
            documents=docs,
            scores=scores,
            method="semantic",
            total_found=len(results),
        )

    def _hybrid_retrieve(self, query: str, top_k: int) -> RetrievalResult:
        """混合检索（RRF融合）"""
        # 并行获取两种检索结果
        keyword_results = self.keyword_retriever.search(query, top_k * 3)
        semantic_results = self._semantic_retrieve(query, top_k * 3)

        # RRF融合
        rrf_scores: dict[str, float] = defaultdict(float)

        # 关键词RRF得分
        for rank, (doc_id, _) in enumerate(keyword_results):
            rrf_scores[doc_id] += self.keyword_weight / (self.rrf_k + rank + 1)

        # 语义RRF得分
        for rank, doc in enumerate(semantic_results.documents):
            rrf_scores[doc.id] += self.semantic_weight / (self.rrf_k + rank + 1)

        # 按融合得分排序
        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        docs = []
        scores = []
        for doc_id, score in sorted_ids[:top_k]:
            doc = self.store.get_doc_by_id(doc_id) if hasattr(self.store, 'get_doc_by_id') else None
            if doc:
                docs.append(doc)
                scores.append(score)

        return RetrievalResult(
            query=query,
            documents=docs,
            scores=scores,
            method="hybrid",
            total_found=len(sorted_ids),
        )
