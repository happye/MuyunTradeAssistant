# -*- coding: utf-8 -*-
"""
RAG 二阶段重排回归测试（v0.8.9.0，P3-C）

背景：双塔向量检索（BGE bi-encoder）query/doc 独立编码，只比余弦相似度，
精度有天花板；交叉编码器（CrossEncoder）逐对打分排序质量更高。
本测试用假重排器（不依赖真实模型、秒级）锁死流程契约：
1. 启用重排时候选池按 candidate_multiplier 放大
2. 最终顺序由重排分决定，而非原召回序
3. 重排后仍受同章节多样性约束（与 v0.8.8.9 协同，不退化成单章霸榜）
4. 未启用/模型不可用时不改变任何原有行为（降级不抛异常）

跑法：
  .venv/Scripts/python.exe -m pytest tests/rag/test_reranker.py -q
  .venv/Scripts/python.exe tests/rag/test_reranker.py   # 直跑
"""
import sys
from pathlib import Path

import pytest

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


class _FakeReranker:
    """按预置 doc_id->分数 排序的假重排器，记录调用参数用于断言"""

    def __init__(self, scores, available=True):
        self.scores = scores
        self.available = available
        self.calls = []

    def is_available(self):
        return self.available

    def rerank(self, query, docs, top_k):
        self.calls.append({"query": query, "n_docs": len(docs), "top_k": top_k})
        ranked = sorted(
            docs, key=lambda d: self.scores.get(d.id, 0.0), reverse=True
        )
        return [(d, float(self.scores.get(d.id, 0.0))) for d in ranked][:top_k]


def _make_docs():
    """单章霸榜场景：6 块同章节 + 4 块其他章节"""
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
        ("rz02_p1", "认知革命第02章", "止损纪律：跌破止损位无条件离场。"),
        ("qp03_p1", "交易千篇第03章", "选股要看景气度，超景气赛道才有超额收益。"),
        ("rz04_p1", "认知革命第04章", "情绪管理：恐慌的时候不要做决策。"),
        ("qp05_p1", "交易千篇第05章", "趋势跟踪：只在上升趋势持股。"),
    ]
    for did, chap, text in others:
        docs.append(RAGDocument(
            id=did, content=text,
            metadata={"chapter": chap, "series": "cognition",
                      "applicable_layers": ["Chat", "Decision"]},
        ))
    return docs


@pytest.fixture(scope="module")
def docs():
    return _make_docs()


def _build(docs, **kwargs):
    embedder = create_embedder("tfidf")
    store = FAISSVectorStore()
    store.add(docs, embedder.embed([d.content for d in docs]))
    r = HybridRetriever(embedder=embedder, store=store, **kwargs)
    r.index_documents(docs)
    return r


def test_rerank_expands_candidate_pool(docs):
    """启用重排后候选池按 multiplier 放大（top_k=2 -> 交给重排 top_k*3 条）"""
    fake = _FakeReranker({})
    r = _build(docs, reranker=fake, rerank_candidate_multiplier=3)
    r.retrieve(QUERY, top_k=2)
    assert fake.calls, "重排器未被调用"
    call = fake.calls[0]
    # 候选池来自原始召回，至少应大于最终 top_k
    assert call["n_docs"] >= 2, f"候选池未放大: {call}"
    assert call["top_k"] == call["n_docs"], "应把全部候选交予重排后再截断"


def test_rerank_reorders_results(docs):
    """最终顺序由重排分决定：召回序末位的文档拿到高分后应被提到首位"""
    # 先取无重排的原始召回序（候选池大小 = top_k * multiplier）
    plain = _build(docs)
    base_ids = [d.id for d in plain.retrieve(QUERY, top_k=9).documents]
    assert len(base_ids) >= 5, base_ids
    tail_doc = base_ids[-1]

    fake = _FakeReranker({tail_doc: 99.0})
    r = _build(docs, reranker=fake, rerank_candidate_multiplier=3)
    result = r.retrieve(QUERY, top_k=3)
    assert result.method.endswith("+rerank"), result.method
    assert result.documents[0].id == tail_doc, (
        f"重排分最高的 {tail_doc} 未到首位: {[d.id for d in result.documents]}"
    )
    assert result.scores[0] == pytest.approx(99.0)


def test_rerank_respects_diversity(docs):
    """重排把同章节挤到头部时，多样性截断仍生效（不退化成单章霸榜）"""
    # 6 块同章节全部给高分，重排后应霸榜；多样性上限 2 必须拦住
    scores = {f"rz01_p{i + 1}": 10.0 - i * 0.1 for i in range(6)}
    fake = _FakeReranker(scores)
    r = _build(docs, reranker=fake, rerank_candidate_multiplier=3,
               diversity_max_per_chapter=2)
    result = r.retrieve(QUERY, top_k=5)
    big = [d for d in result.documents
           if (d.metadata or {}).get("chapter") == BIG_CHAPTER]
    assert len(big) <= 2, f"重排后同章节占了{len(big)}条，多样性失效"


def test_rerank_disabled_keeps_original(docs):
    """未启用重排时：method 不变、结果与非重排版本一致"""
    plain = _build(docs)
    base = plain.retrieve(QUERY, top_k=3)
    assert base.method == "hybrid", base.method
    assert [d.id for d in base.documents]


def test_reranker_unavailable_degrades(docs):
    """模型不可用时：退化为原召回序，不抛异常"""
    fake = _FakeReranker({}, available=False)
    r = _build(docs, reranker=fake, rerank_candidate_multiplier=3)
    result = r.retrieve(QUERY, top_k=3)
    assert result.method == "hybrid", f"不可用时不应走重排: {result.method}"
    assert not fake.calls, "重排器不可用时不应被调用"
    assert len(result.documents) == 3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
