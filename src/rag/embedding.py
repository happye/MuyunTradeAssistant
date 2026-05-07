"""RAG嵌入层 - 文本向量化 (v0.8.1)

主力：SentenceEmbedder (BAAI/bge-small-zh-v1.5, 512维)
降级：TFIDFEmbedder (jieba + TF-IDF)

BGE模型使用说明：
- bge-small-zh-v1.5: 33MB, 512维向量, 中文优化
- 首次使用自动下载模型到 ~/.cache/huggingface/
- 查询时建议添加指令前缀 "为这个句子生成表示以检索相关文章："
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# HuggingFace镜像配置（中国大陆环境）
# 必须在模块级设置，确保sentence_transformers import时就生效
HF_MIRROR = "https://hf-mirror.com"
if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = HF_MIRROR
# 优先离线加载，避免每次启动都尝试连接huggingface.co超时
if not os.environ.get("HF_HUB_OFFLINE"):
    os.environ["HF_HUB_OFFLINE"] = "1"

# BGE中文模型查询前缀（官方推荐，提升检索效果）
BGE_QUERY_PREFIX = "为这个句子生成表示以检索相关文章："

# HuggingFace镜像（中国大陆无法直连huggingface.co）
HF_MIRROR = "https://hf-mirror.com"


class Embedder(ABC):
    """嵌入器抽象基类"""

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        """批量文本向量化

        Args:
            texts: 文本列表

        Returns:
            numpy数组 (n, dim)，L2归一化
        """
        ...

    @abstractmethod
    def embed_query(self, text: str) -> np.ndarray:
        """查询单条文本向量化（可能添加查询前缀）

        Args:
            text: 查询文本

        Returns:
            numpy数组 (dim,)，L2归一化
        """
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度"""
        ...


class SentenceEmbedder(Embedder):
    """主力嵌入器：sentence-transformers + BGE中文模型"""

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        """初始化

        Args:
            model_name: HuggingFace模型名
        """
        self._model_name = model_name
        self._model = None
        self._dim = 512  # bge-small-zh 默认维度

    def _ensure_model(self):
        """延迟加载模型（首次使用时加载，避免启动时卡顿）

        自动设置HuggingFace镜像（中国大陆环境）。
        优先从本地缓存加载，避免每次启动都尝试连接huggingface.co。
        """
        if self._model is not None:
            return

        try:
            from sentence_transformers import SentenceTransformer

            # 设置HuggingFace镜像（如果未配置）
            if not os.environ.get("HF_ENDPOINT"):
                os.environ["HF_ENDPOINT"] = HF_MIRROR
                logger.info(f"使用HuggingFace镜像: {HF_MIRROR}")

            # 优先离线加载：如果本地有缓存，直接用，不尝试连接huggingface.co
            # 这样避免了每次启动都超时等待的问题
            logger.info(f"加载嵌入模型: {self._model_name}...")

            try:
                # 先尝试离线加载（本地缓存）
                os.environ["HF_HUB_OFFLINE"] = "1"
                self._model = SentenceTransformer(self._model_name)
                # 兼容新旧版本API
                if hasattr(self._model, 'get_embedding_dimension'):
                    self._dim = self._model.get_embedding_dimension()
                else:
                    self._dim = self._model.get_sentence_embedding_dimension()
                logger.info(f"嵌入模型加载完成(离线): dim={self._dim}")
            except Exception as offline_err:
                # 离线加载失败，清除离线标志，尝试在线下载
                logger.info(f"离线加载失败({offline_err})，尝试在线下载...")
                os.environ.pop("HF_HUB_OFFLINE", None)
                self._model = SentenceTransformer(self._model_name)
                if hasattr(self._model, 'get_embedding_dimension'):
                    self._dim = self._model.get_embedding_dimension()
                else:
                    self._dim = self._model.get_sentence_embedding_dimension()
                logger.info(f"嵌入模型加载完成(在线): dim={self._dim}")

        except ImportError:
            raise ImportError(
                "sentence-transformers未安装。"
                "请运行: pip install sentence-transformers"
            )
        except Exception as e:
            raise RuntimeError(f"嵌入模型加载失败: {e}")

    def embed(self, texts: list[str]) -> np.ndarray:
        """批量文本向量化"""
        self._ensure_model()

        if not texts:
            return np.array([]).reshape(0, self._dim)

        # BGE模型：文档不需要前缀
        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,  # L2归一化
            show_progress_bar=len(texts) > 100,
            batch_size=32,
        )
        return np.array(embeddings, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        """查询向量化（添加BGE查询前缀）"""
        self._ensure_model()

        # BGE官方推荐：查询时添加指令前缀
        query_text = BGE_QUERY_PREFIX + text
        embedding = self._model.encode(
            [query_text],
            normalize_embeddings=True,
        )
        return np.array(embedding[0], dtype=np.float32)

    @property
    def dimension(self) -> int:
        return self._dim


class TFIDFEmbedder(Embedder):
    """备用嵌入器：jieba分词 + TF-IDF向量化

    当sentence-transformers不可用时的降级方案。
    使用jieba中文分词 + sklearn TfidfVectorizer。
    """

    def __init__(self, max_features: int = 5000):
        """初始化

        Args:
            max_features: TF-IDF最大特征数
        """
        self._max_features = max_features
        self._vectorizer = None
        self._dim = max_features
        self._is_fitted = False

    def _ensure_vectorizer(self):
        """延迟初始化TF-IDF向量化器"""
        if self._vectorizer is not None:
            return

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            import jieba

            # jieba分词器
            def tokenize(text):
                return list(jieba.cut(text))

            self._vectorizer = TfidfVectorizer(
                max_features=self._max_features,
                tokenizer=tokenize,
                token_pattern=None,  # 禁用默认token_pattern
                lowercase=False,       # 中文不需要小写化
            )
        except ImportError as e:
            raise ImportError(
                f"TF-IDF依赖缺失: {e}。"
                f"请运行: pip install jieba scikit-learn"
            )

    def embed(self, texts: list[str]) -> np.ndarray:
        """批量文本向量化"""
        self._ensure_vectorizer()

        if not texts:
            return np.array([]).reshape(0, self._dim)

        if not self._is_fitted:
            # 首次调用：fit_transform
            tfidf_matrix = self._vectorizer.fit_transform(texts)
            self._dim = tfidf_matrix.shape[1]
            self._is_fitted = True
        else:
            # 后续调用：transform
            tfidf_matrix = self._vectorizer.transform(texts)

        # L2归一化
        embeddings = tfidf_matrix.toarray().astype(np.float32)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1  # 避免除零
        embeddings = embeddings / norms

        return embeddings

    def embed_query(self, text: str) -> np.ndarray:
        """查询向量化"""
        self._ensure_vectorizer()

        if not self._is_fitted:
            # 未fit时无法transform，先对单条文本fit
            tfidf_matrix = self._vectorizer.fit_transform([text])
            self._dim = tfidf_matrix.shape[1]
            self._is_fitted = True
            embedding = tfidf_matrix.toarray()[0].astype(np.float32)
        else:
            tfidf_matrix = self._vectorizer.transform([text])
            embedding = tfidf_matrix.toarray()[0].astype(np.float32)

        # L2归一化
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        return embedding

    @property
    def dimension(self) -> int:
        return self._dim


def create_embedder(provider: str = "sentence", model_name: str = "") -> Embedder:
    """工厂方法：创建嵌入器

    Args:
        provider: "sentence" 或 "tfidf"
        model_name: sentence模式下的模型名

    Returns:
        Embedder实例
    """
    if provider == "sentence":
        try:
            model = model_name or "BAAI/bge-small-zh-v1.5"
            return SentenceEmbedder(model_name=model)
        except Exception as e:
            logger.warning(f"SentenceEmbedder创建失败: {e}，降级为TF-IDF")
            return TFIDFEmbedder()
    elif provider == "tfidf":
        return TFIDFEmbedder()
    else:
        logger.warning(f"未知嵌入器类型: {provider}，使用TF-IDF")
        return TFIDFEmbedder()
