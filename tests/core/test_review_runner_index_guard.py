"""Narrow read permission for the repository's protected-index fixtures."""
import os
from types import SimpleNamespace

import pytest

from plan.fusion.iteration9 import review_test_runner as runner


def _frame(path, name, qualname=None, back=None):
    return SimpleNamespace(
        f_code=SimpleNamespace(co_filename=str(path), co_name=name,
                               co_qualname=qualname or name),
        f_back=back,
    )


@pytest.mark.parametrize("case,allowed", [
    ("digest", True), ("copy", True), ("write_mode", False),
    ("write_flags", False), ("wrong_path", False), ("wrong_function", False),
    ("wrong_init", False), ("product", False), ("portfolio", False),
    ("broken_frame", False),
])
def test_index_fixture_read_permission(case, allowed, tmp_path, monkeypatch):
    # Synthetic repository and frames: no real user or knowledge file is opened.
    monkeypatch.setattr(runner, "REPO", tmp_path)
    path = tmp_path / "knowledge" / "index" / "index.faiss"
    conftest = tmp_path / "tests" / "conftest.py"
    frame = _frame(conftest, "_index_digest")
    mode, flags = "rb", os.O_RDONLY
    if case == "copy":
        frame = _frame(tmp_path / "stdlib" / "shutil.py", "copyfile", back=_frame(
            conftest, "_init", "_isolate_rag_index_dir.<locals>._init"))
        mode = None  # os.open's audited read form
    elif case == "write_mode":
        mode = "r+b"
    elif case == "write_flags":
        flags = os.O_RDONLY | os.O_APPEND
    elif case == "wrong_path":
        frame = _frame(tmp_path / "other" / "conftest.py", "_index_digest")
    elif case == "wrong_function":
        frame = _frame(conftest, "_protect_knowledge_index")
    elif case == "wrong_init":
        frame = _frame(conftest, "_init", "_other_fixture.<locals>._init")
    elif case == "product":
        frame = _frame(tmp_path / "src" / "rag" / "service.py", "_index_digest")
    elif case == "portfolio":
        path = tmp_path / "portfolio.yaml"
    elif case == "broken_frame":
        frame = object()
    assert runner._allow_index_fixture_read(path, mode, flags, frame=frame) is allowed
    if case == "write_mode":
        for write_mode in ("w", "a", "x", "r+"):
            assert not runner._allow_index_fixture_read(path, write_mode, os.O_RDONLY, frame=frame)
    elif case == "write_flags":
        for write_flag in (os.O_WRONLY, os.O_RDWR, os.O_CREAT, os.O_TRUNC, os.O_EXCL):
            assert not runner._allow_index_fixture_read(path, "rb", write_flag, frame=frame)
