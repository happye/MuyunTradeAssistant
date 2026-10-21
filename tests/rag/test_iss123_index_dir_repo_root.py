"""ISS-123：生产 RAG 索引/知识目录相对路径锚仓库根，不随启动 cwd 漂移。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.config import PROJECT_ROOT, resolve_under_root
from src.rag.service import RAGService


def test_resolve_under_root_relative_anchors_repo():
    got = resolve_under_root("./knowledge/index")
    assert got == (PROJECT_ROOT / "knowledge" / "index").resolve()


def test_resolve_under_root_absolute_passthrough(tmp_path: Path):
    abs_dir = (tmp_path / "custom_idx").resolve()
    abs_dir.mkdir()
    assert resolve_under_root(str(abs_dir)) == abs_dir


def test_rag_service_relative_dirs_ignore_foreign_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """从仓库外 cwd 启动时，相对 index_dir/knowledge_dir 仍锚仓库根。

    BUG-IDX 会话隔离可能把仓库索引改指临时副本；两种结果都证明未跟随 foreign cwd。
    """
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()
    monkeypatch.chdir(foreign)

    svc = RAGService(
        {
            "enabled": True,
            "knowledge_dir": "./投资策略（持续更新）",
            "index_dir": "./knowledge/index",
        }
    )
    real_index = (PROJECT_ROOT / "knowledge" / "index").resolve()
    got_index = Path(svc.index_dir).resolve()
    assert got_index == real_index or "muyun-test-rag-index-" in str(got_index)
    assert Path(svc.knowledge_dir).resolve() == (
        PROJECT_ROOT / "投资策略（持续更新）"
    ).resolve()
    assert not (foreign / "knowledge").exists()
    assert got_index != (foreign / "knowledge" / "index").resolve()


def test_rag_service_absolute_index_dir_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    foreign = tmp_path / "cwd"
    foreign.mkdir()
    monkeypatch.chdir(foreign)
    abs_idx = (tmp_path / "abs_index").resolve()
    abs_idx.mkdir()
    svc = RAGService(
        {
            "enabled": True,
            "knowledge_dir": str(tmp_path / "kb"),
            "index_dir": str(abs_idx),
        }
    )
    assert Path(svc.index_dir).resolve() == abs_idx


def test_faiss_path_call_unicode_parent_uses_basename(tmp_path: Path):
    """含非 ASCII 父路径时，只把 ASCII 文件名交给回调（faiss Windows 约束）。"""
    from src.rag.store import _faiss_path_call

    uni = tmp_path / "索引目录"
    uni.mkdir()
    target = uni / "index.faiss"
    target.write_bytes(b"x")
    seen = {}

    def _fn(name: str):
        seen["name"] = name
        seen["cwd"] = Path.cwd().resolve()
        return "ok"

    assert _faiss_path_call(target, _fn) == "ok"
    assert seen["name"] == "index.faiss"
    assert seen["cwd"] == uni.resolve()


def test_faiss_path_call_ascii_passthrough():
    from src.rag.store import _faiss_path_call

    ascii_root = (
        Path("C:/muyun_iss123_ascii_tmp")
        if os.name == "nt"
        else Path("/tmp/muyun_iss123_ascii_tmp")
    )
    ascii_root.mkdir(parents=True, exist_ok=True)
    target = ascii_root / "index.faiss"
    try:
        target.write_bytes(b"x")
        seen = {}

        def _fn(name: str):
            seen["name"] = name
            return 1

        assert _faiss_path_call(target, _fn) == 1
        assert seen["name"] == str(target)
    finally:
        try:
            target.unlink(missing_ok=True)
            ascii_root.rmdir()
        except OSError:
            pass
