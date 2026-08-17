"""RAG bootstrap should retry model downloads before falling back."""

import os
import sys
import types

import src.rag.service as service_module
import src.rag.embedding as embedding_module


class _BrokenSentenceEmbedder:
    _model_name = "BAAI/bge-small-zh-v1.5"

    def _ensure_model(self):
        raise OSError("offline model unavailable")


class _FakeTfidfEmbedder:
    _max_features = 5000


def test_sentence_model_failure_falls_back_to_tfidf():
    providers = []

    def fake_create_embedder(provider, model_name=""):
        providers.append(provider)
        if provider == "sentence":
            return _BrokenSentenceEmbedder()
        return _FakeTfidfEmbedder()

    original_create_embedder = service_module.create_embedder
    service_module.create_embedder = fake_create_embedder
    try:
        service = service_module.RAGService(
            {"enabled": True, "embedding": {"provider": "sentence"}}
        )
        selected = service._prepare_embedder()
    finally:
        service_module.create_embedder = original_create_embedder

    assert isinstance(selected, _FakeTfidfEmbedder)
    assert providers == ["sentence", "tfidf"]


def test_sentence_model_download_retries_until_success():
    attempts = []

    class FakeSentenceTransformer:
        def __init__(self, model_name):
            attempts.append((model_name, os.environ.get("HF_ENDPOINT")))
            if len(attempts) <= 4:
                raise OSError("temporary download failure")

        def get_sentence_embedding_dimension(self):
            return 512

    fake_module = types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer)
    original_module = sys.modules.get("sentence_transformers")
    original_sleep = embedding_module.time.sleep
    original_retries = os.environ.get("MUYUN_EMBEDDING_RETRIES")
    original_endpoints = os.environ.get("MUYUN_HF_ENDPOINTS")
    sys.modules["sentence_transformers"] = fake_module
    os.environ["MUYUN_EMBEDDING_RETRIES"] = "3"
    os.environ["MUYUN_HF_ENDPOINTS"] = "https://blocked.example,https://backup.example"
    embedding_module.time.sleep = lambda _seconds: None
    try:
        embedder = embedding_module.SentenceEmbedder()
        embedder._ensure_model()
    finally:
        embedding_module.time.sleep = original_sleep
        if original_retries is None:
            os.environ.pop("MUYUN_EMBEDDING_RETRIES", None)
        else:
            os.environ["MUYUN_EMBEDDING_RETRIES"] = original_retries
        if original_endpoints is None:
            os.environ.pop("MUYUN_HF_ENDPOINTS", None)
        else:
            os.environ["MUYUN_HF_ENDPOINTS"] = original_endpoints
        if original_module is None:
            sys.modules.pop("sentence_transformers", None)
        else:
            sys.modules["sentence_transformers"] = original_module

    assert len(attempts) == 5
    # 首源=hf-mirror.com：embedding.py 模块级默认（87047f8 国内镜像优先，
    # huggingface.co 被墙）；随后 MUYUN_HF_ENDPOINTS 配置源依次重试
    assert [endpoint for _, endpoint in attempts] == [
        "https://hf-mirror.com",
        "https://blocked.example",
        "https://blocked.example",
        "https://blocked.example",
        "https://backup.example",
    ], attempts
    assert embedder.dimension == 512


if __name__ == "__main__":
    test_sentence_model_failure_falls_back_to_tfidf()
    test_sentence_model_download_retries_until_success()
    print("RAG sentence failure fallback -> TF-IDF: PASS")
    print("RAG sentence model download retry -> PASS")