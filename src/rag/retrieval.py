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

# jieba __init__ 会把自己的 logger 设为 DEBUG，chat/CLI 控制台被
# "Building prefix dict..." 等 DEBUG 日志刷屏。压回 WARNING（不影响分词功能）。
logging.getLogger("jieba").setLevel(logging.WARNING)

from src.rag.models import RAGDocument, RetrievalResult
from src.rag.embedding import Embedder
from src.rag.store import VectorStore
from src.rag.userdict import load_userdict

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
        # P2-C: 建关键词索引前确保金融自定义词典已加载（幂等）
        load_userdict()

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
        diversity_max_per_chapter: int = 2,  # 同章节在最终结果中最多占几条（<=0 关闭）
    ):
        """初始化

        Args:
            embedder: 嵌入器
            store: 向量存储
            keyword_weight: 关键词权重
            semantic_weight: 语义权重
            rrf_k: RRF常数
            diversity_max_per_chapter: 同一 metadata.chapter 在截断后结果中
                最多保留的块数（v0.8.8.9 多样性截断；<=0 表示关闭）
        """
        self.embedder = embedder
        self.store = store
        self.keyword_retriever = KeywordRetriever()
        self.keyword_weight = keyword_weight
        self.semantic_weight = semantic_weight
        self.rrf_k = rrf_k
        self.diversity_max_per_chapter = diversity_max_per_chapter

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
        layer: Optional[str] = None,
    ) -> RetrievalResult:
        """执行检索

        Args:
            query: 查询文本
            top_k: 返回数量
            method: 检索方式 "keyword"/"semantic"/"hybrid"
            layer: 适用系统层过滤（"Chat"/"Decision"/"Strategy"/"AI_Modifier"/"Execution"）。
                   传入后只保留 metadata.applicable_layers 含该层的文档块。
                   为 None 时不过滤（兼容旧调用）。

        Returns:
            RetrievalResult
        """
        if not layer:
            return self._dispatch(query, top_k, method)

        # 先多取候选，过滤后再截断到 top_k（避免过滤后结果不足）。
        # 稀有层（如 AI_Modifier）在靠前的候选里占比低，窗口需放宽到 8 倍。
        candidate_k = max(top_k * 8, 40)
        raw = self._dispatch(query, candidate_k, method)
        return self._filter_by_layer(raw, layer, top_k, query, method)

    def _dispatch(self, query: str, top_k: int, method: str) -> RetrievalResult:
        """按 method 分派到具体检索实现"""
        if method == "keyword":
            return self._keyword_retrieve(query, top_k)
        elif method == "semantic":
            return self._semantic_retrieve(query, top_k)
        else:
            return self._hybrid_retrieve(query, top_k)

    def _apply_diversity(
        self,
        ranked: list[tuple[RAGDocument, float]],
        top_k: int,
    ) -> list[tuple[RAGDocument, float]]:
        """同章节多样性截断（v0.8.8.9）

        问题：一个长章节（如456合刊）被切成几十块时，排名前列的结果可能
        全部来自同一章节，top_k 名额被单章占满，其他章节的优质内容进不来。

        策略：贪心保留排名靠前的结果，同一 metadata.chapter 最多保留
        diversity_max_per_chapter 条；一轮扫完后名额不满则按原排名回填
        被限流的项（保证结果数量不缩水）。chapter 缺失的块视为独立项
        不受限。diversity_max_per_chapter <= 0 时关闭（行为与旧版一致）。
        """
        cap = self.diversity_max_per_chapter
        if cap is None or cap <= 0 or top_k <= 0:
            return ranked[:top_k]

        primary: list[tuple[RAGDocument, float]] = []
        overflow: list[tuple[RAGDocument, float]] = []
        counts: dict[str, int] = defaultdict(int)
        for doc, score in ranked:
            chapter = (doc.metadata or {}).get("chapter")
            if chapter is not None and counts[chapter] >= cap:
                overflow.append((doc, score))
                continue
            if chapter is not None:
                counts[chapter] += 1
            primary.append((doc, score))
            if len(primary) >= top_k:
                break
        if len(primary) < top_k:
            primary.extend(overflow[: top_k - len(primary)])
        return primary

    def _filter_by_layer(
        self,
        result: RetrievalResult,
        layer: str,
        top_k: int,
        query: str,
        method: str,
    ) -> RetrievalResult:
        """按 applicable_layers 过滤检索结果

        用途：把 ingestion 一直在算、但检索层从未使用的 applicable_layers 元数据
        真正用起来。典型场景——金融战争（纪实小说）的 applicable_layers 只有
        ["Chat"]，因此不会进入 Decision/Strategy 等决策链路。
        """
        kept_docs, kept_scores = [], []
        for doc, score in zip(result.documents, result.scores):
            layers = doc.metadata.get("applicable_layers") or []
            if layer in layers:
                kept_docs.append(doc)
                kept_scores.append(score)
            if len(kept_docs) >= top_k:
                break

        if not kept_docs:
            logger.debug(
                f"层过滤后无结果（layer={layer}, query={query[:20]}），"
                f"回退为未过滤结果（候选{len(result.documents)}条）"
            )
            kept_docs = result.documents[:top_k]
            kept_scores = result.scores[:top_k]

        # 候选级多样性（_dispatch 内）+ 最终截断级多样性双保险：
        # 层过滤可能把前排多样结果筛掉、只剩同章节，这里对最终结果再截一次。
        kept_pairs = self._apply_diversity(list(zip(kept_docs, kept_scores)), top_k)
        kept_docs = [d for d, _ in kept_pairs]
        kept_scores = [s for _, s in kept_pairs]

        return RetrievalResult(
            query=query,
            documents=kept_docs,
            scores=kept_scores,
            method=method,
            total_found=result.total_found,
        )

    def _keyword_retrieve(self, query: str, top_k: int) -> RetrievalResult:
        """纯关键词检索"""
        results = self.keyword_retriever.search(query, top_k * 2)

        ranked = []
        for doc_id, score in results:
            doc = self.store.get_doc_by_id(doc_id) if hasattr(self.store, 'get_doc_by_id') else None
            if doc:
                ranked.append((doc, score))

        kept = self._apply_diversity(ranked, top_k)
        docs = [d for d, _ in kept]
        scores = [s for _, s in kept]

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
        # 多取 3 倍候选给多样性截断留出替换空间
        results = self.store.query(query_embedding, top_k * 3)

        ranked = []
        for doc_id, score in results:
            doc = self.store.get_doc_by_id(doc_id) if hasattr(self.store, 'get_doc_by_id') else None
            if doc:
                ranked.append((doc, score))

        kept = self._apply_diversity(ranked, top_k)
        docs = [d for d, _ in kept]
        scores = [s for _, s in kept]

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

        ranked = []
        for doc_id, score in sorted_ids:
            doc = self.store.get_doc_by_id(doc_id) if hasattr(self.store, 'get_doc_by_id') else None
            if doc:
                ranked.append((doc, score))

        kept = self._apply_diversity(ranked, top_k)
        docs = [d for d, _ in kept]
        scores = [s for _, s in kept]

        return RetrievalResult(
            query=query,
            documents=docs,
            scores=scores,
            method="hybrid",
            total_found=len(sorted_ids),
        )
