"""测试 chat 会话文件存储 SessionStore（ISS-092 聊天中断恢复）。

覆盖：
1. save/load 往返：current.json 内容完整、原子（无 .tmp 残留）
2. peek：非 system 消息计数（system 是代码常量不算对话）；无文件 → None
3. 损坏容错：JSON 解析失败 / 结构非法 → 重命名 corrupt_ 前缀留档（不删除）→ load None
4. archive_current：重命名 session_ 前缀留档；撞名加 _2 后缀；无文件 → None
5. list_sessions：current + 档案按 mtime 倒序、is_current 标记、坏文件不拖垮列表

纯本地临时目录，无网络。
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 确保项目根在 path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.chat.session_store import SessionStore


def _msgs(n=2):
    """最小合法消息集：system + n 条对话。"""
    out = [{"role": "system", "content": "SYS"}]
    for i in range(n):
        out.append({"role": "user", "content": f"问{i}"})
    return out


def _store():
    """每个用例独立临时目录。"""
    d = tempfile.mkdtemp(prefix="muyun_chat_sess_")
    return SessionStore(base_dir=Path(d)), Path(d)


def test_save_load_roundtrip():
    store, _ = _store()
    meta = store.save(_msgs(3), model="deepseek-v4-flash")
    assert store.current_exists()
    data = store.load()
    assert data is not None, "load 应返回数据"
    assert len(data["messages"]) == 4
    assert data["messages"][1]["content"] == "问0"
    assert data["model"] == "deepseek-v4-flash"
    assert data["created_at"], "created_at 应被补齐"
    assert meta["created_at"] == data["created_at"]


def test_save_atomic_no_tmp_leftover():
    store, d = _store()
    store.save(_msgs(), model="m")
    store.save(_msgs(), model="m")  # 第二次覆盖写
    leftovers = list(d.glob("*.tmp"))
    assert leftovers == [], f"原子写不应残留 tmp 文件: {leftovers}"


def test_save_reuses_created_at():
    store, _ = _store()
    meta1 = store.save(_msgs(), model="m")
    meta2 = store.save(_msgs(), model="m", created_at=meta1["created_at"])
    assert meta2["created_at"] == meta1["created_at"], "传入 created_at 应保持连续"


def test_peek_counts_non_system_only():
    store, _ = _store()
    store.save(_msgs(5), model="m")
    peek = store.peek()
    assert peek is not None
    assert peek["n_messages"] == 5, "system 是代码常量，不应计入对话条数"
    assert peek["model"] == "m"


def test_peek_system_only_file():
    store, _ = _store()
    store.save([{"role": "system", "content": "SYS"}], model="m")
    peek = store.peek()
    assert peek is not None and peek["n_messages"] == 0


def test_peek_missing_file_returns_none():
    store, _ = _store()
    assert store.peek() is None
    assert store.load() is None
    assert store.current_exists() is False


def test_load_corrupt_json_renamed_not_deleted():
    store, d = _store()
    store.save(_msgs(), model="m")
    # 写坏 current.json
    with open(store.current_path, "w", encoding="utf-8") as f:
        f.write("{not valid json!!")
    assert store.load() is None, "损坏文件 load 应返回 None"
    assert not store.current_exists(), "损坏文件应被移走"
    quarantined = list(d.glob("corrupt_*.json"))
    assert len(quarantined) == 1, f"损坏文件应重命名留档: {quarantined}"
    with open(quarantined[0], "r", encoding="utf-8") as f:
        assert "not valid json" in f.read(), "留档应保留原始内容供人工检查"


def test_load_invalid_structure_renamed():
    store, d = _store()
    for bad in (
        {"messages": "不是list"},
        {"messages": []},
        {"no_messages_key": 1},
        [1, 2, 3],  # 顶层不是 dict
        {"messages": [{"role": "hacker", "content": "x"}]},  # 非法角色
    ):
        with open(store.current_path, "w", encoding="utf-8") as f:
            json.dump(bad, f)
        assert store.load() is None, f"结构非法应判损坏: {bad}"
    assert len(list(d.glob("corrupt_*.json"))) == 5
    assert not store.current_exists()


def test_archive_current_renames_with_timestamp():
    store, d = _store()
    store.save(_msgs(2), model="m")
    path = store.archive_current()
    assert path is not None
    assert path.name.startswith("session_") and path.name.endswith(".json")
    assert path.exists()
    assert not store.current_exists(), "归档后 current.json 应不存在"
    # 内容不丢
    with open(path, "r", encoding="utf-8") as f:
        assert len(json.load(f)["messages"]) == 3


def test_archive_collision_gets_suffix():
    store, d = _store()
    store.save(_msgs(), model="m")
    p1 = store.archive_current()
    # 手工造一个同名文件模拟撞名
    store.save(_msgs(), model="m")
    p2 = store.archive_current()
    assert p1 != p2, "撞名必须加后缀而不是覆盖"
    assert p1.exists() and p2.exists()


def test_archive_missing_returns_none():
    store, _ = _store()
    assert store.archive_current() is None


def test_list_sessions_sorted_and_flags():
    store, _ = _store()
    store.save(_msgs(7), model="m")
    store.archive_current()  # 档案1
    store.save(_msgs(3), model="m")  # 新 current
    entries = store.list_sessions()
    assert len(entries) == 2
    current = [e for e in entries if e["is_current"]]
    archived = [e for e in entries if not e["is_current"]]
    assert len(current) == 1 and current[0]["name"] == "current.json"
    assert current[0]["n_messages"] == 3
    assert len(archived) == 1 and archived[0]["n_messages"] == 7


def test_list_sessions_survives_unreadable_file():
    store, d = _store()
    store.save(_msgs(2), model="m")
    with open(d / "session_bad.json", "w", encoding="utf-8") as f:
        f.write("garbage{{{")
    entries = store.list_sessions()
    bad = [e for e in entries if e["name"] == "session_bad.json"]
    assert len(bad) == 1
    assert bad[0]["n_messages"] is None, "坏文件条数未知，但不拖垮列表"
    assert bad[0]["updated_at"], "坏文件用文件 mtime 兜底显示时间"


def test_list_sessions_empty_dir():
    store, _ = _store()
    assert store.list_sessions() == []


def _cleanup():
    """脚本式测试收尾：清临时目录。"""
    for d in Path(tempfile.gettempdir()).glob("muyun_chat_sess_*"):
        shutil.rmtree(d, ignore_errors=True)


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
    _cleanup()
    print(f"\n{len(tests)-failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
