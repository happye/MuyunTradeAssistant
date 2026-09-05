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
import time
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# HuggingFace下载源：官方源优先，镜像仅作为备用
# 必须在模块级设置，确保sentence_transformers import时就生效
HF_OFFICIAL = "https://huggingface.co"
HF_MIRROR = "https://hf-mirror.com"
# v0.8.7.6 审计修复 B12：镜像在前，与下方"国内优先镜像"注释一致
#（原官方源在前，与注释矛盾——国内环境下重试会先撞被墙的官方源）
HF_ENDPOINTS = (
    HF_MIRROR,
    HF_OFFICIAL,
)
if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = HF_MIRROR  # 国内优先镜像(huggingface.co被墙)
# Hugging Face Hub 默认可能长时间等待大文件连接；超时后才能切换下载源。
if not os.environ.get("HF_HUB_ETAG_TIMEOUT"):
    os.environ["HF_HUB_ETAG_TIMEOUT"] = "15"
if not os.environ.get("HF_HUB_DOWNLOAD_TIMEOUT"):
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "45"
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

    @staticmethod
    def _set_hf_endpoint(endpoint: str) -> None:
        """Update both the environment and Hub's already-imported endpoint."""
        os.environ["HF_ENDPOINT"] = endpoint
        try:
            import huggingface_hub.constants as hub_constants

            hub_constants.ENDPOINT = endpoint
        except ImportError:
            pass

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
                self._set_hf_endpoint(HF_MIRROR)
                logger.info(f"使用HuggingFace镜像源: {HF_MIRROR}")

            # 直接交给 Hugging Face Hub 处理缓存命中或在线下载。
            # 不设置全局 HF_HUB_OFFLINE，避免库导入时锁死联网状态。
            logger.info(f"加载嵌入模型: {self._model_name}...")

            try:
                # 本地已有缓存时会直接命中；缺失时由下面的多源下载逻辑处理。
                self._model = SentenceTransformer(self._model_name)
                # 兼容新旧版本API
                if hasattr(self._model, 'get_embedding_dimension'):
                    self._dim = self._model.get_embedding_dimension()
                else:
                    self._dim = self._model.get_sentence_embedding_dimension()
                logger.info(f"嵌入模型加载完成(离线): dim={self._dim}")
            except Exception as offline_err:
                # 缓存或当前下载源失败，进入多源在线重试。
                logger.info(f"模型加载失败({offline_err})，尝试在线下载...")
                retry_count = max(1, int(os.getenv("MUYUN_EMBEDDING_RETRIES", "3")))
                configured_endpoints = os.getenv("MUYUN_HF_ENDPOINTS", "")
                endpoints = tuple(
                    item.strip().rstrip("/")
                    for item in configured_endpoints.split(",")
                    if item.strip()
                ) or HF_ENDPOINTS
                # v0.8.7.6 审计修复 B12：初次加载已用当前 HF_ENDPOINT 失败，
                # 重试顺序把当前端点挪到最后（原顺序官方源在前，国内环境先撞被墙的
                # huggingface.co 白等 3×45s 才轮到镜像）
                current_ep = os.environ.get("HF_ENDPOINT", "")
                endpoints = tuple(e for e in endpoints if e != current_ep) + \
                    tuple(e for e in endpoints if e == current_ep)
                original_endpoint = current_ep  # 全部失败时恢复（防进程余生留在最后尝试的被墙源）
                last_error = offline_err
                loaded = False
                for endpoint in endpoints:
                    self._set_hf_endpoint(endpoint)
                    logger.info("尝试嵌入模型下载源: %s", endpoint)
                    for attempt in range(1, retry_count + 1):
                        try:
                            self._model = SentenceTransformer(self._model_name)
                            if hasattr(self._model, 'get_embedding_dimension'):
                                self._dim = self._model.get_embedding_dimension()
                            else:
                                self._dim = self._model.get_sentence_embedding_dimension()
                            logger.info(
                                "嵌入模型在线加载完成: source=%s dim=%s (第%d/%d次)",
                                endpoint,
                                self._dim,
                                attempt,
                                retry_count,
                            )
                            loaded = True
                            break
                        except Exception as online_err:
                            self._model = None
                            last_error = online_err
                            if attempt < retry_count:
                                delay = min(30, 2 ** (attempt - 1))
                                logger.warning(
                                    "嵌入模型下载失败，源=%s，第%d/%d次重试，%d秒后继续: %s",
                                    endpoint,
                                    attempt,
                                    retry_count,
                                    delay,
                                    online_err,
                                )
                                time.sleep(delay)
                    if loaded:
                        break
                    logger.warning("下载源不可用，切换下一个源: %s", endpoint)
                if not loaded:
                    # B12 修复：全部失败时恢复原端点，防 HF_ENDPOINT 永久污染
                    self._set_hf_endpoint(original_endpoint)
                    raise RuntimeError(
                        f"嵌入模型下载失败，已尝试{len(endpoints)}个源、每源{retry_count}次: {last_error}"
                    ) from last_error

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

            # P2-C: TF-IDF分词前确保金融自定义词典已加载（幂等）
            from src.rag.userdict import load_userdict
            load_userdict()

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
