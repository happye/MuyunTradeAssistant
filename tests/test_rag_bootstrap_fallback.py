"""RAG bootstrap fallback tests.

聚焦验证：
1. Sentence 模型首次加载失败时，initialize() 自动降级到 TF-IDF 并成功完成。
2. 索引嵌入器元数据不兼容时，不复用旧索引而是重建。
"""

import json
import os
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.rag.service as rag_service_module
from src.rag.models import RAGDocument


class FailingSentenceEmbedder:
    def __init__(self, model_name: str = "BAAI/test"):
        self._model_name = model_name
        self._dim = 512

    def _ensure_model(self):
        raise RuntimeError("offline model unavailable")

    def embed(self, texts):
        raise AssertionError("failing sentence embedder should not be used")

    def embed_query(self, text):
        raise AssertionError("failing sentence embedder should not be used")

    @property
    def dimension(self):
        return self._dim


class ReadySentenceEmbedder:
    def __init__(self, model_name: str = "BAAI/test"):
        self._model_name = model_name
        self._dim = 4

    def _ensure_model(self):
        return None

    def embed(self, texts):
        return np.ones((len(texts), self._dim), dtype=np.float32)

    def embed_query(self, text):
        return np.ones(self._dim, dtype=np.float32)

    @property
    def dimension(self):
        return self._dim


class FakeTFIDFEmbedder:
    def __init__(self, max_features: int = 128):
        self._max_features = max_features
        self._dim = 3

    def embed(self, texts):
        return np.ones((len(texts), self._dim), dtype=np.float32)

    def embed_query(self, text):
        return np.ones(self._dim, dtype=np.float32)

    @property
    def dimension(self):
        return self._dim


class FakeStore:
    def __init__(self):
        self._docs = []
        self._load_result = False
        self.load_calls = 0

    def add(self, docs, embeddings):
        assert embeddings.shape[0] == len(docs)
        self._docs = list(docs)

    def query(self, embedding, top_k=5):
        return []

    def save(self, path):
        Path(path).mkdir(parents=True, exist_ok=True)

    def load(self, path):
        self.load_calls += 1
        return self._load_result

    def get_doc_by_id(self, doc_id):
        for doc in self._docs:
            if doc.id == doc_id:
                return doc
        return None

    @property
    def size(self):
        return len(self._docs)


class FakeRetriever:
    def __init__(self, embedder, store, keyword_weight=0.4, semantic_weight=0.6):
        self.embedder = embedder
        self.store = store
        self.docs = []

    def index_documents(self, docs):
        self.docs = list(docs)

    def retrieve(self, query, top_k, method):
        raise AssertionError("retrieve is not part of this bootstrap test")


def _make_docs():
    return [
        RAGDocument(
            id="doc-1",
            content="止损和仓位管理是交易系统的基础。",
            metadata={"chapter": "ch01"},
            source_file="knowledge.txt",
        )
    ]


def test_initialize_falls_back_to_tfidf_when_sentence_model_unavailable():
    with tempfile.TemporaryDirectory() as tmpdir:
        index_dir = Path(tmpdir) / "index"
        store = FakeStore()
        docs = _make_docs()

        with mock.patch.object(
            rag_service_module,
            "create_embedder",
            side_effect=[FailingSentenceEmbedder(), FakeTFIDFEmbedder()],
        ) as create_embedder_mock, mock.patch.object(
            rag_service_module,
            "create_vector_store",
            return_value=store,
        ), mock.patch.object(
            rag_service_module,
            "load_strategy_files",
            return_value=docs,
        ), mock.patch.object(
            rag_service_module,
            "HybridRetriever",
            FakeRetriever,
        ):
            service = rag_service_module.RAGService(
                {
                    "enabled": True,
                    "knowledge_dir": tmpdir,
                    "index_dir": str(index_dir),
                    "embedding": {
                        "provider": "sentence",
                        "model": "BAAI/test",
                    },
                }
            )

            assert service.initialize() is True
            assert service.is_available() is True
            assert service.doc_count == 1
            assert service._get_embedder_provider() == "tfidf"
            assert create_embedder_mock.call_count == 2

            with open(index_dir / "embedder_metadata.json", "r", encoding="utf-8") as f:
                metadata = json.load(f)

            assert metadata == {"provider": "tfidf", "model": "max_features=128"}
            print("✓ sentence 首次加载失败时自动降级到 TF-IDF 并完成初始化")


def test_incompatible_embedder_metadata_forces_rebuild():
    with tempfile.TemporaryDirectory() as tmpdir:
        index_dir = Path(tmpdir) / "index"
        index_dir.mkdir(parents=True, exist_ok=True)
        (index_dir / "embedder_metadata.json").write_text(
            json.dumps({"provider": "tfidf", "model": "max_features=128"}),
            encoding="utf-8",
        )

        store = FakeStore()
        store._load_result = True
        docs = _make_docs()

        with mock.patch.object(
            rag_service_module,
            "create_embedder",
            return_value=ReadySentenceEmbedder(),
        ), mock.patch.object(
            rag_service_module,
            "create_vector_store",
            return_value=store,
        ), mock.patch.object(
            rag_service_module,
            "load_strategy_files",
            return_value=docs,
        ), mock.patch.object(
            rag_service_module,
            "HybridRetriever",
            FakeRetriever,
        ):
            service = rag_service_module.RAGService(
                {
                    "enabled": True,
                    "knowledge_dir": tmpdir,
                    "index_dir": str(index_dir),
                    "embedding": {
                        "provider": "sentence",
                        "model": "BAAI/test",
                    },
                }
            )

            assert service.initialize() is True
            assert store.load_calls == 0, "不兼容元数据时不应复用旧索引"

            with open(index_dir / "embedder_metadata.json", "r", encoding="utf-8") as f:
                metadata = json.load(f)

            assert metadata == {"provider": "sentence", "model": "BAAI/test"}
            print("✓ 嵌入器元数据不兼容时会重建索引而不是静默复用")


if __name__ == "__main__":
    print("\n=== RAG bootstrap fallback tests ===\n")
    test_initialize_falls_back_to_tfidf_when_sentence_model_unavailable()
    test_incompatible_embedder_metadata_forces_rebuild()
    print("\n=== ALL PASS ===")