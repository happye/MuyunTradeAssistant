"""Review IO guard boundaries, including Windows realpath prefix races."""
import os
from pathlib import Path

import pytest

from plan.fusion.iteration9 import review_test_runner as runner


@pytest.mark.parametrize("outside", [False, True])
def test_windows_resolution_race_keeps_extended_prefix(tmp_path, monkeypatch, outside):
    if os.name != "nt":
        pytest.skip("Windows GetFinalPathName branch")
    import ntpath
    root = tmp_path.resolve()
    target = (root.parent / "outside" if outside else root) / "missing" / "cash_materials"
    original = ntpath._getfinalpathname
    calls = []
    def transient(path):
        if path == str(target):
            calls.append(path)
            phase = len(calls) % 3
            if phase == 2:
                return "\\\\?\\" + str(target)
            error = FileNotFoundError("directory changed during resolution")
            error.winerror = 3 if phase == 1 else 2
            raise error
        return original(path)
    monkeypatch.setattr(ntpath, "_getfinalpathname", transient)
    assert str(target.resolve()).startswith("\\\\?\\")
    assert len(calls) == 3
    if outside:
        with pytest.raises(RuntimeError, match="outside temporary root"):
            runner._require_writable(target, root)
    else:
        runner._require_writable(target, root)
    assert len(calls) == 6


def test_guard_allows_missing_child_and_rejects_escape(tmp_path):
    root = (tmp_path / "allowed").resolve()
    root.mkdir()
    runner._require_writable(root / "missing" / "new.txt", root)
    for escape in (tmp_path / "outside.txt", root.parent / "allowed-other" / "file",
                   root / ".." / "outside.txt"):
        with pytest.raises(RuntimeError, match="outside temporary root"):
            runner._require_writable(escape, root)


def test_guard_rejects_link_to_outside_root(tmp_path):
    root = (tmp_path / "allowed").resolve()
    outside = (tmp_path / "outside").resolve()
    root.mkdir()
    outside.mkdir()
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        if os.name != "nt" or error.winerror != 1314:
            raise
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    with pytest.raises(RuntimeError, match="outside temporary root"):
        runner._require_writable(link / "new.txt", root)


@pytest.mark.skipif(os.name != "nt", reason="Windows extended UNC alias")
def test_resolved_unc_prefix_is_an_alias(tmp_path, monkeypatch):
    target = tmp_path / "unc-example"
    original = Path.resolve
    def resolved(path, *args, **kwargs):
        if path == target:
            return Path(r"\\?\UNC\server\share\root\child")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolved)
    assert runner._resolved_path(target) == Path(r"\\server\share\root\child")


def test_guard_allows_parallel_directory_creation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    root = tmp_path.resolve()
    barrier = Barrier(2)
    def create(path):
        barrier.wait(timeout=10)
        runner._require_writable(path, root)
        path.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for index in range(32):
            target = root / str(index) / "store" / "cash_materials" / "blobs"
            list(pool.map(create, [target, target]))
            assert target.is_dir()
