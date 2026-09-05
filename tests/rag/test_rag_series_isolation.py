# -*- coding: utf-8 -*-
"""
RAG 系列隔离回归测试 —— 锁死 73 篇新文章融入后的三个不变量：

1. doc_id 全局唯一（金融战争 / 认知革命 / 交易千篇 章号不得撞车）
2. chapter 显示名无歧义（AI 引用来源必须能区分系列）
3. series 元数据正确标注 + 系列对应的分类/层级映射正确

背景：2026-09-05 新增「认知革命 43 篇 + 我的金融战争 30 篇」后，
      ingestion._get_chapter_number() 对不同系列的「第N章」解析出相同章号，
      导致 doc_id 冲突 91 个 / 182 块，store._id_to_idx 字典覆盖后被静默屏蔽。

跑法：
  .venv/Scripts/python.exe -m pytest tests/rag/test_rag_series_isolation.py -q
  .venv/Scripts/python.exe tests/rag/test_rag_series_isolation.py   # 直跑
"""
import os
import sys
from collections import defaultdict
from pathlib import Path

import pytest

# 直跑时的 sys.path 修复
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.rag.ingestion import load_strategy_files  # noqa: E402

KNOWLEDGE_DIR = _ROOT / "投资策略（持续更新）"

pytestmark = pytest.mark.skipif(
    not KNOWLEDGE_DIR.exists(),
    reason="知识目录不存在，跳过（依赖真实策略文件）",
)


@pytest.fixture(scope="module")
def docs():
    return load_strategy_files(str(KNOWLEDGE_DIR), 500, 50)


# ---------- 不变量 1：doc_id 全局唯一 ----------

def test_doc_id_globally_unique(docs):
    """同 ID 会导致 store._id_to_idx 覆盖 → 文档被静默屏蔽（P0）"""
    id_map = defaultdict(list)
    for d in docs:
        id_map[d.id].append(Path(d.source_file).name)
    dup = {k: v for k, v in id_map.items() if len(v) > 1}
    assert not dup, (
        f"doc_id 冲突 {len(dup)} 个（受影响 {sum(len(v) for v in dup.values())} 块）。"
        f"样例：{dict(list(sorted(dup.items()))[:3])}"
    )


def test_chapter_display_name_unambiguous(docs):
    """chapter 用于 context.py 生成「【第N章】」引用来源，跨系列撞名会张冠李戴"""
    ch_map = defaultdict(set)
    for d in docs:
        ch_map[d.metadata.get("chapter", "")].add(Path(d.source_file).name)
    amb = {k: v for k, v in ch_map.items() if len(v) > 1}
    assert not amb, (
        f"chapter 显示名歧义 {len(amb)} 处。样例："
        f"{ {k: sorted(v)[:2] for k, v in list(sorted(amb.items()))[:3]} }"
    )


# ---------- 不变量 2：series 元数据 ----------

def test_series_metadata_present(docs):
    """每个文档块必须带 series 字段，供检索层做系列过滤"""
    missing = [d.id for d in docs if not d.metadata.get("series")]
    assert not missing, f"{len(missing)} 个块缺 series 字段，样例：{missing[:5]}"


def test_series_values_are_known(docs):
    """series 取值必须落在约定集合内，防止新系列悄悄混入未分类"""
    ALLOWED = {"cognition", "war", "qianpian", "outline", "other"}
    bad = {d.metadata.get("series") for d in docs} - ALLOWED
    assert not bad, f"未知 series 取值：{bad}"


# ---------- 不变量 3：系列 → 分类/层级映射 ----------

def _blocks_of(docs, series):
    return [d for d in docs if d.metadata.get("series") == series]


def test_war_series_chat_only(docs):
    """金融战争是纪实小说，applicable_layers 必须限定为 Chat，不进决策链路"""
    war = _blocks_of(docs, "war")
    if not war:
        pytest.skip("知识目录无金融战争系列")
    bad = [d.id for d in war if d.metadata.get("applicable_layers") != ["Chat"]]
    assert not bad, f"金融战争有 {len(bad)} 块 applicable_layers 不是 ['Chat']，样例：{bad[:5]}"


def test_cognition_66_83_is_psychology(docs):
    """认知革命 66-83 章是 51-65 心理篇的延续，必须归 psychology 而非 technical"""
    psych = [
        d for d in docs
        if d.metadata.get("series") == "cognition"
        and isinstance(d.metadata.get("chapter_num"), int)
        and 66 <= d.metadata["chapter_num"] <= 83
    ]
    if not psych:
        pytest.skip("知识目录无 66-83 章")
    bad = [d.metadata.get("chapter_num") for d in psych
           if d.metadata.get("category") != "psychology"]
    assert not bad, f"66-83 章有 {len(bad)} 块未归 psychology：{sorted(set(bad))}"


def test_qianpian_not_default_market_analysis(docs):
    """交易千篇不得全部落进默认 market_analysis（84-99 情绪管理应为 psychology）"""
    qp = _blocks_of(docs, "qianpian")
    if not qp:
        pytest.skip("知识目录无交易千篇系列")
    cats = {d.metadata.get("category") for d in qp}
    assert len(cats) > 1, (
        f"交易千篇 {len(qp)} 块全部归为单一分类 {cats}，"
        f"说明按主题细分未生效（84-99 情绪管理应 psychology、101-105 止损应 risk_management）"
    )


# ---------- 不变量 4：无内容被静默丢弃 ----------

def test_no_block_silently_shadowed(docs):
    """兜底断言：重建后每个源文件的块都应能通过 id 取回（模拟 store 字典行为）"""
    id_to_doc = {d.id: d for d in docs}  # 复刻 store._id_to_idx 的覆盖语义
    lost = [d for d in docs if id_to_doc[d.id] is not d]
    assert not lost, (
        f"{len(lost)} 个块会因 id 覆盖被静默屏蔽，样例："
        f"{[(d.id, Path(d.source_file).name[:20]) for d in lost[:5]]}"
    )


# ---------- 不变量 5：store 幂等 + 层级过滤 ----------

def test_store_add_is_idempotent():
    """重复 add 同一批文档不得累积。

    历史事故：docs.json 存了 1129 块但唯一内容仅 417 块（306 组各存 3 份），
    语义检索 top_k 被同一段内容占掉多个名额。
    """
    np = pytest.importorskip("numpy")
    pytest.importorskip("faiss")
    from src.rag.store import FAISSVectorStore
    from src.rag.models import RAGDocument

    store = FAISSVectorStore()
    docs = [RAGDocument(id=f"rz01_p{i}", content=f"内容{i}") for i in range(3)]
    emb = np.random.rand(3, 8).astype(np.float32)

    store.add(docs, emb)
    first = store.size
    store.add(docs, emb)  # 完全重复
    store.add(docs, emb)

    assert store.size == first, f"重复 add 后文档数从 {first} 涨到 {store.size}（应保持不变）"
    assert store._index.ntotal == len(store._docs), (
        f"faiss 向量数({store._index.ntotal})与文档数({len(store._docs)})不一致，"
        f"load() 会拒绝加载并触发重建"
    )


def test_layer_filter_excludes_war_from_decision(docs):
    """纪实小说不得进入 Decision 链路（用户裁决：金融战争仅 Chat 可召回）"""
    np = pytest.importorskip("numpy")
    pytest.importorskip("faiss")
    from src.rag.embedding import create_embedder
    from src.rag.store import FAISSVectorStore
    from src.rag.retrieval import HybridRetriever

    if not any(d.metadata.get("series") == "war" for d in docs):
        pytest.skip("知识目录无金融战争系列")

    embedder = create_embedder("tfidf")  # 免下载模型，秒级建索引
    vecs = embedder.embed([d.content for d in docs])

    store = FAISSVectorStore()
    store.add(docs, vecs)
    retriever = HybridRetriever(embedder=embedder, store=store,
                                keyword_weight=0.4, semantic_weight=0.6)
    retriever.index_documents(docs)

    # Chat 层：所有系列都可召回（小说案例供对话参考）
    chat_res = retriever.retrieve("止损怎么设置", top_k=5, method="hybrid", layer="Chat")
    assert chat_res.documents, "Chat 层过滤后不应为空（所有块的 layers 都含 Chat）"

    # Decision 层：金融战争必须零泄漏
    for q in ("止损怎么设置", "情绪失控怎么办", "如何判断趋势反转"):
        res = retriever.retrieve(q, top_k=5, method="hybrid", layer="Decision")
        leaked = [d.id for d in res.documents if d.metadata.get("series") == "war"]
        assert not leaked, f"Decision 层泄漏金融战争内容（query={q}）：{leaked}"


def test_service_get_context_passes_layer(monkeypatch):
    """service.get_context 必须把 target 映射成 layer 透传给 retrieve。

    回归事故：改 HybridRetriever.retrieve 加了 layer 参数后，
    service.get_context 调 self.retrieve(..., layer=) 但 RAGService.retrieve
    未同步加该参数 → TypeError。测试只覆盖 retriever 层时会漏掉这条调用链。
    """
    from src.rag.service import RAGService
    from src.rag.models import RetrievalResult

    captured = {}

    class _MockRetriever:
        def retrieve(self, query, top_k, method, layer=None):
            captured["layer"] = layer
            return RetrievalResult(query=query)

    svc = RAGService({"enabled": True, "default_top_k": 5, "retrieval_method": "hybrid"})
    svc._retriever = _MockRetriever()
    svc._initialized = True

    for target, expect in [("chat", "Chat"), ("event", "Decision"),
                           ("modifier", "AI_Modifier"), ("unknown_target", None)]:
        svc.get_context("止损怎么设", target=target)
        assert captured["layer"] == expect, (
            f"target={target} 应映射为 layer={expect}，实际 {captured['layer']}"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
