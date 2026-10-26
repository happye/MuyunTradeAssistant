"""T05-I：ba/bz 多代码共用批评分的执行参数快照与批次配置冻结（V1-G7-BATCH-BUDGET 有限子批）。

行为锁（先红后绿，全部有限 mock，不触网/不花 AI/不写真实 ~/.muyun）：
1. 整批只解析一次配置（config=None 时 load_config 恰一次）并深拷贝；调用方原 dict 的
   后续突变不得影响逐股评分；客户端构造与每只 auto_score 共用同一份快照。
2. 批次 AI client 构造失败时逐股不得重建 client、不得逐股重读配置；
   auto_score 默认（单股）行为保持不变。
3. 账本只保存白名单非秘密执行参数（provider/model/request_timeout/max_retries/
   force_refresh/cache_version）+ schema + 确定性 SHA256；不保存完整配置、端点 URL、
   密钥或密钥摘要；凭证轮换与键序不改变身份，公开参数变化改变身份；
   配置坏值派生一律 fail-closed（不把任意字符串写成身份）。
4. ba 在评分/取数之前登记 run 且写入与执行同一份冻结配置派生的描述；旧/缺/坏/被篡改
   描述只读显示为未冻结，读侧按 schema/类型/范围/hash 校验后才渲染（纯文本，不解析
   markup）；la/l all/多代码深析仍未冻结；快照只是元数据，不授予缓存/信号资格。
5. 可选描述不影响 T05-H 既有语义：run 令牌、roster、降级/失败转移与写盘失败保护不变。

参考实现坐标：batch_scorer.auto_score_batch / derive_exec_snapshot /
validate_exec_snapshot / freeze_batch_config；auto_scorer.auto_score(allow_client_rebuild)；
session_state.batch_task_start(snapshot=...)；main.benzong_batch_analyze / batch_tasks_cmd。
"""

import copy
import hashlib
import json
import logging
import os
import re
import sys
import unittest.mock as mock

import openai

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import src.cli.main as cli_main
import src.cli.session_state as ss
import src.config as config_module
import src.core.benzong.auto_scorer as auto_scorer
import src.core.benzong.batch_scorer as batch_scorer

_DIMS = ("industry_prosperity", "business_purity", "valuation_position",
         "industry_leader", "market_recognition", "risk_deduction")
_PARAM_KEYS = {"provider", "model", "request_timeout", "max_retries",
               "force_refresh", "cache_version"}
_SECRET_KEY = "sk-secret-UNIQUE-T05I-0001"
_SECRET_HOST = "https://api.secret-host.invalid/v1"
_SHA_RE = re.compile(r"\A[0-9a-f]{64}\Z")


# ── 工具 ─────────────────────────────────────────────────────

def _valid_config():
    """真实形状的 settings 片段（含密钥/端点，用于证明它们不落账本）。"""
    return {
        "ai": {
            "provider": "deepseek",
            "request_timeout": 45,
            "max_retries": 1,
            "deepseek": {"api_key": _SECRET_KEY, "base_url": _SECRET_HOST,
                         "model": "deepseek-flash", "context_window": 65536},
        },
        "rag": {"enabled": False},
        "entry_exit": {"enabled": True},
    }


def _structured_result(total, name="测试", conf=0.8):
    """batch_scorer 需要的 AutoScoredResult 形状（6 维 conf 有效 → complete）。"""
    r = mock.MagicMock()
    r.score.total_score = total
    r.score.grade.return_value = "B"
    r.score.effective_grade.return_value = "B"
    r.score.normalized_score.return_value = float(total)
    r.score.stock_name = name
    r.overall_confidence = conf
    r.warnings = []
    r.invalidate = False
    r.dimensions_meta = {d: {"score": total, "confidence": conf} for d in _DIMS}
    return r


def _redirect_ledger(monkeypatch, tmp_path):
    path = tmp_path / "batch_tasks.json"
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", path)
    return path


def _seed_ledger(path, tasks):
    path.write_text(json.dumps({"tasks": tasks}, ensure_ascii=False), encoding="utf-8")


def _read_ledger(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _flat(text):
    return text.replace("\n", "").replace(" ", "")


# ── 1. 批次配置冻结（once + deep copy + 共用快照）──────────────

def test_batch_freezes_one_deep_copied_config_and_caller_mutation_does_not_leak(monkeypatch):
    """调用方原 dict 之后的突变不进逐股评分；client 与所有 auto_score 共用同一快照。"""
    cfg = _valid_config()
    builds, calls = [], []

    def fake_build(config=None):
        builds.append(config)
        return object(), "deepseek-flash"

    def fake_auto(code, **kw):
        calls.append({"code": code, "config": kw.get("config"),
                      "allow": kw.get("allow_client_rebuild")})
        cfg["ai"]["deepseek"]["model"] = "caller-mutated"   # 模拟批次开始后调用方改写原 dict
        return _structured_result(60, name=code)

    monkeypatch.setattr(batch_scorer, "_build_ai_client", fake_build)
    monkeypatch.setattr(batch_scorer, "auto_score", fake_auto)
    monkeypatch.setattr(batch_scorer, "get_market_turnover", lambda: 1.2)
    # fix2：显式提供配置时不得再触发 load_config（不能用"没提供"语义重读磁盘）
    monkeypatch.setattr(config_module, "load_config",
                        lambda: (_ for _ in ()).throw(AssertionError("已提供配置时不得重读")))

    res = batch_scorer.auto_score_batch(["A", "B"], top_n=10, config=cfg)

    assert res["scored"] == 2
    frozen = calls[0]["config"]
    assert frozen is not cfg, "批次必须用深拷贝快照，不能把调用方原 dict 传下去"
    assert builds[0] is frozen, "client 构造与逐股评分必须用同一份快照"
    assert all(c["config"] is frozen for c in calls), "整批必须共用同一冻结配置"
    assert calls[1]["config"]["ai"]["deepseek"]["model"] == "deepseek-flash", \
        "调用方原 dict 的突变不得影响后续股票"
    assert all(c["allow"] is False for c in calls), \
        "批量必须显式禁止 auto_score 逐股重建 client/重读配置"


def test_batch_none_config_resolves_once_before_client_and_scoring(monkeypatch):
    """config=None：整批只 load_config 一次（客户端构造/逐股评分之前）。"""
    cfg = _valid_config()
    loads = []
    monkeypatch.setattr(config_module, "load_config", lambda: loads.append("load") or cfg)
    monkeypatch.setattr(batch_scorer, "_build_ai_client",
                        lambda config=None: (object(), "m"))
    monkeypatch.setattr(batch_scorer, "auto_score",
                        lambda code, **kw: _structured_result(60, name=code))
    monkeypatch.setattr(batch_scorer, "get_market_turnover", lambda: 1.2)

    batch_scorer.auto_score_batch(["A", "B", "C"])

    assert loads == ["load"], f"整批只允许解析一次配置，实际 {loads}"


def _fake_dims():
    def scorer(code, name, data_summary=None, ai_client=None, ai_model=None,
               rag_service=None):
        return {"score": 50, "confidence": 0.0, "sources": [], "reasoning": "mock",
                "data_freshness": "N/A", "warnings": []}
    return {d: scorer for d in _DIMS}


def _summary():
    return {"industry": {"stock_name": "测试", "industry_name": "测试行业"},
            "market_turnover": 1.2, "fetch_status": {}}


def _offline_auto_score(monkeypatch):
    """把真实 auto_score 的取数/缓存/维度替换为有限替身（保留 client 重建决策）。"""
    monkeypatch.setattr(auto_scorer, "_lazy_load_dimensions", _fake_dims)
    monkeypatch.setattr(auto_scorer.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(auto_scorer.cache, "set", lambda *a, **k: None)
    monkeypatch.setattr(auto_scorer.data_provider, "get_data_summary",
                        lambda code, market_turnover=None: _summary())


def test_missing_batch_client_never_rebuilds_or_reloads_per_stock(monkeypatch):
    """批次 client 构造失败：client 仍只构造一次，逐股不得重建/重读配置。"""
    cfg = _valid_config()
    state = {"load": 0, "batch_build": 0, "score_build": 0}

    def load():
        state["load"] += 1
        return cfg

    def batch_build(config=None):
        state["batch_build"] += 1
        return None, None

    def score_build(config=None):
        state["score_build"] += 1
        return None, None

    monkeypatch.setattr(config_module, "load_config", load)
    monkeypatch.setattr(batch_scorer, "_build_ai_client", batch_build)
    monkeypatch.setattr(auto_scorer, "_build_ai_client", score_build)
    monkeypatch.setattr(batch_scorer, "get_market_turnover", lambda: 1.2)
    _offline_auto_score(monkeypatch)

    res = batch_scorer.auto_score_batch(["600519", "000001", "300750"])

    assert state["batch_build"] == 1, "批次 AI client 只允许构造一次（含失败）"
    assert state["score_build"] == 0, \
        "批量客户端缺失时逐股不得重建 client（重建=逐股重读配置）"
    assert state["load"] == 1, "整批只解析一次配置，失败路径也不得逐股重读"
    assert res["scored"] == 3 and res["failed"] == 0


def test_auto_score_batch_flag_suppresses_rebuild_and_default_keeps_it(monkeypatch):
    """auto_score 新增显式入参：批量传 False 抑制重建；单股默认行为不变。"""
    state = {"build": 0}
    _offline_auto_score(monkeypatch)

    def build(config=None):
        state["build"] += 1
        return None, None

    monkeypatch.setattr(auto_scorer, "_build_ai_client", build)

    auto_scorer.auto_score("600519", data_summary=_summary(), today="2026-10-26",
                           allow_client_rebuild=False)
    assert state["build"] == 0, "显式 False 时不得重建 client"

    auto_scorer.auto_score("600519", data_summary=_summary(), today="2026-10-26")
    assert state["build"] == 1, "单股默认行为必须保持不变（仍自动构造 client）"


# ── 2. 白名单快照：schema/hash/秘密排除/确定性 ─────────────────

def test_derive_snapshot_only_allowlisted_params_with_schema_and_sha256():
    snap = batch_scorer.derive_exec_snapshot(_valid_config(), False)

    assert set(snap) == {"schema", "params", "sha256"}
    assert snap["schema"] == batch_scorer.EXEC_SNAPSHOT_SCHEMA
    assert set(snap["params"]) == _PARAM_KEYS
    assert snap["params"]["provider"] == "deepseek"
    assert snap["params"]["model"] == "deepseek-flash"
    assert snap["params"]["request_timeout"] == 45.0
    assert snap["params"]["max_retries"] == 1
    assert snap["params"]["force_refresh"] is False
    assert isinstance(snap["params"]["cache_version"], str)
    assert _SHA_RE.fullmatch(snap["sha256"]), "必须是 64 位小写十六进制 SHA256"

    blob = json.dumps(snap, ensure_ascii=False)
    for forbidden in (_SECRET_KEY, _SECRET_HOST, "api_key", "base_url", "context_window",
                      "entry_exit", "deepseek-flash\", \"context"):
        assert forbidden not in blob, f"快照不得包含 {forbidden!r}（非白名单/秘密/端点）"
    assert "ai" not in snap and "config" not in snap, "不得保存整份配置"

    # 同一配置重复派生 → 确定性同一身份
    assert batch_scorer.derive_exec_snapshot(_valid_config(), False) == snap


def test_snapshot_identity_ignores_credentials_key_order_but_tracks_public_params(
        monkeypatch):
    base = batch_scorer.derive_exec_snapshot(_valid_config(), False)

    rotated = copy.deepcopy(_valid_config())
    rotated["ai"]["deepseek"]["api_key"] = "sk-rotated-key"
    rotated["ai"]["deepseek"]["base_url"] = "https://other-endpoint.invalid/v2"
    assert batch_scorer.derive_exec_snapshot(rotated, False)["sha256"] == base["sha256"], \
        "凭证轮换/端点变化不得改变公开执行参数身份"

    reordered = {"entry_exit": {"enabled": True}, "rag": {"enabled": False},
                 "ai": {"deepseek": {"context_window": 1, "model": "deepseek-flash",
                                     "api_key": _SECRET_KEY},
                        "max_retries": 1, "request_timeout": 45, "provider": "deepseek"}}
    assert batch_scorer.derive_exec_snapshot(reordered, False)["sha256"] == base["sha256"], \
        "字典键序/无关配置不得改变身份"

    def _changed(mutate):
        cfg = _valid_config()
        mutate(cfg)
        return batch_scorer.derive_exec_snapshot(cfg, False)["sha256"]

    variants = {
        "model": _changed(lambda c: c["ai"]["deepseek"].__setitem__("model", "kimi-k3")),
        "provider": _changed(lambda c: c["ai"].__setitem__("provider", "kimi")),
        "timeout": _changed(lambda c: c["ai"].__setitem__("request_timeout", 46)),
        "retries": _changed(lambda c: c["ai"].__setitem__("max_retries", 2)),
        "force_refresh": batch_scorer.derive_exec_snapshot(_valid_config(), True)["sha256"],
    }
    for name, digest in variants.items():
        assert digest != base["sha256"], f"公开参数 {name} 变化必须改变身份"

    monkeypatch.setattr(batch_scorer, "CACHE_VERSION", "v9.9.9")
    assert batch_scorer.derive_exec_snapshot(_valid_config(), False)["sha256"] != base["sha256"], \
        "cache_version 属于身份参数"


def test_derive_snapshot_fails_closed_on_bad_values():
    bad = []

    def add(mutate):
        cfg = _valid_config()
        mutate(cfg)
        bad.append(cfg)

    add(lambda c: c["ai"].__setitem__("provider", 123))
    add(lambda c: c["ai"].__setitem__("provider", "Bad Provider!"))
    add(lambda c: c["ai"].__setitem__("provider", ""))
    add(lambda c: c["ai"].__setitem__("deepseek", "not-a-dict"))
    add(lambda c: c["ai"]["deepseek"].__setitem__("model", None))
    add(lambda c: c["ai"]["deepseek"].__setitem__("model", "bad model<tag>"))
    add(lambda c: c["ai"].__setitem__("request_timeout", "abc"))
    add(lambda c: c["ai"].__setitem__("request_timeout", float("nan")))
    add(lambda c: c["ai"].__setitem__("request_timeout", 0))
    add(lambda c: c["ai"].__setitem__("request_timeout", 601))
    add(lambda c: c["ai"].__setitem__("request_timeout", True))
    add(lambda c: c["ai"].__setitem__("max_retries", -1))
    add(lambda c: c["ai"].__setitem__("max_retries", 11))
    add(lambda c: c["ai"].__setitem__("max_retries", "x"))
    add(lambda c: c["ai"].__setitem__("max_retries", True))
    add(lambda c: c.__setitem__("ai", "not-a-dict"))

    for cfg in bad:
        assert batch_scorer.derive_exec_snapshot(cfg, False) is None, \
            f"坏配置必须 fail-closed（不得把任意值写成身份）: {cfg!r}"

    assert batch_scorer.derive_exec_snapshot(None, False) is None
    assert batch_scorer.derive_exec_snapshot([], False) is None
    assert batch_scorer.derive_exec_snapshot(_valid_config(), "yes") is None, \
        "force_refresh 非布尔不得派生身份"


def test_derive_snapshot_fails_closed_on_bad_cache_version(monkeypatch):
    for value in ("not-a-version", "", 123, None, "v1\n[red]x[/red]"):
        monkeypatch.setattr(batch_scorer, "CACHE_VERSION", value)
        assert batch_scorer.derive_exec_snapshot(_valid_config(), False) is None, \
            f"坏 cache_version 必须 fail-closed: {value!r}"


def test_validate_snapshot_accepts_canonical_and_rejects_tamper():
    snap = batch_scorer.derive_exec_snapshot(_valid_config(), False)

    got = batch_scorer.validate_exec_snapshot(copy.deepcopy(snap))
    assert got is not None and set(got) == {"schema", "params", "sha256"}
    assert got["params"] == snap["params"] and got["sha256"] == snap["sha256"]

    def _mutated(mutate):
        raw = copy.deepcopy(snap)
        mutate(raw)
        return raw

    tampered = [
        None, "text", [], 123,
        {"params": snap["params"]},                                  # 缺 schema/hash
        dict(snap, extra=1),                                         # 多顶层键
        dict(snap, schema="muyun.ba.other/v1"),                      # schema 不符
        dict(snap, sha256=snap["sha256"].upper()),                   # hash 非规范小写
        dict(snap, sha256="0" * 64),                                 # hash 被篡改
        _mutated(lambda r: r["params"].__setitem__("model", "hijacked")),
        _mutated(lambda r: r["params"].__setitem__("request_timeout", 99999)),
        _mutated(lambda r: r["params"].__setitem__("max_retries", True)),
        _mutated(lambda r: r["params"].__setitem__("force_refresh", 1)),
        _mutated(lambda r: r["params"].__setitem__("cache_version", "v1")),
        _mutated(lambda r: r["params"].__setitem__("provider", "[red]x[/red]")),
        _mutated(lambda r: r["params"].__setitem__("model", _SECRET_KEY)),
        _mutated(lambda r: r["params"].__setitem__("api_key", "leak")),
        _mutated(lambda r: r["params"].pop("cache_version")),
        _mutated(lambda r: r.__setitem__("params", "not-a-dict")),
        _mutated(lambda r: r["params"].__setitem__("request_timeout", "45")),  # 字符串非规范值
    ]
    for raw in tampered:
        assert batch_scorer.validate_exec_snapshot(raw) is None, \
            f"坏/被篡改描述必须拒绝: {raw!r}"


# ── 3. ba 接线：登记前冻结、执行同源、秘密不落账本/不出屏 ───────

def _fake_batch_factory(captured, codes_to_emit=None):
    def fake_batch(codes, top_n=None, force_refresh=False, config=None,
                   progress_cb=None, result_cb=None):
        captured["codes"] = list(codes)
        captured["config"] = config
        captured["force_refresh"] = force_refresh
        captured["ledger_at_call"] = (
            _read_ledger(captured["ledger_path"]) if captured["ledger_path"].exists() else None)
        row = {"code": codes[0], "name": codes[0], "total_score": 50,
               "normalized_score": 50, "grade": "B", "effective_grade": "B",
               "confidence": 0.5, "dim_scores": {}, "dim_confidences": {},
               "warnings": [], "invalidate": False}
        if result_cb:
            result_cb({"code": codes[0], "ok": True, "status": "complete"})
        return {"ranked": [row], "top_n": [row], "failures": [],
                "scanned": len(codes), "scored": 1, "failed": 0}
    return fake_batch


def test_ba_registers_snapshot_before_scoring_and_reuses_same_frozen_config(
        monkeypatch, tmp_path, capsys):
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    cfg = _valid_config()
    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    captured = {"ledger_path": ledger}
    monkeypatch.setattr(batch_scorer, "auto_score_batch", _fake_batch_factory(captured))

    cli_main.benzong_batch_analyze([{"code": "600519", "name": "茅台"}],
                                   force_refresh=True, src_label="x")

    at_call = captured["ledger_at_call"]
    assert at_call is not None and at_call.get("tasks"), "评分开始前必须已登记本 run"
    record = at_call["tasks"][0]
    assert isinstance(record.get("run_id"), str) and record["run_id"]
    snap = record.get("exec_snapshot")
    assert snap is not None, "登记 run 时必须写入同源执行参数快照"
    assert set(snap["params"]) == _PARAM_KEYS and snap["params"]["force_refresh"] is True

    frozen = captured["config"]
    assert frozen is not None and frozen is not cfg, \
        "ba 必须把冻结后的配置传给批次（不是调用方原 dict）"
    assert snap["params"]["model"] == frozen["ai"]["deepseek"]["model"], \
        "账本快照必须与本次实际执行配置同源"
    assert snap["params"]["request_timeout"] == float(frozen["ai"]["request_timeout"])

    ledger_text = ledger.read_text(encoding="utf-8")
    assert _SECRET_KEY not in ledger_text and _SECRET_HOST not in ledger_text
    assert "api_key" not in ledger_text and "base_url" not in ledger_text, \
        "账本不得持久化凭据/端点字段"
    out = capsys.readouterr().out
    assert "执行参数快照已冻结" in _flat(out)
    assert "未核定" in _flat(out), "必须显式声明有限覆盖（端点/环境/输入数据/提示词未核定）"
    assert _SECRET_KEY not in out and _SECRET_HOST not in out


def test_ba_reports_unfrozen_snapshot_when_config_unavailable(monkeypatch, tmp_path, capsys):
    """配置不可解析：快照未冻结但评分继续；不得因元数据失败改变业务结果。"""
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    monkeypatch.setattr(config_module, "load_config",
                        lambda: (_ for _ in ()).throw(OSError("no settings")))
    captured = {"ledger_path": ledger}
    monkeypatch.setattr(batch_scorer, "auto_score_batch", _fake_batch_factory(captured))

    cli_main.benzong_batch_analyze([{"code": "600519"}], src_label="x")

    record = _read_ledger(ledger)["tasks"][0]
    assert "exec_snapshot" not in record, "不可派生时不得写入伪身份"
    out = capsys.readouterr().out
    assert "执行参数快照未冻结" in _flat(out)
    assert "600519" in out, "元数据未冻结不影响评分结果输出"


def test_ba_ledger_write_failure_still_reported_with_snapshot(monkeypatch, tmp_path, capsys):
    _redirect_ledger(monkeypatch, tmp_path)
    monkeypatch.setattr(config_module, "load_config", _valid_config)
    monkeypatch.setattr(ss, "_atomic_write_json", lambda *a, **k: False)
    captured = {"ledger_path": tmp_path / "batch_tasks.json"}
    monkeypatch.setattr(batch_scorer, "auto_score_batch", _fake_batch_factory(captured))

    cli_main.benzong_batch_analyze([{"code": "600519"}], src_label="x")

    out = capsys.readouterr().out
    assert "保存失败" in out and "不影响" in out


# ── 4. 账本读侧：合法描述才渲染，坏/旧/篡改只读未冻结 ───────────

def _run_record(kind="ba", key="k", codes=("A",), run_id=None, snapshot=_valid_config,
                force_refresh=False):
    record = {"kind": kind, "key": key, "codes": list(codes), "done": {}, "degraded": {},
              "failed": {}, "ts_started": "2026-10-26T01:00:00",
              "ts_updated": "2026-10-26T01:00:01"}
    if run_id is not None:
        record["run_id"] = run_id
    if snapshot is not None:
        cfg = snapshot() if callable(snapshot) else snapshot
        record["exec_snapshot"] = batch_scorer.derive_exec_snapshot(cfg, force_refresh)
    return record


def _render(monkeypatch, tmp_path, capsys, records):
    path = _redirect_ledger(monkeypatch, tmp_path)
    _seed_ledger(path, records)
    before = path.read_bytes()
    cli_main.batch_tasks_cmd()
    out = capsys.readouterr().out
    assert path.read_bytes() == before, "tasks 只读展示不得改写账本"
    return out, _flat(out)


def test_tasks_view_renders_verified_ba_snapshot_with_limited_coverage(monkeypatch, tmp_path,
                                                                      capsys):
    record = _run_record(run_id="20261026-000000-" + "a" * 32)
    out, flat = _render(monkeypatch, tmp_path, capsys, [record])

    snap = record["exec_snapshot"]
    assert snap["sha256"][:12] in flat
    assert "provider=deepseek" in flat and "model=deepseek-flash" in flat
    assert "超时=45秒" in flat and "重试=1" in flat and "强制刷新=否" in flat
    assert "缓存版本=" in flat
    assert "仅白名单执行参数" in flat and "未核定" in flat
    assert "不作" in flat and "缓存资格" in flat and "信号" in flat
    assert _SECRET_KEY not in out and _SECRET_HOST not in out


def test_tasks_view_hides_missing_or_malformed_or_tampered_snapshot(monkeypatch, tmp_path,
                                                                    capsys):
    tampered = _run_record(run_id="20261026-000000-" + "b" * 32)
    tampered["exec_snapshot"]["params"]["model"] = "INJECT<markup>"
    hash_only = _run_record(run_id="20261026-000000-" + "c" * 32)
    hash_only["exec_snapshot"]["sha256"] = "0" * 64
    cases = [
        _run_record(run_id="20261026-000000-" + "d" * 32, snapshot=None),  # 缺描述
        dict(_run_record(run_id="20261026-000000-" + "e" * 32),
             exec_snapshot="raw-string"),                                  # 类型非法
        dict(_run_record(run_id="20261026-000000-" + "f" * 32),
             exec_snapshot={"schema": "x", "params": {}, "sha256": "y"}),  # 形状非法
        tampered, hash_only,
    ]
    for record in cases:
        out, flat = _render(monkeypatch, tmp_path, capsys, [record])
        assert "未冻结" in flat, f"坏描述必须显示未冻结: {record.get('exec_snapshot')!r}"
        assert "校验未通过" in flat or "未记录有效执行参数快照" in flat
        assert "已冻结" not in flat, "坏/缺描述绝不能被渲染成已冻结"
        assert "INJECT" not in out, "不得渲染存库里的任意字符串"
        assert _SECRET_KEY not in out


def test_tasks_view_legacy_record_stays_unfrozen_and_unmodified(monkeypatch, tmp_path, capsys):
    legacy = {"kind": "ba", "key": "旧记录", "codes": ["A"], "done": {},
              "failed": {}, "ts_started": "2026-10-01T08:59:00",
              "ts_updated": "2026-10-01T09:00:00"}
    out, flat = _render(monkeypatch, tmp_path, capsys, [legacy])
    assert "旧记录" in out and "不补造" in out
    assert "配置" in flat and ("未冻结" in flat or "未记录" in flat)


def test_non_ba_kinds_remain_unfrozen_even_with_snapshot(monkeypatch, tmp_path, capsys):
    """R3：l multi / la / l all 本批明确仍未冻结（快照只属于 ba）。"""
    for kind in ("l", "l all", "la"):
        record = _run_record(kind=kind, snapshot=_valid_config(),
                             run_id="20261026-000000-" + "9" * 32)
        out, flat = _render(monkeypatch, tmp_path, capsys, [record])
        assert "未冻结" in flat, f"{kind} 必须保持未冻结"
        assert record["exec_snapshot"]["sha256"][:12] not in flat, \
            f"{kind} 不得被渲染成已冻结身份"


# ── 5. 可选描述与 T05-H 既有保护兼容 ──────────────────────────

def test_ledger_snapshot_optional_and_protected_mark_semantics_preserved(monkeypatch,
                                                                       tmp_path):
    ledger = _redirect_ledger(monkeypatch, tmp_path)

    plain = ss.batch_task_start("ba", "无快照", ["A"])
    assert isinstance(plain, str) and plain
    record = _read_ledger(ledger)["tasks"][0]
    assert "exec_snapshot" not in record, "未提供描述时保持与旧调用兼容（不写空壳身份）"

    snap = batch_scorer.derive_exec_snapshot(_valid_config(), False)
    token = ss.batch_task_start("ba", "带快照", ["A", "B"], snapshot=snap)
    assert isinstance(token, str) and token
    raw = _read_ledger(ledger)
    latest = raw["tasks"][0]
    assert latest["exec_snapshot"] == snap
    assert latest["exec_snapshot"] is not snap, "账本侧应保存副本（调用方后续突变不影响记录）"

    # run 身份/roster 保护不变
    before = ledger.read_bytes()
    assert ss.batch_task_mark("ba", "带快照", "A", True) is False, "缺令牌必须拒绝"
    assert ss.batch_task_mark("ba", "带快照", "Z", True, run_id=token) is False, "清单外代码必须拒绝"
    assert ledger.read_bytes() == before, "拒绝路径不得改动账本"

    assert ss.batch_task_mark("ba", "带快照", "A", True, run_id=token) is True
    after = _read_ledger(ledger)["tasks"][0]
    assert after["exec_snapshot"] == snap, "逐项记账不得丢失/改写执行参数快照"
    assert batch_scorer.validate_exec_snapshot(after["exec_snapshot"]) is not None
    assert "verified" not in after and "cache_qualified" not in after, \
        "快照只是元数据，不得引入缓存/信号资格字段"


def test_ledger_snapshot_write_failure_keeps_old_record(monkeypatch, tmp_path):
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    snap = batch_scorer.derive_exec_snapshot(_valid_config(), False)
    token = ss.batch_task_start("ba", "k", ["A"], snapshot=snap)
    assert token
    before = ledger.read_bytes()

    monkeypatch.setattr(ss, "_atomic_write_json", lambda *a, **k: False)
    assert ss.batch_task_start("ba", "k", ["A"], snapshot=snap) is None
    assert ledger.read_bytes() == before, "写失败必须保留旧快照/旧记录"


# ═══════ T05-I fix2（主控独立审查 BLOCKERS 追修，仍属原 R1/R2/R3/R4）════════
# 1) 冻结失败不得再 load/构建 client（显式状态区分"没提供"与"已失败"）
# 2) 模型字段的 URL/凭据形状不得派生身份（即使 hash 合法）
# 3) 写入端白名单门（不能只靠读侧隐藏已落盘秘密）
# 4) 新增失败日志不得携带任意异常正文（含唯一秘密的反例）

_LOG_SECRET = "sk-LOG-UNIQUE-SECRET-20261026"


def _chain_externals(monkeypatch):
    """只替换外部工作（成交额/取数/缓存/维度），保留真实 ba→batch→auto_score 决策链。"""
    monkeypatch.setattr(batch_scorer, "get_market_turnover", lambda: 1.2)
    _offline_auto_score(monkeypatch)


def _assert_not_echoed(secret, caplog, capsys):
    out, err = capsys.readouterr()
    assert secret not in caplog.text, "新增失败日志不得插入任意异常正文"
    assert secret not in out, "stdout 不得回显异常正文/秘密"
    assert secret not in err, "stderr 不得回显异常正文/秘密"


def test_ba_freeze_failure_loads_once_and_builds_no_client(monkeypatch, tmp_path, capsys):
    """真实 ba→batch→auto_score 链：配置冻结失败后只 load 一次、不构造 client，降级继续。"""
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    state = {"load": 0, "build": 0}

    def load():
        state["load"] += 1
        raise ValueError("settings unreadable")

    def build(config=None):
        state["build"] += 1
        return None, None

    monkeypatch.setattr(config_module, "load_config", load)
    monkeypatch.setattr(batch_scorer, "_build_ai_client", build)
    _chain_externals(monkeypatch)

    cli_main.benzong_batch_analyze([{"code": "600519"}, {"code": "000001"}],
                                   src_label="fix2")

    assert state["load"] == 1, f"冻结失败后不得再 load（实际 {state['load']} 次）"
    assert state["build"] == 0, f"冻结失败后不得构造 client（实际 {state['build']} 次）"
    record = _read_ledger(ledger)["tasks"][0]
    assert isinstance(record.get("run_id"), str) and record["run_id"]
    assert "exec_snapshot" not in record, "不可派生时保持未冻结，不写伪身份"
    flat = _flat(capsys.readouterr().out)
    assert "执行参数快照未冻结" in flat
    assert "降级2" in flat and "失败0" in flat, "元数据冻结失败不得改变既有降级/评分路径"


def test_standalone_batch_freeze_failure_loads_once_and_builds_no_client(monkeypatch):
    state = {"load": 0, "build": 0}

    def load():
        state["load"] += 1
        raise OSError("no settings")

    def build(config=None):
        state["build"] += 1
        return None, None

    monkeypatch.setattr(config_module, "load_config", load)
    monkeypatch.setattr(batch_scorer, "_build_ai_client", build)
    _chain_externals(monkeypatch)

    res = batch_scorer.auto_score_batch(["600519", "000001"])

    assert state["load"] == 1, f"standalone 冻结失败只允许一次 load，实际 {state['load']}"
    assert state["build"] == 0, "不得把 None 交给会重读配置的 client 构造"
    assert res["scored"] == 2 and res["failed"] == 0


def test_standalone_batch_freeze_failure_real_build_path_loads_once(monkeypatch):
    """不 mock client 构造函数：真实 build 路径下失败也只 load 一次。"""
    state = {"load": 0, "openai": 0}

    def load():
        state["load"] += 1
        raise OSError("no settings")

    def fake_openai(**kwargs):
        state["openai"] += 1
        return mock.MagicMock()

    monkeypatch.setattr(config_module, "load_config", load)
    monkeypatch.setattr(openai, "OpenAI", fake_openai)
    _chain_externals(monkeypatch)

    res = batch_scorer.auto_score_batch(["600519", "000001"])

    assert state["load"] == 1, f"失败路径不得二次 load（实际 {state['load']}）"
    assert state["openai"] == 0, "失败路径不得构造 client"
    assert res["scored"] == 2


def test_standalone_batch_first_load_failure_never_uses_second_success(monkeypatch):
    """反例：第一次 load 失败后，不得靠第二次 load 悄悄转成成功/冻结。"""
    state = {"load": 0, "openai": 0}
    cfg = _valid_config()

    def load():
        state["load"] += 1
        if state["load"] == 1:
            raise ValueError("transient load failure")
        return cfg

    def fake_openai(**kwargs):
        state["openai"] += 1
        return mock.MagicMock()

    monkeypatch.setattr(config_module, "load_config", load)
    monkeypatch.setattr(openai, "OpenAI", fake_openai)
    _chain_externals(monkeypatch)

    res = batch_scorer.auto_score_batch(["600519"])

    assert state["load"] == 1, f"失败后不得再 load（第二次成功也不得被采用），实际 {state['load']}"
    assert state["openai"] == 0, "第二次 load 才成功的 client 不得被构造"
    assert res["scored"] == 1


# ── fix2-2：模型字段 URL/凭据形状不得派生身份（即使 hash 合法）────────

def test_model_url_or_credential_shape_never_derives_identity():
    bad_models = (
        "https://api.secret-host.invalid/v1",
        "sk-secret-UNIQUE",
        "sk-abcdef123456",
        "sk_live_abcdef",
        "eyJhbGciOiJIUzI1NiJ9",
        "api-key-deadbeef",
    )
    for model in bad_models:
        cfg = _valid_config()
        cfg["ai"]["deepseek"]["model"] = model
        assert batch_scorer.derive_exec_snapshot(cfg, False) is None, \
            f"URL/凭据形状不得派生执行身份: {model!r}"

    for model in ("deepseek-flash", "deepseek-chat", "kimi-k2.6"):
        cfg = _valid_config()
        cfg["ai"]["deepseek"]["model"] = model
        snap = batch_scorer.derive_exec_snapshot(cfg, False)
        assert snap is not None and snap["params"]["model"] == model


def test_forged_valid_hash_url_model_rejected_by_writer_and_tasks(monkeypatch, tmp_path,
                                                                capsys):
    """URL/凭据形状模型 + 独立重算合法 hash：写入门拒绝、任务只读未冻结、不回显。

    红跑时本测试已因 URL 用例失败；修复后同一路径追加了凭据形状用例（`sk-…`），
    覆盖 derive/validate/写入门/tasks 四处同一形状策略。
    """
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    bad_models = (
        "https://api.secret-host.invalid/v1",
        "sk-secret-UNIQUE",
        "sk-abcdef123456",
    )
    for bad_model in bad_models:
        ledger.write_text(json.dumps({"tasks": []}), encoding="utf-8")
        params = {"provider": "deepseek", "model": bad_model, "request_timeout": 45.0,
                  "max_retries": 1, "force_refresh": False,
                  "cache_version": batch_scorer.CACHE_VERSION}
        payload = json.dumps(
            {"schema": batch_scorer.EXEC_SNAPSHOT_SCHEMA, "params": params},
            sort_keys=True, separators=(",", ":"))
        forged = {"schema": batch_scorer.EXEC_SNAPSHOT_SCHEMA, "params": dict(params),
                  "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()}
        assert batch_scorer.validate_exec_snapshot(forged) is None, \
            f"形状不安全时即使 hash 重算正确也不得通过: {bad_model!r}"

        token = ss.batch_task_start("ba", "fix2", ["A"], snapshot=forged)
        assert isinstance(token, str) and token, "描述不安全时 run 仍必须正常登记"
        record = _read_ledger(ledger)["tasks"][0]
        assert "exec_snapshot" not in record, \
            f"写入端白名单门必须拒绝不安全模型: {bad_model!r}"
        written = ledger.read_text(encoding="utf-8")
        assert bad_model not in written and "https://" not in written
        assert "secret-host" not in written and "sk-secret" not in written

        # 模拟外部/更早写入的记录：合法 hash + 不安全模型 → 读侧只读未冻结、不回显
        path = tmp_path / "batch_tasks.json"
        _seed_ledger(path, [dict(record, exec_snapshot=copy.deepcopy(forged))])
        before = path.read_bytes()
        cli_main.batch_tasks_cmd()
        out = capsys.readouterr().out
        assert path.read_bytes() == before, "坏描述只读展示不得改写账本"
        assert "未冻结" in _flat(out)
        assert "已冻结" not in _flat(out)
        assert bad_model not in out and "secret-host" not in out


def test_ba_unfrozen_when_model_shape_unsafe(monkeypatch, tmp_path, capsys):
    """真实 ba 接线：配置 model 为 URL 形状 → 不派生/不登记快照，评分照常。"""
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    cfg = _valid_config()
    cfg["ai"]["deepseek"]["model"] = "https://api.secret-host.invalid/v1"
    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    monkeypatch.setattr(batch_scorer, "_build_ai_client",
                        lambda config=None: (object(), "m"))
    _chain_externals(monkeypatch)

    cli_main.benzong_batch_analyze([{"code": "600519"}], src_label="fix2")

    record = _read_ledger(ledger)["tasks"][0]
    assert isinstance(record.get("run_id"), str) and record["run_id"]
    assert "exec_snapshot" not in record
    assert "secret-host" not in ledger.read_text(encoding="utf-8")
    flat = _flat(capsys.readouterr().out)
    assert "执行参数快照未冻结" in flat
    assert "600519" in flat


# ── fix2-3：写入端白名单门（不只靠读侧隐藏）──────────────────────

def test_ledger_write_gate_rejects_secret_and_invalid_snapshots(monkeypatch, tmp_path):
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    valid = batch_scorer.derive_exec_snapshot(_valid_config(), False)
    bad_snapshots = (
        {"api_key": _SECRET_KEY},
        {"schema": batch_scorer.EXEC_SNAPSHOT_SCHEMA,
         "params": {"api_key": _SECRET_KEY}, "sha256": "x"},
        dict(valid, extra="x"),
        dict(valid, sha256="0" * 64),
        dict(valid, schema="muyun.ba.other/v1"),
        dict(valid, params=dict(valid["params"], provider="<script>")),
        "raw-string",
        ["list"],
    )
    for raw in bad_snapshots:
        token = ss.batch_task_start("ba", "fix2", ["A"], snapshot=raw)
        assert isinstance(token, str) and token, f"run 必须正常登记: {raw!r}"
        record = _read_ledger(ledger)["tasks"][0]
        assert "exec_snapshot" not in record, f"非法/含额外键描述不得落盘: {raw!r}"
        assert record["codes"] == ["A"], "拒绝描述不得破坏 roster/run 身份"
    text = ledger.read_text(encoding="utf-8")
    assert _SECRET_KEY not in text, "不能只靠读侧隐藏已落盘秘密"
    assert "api_key" not in text


def test_ledger_write_gate_stores_only_canonical_copy(monkeypatch, tmp_path):
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    snap = batch_scorer.derive_exec_snapshot(_valid_config(), False)
    token = ss.batch_task_start("ba", "fix2", ["A"], snapshot=snap)
    assert isinstance(token, str) and token

    stored = _read_ledger(ledger)["tasks"][0]["exec_snapshot"]
    assert stored == batch_scorer.validate_exec_snapshot(snap), "落盘必须是规范化副本"
    assert stored is not snap
    snap["params"]["model"] = "mutated-after-start"
    assert _read_ledger(ledger)["tasks"][0]["exec_snapshot"] == stored
    assert batch_scorer.validate_exec_snapshot(stored) is not None
    assert len(token.split("-")[-1]) == 32, "run 令牌仍为完整 UUID hex"


# ── fix2-4：新增失败日志不得携带任意异常正文（唯一秘密反例）────────

def test_freeze_load_failure_log_does_not_echo_exception_body(monkeypatch, caplog,
                                                              capsys):
    caplog.set_level(logging.DEBUG)

    def load():
        raise ValueError(f"yaml int(value) failed for {_LOG_SECRET}")

    monkeypatch.setattr(config_module, "load_config", load)
    frozen = batch_scorer.freeze_batch_config()

    assert frozen is batch_scorer.FROZEN_CONFIG_UNAVAILABLE, \
        "加载失败必须进入显式不可用状态（与'未提供'区分）"
    _assert_not_echoed(_LOG_SECRET, caplog, capsys)


def test_freeze_copy_failure_log_does_not_echo_exception_body(monkeypatch, caplog,
                                                              capsys):
    caplog.set_level(logging.DEBUG)

    class _Bomb:
        def __deepcopy__(self, memo):
            raise ValueError(f"copy failed {_LOG_SECRET}")

    monkeypatch.setattr(config_module, "load_config", lambda: {"ai": {}, "bomb": _Bomb()})
    frozen = batch_scorer.freeze_batch_config()

    assert frozen is batch_scorer.FROZEN_CONFIG_UNAVAILABLE
    _assert_not_echoed(_LOG_SECRET, caplog, capsys)


def test_ledger_snapshot_validation_failure_log_does_not_echo_exception_body(
        monkeypatch, tmp_path, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    snap = batch_scorer.derive_exec_snapshot(_valid_config(), False)

    def boom(raw):
        raise ValueError(f"validator exploded {_LOG_SECRET}")

    monkeypatch.setattr(batch_scorer, "validate_exec_snapshot", boom)
    token = ss.batch_task_start("ba", "fix2", ["A"], snapshot=snap)

    assert isinstance(token, str) and token, "描述处理异常不得影响 run 登记"
    assert "exec_snapshot" not in _read_ledger(ledger)["tasks"][0], "异常时保持未冻结"
    _assert_not_echoed(_LOG_SECRET, caplog, capsys)


# ═══════ T05-I fix3（同类字段白名单漏门 + 直接构造异常点；仍属原 R2/R3）════════
# A) provider 也须拒绝凭证形状（字段全集扫描含 provider/model/cache_version）
# B) cache_version 只接受 v+数字点分版本（历史数字版本仍可读）
# C) 既有 _build_ai_client warning 不再拼接任意异常正文（前缀/降级语义不变）


def _forged_snapshot(**overrides):
    """独立重算合法 SHA256 的手工描述（绕过 derive 的字段校验，模拟外部/坏账本）。"""
    params = {"provider": "deepseek", "model": "deepseek-flash",
              "request_timeout": 45.0, "max_retries": 1, "force_refresh": False,
              "cache_version": batch_scorer.CACHE_VERSION}
    params.update(overrides)
    payload = json.dumps(
        {"schema": batch_scorer.EXEC_SNAPSHOT_SCHEMA, "params": params},
        sort_keys=True, separators=(",", ":"))
    return {"schema": batch_scorer.EXEC_SNAPSHOT_SCHEMA, "params": params,
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()}


def _cfg_with_field(field, value):
    """构造把某个身份字段填成 value 的真实形状配置（用于 derive 侧反例）。"""
    cfg = _valid_config()
    if field == "provider":
        cfg["ai"]["provider"] = value
        cfg["ai"][value] = dict(cfg["ai"]["deepseek"])
    elif field == "model":
        cfg["ai"]["deepseek"]["model"] = value
    else:
        raise AssertionError(f"不支持在配置里伪造 {field}")
    return cfg


def test_identity_field_scan_rejects_credential_shapes():
    """A/B) 字段全集：provider/model/cache_version 任一凭据形状都不得过同一门。"""
    unsafe = {
        "provider": ("sk_live_abcdef", "AKIAIOSFODNN7EXAMPLE", "sk-secret-UNIQUE"),
        "model": ("sk-secret-UNIQUE", "https://api.secret-host.invalid/v1"),
        "cache_version": ("vsk-secret-UNIQUE", "v0.8.29.1-sk_live_abcdef",
                          "v0.8.29.1secret", "0.8.29.1", "v20261026"),
    }
    for field, values in unsafe.items():
        for value in values:
            forged = _forged_snapshot(**{field: value})
            assert batch_scorer.validate_exec_snapshot(forged) is None, \
                f"{field} 凭据/非规范形状不得过门: {value!r}"
            if field in ("provider", "model"):
                assert batch_scorer.derive_exec_snapshot(
                    _cfg_with_field(field, value), False) is None, \
                    f"{field} 凭据形状不得派生身份: {value!r}"


def test_public_provider_values_still_derive_identity():
    """A) 合法公开 provider（含大小写变体）仍可派生，不被误伤。"""
    for provider in ("deepseek", "kimi", "DeepSeek"):
        snap = batch_scorer.derive_exec_snapshot(
            _cfg_with_field("provider", provider), False)
        assert snap is not None and snap["params"]["provider"] == provider


def test_forged_valid_hash_unsafe_provider_or_cache_version_rejected_by_writer_and_tasks(
        monkeypatch, tmp_path, capsys):
    """A/B) 重新派生合法 hash 也不得让 provider/cache_version 凭据形状落盘或回显。"""
    ledger = _redirect_ledger(monkeypatch, tmp_path)
    cases = (
        ("provider", "sk_live_abcdef"),
        ("provider", "AKIAIOSFODNN7EXAMPLE"),
        ("cache_version", "vsk-secret-UNIQUE"),
    )
    for field, value in cases:
        ledger.write_text(json.dumps({"tasks": []}), encoding="utf-8")
        forged = _forged_snapshot(**{field: value})
        assert batch_scorer.validate_exec_snapshot(forged) is None, (field, value)

        token = ss.batch_task_start("ba", "fix3", ["A"], snapshot=forged)
        assert isinstance(token, str) and token, "描述不安全时 run 仍必须正常登记"
        record = _read_ledger(ledger)["tasks"][0]
        assert "exec_snapshot" not in record, \
            f"写入端白名单门必须拒绝不安全 {field}: {value!r}"
        written = ledger.read_text(encoding="utf-8")
        assert value not in written and "secret" not in written.lower()

        path = tmp_path / "batch_tasks.json"
        _seed_ledger(path, [dict(record, exec_snapshot=copy.deepcopy(forged))])
        before = path.read_bytes()
        cli_main.batch_tasks_cmd()
        out = capsys.readouterr().out
        assert path.read_bytes() == before, "坏描述只读展示不得改写账本"
        assert "未冻结" in _flat(out) and "已冻结" not in _flat(out)
        assert value not in out and "secret" not in out.lower()


def test_cache_version_historical_numeric_versions_accepted(monkeypatch, tmp_path):
    """B) 历史数字版本仍可读/可存（不要求等于当前版本）。"""
    for version in ("v0.8.29.1", "v0.8.20.1", "v0.8.6.4", "v1.0"):
        verified = batch_scorer.validate_exec_snapshot(
            _forged_snapshot(cache_version=version))
        assert verified is not None, f"历史数字版本必须可读: {version!r}"
        assert verified["params"]["cache_version"] == version

    ledger = _redirect_ledger(monkeypatch, tmp_path)
    token = ss.batch_task_start("ba", "fix3", ["A"],
                                snapshot=_forged_snapshot(cache_version="v0.8.20.1"))
    assert isinstance(token, str) and token
    stored = _read_ledger(ledger)["tasks"][0]["exec_snapshot"]
    assert stored["params"]["cache_version"] == "v0.8.20.1", "历史数字版本写明后可持久化"


def test_client_build_failure_log_does_not_echo_exception_body(monkeypatch, caplog,
                                                               capsys):
    """C) 直接构造失败：既有 warning 前缀保留，但不再拼接任意异常正文。"""
    caplog.set_level(logging.WARNING)
    cfg = _valid_config()
    cfg["ai"]["request_timeout"] = _LOG_SECRET   # 转换异常正文会带出该值

    client, model = auto_scorer._build_ai_client(cfg)

    assert client is None and model is None, "既有降级语义保持（失败返回 None, None）"
    assert "AI client 构造失败" in caplog.text, "既有 warning 前缀/映射保持不变"
    _assert_not_echoed(_LOG_SECRET, caplog, capsys)


def test_batch_client_build_failure_log_does_not_echo_exception_body(monkeypatch,
                                                                     caplog):
    """C) 批量默认构造路径：冻结配置后的转换异常也不得把秘密写进本批日志。"""
    caplog.set_level(logging.WARNING)
    cfg = _valid_config()
    cfg["ai"]["max_retries"] = _LOG_SECRET       # int() 异常正文会带出该值
    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    _chain_externals(monkeypatch)

    res = batch_scorer.auto_score_batch(["600519", "000001"])

    assert "AI client 构造失败" in caplog.text
    assert _LOG_SECRET not in caplog.text
    assert res["scored"] == 2 and res["failed"] == 0, "构造失败仍按既有降级路径继续"


