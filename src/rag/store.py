"""RAG向量存储层 - FAISS + numpy (v0.8.1)

主力：FAISSVectorStore (IndexFlatIP, 内积搜索)
降级：NumpyVectorStore (numpy余弦相似度)

L2归一化后的内积等价于余弦相似度，因此使用IndexFlatIP。
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np

from src.rag.models import RAGDocument

logger = logging.getLogger(__name__)


class VectorStore(ABC):
    """向量存储抽象基类"""

    @abstractmethod
    def add(self, docs: list[RAGDocument], embeddings: np.ndarray) -> None:
        """添加文档和对应的嵌入向量

        Args:
            docs: RAGDocument列表
            embeddings: 嵌入向量 (n, dim)
        """
        ...

    @abstractmethod
    def query(self, embedding: np.ndarray, top_k: int = 5) -> list[tuple[str, float]]:
        """查询最相似的文档

        Args:
            embedding: 查询向量 (dim,)
            top_k: 返回前K个结果

        Returns:
            [(doc_id, score), ...] 按相似度降序
        """
        ...

    @abstractmethod
    def save(self, path: str) -> None:
        """持久化索引到磁盘"""
        ...

    @abstractmethod
    def load(self, path: str) -> bool:
        """从磁盘加载索引

        Returns:
            是否成功加载
        """
        ...

    @property
    @abstractmethod
    def size(self) -> int:
        """当前存储的文档数量"""
        ...


class FAISSVectorStore(VectorStore):
    """FAISS向量存储 - 主力方案

    使用IndexFlatIP（内积搜索），配合L2归一化等价于余弦相似度。
    适合文档数<100万的场景。
    """

    def __init__(self):
        self._index = None
        self._docs: list[RAGDocument] = []
        self._id_to_idx: dict[str, int] = {}
        self._dimension: int = 0

    def add(self, docs: list[RAGDocument], embeddings: np.ndarray) -> None:
        """添加文档"""
        if not docs or embeddings.shape[0] == 0:
            return

        try:
            import faiss
        except ImportError:
            raise ImportError("faiss-cpu未安装。请运行: pip install faiss-cpu")

        n, dim = embeddings.shape
        assert n == len(docs), f"文档数({len(docs)})和嵌入数({n})不匹配"

        # 确保float32
        embeddings = embeddings.astype(np.float32)

        if self._index is None:
            self._dimension = dim
            self._index = faiss.IndexFlatIP(dim)  # 内积索引
        else:
            assert dim == self._dimension, f"维度不匹配: 期望{self._dimension}，实际{dim}"

        # 添加到FAISS索引
        self._index.add(embeddings)

        # 添加到文档列表
        start_idx = len(self._docs)
        for i, doc in enumerate(docs):
            self._id_to_idx[doc.id] = start_idx + i
        self._docs.extend(docs)

        logger.info(f"FAISS: 添加{n}个文档，总计{len(self._docs)}个")

    def query(self, embedding: np.ndarray, top_k: int = 5) -> list[tuple[str, float]]:
        """查询最相似的文档"""
        if self._index is None or len(self._docs) == 0:
            return []

        # 确保形状正确
        if embedding.ndim == 1:
            embedding = embedding.reshape(1, -1)
        embedding = embedding.astype(np.float32)

        # 搜索
        actual_k = min(top_k, len(self._docs))
        scores, indices = self._index.search(embedding, actual_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:  # FAISS返回-1表示无结果
                continue
            if idx < len(self._docs):
                doc_id = self._docs[idx].id
                results.append((doc_id, float(score)))

        return results

    def save(self, path: str) -> None:
        """持久化到磁盘

        v0.8.7.8 裁决修复 H03：三文件改 tmp+os.replace 原子替换（原直接写，
        中断会产生 index 与 docs 数量错配 → load 静默截断/张冠李戴）。
        跨文件事务不可行，配套 load() 侧 ntotal==len(docs) 一致性校验兜底。
        """
        if self._index is None:
            logger.warning("无索引数据可保存")
            return

        dir_path = Path(path)
        dir_path.mkdir(parents=True, exist_ok=True)

        try:
            import faiss

            # 先全部写 tmp，再依次原子替换（把不一致窗口压到最小）
            tmp_index = dir_path / "index.faiss.tmp"
            tmp_docs = dir_path / "docs.json.tmp"
            tmp_cfg = dir_path / "config.json.tmp"

            faiss.write_index(self._index, str(tmp_index))
            docs_data = [doc.model_dump() for doc in self._docs]
            with open(tmp_docs, 'w', encoding='utf-8') as f:
                json.dump(docs_data, f, ensure_ascii=False, indent=2)
            config = {"dimension": self._dimension, "size": len(self._docs)}
            with open(tmp_cfg, 'w', encoding='utf-8') as f:
                json.dump(config, f)

            os.replace(tmp_index, dir_path / "index.faiss")
            os.replace(tmp_docs, dir_path / "docs.json")
            os.replace(tmp_cfg, dir_path / "config.json")

            logger.info(f"FAISS索引已保存: {dir_path} ({len(self._docs)}个文档)")
        except Exception as e:
            logger.error(f"FAISS索引保存失败: {e}")

    def load(self, path: str) -> bool:
        """从磁盘加载"""
        dir_path = Path(path)

        if not (dir_path / "index.faiss").exists():
            return False

        try:
            import faiss

            # 加载FAISS索引
            self._index = faiss.read_index(str(dir_path / "index.faiss"))
            self._dimension = self._index.d

            # 加载文档元数据
            with open(dir_path / "docs.json", 'r', encoding='utf-8') as f:
                docs_data = json.load(f)

            self._docs = [RAGDocument(**d) for d in docs_data]
            self._id_to_idx = {doc.id: i for i, doc in enumerate(self._docs)}

            # v0.8.7.8 裁决修复 H03：一致性校验——save 中断会产生 index 与 docs
            # 数量错配，原实现静默加载（实测 docs=2/ntotal=3 时 top3 只回 2 且
            # 无告警；反向错配更会张冠李戴）。拒绝加载 → 触发下次全量重建。
            if self._index.ntotal != len(self._docs):
                logger.error(
                    f"RAG索引与文档数量不一致(index={self._index.ntotal}, "
                    f"docs={len(self._docs)})，拒绝加载（将触发重建）——"
                    f"疑似上次保存中断（H03）")
                self._index = None
                self._docs = []
                self._id_to_idx = {}
                return False

            logger.info(f"FAISS索引已加载: {len(self._docs)}个文档, dim={self._dimension}")
            return True

        except Exception as e:
            logger.error(f"FAISS索引加载失败: {e}")
            return False

    def get_doc_by_id(self, doc_id: str) -> Optional[RAGDocument]:
        """根据ID获取文档"""
        idx = self._id_to_idx.get(doc_id)
        if idx is not None and idx < len(self._docs):
            return self._docs[idx]
        return None

    @property
    def size(self) -> int:
        return len(self._docs)


class NumpyVectorStore(VectorStore):
    """Numpy向量存储 - 降级方案

    使用numpy矩阵+余弦相似度，无需额外依赖。
    适合文档数<10000的场景。
    """

    def __init__(self):
        self._embeddings: Optional[np.ndarray] = None
        self._docs: list[RAGDocument] = []
        self._id_to_idx: dict[str, int] = {}
        self._dimension: int = 0

    def add(self, docs: list[RAGDocument], embeddings: np.ndarray) -> None:
        """添加文档"""
        if not docs or embeddings.shape[0] == 0:
            return

        n, dim = embeddings.shape
        assert n == len(docs)

        embeddings = embeddings.astype(np.float32)

        if self._embeddings is None:
            self._embeddings = embeddings
            self._dimension = dim
        else:
            assert dim == self._dimension
            self._embeddings = np.vstack([self._embeddings, embeddings])

        start_idx = len(self._docs)
        for i, doc in enumerate(docs):
            self._id_to_idx[doc.id] = start_idx + i
        self._docs.extend(docs)

        logger.info(f"NumpyVectorStore: 添加{n}个文档，总计{len(self._docs)}个")

    def query(self, embedding: np.ndarray, top_k: int = 5) -> list[tuple[str, float]]:
        """查询最相似的文档（余弦相似度）"""
        if self._embeddings is None or len(self._docs) == 0:
            return []

        # 确保形状正确
        if embedding.ndim == 1:
            query_vec = embedding.reshape(1, -1).astype(np.float32)
        else:
            query_vec = embedding.astype(np.float32)

        # 余弦相似度 = L2归一化后的内积
        norms = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1
        normed = self._embeddings / norms

        query_norm = np.linalg.norm(query_vec)
        if query_norm > 0:
            query_vec = query_vec / query_norm

        scores = (normed @ query_vec.T).flatten()

        # 取top_k
        actual_k = min(top_k, len(self._docs))
        top_indices = np.argsort(scores)[::-1][:actual_k]

        results = []
        for idx in top_indices:
            doc_id = self._docs[idx].id
            results.append((doc_id, float(scores[idx])))

        return results

    def save(self, path: str) -> None:
        """持久化到磁盘"""
        if self._embeddings is None:
            return

        dir_path = Path(path)
        dir_path.mkdir(parents=True, exist_ok=True)

        np.save(str(dir_path / "embeddings.npy"), self._embeddings)

        docs_data = [doc.model_dump() for doc in self._docs]
        with open(dir_path / "docs.json", 'w', encoding='utf-8') as f:
            json.dump(docs_data, f, ensure_ascii=False, indent=2)

        logger.info(f"NumpyVectorStore已保存: {len(self._docs)}个文档")

    def load(self, path: str) -> bool:
        """从磁盘加载"""
        dir_path = Path(path)

        if not (dir_path / "embeddings.npy").exists():
            return False

        try:
            self._embeddings = np.load(str(dir_path / "embeddings.npy"))

            with open(dir_path / "docs.json", 'r', encoding='utf-8') as f:
                docs_data = json.load(f)

            self._docs = [RAGDocument(**d) for d in docs_data]
            self._id_to_idx = {doc.id: i for i, doc in enumerate(self._docs)}
            self._dimension = self._embeddings.shape[1] if self._embeddings.ndim > 1 else 0

            logger.info(f"NumpyVectorStore已加载: {len(self._docs)}个文档")
            return True

        except Exception as e:
            logger.error(f"NumpyVectorStore加载失败: {e}")
            return False

    def get_doc_by_id(self, doc_id: str) -> Optional[RAGDocument]:
        """根据ID获取文档"""
        idx = self._id_to_idx.get(doc_id)
        if idx is not None and idx < len(self._docs):
            return self._docs[idx]
        return None

    @property
    def size(self) -> int:
        return len(self._docs)


def create_vector_store(prefer_faiss: bool = True) -> VectorStore:
    """工厂方法：创建向量存储

    Args:
        prefer_faiss: 是否优先使用FAISS

    Returns:
        VectorStore实例
    """
    if prefer_faiss:
        try:
            import faiss  # noqa: F401
            return FAISSVectorStore()
        except ImportError:
            logger.warning("faiss-cpu不可用，使用numpy存储")

    return NumpyVectorStore()
