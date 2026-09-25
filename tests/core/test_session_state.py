"""session_state 模块测试（体验重构 Step 1）

覆盖：save/get 往返、resolve_index 正常/越界/无scan、persist 落盘、count、过期提示。
用 tmp_path monkeypatch 避免污染真实 ~/.muyun/last_scan.json 与 scan_history.jsonl
（v0.8.11 起 save_last_scan 同时追加历史，重定向必须两者都盖）。
"""

from datetime import datetime, timedelta
from pathlib import Path

from src.cli import session_state


def test_save_and_get(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(session_state, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    items = [
        {"code": "002230", "name": "科大讯飞", "score": 82, "grade": "B", "confidence": 0.8},
        {"code": "688256", "name": "寒武纪", "score": 78, "grade": "B", "confidence": 0.75},
    ]
    assert session_state.save_last_scan(items, "bz scan AI,半导体")
    data = session_state.get_last_scan()
    assert data is not None
    assert data["count"] == 2
    assert data["source"] == "bz scan AI,半导体"
    assert data["items"][0]["code"] == "002230"
    assert data["items"][1]["name"] == "寒武纪"
    assert "timestamp" in data


def test_resolve_index_normal(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(session_state, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    items = [{"code": "A", "name": "a"}, {"code": "B", "name": "b"}, {"code": "C", "name": "c"}]
    session_state.save_last_scan(items, "test")
    r = session_state.resolve_index(1)
    assert r is not None
    item, warning = r
    assert item["code"] == "A"
    assert warning == ""  # 刚存，不过期
    r2 = session_state.resolve_index(3)
    assert r2 is not None
    assert r2[0]["code"] == "C"


def test_resolve_index_out_of_range(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(session_state, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    session_state.save_last_scan([{"code": "X"}], "test")
    assert session_state.resolve_index(0) is None   # 1-based，0 越界
    assert session_state.resolve_index(2) is None   # 超出
    assert session_state.resolve_index(-1) is None


def test_resolve_index_no_scan(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "none.json")
    assert session_state.resolve_index(1) is None
    assert session_state.last_scan_count() == 0


def test_persist_report(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_REPORT_DIR", tmp_path / "scan")
    items = [
        {"code": "002230", "name": "科大讯飞", "score": 82, "grade": "B",
         "confidence": 0.8, "dims": {"industry_prosperity": 90}},
    ]
    path = session_state.persist_scan_report(items, "bz scan AI,半导体")
    assert path
    content = Path(path).read_text(encoding="utf-8")
    assert "002230" in content
    assert "科大讯飞" in content
    assert "| 1 |" in content
    assert "industry_prosperity" in content  # 明细
    # 文件名含 safe source
    assert "AI" in path or "AI_" in path


def test_persist_report_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_REPORT_DIR", tmp_path / "scan")
    path = session_state.persist_scan_report([], "bz scan test")
    assert path  # 空列表也应能落盘（共0只）


def test_persist_report_custom_columns(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_REPORT_DIR", tmp_path / "scan")
    items = [
        {"code": "600519", "name": "贵州茅台", "price": 1800.0, "change_pct": 2.5,
         "turnover_rate": 0.8, "volume_ratio": 1.2, "amplitude": 3.1, "amount_yi": 25.6},
    ]
    cols = [("price", "价"), ("change_pct", "涨跌%"), ("turnover_rate", "换手%"),
            ("volume_ratio", "量比"), ("amplitude", "振幅%"), ("amount_yi", "额(亿)")]
    path = session_state.persist_scan_report(items, "scan market", columns=cols)
    assert path
    content = Path(path).read_text(encoding="utf-8")
    assert "600519" in content
    assert "贵州茅台" in content
    assert "1800.00" in content
    assert "2.50" in content  # change_pct
    # 自定义列表头出现
    assert "涨跌%" in content
    assert "额(亿)" in content


def test_count(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(session_state, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    assert session_state.last_scan_count() == 0
    session_state.save_last_scan([{"code": str(i)} for i in range(5)], "test")
    assert session_state.last_scan_count() == 5


def test_stale_warning():
    old = (datetime.now() - timedelta(minutes=60)).isoformat()
    assert "过期" in session_state._stale_warning(old)
    fresh = datetime.now().isoformat()
    assert session_state._stale_warning(fresh) == ""
    assert session_state._stale_warning(None) == ""
    assert session_state._stale_warning("not-a-date") == ""


# ── M2（plan/TECHNICAL_HANDOFF §4）：原子写 + 读取边界校验 ──────

def _redirect_all(monkeypatch, tmp_path):
    """M2 用例统一重定向全部状态文件（含 deep_analyzed / watchlist）。"""
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(session_state, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    monkeypatch.setattr(session_state, "_DEEP_ANALYZED_FILE", tmp_path / "deep_analyzed.json")
    monkeypatch.setattr(session_state, "_WATCH_FILE", tmp_path / "watchlist.jsonl")


def test_serialize_failure_no_raise_and_keeps_old(monkeypatch, tmp_path):
    """items 含不可序列化对象 → 不抛异常、返回假、旧快照字节不变。"""
    _redirect_all(monkeypatch, tmp_path)
    assert session_state.save_last_scan([{"code": "A"}], "test")
    old = (tmp_path / "last_scan.json").read_bytes()
    r = session_state.save_last_scan([{"code": "B", "dims": {("tuple", "key")}}], "bad")
    assert not r.snapshot
    assert (tmp_path / "last_scan.json").read_bytes() == old


def test_replace_failure_keeps_old_snapshot(monkeypatch, tmp_path):
    """os.replace 失败（磁盘满等）→ 旧快照字节不变，临时文件不残留。"""
    _redirect_all(monkeypatch, tmp_path)
    assert session_state.save_last_scan([{"code": "A"}], "test")
    old = (tmp_path / "last_scan.json").read_bytes()

    real_replace = __import__("os").replace

    def _boom(src, dst):
        raise OSError("磁盘满了")
    monkeypatch.setattr(__import__("os"), "replace", _boom)
    r = session_state.save_last_scan([{"code": "B"}], "test")
    monkeypatch.setattr(__import__("os"), "replace", real_replace)
    assert not r.snapshot
    assert (tmp_path / "last_scan.json").read_bytes() == old
    assert not list(tmp_path.glob("*.tmp")), "失败的临时文件必须清理，不许残留"


def test_last_scan_rejects_null_root(monkeypatch, tmp_path):
    _redirect_all(monkeypatch, tmp_path)
    (tmp_path / "last_scan.json").write_text("null", encoding="utf-8")
    assert session_state.get_last_scan() is None
    assert session_state.resolve_index(1) is None


def test_last_scan_rejects_array_root(monkeypatch, tmp_path):
    """JSON 根是数组 → 拒绝整个快照（回归锁：原实现 resolve_index 会 AttributeError）。"""
    _redirect_all(monkeypatch, tmp_path)
    (tmp_path / "last_scan.json").write_text('[{"code":"A"}]', encoding="utf-8")
    assert session_state.get_last_scan() is None
    assert session_state.resolve_index(1) is None


def test_last_scan_rejects_bad_items_structure(monkeypatch, tmp_path):
    """items 非 list / item 非 dict / item 缺 code → 拒绝整个快照，#N 不错位。"""
    _redirect_all(monkeypatch, tmp_path)
    cases = [
        {"timestamp": "2026-09-24T10:00:00", "items": {"0": {"code": "A"}}},   # items 非 list
        {"timestamp": "2026-09-24T10:00:00", "items": ["A", {"code": "B"}]},   # item 非 dict
        {"timestamp": "2026-09-24T10:00:00", "items": [{"name": "无代码"}]},    # code 不可用
    ]
    for payload in cases:
        (tmp_path / "last_scan.json").write_text(
            __import__("json").dumps(payload, ensure_ascii=False), encoding="utf-8")
        assert session_state.get_last_scan() is None, f"应拒绝: {payload}"
        assert session_state.resolve_index(1) is None


def test_last_scan_rejects_bad_timestamp(monkeypatch, tmp_path):
    _redirect_all(monkeypatch, tmp_path)
    for ts in (123, None, "not-a-date"):
        payload = {"timestamp": ts, "items": [{"code": "A"}]}
        (tmp_path / "last_scan.json").write_text(
            __import__("json").dumps(payload), encoding="utf-8")
        assert session_state.get_last_scan() is None, f"应拒绝 timestamp={ts!r}"


def test_last_scan_empty_items_is_valid(monkeypatch, tmp_path):
    """items=[] 是合法快照（空扫描），不算损坏。"""
    _redirect_all(monkeypatch, tmp_path)
    assert session_state.save_last_scan([], "scan market 空规则")
    data = session_state.get_last_scan()
    assert data is not None and data["items"] == []
    assert session_state.resolve_index(1) is None  # 但 #N 取不到


def test_scan_history_survives_bad_encoding_and_truncated_line(monkeypatch, tmp_path):
    """单行无效 UTF-8 / 截断尾行 → 隔离跳过，其余有效行保留（不崩不丢）。"""
    _redirect_all(monkeypatch, tmp_path)
    good = __import__("json").dumps(
        {"timestamp": "2026-09-24T10:00:00", "source": "s", "count": 1,
         "items": [{"code": "A"}]}, ensure_ascii=False)
    (tmp_path / "scan_history.jsonl").write_bytes(
        b"\xff\xfe garbage line\n"          # 无效 UTF-8
        + good.encode("utf-8") + b"\n"
        + b'{"timestamp": "2026-09-25T10'   # 截断尾行（崩溃残留）
        + b"\n")
    rows = session_state.get_scan_history()
    assert len(rows) == 1
    assert rows[0]["items"][0]["code"] == "A"


def test_scan_history_sort_mixed_timestamp_types(monkeypatch, tmp_path):
    """timestamp 混排 None/数字/字符串 → 排序不 TypeError，全部行可读。"""
    _redirect_all(monkeypatch, tmp_path)
    rows = [
        {"timestamp": None, "source": "a", "items": []},
        {"timestamp": 123, "source": "b", "items": []},
        {"timestamp": "2026-09-24T10:00:00", "source": "c", "items": []},
    ]
    (tmp_path / "scan_history.jsonl").write_text(
        "\n".join(__import__("json").dumps(r, ensure_ascii=False) for r in rows),
        encoding="utf-8")
    got = session_state.get_scan_history()
    assert len(got) == 3  # 不崩即过（原实现 sort 直接 TypeError）


def test_deep_analyzed_rejects_non_dict_root(monkeypatch, tmp_path):
    """deep_analyzed.json 根是数组/null → 返回 {}（回归锁：原实现 .get 会 AttributeError）。"""
    _redirect_all(monkeypatch, tmp_path)
    for content in ("[]", "null", '"string"'):
        (tmp_path / "deep_analyzed.json").write_text(content, encoding="utf-8")
        assert session_state.get_deep_analyzed() == {}, f"应拒绝根: {content}"


def test_watch_events_survive_corrupt_lines_and_bad_items(monkeypatch, tmp_path):
    """观察池事件流：坏行隔离 + items 非列表的事件不拖垮重放。"""
    _redirect_all(monkeypatch, tmp_path)
    good_add = __import__("json").dumps(
        {"timestamp": "2026-09-24T10:00:00", "action": "add", "source": "s",
         "items": [{"code": "600519", "name": "茅台"}]}, ensure_ascii=False)
    bad_items = __import__("json").dumps(
        {"timestamp": "2026-09-24T10:01:00", "action": "remove", "source": "s",
         "items": "不是列表"}, ensure_ascii=False)
    (tmp_path / "watchlist.jsonl").write_bytes(
        b"\xff\xfe bad utf8\n"
        + good_add.encode("utf-8") + b"\n"
        + bad_items.encode("utf-8") + b"\n"
        + b'{"timestamp": "2026-09-24T10:02' + b"\n")
    active = session_state.get_watch_active()
    assert len(active) == 1
    assert active[0]["items"][0]["code"] == "600519"


def test_empty_files_are_tolerated(monkeypatch, tmp_path):
    """空文件（0 字节，崩溃残留常见）→ 快照按无扫描、历史按空处理，不崩不告警刷屏。"""
    _redirect_all(monkeypatch, tmp_path)
    (tmp_path / "last_scan.json").write_bytes(b"")
    assert session_state.get_last_scan() is None
    assert session_state.resolve_index(1) is None
    (tmp_path / "scan_history.jsonl").write_bytes(b"")
    assert session_state.get_scan_history() == []


def test_save_last_scan_reports_partial_success(monkeypatch, tmp_path):
    """快照成功但历史追加失败 → 返回值如实表达部分成功（snapshot=True, history=False）。"""
    _redirect_all(monkeypatch, tmp_path)
    monkeypatch.setattr(session_state, "append_scan_history", lambda *a, **k: False)
    r = session_state.save_last_scan([{"code": "A"}], "test")
    assert r.snapshot is True
    assert r.history is False
    assert not r  # 整体不算成功（__bool__ = 两者都成）


# ── C2（v0.8.17）：批量任务账本 ──────────────────────────────

def _redirect_batch(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_BATCH_TASKS_FILE", tmp_path / "batch_tasks.json")


def test_batch_task_start_mark_and_progress(monkeypatch, tmp_path):
    _redirect_batch(monkeypatch, tmp_path)
    session_state.batch_task_start("l all", "scan market 健康", ["A", "B", "C"])
    session_state.batch_task_mark("l all", "scan market 健康", "A", True)
    session_state.batch_task_mark("l all", "scan market 健康", "B", False, "数据获取失败")
    tasks = session_state.get_recent_batch_tasks()
    assert len(tasks) == 1
    t = tasks[0]
    assert "A" in t["done"] and "B" in t["failed"] and "B" not in t["done"]
    assert t["failed"]["B"] == "数据获取失败"
    assert "C" not in t["done"] and "C" not in t["failed"]   # 未跑项


def test_batch_task_reinherit_on_restart(monkeypatch, tmp_path):
    """同名任务重启 → 继承既有逐项状态（done 保留，可继续记账）。"""
    _redirect_batch(monkeypatch, tmp_path)
    session_state.batch_task_start("ba", "bz scan AI", ["A", "B", "C"])
    session_state.batch_task_mark("ba", "bz scan AI", "A", True)
    session_state.batch_task_start("ba", "bz scan AI", ["A", "B", "C"])   # 中断后重跑
    session_state.batch_task_mark("ba", "bz scan AI", "B", True)
    t = session_state.get_recent_batch_tasks()[0]
    assert "A" in t["done"] and "B" in t["done"]   # 继承 + 新增


def test_batch_task_mark_unknown_is_noop(monkeypatch, tmp_path):
    _redirect_batch(monkeypatch, tmp_path)
    session_state.batch_task_mark("l", "不存在", "A", True)   # 不抛不建
    assert session_state.get_recent_batch_tasks() == []


def test_batch_tasks_keep_latest_five(monkeypatch, tmp_path):
    _redirect_batch(monkeypatch, tmp_path)
    for i in range(7):
        session_state.batch_task_start("l all", f"任务{i}", ["A"])
    assert len(session_state.get_recent_batch_tasks(99)) == 5
    keys = [t["key"] for t in session_state.get_recent_batch_tasks(99)]
    assert "任务6" in keys and "任务0" not in keys   # 最新的留下


def test_batch_tasks_corrupt_file_resets(monkeypatch, tmp_path):
    _redirect_batch(monkeypatch, tmp_path)
    (tmp_path / "batch_tasks.json").write_text("{bad json", encoding="utf-8")
    session_state.batch_task_start("l all", "k", ["A"])   # 不抛，账本重置
    assert len(session_state.get_recent_batch_tasks()) == 1


def test_batch_tasks_survives_bad_encoding_line(monkeypatch, tmp_path):
    """坏编码字节 → 账本重置不崩（可见性数据的合理取舍，JSON 非逐行格式）。"""
    _redirect_batch(monkeypatch, tmp_path)
    (tmp_path / "batch_tasks.json").write_bytes(b"\xff\xfe\xff")
    session_state.batch_task_start("l all", "k", ["A"])   # 不抛
    assert len(session_state.get_recent_batch_tasks()) == 1
