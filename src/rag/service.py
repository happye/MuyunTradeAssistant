"""RAG Service - 统一入口 (v0.8.1)

整合 ingestion → embedding → store → retrieval → context 的完整流程。

使用方式：
  service = RAGService(config)
  service.initialize()  # 加载/构建索引
  result = service.retrieve("止损怎么设")
  context = service.get_context("止损怎么设", target="chat")

v0.8.1 增量索引：
  - 检测策略文件变化（文件数/修改时间）
  - 仅在文件变化时重建索引，否则加载缓存
  - 支持 force_rebuild 强制重建
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from src.rag.models import RAGDocument, RetrievalResult
from src.rag.ingestion import load_strategy_files
from src.rag.embedding import create_embedder, Embedder
from src.rag.store import create_vector_store, VectorStore
from src.rag.retrieval import HybridRetriever
from src.rag.context import build_chat_context, build_event_context, build_modifier_context

logger = logging.getLogger(__name__)

EMBEDDER_METADATA_FILE = "embedder_metadata.json"


class RAGService:
    """RAG服务 - 统一入口

    管理 RAG 子系统的完整生命周期：
    1. 加载策略文件（ingestion）
    2. 向量化（embedding）
    3. 索引（store）
    4. 检索（retrieval）
    5. 上下文构建（context）
    """

    def __init__(self, config: dict):
        """初始化RAG服务

        Args:
            config: settings.yaml中的rag配置节
        """
        self.config = config
        self.enabled = config.get("enabled", False)
        self._initialized = False  # 初始化标志（必须在enabled检查前设置）

        if not self.enabled:
            logger.info("RAG服务未启用")
            return

        # 配置参数
        self.knowledge_dir = config.get("knowledge_dir", "./投资策略（持续更新）")
        self.index_dir = config.get("index_dir", "./knowledge/index")
        self.chunk_size = config.get("chunk_size", 500)
        self.chunk_overlap = config.get("chunk_overlap", 50)
        self.default_top_k = config.get("default_top_k", 5)
        self.retrieval_method = config.get("retrieval_method", "hybrid")

        hybrid_cfg = config.get("hybrid_weights", {})
        self.keyword_weight = hybrid_cfg.get("keyword", 0.4)
        self.semantic_weight = hybrid_cfg.get("semantic", 0.6)

        embed_cfg = config.get("embedding", {})
        self.embedding_provider = embed_cfg.get("provider", "sentence")
        self.embedding_model = embed_cfg.get("model", "BAAI/bge-small-zh-v1.5")

        # 延迟初始化的组件
        self._embedder: Optional[Embedder] = None
        self._store: Optional[VectorStore] = None
        self._retriever: Optional[HybridRetriever] = None
        self._initialized = False

    def initialize(self, force_rebuild: bool = False) -> bool:
        """初始化RAG服务（加载或构建索引）

        增量索引逻辑（v0.8.1）：
        1. 尝试加载已有索引
        2. 检查知识文件是否有变化（文件数/修改时间）
        3. 有变化则自动重建索引

        Args:
            force_rebuild: 是否强制重建索引

        Returns:
            是否初始化成功
        """
        if not self.enabled:
            return False

        if self._initialized and not force_rebuild:
            return True

        try:
            # 1. 创建嵌入器和存储
            self._embedder = self._prepare_embedder()
            self._store = create_vector_store(prefer_faiss=True)

            # 2. 尝试加载已有索引
            if not force_rebuild and self._try_load_index():
                # 索引加载成功，检查是否需要增量重建
                if self._check_knowledge_changed():
                    logger.info("检测到策略文件变化，自动重建索引...")
                else:
                    # 无变化，直接使用缓存
                    self._create_retriever()
                    self._initialized = True
                    # 确保指纹文件存在（首次升级到v0.8.1时可能缺失）
                    self._save_knowledge_fingerprint()
                    return True

            # 3. 构建新索引
            logger.info("开始构建RAG索引...")
            docs = load_strategy_files(
                self.knowledge_dir, self.chunk_size, self.chunk_overlap
            )

            if not docs:
                logger.warning("无策略文档可索引，RAG服务不可用")
                return False

            # 4. 向量化
            texts = [doc.content for doc in docs]
            logger.info(f"开始向量化 {len(texts)} 个文档...")
            embeddings = self._embedder.embed(texts)

            # 5. 添加到向量存储
            self._store.add(docs, embeddings)

            # 6. 创建检索器
            self._create_retriever(docs)

            # 7. 持久化索引
            self._store.save(self.index_dir)
            self._save_embedder_metadata()

            # 8. 保存文件指纹（用于增量检测）
            self._save_knowledge_fingerprint()

            self._initialized = True
            logger.info(f"RAG服务初始化完成: {len(docs)}个文档块")
            return True

        except Exception as e:
            logger.error(f"RAG服务初始化失败: {e}")
            return False

    def _prepare_embedder(self) -> Embedder:
        """创建嵌入器；sentence 模型不可用时自动降级为 TF-IDF。"""
        embedder = create_embedder(self.embedding_provider, self.embedding_model)
        if self.embedding_provider == "sentence" and hasattr(embedder, "_ensure_model"):
            try:
                embedder._ensure_model()
            except Exception as exc:
                logger.warning("句向量模型不可用，RAG自动降级为TF-IDF: %s", exc)
                embedder = create_embedder("tfidf")
        return embedder

    def _get_embedder_metadata(self) -> dict:
        """返回当前嵌入器标识，防止不同向量维度复用旧索引。"""
        embedder = self._embedder
        if embedder is not None and hasattr(embedder, "_ensure_model"):
            return {
                "provider": "sentence",
                "model": getattr(embedder, "_model_name", self.embedding_model),
            }
        if embedder is not None and hasattr(embedder, "_max_features"):
            return {
                "provider": "tfidf",
                "model": f"max_features={getattr(embedder, '_max_features', 5000)}",
            }
        return {"provider": self.embedding_provider, "model": self.embedding_model}

    def _embedder_metadata_path(self) -> Path:
        return Path(self.index_dir) / EMBEDDER_METADATA_FILE

    def _save_embedder_metadata(self) -> None:
        try:
            index_dir = Path(self.index_dir)
            index_dir.mkdir(parents=True, exist_ok=True)
            with open(self._embedder_metadata_path(), "w", encoding="utf-8") as handle:
                json.dump(self._get_embedder_metadata(), handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("保存RAG嵌入器元数据失败: %s", exc)

    def _is_index_embedder_compatible(self) -> bool:
        metadata_path = self._embedder_metadata_path()
        if not metadata_path.exists():
            # 旧索引没有元数据；sentence 降级时必须重建，避免维度或算法不匹配。
            return not (
                self.embedding_provider == "sentence"
                and self._get_embedder_metadata().get("provider") == "tfidf"
            )
        try:
            with open(metadata_path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
        except Exception:
            return False
        return saved == self._get_embedder_metadata()

    def _try_load_index(self) -> bool:
        """尝试加载已有索引

        Returns:
            是否成功加载
        """
        if not self._is_index_embedder_compatible():
            logger.info("RAG索引与当前嵌入器不兼容，重新构建索引")
            return False
        if self._store.load(self.index_dir):
            logger.info(f"RAG索引加载成功: {self._store.size}个文档")
            return True
        return False

    def _create_retriever(self, docs: list[RAGDocument] = None):
        """创建混合检索器"""
        self._retriever = HybridRetriever(
            embedder=self._embedder,
            store=self._store,
            keyword_weight=self.keyword_weight,
            semantic_weight=self.semantic_weight,
        )
        # 关键词索引需要文档列表
        if docs:
            self._retriever.index_documents(docs)
        else:
            # 从store中获取文档列表构建关键词索引
            if hasattr(self._store, '_docs') and self._store._docs:
                self._retriever.index_documents(self._store._docs)

    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        method: Optional[str] = None,
    ) -> RetrievalResult:
        """执行检索

        Args:
            query: 查询文本
            top_k: 返回数量（默认使用配置值）
            method: 检索方式（默认使用配置值）

        Returns:
            RetrievalResult
        """
        if not self._initialized or self._retriever is None:
            return RetrievalResult(query=query)

        k = top_k or self.default_top_k
        m = method or self.retrieval_method

        return self._retriever.retrieve(query, k, m)

    def get_context(
        self,
        query: str,
        target: str = "chat",
        top_k: Optional[int] = None,
        max_length: int = 2000,
        **kwargs,
    ) -> str:
        """获取格式化的策略知识上下文

        Args:
            query: 查询文本
            target: 目标 "chat"/"event"/"modifier"
            top_k: 检索数量
            max_length: 最大上下文长度
            **kwargs: 额外参数（event_type, stock_name等）

        Returns:
            格式化的策略知识上下文文本
        """
        result = self.retrieve(query, top_k)

        if not result.documents:
            return ""

        if target == "event":
            return build_event_context(
                result,
                event_type=kwargs.get("event_type", ""),
                max_length=max_length,
            )
        elif target == "modifier":
            return build_modifier_context(
                result,
                stock_name=kwargs.get("stock_name", ""),
                max_length=max_length,
            )
        else:  # chat
            return build_chat_context(result, max_length=max_length)

    def is_available(self) -> bool:
        """检查RAG服务是否可用"""
        return self.enabled and self._initialized

    @property
    def doc_count(self) -> int:
        """当前索引文档数"""
        if self._store:
            return self._store.size
        return 0

    # ===== 增量索引方法（v0.8.1） =====

    def _get_knowledge_fingerprint(self) -> dict:
        """计算知识目录的文件指纹

        记录每个txt文件的修改时间，用于增量检测。

        Returns:
            {filename: mtime} 的字典
        """
        fingerprint = {}
        knowledge_path = Path(self.knowledge_dir)
        if not knowledge_path.exists():
            return fingerprint

        # v0.8.6.2: rglob 递归 + 同时跟踪 .txt / .md
        for f in list(knowledge_path.rglob("*.txt")) + list(knowledge_path.rglob("*.md")):
            try:
                # 用相对路径做 key，避免子目录同名冲突
                key = str(f.relative_to(knowledge_path))
                fingerprint[key] = os.path.getmtime(f)
            except (OSError, ValueError):
                continue

        return fingerprint

    def _save_knowledge_fingerprint(self):
        """保存知识文件指纹到索引目录"""
        import json
        fingerprint = self._get_knowledge_fingerprint()
        if not fingerprint:
            return

        try:
            index_path = Path(self.index_dir)
            index_path.mkdir(parents=True, exist_ok=True)
            with open(index_path / "fingerprint.json", 'w', encoding='utf-8') as f:
                json.dump(fingerprint, f)
            logger.debug(f"知识文件指纹已保存: {len(fingerprint)}个文件")
        except Exception as e:
            logger.warning(f"保存知识文件指纹失败: {e}")

    def _check_knowledge_changed(self) -> bool:
        """检查知识文件是否有变化

        比较当前文件指纹与保存的指纹，判断是否需要重建索引。
        如果fingerprint.json不存在（首次升级到v0.8.1），视为无变化，
        因为索引已成功加载说明之前构建过。

        Returns:
            True=文件有变化需要重建，False=无变化可使用缓存
        """
        import json

        fingerprint_path = Path(self.index_dir) / "fingerprint.json"
        if not fingerprint_path.exists():
            # 首次升级：没有fingerprint记录，但索引已成功加载
            # 视为无变化，后续会在_save_knowledge_fingerprint中创建
            logger.debug("无文件指纹记录（首次v0.8.1升级），使用已加载索引")
            return False

        try:
            with open(fingerprint_path, 'r', encoding='utf-8') as f:
                saved_fingerprint = json.load(f)
        except Exception:
            return True

        current_fingerprint = self._get_knowledge_fingerprint()

        # 文件数变化
        if set(saved_fingerprint.keys()) != set(current_fingerprint.keys()):
            new_files = set(current_fingerprint.keys()) - set(saved_fingerprint.keys())
            removed_files = set(saved_fingerprint.keys()) - set(current_fingerprint.keys())
            if new_files:
                logger.info(f"新增策略文件: {new_files}")
            if removed_files:
                logger.info(f"删除策略文件: {removed_files}")
            return True

        # 文件修改时间变化
        changed_files = []
        for name, mtime in current_fingerprint.items():
            if saved_fingerprint.get(name) != mtime:
                changed_files.append(name)

        if changed_files:
            logger.info(f"策略文件已修改: {changed_files}")
            return True

        return False


# v0.8.6.2: 模块级 singleton 工厂（懒加载，失败返回 None 不抛）
_rag_service_singleton = None


def get_rag_service(config: dict = None, auto_initialize: bool = True):
    """获取 RAGService 单例（懒加载）。

    Args:
        config: 可选配置；None 时自动从 settings.yaml 加载
        auto_initialize: True 时自动调 initialize()（首次会构建索引，较慢）

    Returns:
        RAGService 实例，或 None（初始化失败时）
    """
    global _rag_service_singleton
    if _rag_service_singleton is not None:
        return _rag_service_singleton
    try:
        if config is None:
            from src.cli.main import load_config
            config = load_config()
        # RAGService 期望 rag 子节（含 enabled/knowledge_dir 等），非整个 settings.yaml
        rag_cfg = config.get("rag", config) if isinstance(config, dict) else {}
        svc = RAGService(rag_cfg)
        if auto_initialize:
            svc.initialize()
        if svc.is_available():
            _rag_service_singleton = svc
            return svc
        logger.warning("RAG service 初始化后不可用（可能配置未启用或嵌入模型加载失败）")
        return None
    except Exception as e:
        logger.warning(f"RAG service 初始化失败: {type(e).__name__}: {e}")
        return None
