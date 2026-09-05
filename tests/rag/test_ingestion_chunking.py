# -*- coding: utf-8 -*-
"""
RAG 分块修复 + TF-IDF索引兼容守卫 + jieba金融词典 回归测试（v0.8.9.0）

覆盖审查报告（docs/2026-09-05_RAG系统深度审查报告.md）四个问题：
- P2-A: _split_into_chunks 的 chunk_overlap 死参数 -> 实装（闭块携带前块尾部）
- P2-B: 单个超长段落直通成块（实测2831字，bge 512 token截断）-> 先按句硬切+硬顶550字
- P1-A: TF-IDF 词汇表不持久化，旧索引加载后重启维度错配 -> tfidf 恒判不兼容
- P2-C: jieba 零金融自定义词典 -> configs/jieba_userdict.txt + userdict.load_userdict

跑法：
  .venv/Scripts/python.exe -m pytest tests/rag/test_ingestion_chunking.py -q
  .venv/Scripts/python.exe tests/rag/test_ingestion_chunking.py   # 直跑
"""
import sys
from pathlib import Path

import pytest

# 直跑时的 sys.path 修复
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.rag.ingestion import _split_into_chunks, _split_by_sentences  # noqa: E402


def _para(n_chars: int, seed: int = 0) -> str:
    """生成 n_chars 字的无换行长段（含句号，段落尾部带唯一编号避免跨段误匹配）"""
    parts = []
    total = 0
    i = seed
    while True:
        s = f"这是第{i}个测试句子，用于验证分块行为不会丢失内容，段落{seed}尾。"
        if total + len(s) > n_chars + 60:
            break
        parts.append(s)
        total += len(s)
        i += 1
    return "".join(parts)[:n_chars]


class TestOverlapImplemented:
    """P2-A: chunk_overlap 实装"""

    def test_overlap_carried_between_chunks(self):
        # 多段落文本，每段 ~200 字，目标块 500 -> 至少两块
        text = "\n\n".join(_para(200, seed=i) for i in range(4))
        chunks = _split_into_chunks(text, chunk_size=500, chunk_overlap=50)
        assert len(chunks) >= 2
        # 相邻块间应存在重叠：前块的尾部片段应出现在下一块开头
        for prev, nxt in zip(chunks, chunks[1:]):
            tail = prev[-50:]
            assert tail[:10] in nxt, (
                f"chunk_overlap未实装: 前块尾部 [{tail[:10]}] 未出现在后块开头"
            )

    def test_overlap_zero_keeps_old_behavior(self):
        # overlap=0: 等价旧行为，后块不应以前块尾部开头（无人工重叠）
        text = "\n\n".join(_para(200, seed=i) for i in range(4))
        chunks = _split_into_chunks(text, chunk_size=500, chunk_overlap=0)
        assert len(chunks) >= 2
        for prev, nxt in zip(chunks, chunks[1:]):
            assert not nxt.startswith(prev[-50:]), "overlap=0不应携带前块尾部"


class TestHardCapAndHardSplit:
    """P2-B: 超长段硬切 + chunk_size+chunk_overlap 硬顶"""

    def test_2831_char_paragraph_regressed(self):
        # 复现审查实测：2831 字无换行单段，旧逻辑整段直通成一块
        text = _para(2831)
        chunks = _split_into_chunks(text, chunk_size=500, chunk_overlap=50)
        assert len(chunks) >= 3, "2831字必须被切开"
        for c in chunks:
            assert len(c) <= 550, f"块长{len(c)}超过硬顶550（bge 512 token 截断风险）"

    def test_hard_cap_never_exceeded(self):
        # 混合场景：长短段落交错，任何块不得超 chunk_size+chunk_overlap
        text = "\n\n".join(
            [_para(120, 1), _para(700, 2), _para(80, 3), _para(2831, 4), _para(300, 5)]
        )
        chunks = _split_into_chunks(text, chunk_size=500, chunk_overlap=50)
        for c in chunks:
            assert len(c) <= 550, f"块长{len(c)}超硬顶"

    def test_no_punctuation_long_sentence_no_loss(self):
        # 极端：无任何标点的超长句，滑窗硬切也不丢内容
        text = "测" * 800
        chunks = _split_by_sentences(text, chunk_size=500, min_chunk_size=100, chunk_overlap=50)
        assert len(chunks) >= 2
        joined = "".join(chunks)
        # 滑窗+重叠只会重复字符，不会丢失
        assert joined.count("测") >= 800


class TestNoContentLoss:
    """分块内容不丢失"""

    def test_all_sentences_present(self):
        text = "\n\n".join(_para(200, seed=i) for i in range(5))
        chunks = _split_into_chunks(text, chunk_size=500, chunk_overlap=50)
        for i in range(5):
            marker = f"这是第{i}个测试句子"
            found = any(marker in c for c in chunks)
            # 句子可能被块边界切开的情形已由按句切分排除；若找不到则丢失
            assert found, f"句子{i}在分块后丢失"


class TestTfidfNeverCompatible:
    """P1-A: TF-IDF 嵌入器对旧索引恒判不兼容（词汇表不持久化，重启维度必错配）"""

    def _bare_service(self, tmp_path, provider):
        from src.rag.service import RAGService
        from src.rag.embedding import create_embedder

        svc = RAGService.__new__(RAGService)
        svc._embedder = create_embedder(provider)
        svc.embedding_provider = provider
        svc.embedding_model = ""
        svc.index_dir = str(tmp_path)
        return svc

    def test_tfidf_without_metadata(self, tmp_path):
        svc = self._bare_service(tmp_path, "tfidf")
        # 无元数据文件也必须 False（旧逻辑此路径会返回 True -> 加载错配索引）
        assert svc._is_index_embedder_compatible() is False

    def test_tfidf_with_matching_metadata(self, tmp_path):
        import json
        svc = self._bare_service(tmp_path, "tfidf")
        (tmp_path / "embedder_metadata.json").write_text(
            json.dumps({"provider": "tfidf", "model": "max_features=5000"}),
            encoding="utf-8",
        )
        # 元数据字符串完全一致也必须 False：词汇表在内存，复用必错配
        assert svc._is_index_embedder_compatible() is False


class TestJiebaUserdict:
    """P2-C: 金融自定义词典生效"""

    def test_load_userdict_success(self):
        from src.rag.userdict import load_userdict

        assert load_userdict() is True

    def test_domain_words_not_split(self):
        import jieba
        from src.rag.userdict import load_userdict

        load_userdict()
        tokens = set(jieba.lcut("今天打板低吸，气宗剑宗笨总评分"))
        assert "打板" in tokens
        assert "低吸" in tokens
        assert "气宗" in tokens
        assert "剑宗" in tokens

    def test_keyword_retriever_triggers_userdict(self):
        # KeywordRetriever.__init__ 应触发 load_userdict（幂等，不抛异常即可）
        from src.rag.retrieval import KeywordRetriever

        kr = KeywordRetriever()
        assert kr is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
