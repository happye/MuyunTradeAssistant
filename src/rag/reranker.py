"""RAG 二阶段重排器（P3-C，v0.8.9.0）

为什么需要：双塔向量检索（BGE bi-encoder）把 query 和 doc 各自独立编码成向量，
只比余弦相似度，query 与 doc 之间没有交互，精度有天花板。
交叉编码器（CrossEncoder）把 (query, doc) 拼成一个序列喂给模型逐对打分，
query-doc 全交互，排序质量显著更高。

代价：不能像向量那样预先算好 doc 侧，检索时必须对每对实时推理。
因此走二阶段：向量/关键词先召回 top_k*N 候选 → CrossEncoder 精排 → 取 top_k。
本项目知识库 837 块，top_k*3=15 对，CPU 推理秒级内，完全可接受。

降级策略：模型缺失/加载失败时 is_available() 恒为 False，检索自动退化为
无重排路径（不抛异常、不影响主流程），符合项目"网络失败=降级不编造"的铁律。
"""

import logging
from typing import Sequence

logger = logging.getLogger(__name__)


class Reranker:
    """交叉编码器重排器

    Args:
        model_name: CrossEncoder 模型名
        enabled: 总开关（配置关闭或模型不可用时整体跳过）
        max_chars: 送入重排的文档最大字符数（超长块截断，省推理开销）
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-base",
        enabled: bool = True,
        max_chars: int = 1024,
    ):
        self.model_name = model_name
        self.enabled = enabled
        self.max_chars = max_chars
        self._model = None
        self._load_failed = False

    def is_available(self) -> bool:
        """重排是否可用（配置开启 且 模型加载成功，只尝试加载一次）"""
        return self.enabled and self._ensure_model()

    def _ensure_model(self) -> bool:
        if self._load_failed:
            return False
        if self._model is not None:
            return True
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
            logger.info("重排模型加载完成: %s", self.model_name)
            return True
        except Exception as exc:
            logger.warning("重排模型加载失败，检索退化为无重排: %s", exc)
            self._load_failed = True
            self._model = None
            return False

    def rerank(
        self,
        query: str,
        docs: Sequence,
        top_k: int,
    ) -> list:
        """对候选文档精排

        Args:
            query: 查询文本
            docs: 候选 RAGDocument 列表（已按召回序排列）
            top_k: 返回条数

        Returns:
            [(doc, score), ...] 按重排分降序
        """
        docs = list(docs)
        if not docs:
            return []
        if not self.is_available():
            # 降级：保持原召回序，分数沿用占位 0.0
            return [(d, 0.0) for d in docs][:top_k]

        pairs = [[query, (getattr(d, "content", "") or "")[: self.max_chars]] for d in docs]
        try:
            scores = self._model.predict(pairs)
        except Exception as exc:
            logger.warning("重排推理失败，退回原召回序: %s", exc)
            return [(d, 0.0) for d in docs][:top_k]

        scored = sorted(
            zip(docs, (float(s) for s in scores)),
            key=lambda item: item[1],
            reverse=True,
        )
        return scored[:top_k]
