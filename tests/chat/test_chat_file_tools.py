"""测试 chat 本地文件读写工具（ISS-092：read_file/write_file/list_files）。

覆盖：
1. 沙箱：读限仓库根内（../ 逃逸、绝对路径、越界一律拒绝）；写仅限 AI笔记/ 目录
2. 密钥防线：configs/settings.local.yaml / .env 不允许读（内容进对话=发给AI服务商）
3. 读写往返：utf-8/gbk 解码、二进制拒绝、大小上限、子目录创建、覆盖提示
4. list_files：文件+目录列举、噪音目录隐藏、真实仓库根锚点

纯本地临时目录 + 真实仓库只读，无网络。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 确保项目根在 path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.chat import tools as chat_tools


def _setup():
    """临时读写沙箱（读根/写根分开），返回 (读根, 写根)。"""
    read_root = Path(tempfile.mkdtemp(prefix="muyun_fr_"))
    write_root = Path(tempfile.mkdtemp(prefix="muyun_fw_"))
    orig_read, orig_write = chat_tools._FILES_READ_ROOT, chat_tools._FILES_WRITE_ROOT
    chat_tools._FILES_READ_ROOT = read_root
    chat_tools._FILES_WRITE_ROOT = write_root
    return read_root, write_root, orig_read, orig_write


def _teardown(orig_read, orig_write):
    chat_tools._FILES_READ_ROOT = orig_read
    chat_tools._FILES_WRITE_ROOT = orig_write
    for d in Path(tempfile.gettempdir()).glob("muyun_f[rw]_*"):
        shutil.rmtree(d, ignore_errors=True)


def _mk(root: Path, rel: str, data, binary=False):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        p.write_bytes(data)
    else:
        p.write_text(data, encoding="utf-8")
    return p


# ── 注册 ───────────────────────────────────────────────

def test_registry_contains_file_tools():
    for name in ("read_file", "write_file", "list_files"):
        assert name in chat_tools.TOOL_REGISTRY, f"{name} 未注册"


def test_tool_definitions_cover_registry():
    """schema 与注册表对账：TOOL_DEFINITIONS 与 TOOL_REGISTRY 一一对应（防漂移）。"""
    from src.chat.prompts import TOOL_DEFINITIONS
    names = {d["function"]["name"] for d in TOOL_DEFINITIONS}
    for n in chat_tools.TOOL_REGISTRY:
        assert n in names, f"registry 工具 {n} 缺 schema"
    for n in names:
        assert n in chat_tools.TOOL_REGISTRY, f"schema 工具 {n} 未注册"


# ── 读：沙箱与内容 ──────────────────────────────────────

def test_read_file_roundtrip_utf8():
    r, w, orr, ow = _setup()
    try:
        _mk(r, "docs/readme.md", "# 标题\n正文内容")
        result = chat_tools.read_file("docs/readme.md")
        assert not result.startswith(chat_tools.TOOL_ERROR_MARK)
        assert "# 标题" in result and "正文内容" in result
    finally:
        _teardown(orr, ow)


def test_read_file_gbk_fallback():
    r, w, orr, ow = _setup()
    try:
        (r / "gbk.txt").write_bytes("中文GBK编码".encode("gbk"))
        result = chat_tools.read_file("gbk.txt")
        assert "中文GBK编码" in result
    finally:
        _teardown(orr, ow)


def test_read_file_binary_rejected():
    r, w, orr, ow = _setup()
    try:
        _mk(r, "bin.dat", b"\x00\x01\x02\x00binary", binary=True)
        result = chat_tools.read_file("bin.dat")
        assert result.startswith(chat_tools.TOOL_ERROR_MARK)
        assert "二进制" in result
    finally:
        _teardown(orr, ow)


def test_read_file_sandbox_escapes_rejected():
    r, w, orr, ow = _setup()
    try:
        _mk(r, "inside.txt", "ok")
        # 外部真实存在的文件，尝试各类逃逸路径
        outside = Path(tempfile.mkdtemp(prefix="muyun_outside_"))
        (outside / "secret.txt").write_text("外面")
        escapes = [
            f"../{outside.name}/secret.txt",
            f"a/../../{outside.name}/secret.txt",
            str(outside / "secret.txt"),           # 绝对路径
            "C:/Windows/win.ini",                   # 系统绝对路径
            "",
        ]
        for esc in escapes:
            result = chat_tools.read_file(esc)
            assert result.startswith(chat_tools.TOOL_ERROR_MARK), \
                f"逃逸路径未被拒绝: {esc!r} -> {result[:80]}"
        shutil.rmtree(outside, ignore_errors=True)
    finally:
        _teardown(orr, ow)


def test_read_file_secret_files_rejected():
    r, w, orr, ow = _setup()
    try:
        _mk(r, "configs/settings.local.yaml", "api_key: sk-leaked")
        _mk(r, ".env", "KEY=xxx")
        result = chat_tools.read_file("configs/settings.local.yaml")
        assert result.startswith(chat_tools.TOOL_ERROR_MARK)
        assert "密钥" in result
        assert "sk-leaked" not in result, "密钥内容绝不能进入工具结果"
        result2 = chat_tools.read_file(".env")
        assert result2.startswith(chat_tools.TOOL_ERROR_MARK)
    finally:
        _teardown(orr, ow)


def test_read_file_missing_and_too_large():
    r, w, orr, ow = _setup()
    try:
        assert chat_tools.read_file("nope.md").startswith(chat_tools.TOOL_ERROR_MARK)
        _mk(r, "big.txt", "x" * (chat_tools._MAX_FILE_IO_BYTES + 10))
        result = chat_tools.read_file("big.txt")
        assert result.startswith(chat_tools.TOOL_ERROR_MARK)
        assert "上限" in result
    finally:
        _teardown(orr, ow)


# ── 写：仅限 AI笔记/ ──────────────────────────────────

def test_write_file_new_with_subdir_and_overwrite():
    r, w, orr, ow = _setup()
    try:
        result = chat_tools.write_file("行业总结/2026-09-11_锂电.md", "# 锂电总结\n要点1")
        assert result.startswith("✅") and "新建" in result
        assert (w / "行业总结" / "2026-09-11_锂电.md").read_text(encoding="utf-8") == "# 锂电总结\n要点1"
        # 覆盖提示
        result2 = chat_tools.write_file("行业总结/2026-09-11_锂电.md", "改写")
        assert "覆盖" in result2
        assert (w / "行业总结" / "2026-09-11_锂电.md").read_text(encoding="utf-8") == "改写"
    finally:
        _teardown(orr, ow)


def test_write_file_cannot_escape_or_write_repo_files():
    r, w, orr, ow = _setup()
    try:
        # 逃逸：../ 出写沙箱
        result = chat_tools.write_file("../evil.md", "越界内容")
        assert result.startswith(chat_tools.TOOL_ERROR_MARK)
        assert not (w.parent / "evil.md").exists(), "逃逸文件绝不能被创建"
        # 深层逃逸到真实仓库文件（写沙箱=AI笔记/，仓库内其他位置不可写）
        result2 = chat_tools.write_file("../../src/chat/agent.py", "恶意覆盖")
        assert result2.startswith(chat_tools.TOOL_ERROR_MARK), \
            "写沙箱外（仓库根其他位置）必须被拒绝"
        # 注：写 "src/chat/agent.py"（不带 ../）是沙箱内合法路径，
        # 落在 AI笔记/src/chat/agent.py，不碰真实仓库文件
        result3 = chat_tools.write_file("src/chat/agent.py", "沙箱内副本")
        assert result3.startswith("✅")
    finally:
        _teardown(orr, ow)


def test_write_file_content_guards():
    r, w, orr, ow = _setup()
    try:
        assert chat_tools.write_file("a.md", 123).startswith(chat_tools.TOOL_ERROR_MARK)
        big = "x" * (chat_tools._MAX_FILE_IO_BYTES + 10)
        assert chat_tools.write_file("big.md", big).startswith(chat_tools.TOOL_ERROR_MARK)
    finally:
        _teardown(orr, ow)


def test_written_files_readable_via_read_root():
    """AI笔记/ 在仓库根内 → 写进去的文件可读回来（读写闭环）。"""
    r, w, orr, ow = _setup()
    try:
        # 模拟真实结构：读根下挂 AI笔记（与生产一致的相对位置）
        chat_tools._FILES_WRITE_ROOT = r / "AI笔记"
        chat_tools.write_file("总结.md", "精华内容")
        result = chat_tools.read_file("AI笔记/总结.md")
        assert not result.startswith(chat_tools.TOOL_ERROR_MARK)
        assert "精华内容" in result
    finally:
        _teardown(orr, ow)


# ── 列表 ─────────────────────────────────────────────

def test_list_files_with_skip_dirs():
    r, w, orr, ow = _setup()
    try:
        _mk(r, "a.md", "1")
        _mk(r, "b.py", "2")
        (r / ".git").mkdir()
        (r / ".venv").mkdir()
        (r / "sub").mkdir()
        _mk(r, "sub/c.txt", "3")
        result = chat_tools.list_files("")
        assert not result.startswith(chat_tools.TOOL_ERROR_MARK)
        assert "a.md" in result and "b.py" in result
        assert "sub/" in result
        # 列表行有两格缩进；页脚说明会提到 .git 字样，别误伤
        assert "  .git/" not in result and "  .venv/" not in result, "噪音目录应隐藏"
        # 子目录列举
        result2 = chat_tools.list_files("sub")
        assert "c.txt" in result2
        # 不存在/非目录
        assert chat_tools.list_files("nope").startswith(chat_tools.TOOL_ERROR_MARK)
        assert chat_tools.list_files("a.md").startswith(chat_tools.TOOL_ERROR_MARK)
    finally:
        _teardown(orr, ow)


def test_list_files_truncates_at_cap():
    r, w, orr, ow = _setup()
    try:
        big_dir = r / "many"
        big_dir.mkdir()
        for i in range(chat_tools._LIST_MAX_ENTRIES + 20):
            (big_dir / f"f{i:04d}.txt").write_text("x", encoding="utf-8")
        result = chat_tools.list_files("many")
        assert "截断" in result
    finally:
        _teardown(orr, ow)


# ── 真实仓库根锚点（不 patch，只读）─────────────────────

def test_real_repo_root_anchor():
    """未 patch 时读根锚在真实仓库根：能读 CLAUDE.md，密钥文件拒绝。"""
    result = chat_tools.read_file("CLAUDE.md")
    assert not result.startswith(chat_tools.TOOL_ERROR_MARK)
    assert "Muyun" in result or "暮云" in result
    secret = chat_tools.read_file("configs/settings.local.yaml")
    assert secret.startswith(chat_tools.TOOL_ERROR_MARK)
    lst = chat_tools.list_files("src/chat")
    assert "agent.py" in lst and "tools.py" in lst


if __name__ == "__main__":
    import traceback

    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
