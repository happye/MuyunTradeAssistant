# -*- coding: utf-8 -*-
"""
RAG 同章节多样性截断回归测试（v0.8.8.9）

问题：一个长章节（如456合刊）被切成几十块时，BM25/语义/RRF 融合的
top_k 名额可能被同一章节的分块全部占满，其他章节的优质内容进不来。

修复：HybridRetriever._apply_diversity —— 同一 metadata.chapter 在最终
结果中最多保留 diversity_max_per_chapter 条（默认2，0=关闭），三个检索
路径（keyword/semantic/hybrid）+ 层过滤路径的最终截断全部生效。

跑法：
  .venv/Scripts/python.exe -m pytest tests/rag/test_rag_diversity.py -q
  .venv/Scripts/python.exe tests/rag/test_rag_diversity.py   # 直跑
"""
import sys
from pathlib import Path

import pytest

# 直跑时的 sys.path 修复
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

np = pytest.importorskip("numpy")
pytest.importorskip("faiss")

from src.rag.embedding import create_embedder  # noqa: E402
from src.rag.store import FAISSVectorStore  # noqa: E402
from src.rag.retrieval import HybridRetriever  # noqa: E402
from src.rag.models import RAGDocument  # noqa: E402

BIG_CHAPTER = "认知革命第01章"
QUERY = "熊市怎么控制仓位"


def _make_docs():
    """构造单章霸榜场景：6 块同章节高匹配 + 4 块其他章节弱匹配"""
    docs = []
    for i in range(6):
        docs.append(RAGDocument(
            id=f"rz01_p{i + 1}",
            content=f"熊市仓位管理第{i + 1}讲：熊市里仓位控制是保命第一要务，"
                    f"必须降低仓位多留现金，用仓位控制应对熊市不确定性。",
            metadata={"chapter": BIG_CHAPTER, "series": "cognition",
                      "applicable_layers": ["Chat", "Decision"]},
        ))
    others = [
        ("rz02_p1", "认知革命第02章", "止损纪律：跌破止损位无条件离场，止损是成本不是错误。"),
        ("qp03_p1", "交易千篇第03章", "选股要看景气度，超景气赛道才有超额收益。"),
        ("rz04_p1", "认知革命第04章", "情绪管理：恐慌的时候不要做决策，冷静复盘再行动。"),
        ("qp05_p1", "交易千篇第05章", "趋势跟踪：只在上升趋势持股，跌破趋势线减仓观望。"),
    ]
    for did, chap, text in others:
        docs.append(RAGDocument(
            id=did, content=text,
            metadata={"chapter": chap, "series": "cognition",
                      "applicable_layers": ["Chat", "Decision"]},
        ))
    return docs


@pytest.fixture(scope="module")
def retriever():
    embedder = create_embedder("tfidf")  # 免下载模型，秒级建索引
    docs = _make_docs()
    store = FAISSVectorStore()
    store.add(docs, embedder.embed([d.content for d in docs]))
    r = HybridRetriever(embedder=embedder, store=store,
                        keyword_weight=0.4, semantic_weight=0.6,
                        diversity_max_per_chapter=2)
    r.index_documents(docs)
    return r


def _chapter_counts(result):
    from collections import Counter
    return Counter((d.metadata or {}).get("chapter") for d in result.documents)


# ---------- 三个检索路径 ----------

def test_semantic_diversity_caps_chapter(retriever):
    res = retriever.retrieve(QUERY, top_k=5, method="semantic")
    counts = _chapter_counts(res)
    assert len(res.documents) == 5, f"应返回5条，实际{len(res.documents)}"
    assert counts[BIG_CHAPTER] <= 2, (
        f"语义路径同章节仍霸榜：{dict(counts)}")


def test_keyword_diversity_caps_chapter(retriever):
    res = retriever.retrieve(QUERY, top_k=5, method="keyword")
    counts = _chapter_counts(res)
    assert len(res.documents) == 5, f"应返回5条，实际{len(res.documents)}"
    assert counts[BIG_CHAPTER] <= 2, (
        f"关键词路径同章节仍霸榜：{dict(counts)}")


def test_hybrid_diversity_caps_chapter(retriever):
    res = retriever.retrieve(QUERY, top_k=5, method="hybrid")
    counts = _chapter_counts(res)
    assert len(res.documents) == 5, f"应返回5条，实际{len(res.documents)}"
    assert counts[BIG_CHAPTER] <= 2, (
        f"混合路径同章节仍霸榜：{dict(counts)}")


# ---------- 层过滤路径（chat 实际调用链） ----------

def test_layer_path_final_diversity(retriever):
    res = retriever.retrieve(QUERY, top_k=5, method="hybrid", layer="Chat")
    counts = _chapter_counts(res)
    assert len(res.documents) == 5, f"层过滤+多样性后应返回5条，实际{len(res.documents)}"
    assert counts[BIG_CHAPTER] <= 2, (
        f"层过滤路径同章节仍霸榜：{dict(counts)}")


# ---------- 配置与边界 ----------

def test_disabled_cap_restores_old_behavior(retriever):
    """diversity_max_per_chapter=0 应恢复旧行为（单章可占满 top_k）"""
    embedder = create_embedder("tfidf")
    docs = _make_docs()
    store = FAISSVectorStore()
    store.add(docs, embedder.embed([d.content for d in docs]))
    r = HybridRetriever(embedder=embedder, store=store,
                        diversity_max_per_chapter=0)
    r.index_documents(docs)
    res = r.retrieve(QUERY, top_k=5, method="hybrid")
    counts = _chapter_counts(res)
    assert counts[BIG_CHAPTER] == 5, (
        f"cap=0 应复现旧行为（单章占满5条），实际 {dict(counts)}")


def test_backfill_preserves_count_when_all_same_chapter(retriever):
    """候选池全是同一章节时，回填机制保证结果数量不缩水（2+3回填=5）"""
    docs = [RAGDocument(
        id=f"rz01_p{i + 1}",
        content=f"熊市仓位控制第{i + 1}讲：熊市降低仓位多留现金。",
        metadata={"chapter": BIG_CHAPTER, "series": "cognition",
                  "applicable_layers": ["Chat"]},
    ) for i in range(6)]
    embedder = create_embedder("tfidf")
    store = FAISSVectorStore()
    store.add(docs, embedder.embed([d.content for d in docs]))
    r = HybridRetriever(embedder=embedder, store=store,
                        diversity_max_per_chapter=2)
    r.index_documents(docs)
    res = r.retrieve(QUERY, top_k=5, method="hybrid")
    assert len(res.documents) == 5, (
        f"同章节池回填后仍应返回5条，实际{len(res.documents)}（回填机制失效）")


def test_missing_chapter_metadata_not_affected(retriever):
    """无 chapter 元数据的块应视为独立项，不受多样性限制"""
    docs = [RAGDocument(id=f"ot_p{i + 1}", content=f"熊市仓位控制要点{i + 1}：降仓留现金。")
            for i in range(5)]
    embedder = create_embedder("tfidf")
    store = FAISSVectorStore()
    store.add(docs, embedder.embed([d.content for d in docs]))
    r = HybridRetriever(embedder=embedder, store=store,
                        diversity_max_per_chapter=2)
    r.index_documents(docs)
    res = r.retrieve(QUERY, top_k=5, method="hybrid")
    assert len(res.documents) == 5, (
        f"无chapter元数据不应被误过滤，实际返回{len(res.documents)}")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
