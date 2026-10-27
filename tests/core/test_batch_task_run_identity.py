"""T05-H：批量任务运行身份与可靠逐项记录（V1-G7-BATCH-BUDGET 第一有限子合同）。

行为锁（八组）：
1. 每次 batch_task_start 新建不可混淆 run_id；同显示 key 的完整清单改变/重启不继承旧 done/failed
2. 同身份新 run 独立：旧 run 记录只作历史，不再被新 run 的逐项结果污染
3. 旧无 run_id 记录只读展示，不补造身份，不接受旧式（无令牌）mark
4. 旧 run / 缺令牌 / 清单外 mark fail-closed，不改新旧记录；成功/失败/降级转移互斥
5. 中断后新 run 重新记账，旧完成项不冒充新 run 已完成
6. 结构化成功/失败/降级与 callback 异常隔离（见 tests/benzong/test_benzong_batch_scorer.py）
7. 原子写失败保旧文件，入口给用户可见提示
8. 真实四入口（l 多代码 / la / l all / ba，含 bz 多代码共用 ba）绑定 start 令牌；保留最近 5 条

全部 mock，不触网/不花 AI/不写真实 ~/.muyun；账本文件重定向到 tmp_path。
"""

import json
import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import src.cli.session_state as ss


# ── 工具 ──────────────────────────────────────────────────

def _redirect_ledger(monkeypatch, tmp_path):
    path = tmp_path / "batch_tasks.json"
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", path)
    return path


def _read_ledger(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_ledger(path, tasks):
    path.write_text(json.dumps({"tasks": tasks}, ensure_ascii=False), encoding="utf-8")


def _legacy_record(kind="ba", key="bz scan 测试", codes=("A", "B")):
    return {
        "kind": kind, "key": key, "codes": list(codes),
        "done": {codes[0]: "2026-10-01T09:00:00"},
        "failed": {},
        "ts_started": "2026-10-01T08:59:00",
        "ts_updated": "2026-10-01T09:00:00",
    }


def _spy_ledger(monkeypatch, run_id="RUN-1"):
    """把 session_state 账本函数替换为记录器（不写磁盘），返回记录容器。"""
    seen = {"start": [], "mark": []}

    def fake_start(kind, key, codes, snapshot=None):
        # T05-I：start 新增可选执行参数描述（ba 传入）；本文件只核既有 run 身份语义。
        seen["start"].append((kind, key, list(codes)))
        seen.setdefault("snapshots", []).append(snapshot)
        return run_id

    def fake_mark(kind, key, code, ok, note="", run_id=None, degraded=False):
        seen["mark"].append({"kind": kind, "key": key, "code": code, "ok": ok,
                             "note": note, "run_id": run_id, "degraded": degraded})
        return True

    monkeypatch.setattr(ss, "batch_task_start", fake_start)
    monkeypatch.setattr(ss, "batch_task_mark", fake_mark)
    return seen


# ── 1/2/5：run 身份、独立 run、清单变化不继承 ──────────────

def test_each_start_opens_distinct_run_and_does_not_inherit(monkeypatch, tmp_path):
    """同显示 key 再启动 → 新 run 令牌不同、旧完成项不继承；旧 run 记录保留可查。"""
    _redirect_ledger(monkeypatch, tmp_path)
    key = "手动多代码 600519,000001"
    first = ss.batch_task_start("l", key, ["600519", "000001"])
    assert isinstance(first, str) and first, "start 必须返回非空 run 令牌"
    assert ss.batch_task_mark("l", key, "600519", True, run_id=first) is True

    second = ss.batch_task_start("l", key, ["600519", "000001"])
    assert isinstance(second, str) and second and second != first, "每次启动必须新建不可混淆 run_id"

    tasks = ss.get_recent_batch_tasks(99)
    assert [t.get("run_id") for t in tasks] == [second, first], "最新 run 在前，旧 run 记录保留"
    new, old = tasks
    assert new["codes"] == ["600519", "000001"]
    assert new["done"] == {} and new["failed"] == {} and new.get("degraded", {}) == {}
    assert "600519" in old["done"], "旧 run 自己的完成记录不被新 run 覆盖"


def test_run_id_stores_full_uuid_even_when_prefix_collides(monkeypatch, tmp_path):
    """同秒两次启动、UUID 前 6 位相同也不得碰撞（不能只存短前缀）。"""
    _redirect_ledger(monkeypatch, tmp_path)
    from datetime import datetime as _real_datetime
    import uuid as _uuid

    class _FixedDateTime(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 26, 1, 2, 3)

    monkeypatch.setattr(ss, "datetime", _FixedDateTime)
    same_prefix = ["abcdef" + "0" * 24 + "01", "abcdef" + "0" * 24 + "02"]
    seq = iter([_uuid.UUID(hex=h) for h in same_prefix])
    monkeypatch.setattr(ss.uuid, "uuid4", lambda: next(seq))

    first = ss.batch_task_start("l", "k", ["A", "B"])
    second = ss.batch_task_start("l", "k", ["A", "B"])
    assert isinstance(first, str) and isinstance(second, str)
    assert first != second, "同秒 + 前6位相同的 UUID 也必须产生不同 run 令牌（存完整 UUID）"
    assert first.split("-")[-1] == same_prefix[0], "令牌须含完整 UUID，不能截断短前缀"
    assert second.split("-")[-1] == same_prefix[1]
    tasks = ss.get_recent_batch_tasks(99)
    assert [t["run_id"] for t in tasks] == [second, first]
    assert ss.batch_task_mark("l", "k", "A", True, run_id=first) is False, "旧 token 不能写新 run"
    assert ss.batch_task_mark("l", "k", "A", True, run_id=second) is True


def test_same_key_changed_roster_new_run_starts_from_zero(monkeypatch, tmp_path):
    """同显示 key 但完整清单变化（截断 key 相同）→ roster 各自冻结，不合并旧 done。"""
    _redirect_ledger(monkeypatch, tmp_path)
    key = "l all 扫描A"   # 旧代码的截断/同名 key 场景
    first = ss.batch_task_start("l all", key, ["A", "B"])
    ss.batch_task_mark("l all", key, "A", True, run_id=first)

    second = ss.batch_task_start("l all", key, ["B", "C"])
    tasks = ss.get_recent_batch_tasks(99)
    assert len(tasks) == 2 and tasks[0]["run_id"] == second
    assert tasks[0]["codes"] == ["B", "C"] and tasks[0]["done"] == {}
    assert tasks[1]["codes"] == ["A", "B"] and "A" in tasks[1]["done"]

    # 清单外代码（旧清单的 A）不得写入新 run
    assert ss.batch_task_mark("l all", key, "A", True, run_id=second) is False
    # 旧 run 晚到结果也不得写新 run
    assert ss.batch_task_mark("l all", key, "B", True, run_id=first) is False
    assert _read_ledger(tmp_path / "batch_tasks.json")["tasks"][0]["done"] == {}


def test_interrupted_batch_rerun_records_new_results_only(monkeypatch, tmp_path):
    """中断后重跑：新 run 从零记账，旧 run 未跑项不被冒充为完成。"""
    _redirect_ledger(monkeypatch, tmp_path)
    key = "持仓 600519,000001,300750"
    first = ss.batch_task_start("la", key, ["600519", "000001", "300750"])
    ss.batch_task_mark("la", key, "600519", True, run_id=first)   # 中断：后两只未跑

    second = ss.batch_task_start("la", key, ["600519", "000001", "300750"])
    fresh = ss.get_recent_batch_tasks(99)[0]
    assert fresh["run_id"] == second and fresh["done"] == {}
    assert ss.batch_task_mark("la", key, "000001", False, "数据获取失败", run_id=second) is True
    assert ss.batch_task_mark("la", key, "300750", True, run_id=second) is True
    fresh = ss.get_recent_batch_tasks(99)[0]
    assert set(fresh["done"]) == {"300750"}
    assert set(fresh["failed"]) == {"000001"}
    assert "600519" not in fresh["done"]
    old = ss.get_recent_batch_tasks(99)[1]
    assert set(old["done"]) == {"600519"}


# ── 3/4：旧记录只读、缺令牌/旧 run/清单外 fail-closed ─────

def test_legacy_record_without_run_id_is_readonly(monkeypatch, tmp_path):
    """旧无 run_id 记录：可展示原样，不补造身份，不接受旧式 mark。"""
    path = _redirect_ledger(monkeypatch, tmp_path)
    _seed_ledger(path, [_legacy_record()])
    before = path.read_bytes()

    assert ss.batch_task_mark("ba", "bz scan 测试", "B", True) is False, \
        "缺 run 令牌必须 fail-closed 且返回准确 False"
    assert path.read_bytes() == before, "旧式 mark 不得改动旧记录"

    got = ss.get_recent_batch_tasks(99)[0]
    assert "run_id" not in got, "不得给旧记录补造 run 身份"

    token = ss.batch_task_start("ba", "bz scan 测试", ["A", "B"])
    assert isinstance(token, str) and token
    raw = _read_ledger(path)
    assert raw["tasks"][0]["run_id"] == token
    assert raw["tasks"][0]["codes"] == ["A", "B"]
    assert raw["tasks"][0]["done"] == {}, "旧记录/旧日期的 done 不得被新 run 继承"
    assert "run_id" not in raw["tasks"][1], "旧记录保持原样（不补造身份/配置版本）"
    assert ss.batch_task_mark("ba", "bz scan 测试", "A", True) is False


def test_mark_requires_exact_latest_run_token_and_roster(monkeypatch, tmp_path):
    _redirect_ledger(monkeypatch, tmp_path)
    key = "l all 扫描B"
    old = ss.batch_task_start("l all", key, ["A", "B"])
    assert ss.batch_task_mark("l all", key, "A", True, run_id=old) is True
    new = ss.batch_task_start("l all", key, ["A", "B"])
    before = _read_ledger(tmp_path / "batch_tasks.json")

    assert ss.batch_task_mark("l all", key, "A", True, run_id=old) is False, "旧 run 晚到必须拒绝"
    assert ss.batch_task_mark("l all", key, "A", True) is False, "缺令牌必须拒绝"
    assert ss.batch_task_mark("l all", key, "A", True, run_id="other-run") is False
    assert ss.batch_task_mark("l all", key, "Z", True, run_id=new) is False, "清单外代码必须拒绝"
    assert ss.batch_task_mark("l all", key, "A", True, run_id=new, degraded="yes") is False, \
        "degraded 非布尔值/未知状态不得当作成功"
    assert _read_ledger(tmp_path / "batch_tasks.json") == before, "拒绝路径不得改动账本"

    assert ss.batch_task_mark("l all", key, "A", True, run_id=new) is True
    latest = ss.get_recent_batch_tasks(99)[0]
    assert "A" in latest["done"] and "A" not in latest["failed"]
    assert "A" not in latest.get("degraded", {})


def test_success_failure_degraded_transitions_are_mutually_exclusive(monkeypatch, tmp_path):
    _redirect_ledger(monkeypatch, tmp_path)
    key = "ba bz scan 测试"
    token = ss.batch_task_start("ba", key, ["A"])
    assert ss.batch_task_mark("ba", key, "A", True, run_id=token, degraded=True) is True
    t = ss.get_recent_batch_tasks(99)[0]
    assert "A" in t["degraded"] and "A" not in t["done"] and "A" not in t["failed"]

    assert ss.batch_task_mark("ba", key, "A", True, run_id=token) is True
    t = ss.get_recent_batch_tasks(99)[0]
    assert "A" in t["done"] and "A" not in t["degraded"] and "A" not in t["failed"]

    assert ss.batch_task_mark("ba", key, "A", False, "数据获取失败", run_id=token) is True
    t = ss.get_recent_batch_tasks(99)[0]
    assert "A" in t["failed"] and "A" not in t["done"] and "A" not in t["degraded"]


# ── 7：原子写失败保旧文件 + 入口提示 ─────────────────────

def test_atomic_write_failure_keeps_old_file(monkeypatch, tmp_path):
    path = _redirect_ledger(monkeypatch, tmp_path)
    token = ss.batch_task_start("l", "k", ["A", "B"])
    assert ss.batch_task_mark("l", "k", "A", True, run_id=token) is True
    before = path.read_bytes()

    monkeypatch.setattr(ss, "_atomic_write_json", lambda *a, **k: False)
    assert ss.batch_task_start("l", "k", ["A", "B"]) is None, "写入失败必须明确返回 None"
    assert path.read_bytes() == before, "start 写失败必须保留旧文件"
    assert ss.batch_task_mark("l", "k", "B", True, run_id=token) is False
    assert path.read_bytes() == before, "mark 写失败必须保留旧文件"

    # 新 run 令牌不能凭空出现：旧文件仍只含原 run
    tasks = ss.get_recent_batch_tasks(99)
    assert len(tasks) == 1 and tasks[0]["run_id"] == token


def test_atomic_write_raise_keeps_old_file(monkeypatch, tmp_path):
    path = _redirect_ledger(monkeypatch, tmp_path)
    token = ss.batch_task_start("l", "k", ["A"])
    before = path.read_bytes()

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ss, "_atomic_write_json", _boom)
    assert ss.batch_task_start("l", "k", ["A"]) is None
    assert path.read_bytes() == before
    assert ss.get_recent_batch_tasks(99)[0]["run_id"] == token


def test_keep_latest_five_runs_with_identity(monkeypatch, tmp_path):
    _redirect_ledger(monkeypatch, tmp_path)
    tokens = [ss.batch_task_start("l all", f"任务{i}", ["A"]) for i in range(7)]
    tasks = ss.get_recent_batch_tasks(99)
    assert len(tasks) == 5
    assert [t["run_id"] for t in tasks] == list(reversed(tokens))[:5]
    assert [t["key"] for t in tasks] == [f"任务{i}" for i in range(6, 1, -1)]


# ── 任务视图：run 身份、真实计数、旧标签、重跑提示 ─────────

def test_tasks_view_shows_run_identity_counts_and_legacy_label(monkeypatch, tmp_path, capsys):
    path = _redirect_ledger(monkeypatch, tmp_path)
    _seed_ledger(path, [_legacy_record(kind="l all", key="用户旧记录",
                                       codes=("600519", "000001"))])
    token = ss.batch_task_start("ba", "bz scan 测试", ["A", "B", "C", "D"])
    ss.batch_task_mark("ba", "bz scan 测试", "A", True, run_id=token)
    ss.batch_task_mark("ba", "bz scan 测试", "B", True, run_id=token, degraded=True)
    ss.batch_task_mark("ba", "bz scan 测试", "C", False, "AI 超时", run_id=token)

    from src.cli import main as cli_main
    cli_main.batch_tasks_cmd()
    out = capsys.readouterr().out
    flat = out.replace("\n", "").replace(" ", "")   # rich 按终端宽度折行，断言前拉平

    assert token in out, "任务视图必须显示本次 run 身份"
    assert "已完成 1" in out and "降级 1" in out and "失败 1" in out and "未记录结果 1" in out
    assert "完整成功" not in out and "未跑" not in out
    assert "旧记录" in out and "不补造" in out, "旧无 run_id 记录必须显式标注且不补造身份"
    assert "配置" in out and ("未冻结" in out or "未记录" in out)
    assert "任务记录不等于缓存资格" in flat, "重跑提示必须说明任务记录不等于缓存资格"
    assert "自动跳成功" not in flat and "零AI" not in flat, "不得泛称重跑自动跳成功/零AI"
    assert "⚠ B" in out and "✗ C" in out, "降级/失败项必须逐项可见"


# ── 任务视图只读隔离：嵌套坏账本不假成功、不崩溃、不改文件 ─────

def _render_tasks(monkeypatch, tmp_path, capsys, records):
    path = _redirect_ledger(monkeypatch, tmp_path)
    _seed_ledger(path, records)
    before = path.read_bytes()
    from src.cli import main as cli_main
    cli_main.batch_tasks_cmd()          # 坏账本也不得抛
    out = capsys.readouterr().out
    assert path.read_bytes() == before, "tasks 只读展示不得改写坏账本"
    return out, out.replace("\n", "").replace(" ", "")


def _bad_record(kind="l all", key="坏记录"):
    return {"kind": kind, "key": key, "codes": ["A"], "done": "A",
            "degraded": ["A"], "failed": ["A"],
            "ts_started": "2026-10-26T01:00:00", "ts_updated": "2026-10-26T01:01:00"}


def test_tasks_view_isolates_bad_field_types(monkeypatch, tmp_path, capsys):
    """codes=['A'], done='A', degraded/failed=['A'] → 标记录异常，不假成功、不 .items 崩溃。"""
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [_bad_record()])
    assert "记录异常" in flat
    assert "字段类型非法" in flat
    assert "已完成0" in flat, "非法状态不得假成功"
    assert "只读" in flat


def test_tasks_view_flags_conflicting_code_states(monkeypatch, tmp_path, capsys):
    """同一 code 同时在 done+failed / degraded+failed → 标状态冲突，不计入任何状态。"""
    rec = {"kind": "ba", "key": "冲突记录", "codes": ["A", "B"],
           "done": {"A": "2026-10-26T01:00:00"}, "degraded": {"B": "降级"},
           "failed": {"A": "失败A", "B": "失败B"},
           "ts_started": "t", "ts_updated": "t"}
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "状态冲突2" in flat
    assert "已完成0" in flat and "降级0" in flat and "失败0" in flat
    assert "未记录结果0" in flat
    assert "A" in out and "B" in out
    assert "已完成1" not in flat


def test_tasks_view_bad_codes_is_unknown_not_success(monkeypatch, tmp_path, capsys):
    """codes 非列表 → 进度未知（无法核对），不得把 done 条目冒充完成。"""
    rec = {"kind": "l", "key": "坏清单", "codes": "A", "done": {"A": "t"},
           "degraded": {}, "failed": {}, "ts_started": "t", "ts_updated": "t"}
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "记录异常" in flat
    assert "清单不可读" in flat or "无法核对" in flat
    assert "已完成 1" not in flat and "已完成 0/1" not in flat


def test_tasks_view_ignores_foreign_entries(monkeypatch, tmp_path, capsys):
    """状态映射含清单外代码 → 标清单外、不计入完成/失败。"""
    rec = {"kind": "l all", "key": "外来条目", "codes": ["A"],
           "done": {"A": "t", "Z": "t"}, "degraded": {}, "failed": {"Z2": "坏"},
           "ts_started": "t", "ts_updated": "t"}
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "清单外" in flat
    assert "已完成1" in flat
    assert "失败0" in flat, "清单外失败条目不得计数"
    assert "已完成2" not in flat


def test_tasks_view_invalid_done_values_do_not_count_as_success(monkeypatch, tmp_path, capsys):
    """done 值为 None/[]/{}/空串 → 只读隔离待确认，绝不假计已完成；真实记录照常计数。"""
    rec = {"kind": "l all", "key": "坏 done 值", "codes": ["A", "B", "C", "D", "E"],
           "done": {"A": None, "B": [], "C": {}, "D": "",
                    "E": "2026-10-26T01:00:00"},
           "degraded": {}, "failed": {},
           "ts_started": "t", "ts_updated": "t"}
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "记录异常" in flat and "无效记录值" in flat and "待确认" in flat
    assert "A:done记录值无效" in flat and "B:done记录值无效" in flat
    assert "C:done记录值无效" in flat and "D:done记录值无效" in flat
    assert "已完成1/5" in flat, "只有真实字符串记录计入已完成"
    assert "已完成5" not in flat and "已完成4" not in flat, "坏值不得假计已完成"
    assert "未记录结果4" in flat


def test_tasks_view_invalid_degraded_and_failed_values_not_counted(monkeypatch, tmp_path, capsys):
    """degraded/failed 同类坏值（None/空串/[]/数字）→ 隔离待确认，不假计降级/失败。"""
    rec = {"kind": "ba", "key": "坏 degraded/failed 值", "codes": ["A", "B", "C", "D", "E"],
           "done": {"A": "2026-10-26T01:00:00"},
           "degraded": {"B": None, "C": ""},
           "failed": {"D": [], "E": 0},
           "ts_started": "t", "ts_updated": "t"}
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "B:degraded记录值无效" in flat and "C:degraded记录值无效" in flat
    assert "D:failed记录值无效" in flat and "E:failed记录值无效" in flat
    assert "已完成1/5" in flat and "降级0" in flat and "失败0" in flat
    assert "降级2" not in flat and "失败2" not in flat, "坏值不得假计降级/失败"
    assert "未记录结果4" in flat


def test_tasks_view_invalid_done_value_does_not_hide_valid_failed(monkeypatch, tmp_path, capsys):
    """同一代码 done 值坏、failed 记录真实 → 只标 done 待确认，失败照常计数、不伪造冲突。"""
    rec = {"kind": "ba", "key": "done坏值+失败真实", "codes": ["A"],
           "done": {"A": None}, "degraded": {}, "failed": {"A": "AI 超时"},
           "ts_started": "t", "ts_updated": "t"}
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [rec])
    assert "A:done记录值无效" in flat
    assert "已完成0/1" in flat and "失败1" in flat
    assert "状态冲突" not in flat, "坏值不是有效状态，不得伪造状态冲突"
    assert "✗ A" in out


# ── 8：真实四入口绑定 start 令牌 ──────────────────────────

def _fake_account_facts(entries=None, incomplete=False):
    facts = mock.MagicMock(name="acct_facts")
    facts.holding_entries.return_value = (list(entries or []), incomplete)
    return facts


def test_live_multi_entry_binds_run_token(monkeypatch, capsys):
    seen = _spy_ledger(monkeypatch, run_id="RUN-L-MULTI")
    import start
    import src.cli.main as cli_main
    import src.data.akshare_client as ak_mod
    import src.data.portfolio as portfolio_mod

    monkeypatch.setattr(ak_mod.AKShareClient, "prefetch_realtime_quotes",
                        staticmethod(lambda codes: len(codes)))
    pm = mock.MagicMock(name="PortfolioManager")
    pm.return_value.request_account_facts.return_value = _fake_account_facts()
    monkeypatch.setattr(portfolio_mod, "PortfolioManager", pm)
    monkeypatch.setattr(ss, "mark_deep_analyzed", lambda *a, **k: True)

    def fake_analyze(code, ai_overrides=None, ai_debug=False, compact=False, account_facts=None):
        if code == "000001":
            raise SystemExit
        return cli_main.LiveOutcome.COMPLETE   # T05-L：完整分析才记完成

    monkeypatch.setattr(cli_main, "analyze_live", fake_analyze)
    start.run_cli("live_multi", {"codes": ["600519", "000001"]})
    out = capsys.readouterr().out

    assert seen["start"], "l 多代码必须登记任务并接收令牌"
    assert seen["start"][0] == ("l", "手动多代码 " + "600519,000001", ["600519", "000001"])
    assert "RUN-L-MULTI" in out, "入口必须给用户可见 run 身份"
    assert [(m["code"], m["ok"], m["run_id"]) for m in seen["mark"]] == [
        ("600519", True, "RUN-L-MULTI"), ("000001", False, "RUN-L-MULTI")]


def test_live_all_entry_binds_run_token(monkeypatch, capsys):
    seen = _spy_ledger(monkeypatch, run_id="RUN-LA")
    import start
    import src.cli.main as cli_main
    import src.data.portfolio as portfolio_mod

    e1 = mock.MagicMock(stock_code="600519", stock_name="茅台", from_ledger=False)
    e2 = mock.MagicMock(stock_code="000001", stock_name="平安", from_ledger=False)
    pm = mock.MagicMock(name="PortfolioManager")
    pm.return_value.request_account_facts.return_value = _fake_account_facts([e1, e2])
    monkeypatch.setattr(portfolio_mod, "PortfolioManager", pm)
    monkeypatch.setattr(cli_main, "analyze_live",
                        lambda code, **kw: cli_main.LiveOutcome.COMPLETE)   # T05-L
    start.run_cli("live_all", {})
    out = capsys.readouterr().out

    assert seen["start"] and seen["start"][0][0] == "la"
    assert "RUN-LA" in out
    assert [(m["code"], m["ok"], m["run_id"]) for m in seen["mark"]] == [
        ("600519", True, "RUN-LA"), ("000001", True, "RUN-LA")]


def test_live_scan_all_entry_binds_run_token(monkeypatch, capsys):
    seen = _spy_ledger(monkeypatch, run_id="RUN-L-ALL")
    import start
    import src.cli.main as cli_main
    from datetime import datetime

    monkeypatch.setattr(ss, "get_last_scan", lambda: {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "source": "bz scan 测试",
        "items": [{"code": "600519", "name": "茅台"}, {"code": "000001", "name": "平安"}],
    })
    monkeypatch.setattr(ss, "get_deep_analyzed", lambda: {})
    monkeypatch.setattr(ss, "mark_deep_analyzed", lambda *a, **k: True)
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    monkeypatch.setattr(cli_main, "analyze_live",
                        lambda code, **kw: (_ for _ in ()).throw(SystemExit) if code == "000001"
                        else cli_main.LiveOutcome.COMPLETE)   # T05-L：完整分析才记完成
    start.run_cli("live_scan_all", {})
    out = capsys.readouterr().out

    assert seen["start"] and seen["start"][0][0] == "l all"
    assert "RUN-L-ALL" in out
    assert [(m["code"], m["ok"], m["run_id"]) for m in seen["mark"]] == [
        ("600519", True, "RUN-L-ALL"), ("000001", False, "RUN-L-ALL")]


def test_ba_entry_binds_run_token_and_records_structured_status(monkeypatch, capsys):
    """ba 只按结构化结果记账：progress 文案不决定成败，末尾不按 kind/key 盲补。"""
    seen = _spy_ledger(monkeypatch, run_id="RUN-BA")
    import src.cli.main as cli_main

    def fake_batch(codes, top_n=None, force_refresh=False, config=None,
                   progress_cb=None, result_cb=None):
        recs = [
            {"code": "600519", "ok": True, "status": "complete", "total_score": 80},
            {"code": "000001", "ok": True, "status": "degraded",
             "degraded_dims": ["risk_deduction"]},
            {"code": "300750", "ok": False, "status": "failed", "error": "RuntimeError: 网络挂"},
        ]
        ranked, failures = [], []
        for i, rec in enumerate(recs, 1):
            # progress 文案故意与结构化结果相反，锁“展示不参与记账”
            status_text = "失败" if rec["ok"] else "50/B"
            if progress_cb:
                progress_cb(i, len(codes), rec["code"], status_text)
            if result_cb:
                result_cb(dict(rec))
            if rec["ok"]:
                ranked.append({"code": rec["code"], "name": rec["code"],
                               "total_score": 50, "normalized_score": 50,
                               "grade": "B", "effective_grade": "B", "confidence": 0.5,
                               "dim_scores": {}, "dim_confidences": {},
                               "warnings": [], "invalidate": False,
                               "scoring_status": rec["status"],
                               "degraded_dims": rec.get("degraded_dims", [])})
            else:
                failures.append({"code": rec["code"], "error": rec["error"]})
        ranked.append({"code": "GHOST", "name": "GHOST", "total_score": 1,
                       "normalized_score": 1, "grade": "F", "effective_grade": "F",
                       "confidence": 0, "dim_scores": {}, "dim_confidences": {}})
        return {"ranked": ranked, "top_n": ranked[:2], "failures": failures,
                "scanned": len(codes), "scored": len(ranked), "failed": len(failures)}

    monkeypatch.setattr("src.core.benzong.batch_scorer.auto_score_batch", fake_batch)
    cli_main.benzong_batch_analyze(
        [{"code": "600519"}, {"code": "000001"}, {"code": "300750"}],
        src_label="bz scan 测试")
    out = capsys.readouterr().out

    assert seen["start"] and seen["start"][0] == (
        "ba", "bz scan 测试", ["600519", "000001", "300750"])
    assert "RUN-BA" in out
    by_code = {m["code"]: m for m in seen["mark"]}
    assert by_code["600519"]["run_id"] == "RUN-BA" and by_code["600519"]["ok"] is True
    assert by_code["000001"]["run_id"] == "RUN-BA" and by_code["000001"]["ok"] is True
    assert by_code["000001"]["degraded"] is True, "部分维度未评出必须记降级而非已完成"
    assert by_code["300750"]["ok"] is False and by_code["300750"]["run_id"] == "RUN-BA"
    assert "GHOST" not in by_code, "结束不得按 kind/key 盲目补记 ranked 条目"
    assert len(seen["mark"]) == 3, "每个结构化结果只记一次（progress 不重复记账）"
    assert "已完成 1" in out and "降级 1" in out and "失败 1" in out and "未记录结果 0" in out


def test_ba_entry_ignores_unknown_structured_result(monkeypatch, capsys):
    """坏/未知结构化结果（缺 ok、状态非法）不伪造成功也不伪造失败。"""
    seen = _spy_ledger(monkeypatch, run_id="RUN-BA-UNKNOWN")
    import src.cli.main as cli_main

    def fake_batch(codes, top_n=None, force_refresh=False, config=None,
                   progress_cb=None, result_cb=None):
        if result_cb:
            result_cb({"code": codes[0]})   # 缺 ok/status：未知
        return {"ranked": [{"code": codes[0], "name": codes[0], "total_score": 50,
                            "normalized_score": 50, "grade": "B", "effective_grade": "B",
                            "confidence": 0.5, "dim_scores": {}, "dim_confidences": {},
                            "warnings": [], "invalidate": False}],
                "top_n": [], "failures": [], "scanned": 1, "scored": 1, "failed": 0}

    monkeypatch.setattr("src.core.benzong.batch_scorer.auto_score_batch", fake_batch)
    cli_main.benzong_batch_analyze([{"code": "600519"}], src_label="x")
    out = capsys.readouterr().out
    assert seen["mark"] == [], "未知结果不得记成功或失败"
    assert "结果记录不可靠" in out, "未知结果必须显式告知记录不可靠（不能静默）"
    assert "未记录结果 1" in out
    assert "未跑" not in out and "已完成 1" not in out


def test_ba_entry_ignores_unknown_status_on_ok_record(monkeypatch, capsys):
    """ok=True 但 status 非 complete/degraded → 未知状态不冒充完整成功。"""
    seen = _spy_ledger(monkeypatch, run_id="RUN-BA-UNKNOWN-2")
    import src.cli.main as cli_main

    def fake_batch(codes, top_n=None, force_refresh=False, config=None,
                   progress_cb=None, result_cb=None):
        if result_cb:
            result_cb({"code": codes[0], "ok": True, "status": "weird"})
        return {"ranked": [{"code": codes[0], "name": codes[0], "total_score": 50,
                            "normalized_score": 50, "grade": "B", "effective_grade": "B",
                            "confidence": 0.5, "dim_scores": {}, "dim_confidences": {},
                            "warnings": [], "invalidate": False}],
                "top_n": [], "failures": [], "scanned": 1, "scored": 1, "failed": 0}

    monkeypatch.setattr("src.core.benzong.batch_scorer.auto_score_batch", fake_batch)
    cli_main.benzong_batch_analyze([{"code": "600519"}], src_label="x")
    out = capsys.readouterr().out
    assert seen["mark"] == [], "非法状态不得伪造成功"
    assert "结果记录不可靠" in out, "未知状态必须显式告知记录不可靠（不能静默 return）"
    assert "未记录结果 1" in out
    assert "未跑" not in out and "已完成 1" not in out


def test_ba_entry_rejects_foreign_inconsistent_and_duplicate_events(monkeypatch, capsys):
    """回调事件须属于本 roster 且合法才计数：外来/矛盾/重复一律不记完成并报不可靠。"""
    seen = _spy_ledger(monkeypatch, run_id="RUN-BA-BAD")
    import src.cli.main as cli_main

    def fake_batch(codes, top_n=None, force_refresh=False, config=None,
                   progress_cb=None, result_cb=None):
        legal = {"code": codes[0], "ok": True, "status": "complete"}
        events = [
            {"code": "999999", "ok": True, "status": "complete"},     # 外来代码
            {"code": codes[0], "ok": True, "status": "complete",
             "degraded_dims": ["risk_deduction"]},                   # complete 却有降级维（矛盾）
            {"code": codes[0], "ok": True, "status": "degraded",
             "degraded_dims": []},                                   # degraded 却无降级维（矛盾）
            {"code": codes[0], "ok": "yes", "status": "complete"},   # ok 非 bool
            {"code": codes[0], "ok": False, "status": "complete"},   # failed 状态非法
            {"code": None, "ok": True, "status": "complete"},        # code 缺失
            [1, 2, 3],                                               # 非 dict 事件
        ]
        for ev in events:
            result_cb(ev)
        result_cb(dict(legal))
        result_cb(dict(legal))                                       # 重复事件
        row = {"code": codes[0], "name": codes[0], "total_score": 50,
               "normalized_score": 50, "grade": "B", "effective_grade": "B",
               "confidence": 0.5, "dim_scores": {}, "dim_confidences": {},
               "warnings": [], "invalidate": False}
        return {"ranked": [row], "top_n": [row], "failures": [],
                "scanned": 1, "scored": 1, "failed": 0}

    monkeypatch.setattr("src.core.benzong.batch_scorer.auto_score_batch", fake_batch)
    cli_main.benzong_batch_analyze([{"code": "600519"}], src_label="x")
    out = capsys.readouterr().out
    assert "结果记录不可靠" in out
    assert "已完成 1" in out, "合法事件只计一次（重复/外来/矛盾均不重复计数）"
    assert "降级 0" in out and "失败 0" in out and "未记录结果 0" in out
    assert sum(1 for m in seen["mark"] if m["code"] == "600519" and m["ok"] is True) == 1


def test_ba_entry_reports_ledger_save_failure(monkeypatch, capsys):
    """start 返回 None → 用户可见保存失败提示，分析仍继续（不改业务结果）。"""
    import src.cli.main as cli_main

    monkeypatch.setattr(ss, "batch_task_start",
                        lambda kind, key, codes, snapshot=None: None)
    monkeypatch.setattr(ss, "batch_task_mark", lambda *a, **k: False)

    def fake_batch(codes, top_n=None, force_refresh=False, config=None,
                   progress_cb=None, result_cb=None):
        if progress_cb:
            progress_cb(1, len(codes), codes[0], "50/B")
        if result_cb:
            result_cb({"code": codes[0], "ok": True, "status": "complete"})
        return {"ranked": [{"code": codes[0], "name": codes[0], "total_score": 50,
                            "normalized_score": 50, "grade": "B", "effective_grade": "B",
                            "confidence": 0.5, "dim_scores": {}, "dim_confidences": {},
                            "warnings": [], "invalidate": False}],
                "top_n": [], "failures": [], "scanned": 1, "scored": 1, "failed": 0}

    monkeypatch.setattr("src.core.benzong.batch_scorer.auto_score_batch", fake_batch)
    cli_main.benzong_batch_analyze([{"code": "600519"}], src_label="x")
    out = capsys.readouterr().out
    assert "保存失败" in out and "不影响" in out


def test_ba_entry_reports_mark_write_failure(monkeypatch, tmp_path, capsys):
    """mark 写盘失败 → 用户可见“进度可能不完整”，但评分结果照常输出。"""
    _redirect_ledger(monkeypatch, tmp_path)
    import src.cli.main as cli_main

    def fake_batch(codes, top_n=None, force_refresh=False, config=None,
                   progress_cb=None, result_cb=None):
        monkeypatch.setattr(ss, "_atomic_write_json", lambda *a, **k: False)
        if result_cb:
            result_cb({"code": codes[0], "ok": True, "status": "complete"})
        return {"ranked": [{"code": codes[0], "name": codes[0], "total_score": 50,
                            "normalized_score": 50, "grade": "B", "effective_grade": "B",
                            "confidence": 0.5, "dim_scores": {}, "dim_confidences": {},
                            "warnings": [], "invalidate": False}],
                "top_n": [], "failures": [], "scanned": 1, "scored": 1, "failed": 0}

    monkeypatch.setattr("src.core.benzong.batch_scorer.auto_score_batch", fake_batch)
    cli_main.benzong_batch_analyze([{"code": "600519"}], src_label="x")
    out = capsys.readouterr().out
    assert "写入失败" in out and "进度可能不完整" in out


# ── 4：ba 呈现直接消费 degraded_dims（不展示数值/不参与强弱点评）──

def test_ba_dim_cell_hides_degraded_dim_even_with_numeric_score():
    from src.cli.main import _ba_dim_cell
    row = {"dim_scores": {"industry_prosperity": 95},
           "dim_confidences": {"industry_prosperity": None},
           "degraded_dims": ["industry_prosperity"]}
    assert _ba_dim_cell(row, "industry_prosperity") == "未评出", \
        "degraded_dims 内的维度即使有数值分也必须显示未评出"


def test_ba_presentation_excludes_degraded_dim_values_and_commentary(monkeypatch, capsys):
    """实际 ba 呈现：降级维不显示数值、不参与最强/最弱点评。"""
    _spy_ledger(monkeypatch, run_id="RUN-BA-PRES")
    import src.cli.main as cli_main

    def fake_batch(codes, top_n=None, force_refresh=False, config=None,
                   progress_cb=None, result_cb=None):
        if result_cb:
            result_cb({"code": codes[0], "ok": True, "status": "degraded",
                       "degraded_dims": ["industry_prosperity"]})
        row = {
            "code": codes[0], "name": "茅台", "total_score": 80,
            "normalized_score": 80, "grade": "B", "effective_grade": "B",
            "confidence": 0.6, "invalidate": False,
            "dim_scores": {"industry_prosperity": 95, "business_purity": 70,
                           "valuation_position": 60, "industry_leader": 65,
                           "market_recognition": 55, "risk_deduction": 50},
            "dim_confidences": {"industry_prosperity": None, "business_purity": 0.8,
                                "valuation_position": 0.8, "industry_leader": 0.8,
                                "market_recognition": 0.8, "risk_deduction": 0.8},
            "degraded_dims": ["industry_prosperity"],
            "scoring_status": "degraded", "warnings": [],
        }
        return {"ranked": [row], "top_n": [row], "failures": [],
                "scanned": 1, "scored": 1, "failed": 0}

    monkeypatch.setattr("src.core.benzong.batch_scorer.auto_score_batch", fake_batch)
    cli_main.benzong_batch_analyze([{"code": "600519"}], src_label="x")
    out = capsys.readouterr().out
    flat = out.replace("\n", "").replace(" ", "")

    assert "行业景气最强" not in flat, "降级维度不得作为最强点评"
    assert "主业纯度最强" in flat, "点评应使用未降级维度中的真实最高分"
    assert "95" not in out, "降级维度的数值分不得显示在表格/点评里"
    assert "未评出" in out
    assert "降级1" in flat and "已完成0" in flat
