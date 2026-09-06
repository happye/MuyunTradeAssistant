# -*- coding: utf-8 -*-
"""
RAG 索引新鲜度与 doc_id 去重回归测试（2026-09-06 审查 P2-A/P2-B）：

P2-A（僵尸索引）：知识文件变化触发"重建"时，必须从空 store 开始。
   旧实现 _try_load_index 先把旧索引装进 store，重建分支只做 add 同 id 替换——
   已删除/改名文件的旧块作为僵尸向量残留并被 save 固化（探针实证）。

P2-B（批内 doc_id 重复）：同批 add 出现重复 id 时不得把全部向量塞进 FAISS
   （否则查询返回重复条目、get_doc_by_id 错位）；ingestion 层须把跨文件撞号
   暴露出来。

跑法：
  .venv/Scripts/python.exe -m pytest tests/rag/test_index_freshness.py -q
  .venv/Scripts/python.exe tests/rag/test_index_freshness.py   # 直跑
"""
import logging
import sys
from pathlib import Path

import numpy as np
import pytest

# 直跑时的 sys.path 修复
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.rag.service import RAGService  # noqa: E402
from src.rag.store import FAISSVectorStore  # noqa: E402
from src.rag.models import RAGDocument  # noqa: E402
from src.rag.ingestion import load_strategy_files  # noqa: E402


class FakeEmbedder:
    """确定性假嵌入器：按文本生成固定向量，绕开真实模型加载（零网络零显存）。

    进程内同一文本向量恒定，满足"两次 service 实例元数据一致 → 索引可加载"。
    """

    _dim = 8

    def __init__(self):
        self._model_name = "fake-embedder"

    def _ensure_model(self):
        return None

    def _vec(self, text: str) -> np.ndarray:
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        v = rng.random(self._dim).astype(np.float32)
        norm = np.linalg.norm(v)
        return v / norm if norm > 0 else v

    def embed(self, texts):
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        return np.vstack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)

    @property
    def dimension(self):
        return self._dim


def _make_service(tmp_path: Path) -> RAGService:
    cfg = {
        "enabled": True,
        "knowledge_dir": str(tmp_path / "kb"),
        "index_dir": str(tmp_path / "idx"),
        "chunk_size": 500,
        "chunk_overlap": 50,
        "default_top_k": 5,
        "retrieval_method": "hybrid",
        "embedding": {"provider": "sentence", "model": "fake"},
    }
    return RAGService(cfg)


@pytest.fixture()
def fake_embedder(monkeypatch):
    import src.rag.service as svc_mod

    emb = FakeEmbedder()
    monkeypatch.setattr(svc_mod, "create_embedder", lambda *a, **k: emb)
    return emb


def _write_kb(tmp_path: Path, files: dict):
    kb = tmp_path / "kb"
    kb.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (kb / name).write_text(text, encoding="utf-8")
    return kb


def test_rebuild_after_knowledge_change_drops_zombie_docs(tmp_path, monkeypatch, fake_embedder):
    """P2-A：删除知识文件后触发重建，旧文件的块不得残留在索引里。"""
    monkeypatch.setenv("MUYUN_TEST", "1")  # 占位：确保环境隔离可读
    kb = _write_kb(tmp_path, {
        "a文件.txt": "仓位管理是生存的根本。\n\n永远不要满仓，分批建仓。\n\n止损纪律高于一切观点。",
        "b文件.txt": "这一章的内容将被删除。\n\n删除后它的旧块不允许再出现在索引里。",
    })

    # 第一次：全量建索引（含 b 文件）
    svc1 = _make_service(tmp_path)
    assert svc1.initialize() is True
    ids_before = {d.id for d in svc1._store._docs}
    b_ids = {i for i in ids_before if "b文件" in i}
    assert b_ids, "前置失败：b 文件的块未入库"

    # 删除 b 文件后，新 service 实例应加载旧索引 -> 检测变化 -> 从空 store 重建
    (kb / "b文件.txt").unlink()
    svc2 = _make_service(tmp_path)
    assert svc2.initialize() is True

    ids_after = {d.id for d in svc2._store._docs}
    assert not (ids_after & b_ids), f"僵尸块残留: {sorted(ids_after & b_ids)}"
    # 数量与现存文件一致（a 文件的全部块），且与删除前不同
    a_docs = load_strategy_files(str(kb), 500, 50)
    assert len(ids_after) == len(a_docs)
    assert ids_after == {d.id for d in a_docs}


def test_store_add_dedups_same_batch_duplicate_ids(caplog):
    """P2-B：同批重复 doc_id 只保留最后出现的块，FAISS 不存重复向量。"""
    store = FAISSVectorStore()
    emb = np.eye(3, dtype=np.float32)
    docs = [
        RAGDocument(id="x", content="版本1", source_file="a.txt", metadata={}),
        RAGDocument(id="x", content="版本2", source_file="b.txt", metadata={}),
        RAGDocument(id="y", content="其他", source_file="c.txt", metadata={}),
    ]
    with caplog.at_level(logging.WARNING, logger="src.rag.store"):
        store.add(docs, emb)

    assert store.size == 2
    results = store.query(emb[0], top_k=5)
    x_hits = [doc_id for doc_id, _ in results if doc_id == "x"]
    assert len(x_hits) == 1, f"查询返回重复 doc_id: {results}"
    assert store.get_doc_by_id("x").content == "版本2"  # 最后写入者胜
    assert any("批内检测到" in r.message for r in caplog.records)


def test_ingestion_warns_on_cross_file_doc_id_collision(tmp_path, caplog):
    """P2-B 上游诊断：两个文件解析出同系列同章号时，摄入层必须告警。"""
    _write_kb(tmp_path, {
        "第10章上.txt": "上半章内容，讲突破买入的确认条件。",
        "第10章下.txt": "下半章内容，讲突破失败后的止损处理。",
    })
    with caplog.at_level(logging.WARNING, logger="src.rag.ingestion"):
        docs = load_strategy_files(str(tmp_path / "kb"), 500, 50)

    assert docs
    dup_ids = {d.id for d in docs}
    assert any(
        "doc_id 被多个文件共用" in r.message for r in caplog.records
    ), "同章号撞号未告警"
    # 且确实存在撞号（两个文件的同序号块 id 相同）
    seen = {}
    collided = False
    for d in docs:
        files = seen.setdefault(d.id, set())
        files.add(d.source_file)
        if len(files) > 1:
            collided = True
    assert collided and dup_ids


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
