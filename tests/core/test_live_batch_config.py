# -*- coding: utf-8 -*-
"""T05-M：live 深分析批(l 多代码 / la / l all)的一次配置冻结与非秘密配置指纹。

行为锁(T05-M):
1. 三个 REPL 入口在原预算起点之后、首只股票之前**只加载一次**配置并深拷贝冻结（含
   REPL noai 覆盖）；批内改盘/改原 dict 不改变本批；每只拿到独立副本，覆盖/debug 不泄漏。
2. 冻结失败（读取/解析/非 dict/复制）→ 首只之前显式终止「配置加载失败：本次未开始分析」，
   不登记 run、不调用分析/AI；日志只记异常类型、不含正文。
3. 冻结前预算已到期 → 不加载配置，按 T05-L 记录未开始；冻结期间预算耗尽 → 不启动股票。
4. 从冻结后的有效配置派生 `muyun.live.config_fingerprint/v1`：白名单 decision/skills/
   event/entry_exit/fusion 整节 + ai 的 enabled/provider/当前 provider 的 model/
   direction_aware_adjustment/request_timeout/max_retries/modifier；递归剔除密钥/URL 类键与
   URL/sk- 形状字符串；只落 schema/scope/sha256；秘密变化不影响指纹；非 JSON/非字符串键/
   NaN/循环等 fail-closed（不冒称已冻结、不静默 repr）。
5. 账本按 kind 读写同门（live 只认 live schema，ba 原合同不变，跨 kind 拒绝）；坏/旧/缺
   描述按未冻结；run 与描述同一原子落盘。
6. tasks 与启动行显示同一规范指纹 + 「live 批配置已冻结」+ 范围说明（技能 YAML 内容/
   数据/提示词未核定）；旧/坏/缺诚实显示未冻结；ba 展示与措辞不变。
7. 单只 l / -l / chat 默认 config=None 行为不变（照旧读盘）。

全部离线：数据源/账户/编排器/配置读取全用替身，账本/当日已析落 tmp_path，
不触网、不调用产品 AI、不碰真实 ~/.muyun 与真实账户。

跑法: pytest tests/core/test_live_batch_config.py
"""

import copy
import inspect
import json
import logging
import math
import os
import re
import sys
import unittest.mock as mock
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

# patch 期间不得让模块首次 import（按值绑定会把替身永久捕获进导入方）：先预导入
import start
import src.cli.main as cli_main
import src.cli.session_state as ss
import src.core.analysis_service as analysis_service
import src.core.benzong.batch_scorer as batch_scorer
import src.core.shadow_diff as shadow_diff
import src.data.account_service as account_module
import src.data.akshare_client as ak_mod
import src.data.portfolio as portfolio_mod
import src.data.proposals as proposals_module
from src.data.models import StockData


MODES = ["live_multi", "live_all", "live_scan_all"]
KIND_OF = {"live_multi": "l", "live_all": "la", "live_scan_all": "l all"}
LIVE_KINDS = ["l", "la", "l all"]
SCAN_SOURCE = "bz scan 测试"
CODES2 = ["600519", "000001"]
CODES3 = ["600001", "600002", "600003"]

# 合同固定值：schema 由 M4 固定；scope 是本实现固定的字面量（写读同一常量）。
LIVE_FP_SCHEMA = "muyun.live.config_fingerprint/v1"
LIVE_FP_SCOPE = "live_batch_effective_config"
_CONFIG_KEYS = {"schema", "scope", "sha256"}
_SHA_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_SHA_IN_TEXT = re.compile(r"[0-9a-f]{64}")

_SECRET_KEY = "sk-live-UNIQUE-T05M-0001"
_SECRET_URL = "https://api.secret-host.invalid/v1"
_SECRET_TOKEN = "tok_live_UNIQUE_T05M_0002"


# ── 状态隔离：绝不碰真实 ~/.muyun ─────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", tmp_path / "batch_tasks.json")
    monkeypatch.setattr(ss, "_DEEP_ANALYZED_FILE", tmp_path / "deep_analyzed.json")
    monkeypatch.setattr(ss, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(ss, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    monkeypatch.setattr(ss, "_WATCH_FILE", tmp_path / "watchlist.jsonl")


# ── 通用工具 ─────────────────────────────────────────────────

def _flat(text):
    return text.replace("\n", "").replace(" ", "")


def _deep_merge(base, override):
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = copy.deepcopy(v)
    return merged


def _effective_config(**overrides):
    """真实形状的有效配置（含密钥/端点，用于证明它们不进指纹/账本）。"""
    cfg = {
        "ai": {
            "enabled": True,
            "debug": False,
            "provider": "deepseek",
            "direction_aware_adjustment": True,
            "request_timeout": 60,
            "max_retries": 2,
            "deepseek": {
                "api_key": _SECRET_KEY,
                "base_url": _SECRET_URL,
                "model": "deepseek-flash",
                "model_pro": "deepseek-pro",
                "context_window": 1000000,
                "max_output_tokens": 384000,
            },
            "kimi": {"api_key": "kimi-secret", "base_url": "https://api.moonshot.cn/v1",
                     "model": "kimi-k2.6", "model_pro": "kimi-k3",
                     "context_window": 262144},
            "modifier": {"sentiment_weight": 0.3, "risk_position_cap": 0.7,
                         "max_news_per_stock": 10, "cache_ttl": 3600,
                         "tech_context_enabled": True},
        },
        "decision": {
            "signal_weights": {"ma_trend": 1.0, "volume_price": 1.0, "stop_loss": 1.5},
        },
        "skills": {
            "dir": "./src/skills",
            "enabled": ["ma_trend", "macd", "rsi"],
            "types": {"ma_trend": "base", "macd": "base", "rsi": "base"},
        },
        "event": {
            "enabled": True,
            "rules_path": "./src/scanner/event_rules.yaml",
            "auto_scan_on_analyze": True,
            "keyword_scan": True,
            "ai_classify": True,
            "max_events_display": 10,
        },
        "entry_exit": {"enabled": True, "atr": {"period": 14},
                       "breakout": {"high_window": 60, "volume_ratio": 1.5}},
        "fusion": {"shadow_capture": True,
                   "risk_profile": {"per_stock_max": 0.20, "industry_max": 0.50}},
        # 以下均不在白名单：改动不得影响指纹
        "rag": {"enabled": True, "chunk_size": 500, "default_top_k": 5},
        "chat": {"enabled": True, "max_tokens": 384000},
        "scanner": {"cache_ttl": 300, "rules_path": "./src/scanner/scan_rules.yaml"},
        "ranking": {"enabled": True, "weights": {"technical": 0.4}},
        "app": {"name": "暮云思辨投资助手"},
    }
    return _deep_merge(cfg, overrides)


def _reordered(value):
    """递归逆序构造同内容 dict（键序不得影响指纹）。"""
    if isinstance(value, dict):
        return {k: _reordered(value[k]) for k in reversed(list(value))}
    if isinstance(value, list):
        return [_reordered(v) for v in value]
    return value


def _fp(config):
    desc = ss.derive_live_config_descriptor(config)
    assert desc is not None, f"可安全派生的配置不得返回 None: {config!r}"[:400]
    return desc["sha256"]


def _live_desc(sha=None):
    return {"schema": LIVE_FP_SCHEMA, "scope": LIVE_FP_SCOPE,
            "sha256": sha or ("a" * 64)}


def _ledger(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _latest():
    runs = ss.get_recent_batch_tasks(10)
    assert runs, "必须登记过批量任务 run"
    return runs[0]


def _seed_ledger(path, tasks):
    path.write_text(json.dumps({"tasks": tasks}, ensure_ascii=False), encoding="utf-8")


def _run_record(kind="l", key="手动多代码 600519", codes=("600519",), snapshot="live"):
    record = {
        "kind": kind, "key": key, "codes": list(codes), "done": {}, "degraded": {},
        "failed": {}, "ts_started": "2026-10-27T01:00:00",
        "ts_updated": "2026-10-27T01:00:01",
        "run_id": "20261027-010000-" + "0" * 32,
    }
    if snapshot == "live":
        record["exec_snapshot"] = _live_desc()
    elif snapshot is not None:
        record["exec_snapshot"] = snapshot
    return record


def _render_tasks(monkeypatch, tmp_path, capsys, records):
    path = tmp_path / "batch_tasks.json"
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", path)
    _seed_ledger(path, records)
    before = path.read_bytes()
    cli_main.batch_tasks_cmd()
    out = capsys.readouterr().out
    assert path.read_bytes() == before, "tasks 只读展示不得改写账本"
    return out, _flat(out)


# ── 假钟（与 T05-L 预算用例同构，本文件只用于 T05-M 冻结时序）──

class _Clock:
    def __init__(self, t=1000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class _JumpClock(_Clock):
    """首次读取后所有读取都远超任何测试预算（表达「起算后立刻到期」）。"""

    def __init__(self, t=1000.0):
        super().__init__(t)
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.t if self.reads == 1 else self.t + 1e6


def _clock(monkeypatch, cls=_Clock, t=1000.0):
    clock = cls(t)
    monkeypatch.setattr(start, "_monotonic", clock)
    return clock


# ── live 批离线环境（配置/分析/账户/预取/扫描全替身）──────────

_UNSET = object()   # load_result 的「未指定」哨兵（None 本身是合法坏配置）


def _entry(code):
    return mock.MagicMock(stock_code=code, stock_name="N" + code, from_ledger=False)


class _Env:
    """三入口共用的离线环境。

    - load_config 替身：记录调用次数；on_load() 可推进假钟/抛异常/返回坏值
    - analyze_live 替身：记录 (code, kwargs)；on_call(code) 可推进假钟/改原配置
    - PortfolioManager / prefetch / get_last_scan / input 均为替身
    """

    def __init__(self, monkeypatch, capsys, mode, codes, *,
                 config=None, confirm="y", load_result=_UNSET):
        self.mp = monkeypatch
        self.capsys = capsys
        self.mode = mode
        self.codes = list(codes)
        self.confirm = confirm
        self.cfg = _effective_config() if config is None else config
        self.load_result = load_result        # 指定时 load_config 直接返回它
        self.loads = []
        self.calls = []
        self.prefetches = []
        self.prompts = []
        self.pre = []
        self.buf = ""
        self.on_load = None
        self.on_call = None
        self.facts = mock.MagicMock(name="acct_facts")
        self.facts.holding_entries.return_value = ([_entry(c) for c in self.codes], False)
        self._wire()

    def _wire(self):
        mp = self.mp

        def fake_load(*a, **k):
            self.loads.append(1)
            if self.on_load:
                self.on_load()
            if self.load_result is not _UNSET:
                return self.load_result
            return copy.deepcopy(self.cfg)

        mp.setattr(cli_main, "load_config", fake_load)

        def fake_analyze(code, **kw):
            self.calls.append((code, kw))
            if self.on_call:
                self.on_call(code)
            return cli_main.LiveOutcome.COMPLETE

        mp.setattr(cli_main, "analyze_live", fake_analyze)

        self.pm = mock.MagicMock(name="PortfolioManager")
        self.pm.return_value.request_account_facts.return_value = self.facts
        mp.setattr(portfolio_mod, "PortfolioManager", self.pm)

        def prefetch(codes):
            self.prefetches.append(list(codes))
            return len(codes)

        mp.setattr(ak_mod.AKShareClient, "prefetch_realtime_quotes", staticmethod(prefetch))
        if self.mode == "live_scan_all":
            items = [{"code": c, "name": "N" + c} for c in self.codes]
            mp.setattr(ss, "get_last_scan", lambda: {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "source": SCAN_SOURCE, "count": len(items), "items": items})
        mp.setattr("builtins.input", self._input)

    def _drain(self):
        chunk = self.capsys.readouterr().out
        self.buf += chunk
        return chunk

    def _input(self, prompt=""):
        self.prompts.append(prompt)
        self.pre.append(self._drain())
        if self.mode != "live_scan_all":
            raise AssertionError("l 多代码 / la 不得新增确认门")
        return self.confirm

    def args(self, **extra):
        base = {"live_multi": {"codes": list(self.codes)}, "live_all": {},
                "live_scan_all": {"force": False}}[self.mode]
        base.update(extra)
        return base

    def run(self, **extra):
        start.run_cli(self.mode, self.args(**extra))
        return self.out()

    def out(self):
        self._drain()
        return self.buf

    def configs(self):
        return [kw.get("config") for _, kw in self.calls]


# ═══════ 1. 每批只加载一次 + 深拷贝冻结（三入口） ═══════

@pytest.mark.parametrize("mode", MODES)
def test_batch_loads_config_exactly_once_before_first_stock(mode, monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES3)
    out = env.run()

    assert len(env.loads) == 1, f"{mode} 每批只允许 load_config 一次: {len(env.loads)}"
    assert [c for c, _ in env.calls] == CODES3
    assert out.index("live 批配置已冻结") < out.index("[1/3]"), \
        "启动行必须在首只之前"


@pytest.mark.parametrize("mode", MODES)
def test_batch_passes_one_frozen_snapshot_to_every_stock(mode, monkeypatch, capsys):
    """冻结快照只加载/复制一次，批内每只拿到同一份（逐股深拷贝由 analyze_live 负责）。"""
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES3)
    env.run()

    configs = env.configs()
    assert len(configs) == len(CODES3)
    assert len({id(c) for c in configs}) == 1, "批内应共用同一份冻结快照"
    frozen = configs[0]
    assert frozen is not env.cfg, "冻结必须是副本，不得把 load_config 的原 dict 交给分析"
    assert frozen["ai"]["deepseek"]["model"] == "deepseek-flash"
    assert frozen["skills"]["enabled"] == ["ma_trend", "macd", "rsi"]
    assert len(env.loads) == 1


@pytest.mark.parametrize("mode", MODES)
def test_config_change_after_freeze_does_not_affect_this_batch(mode, monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2)

    def change_disk(code):
        # 模拟盘中改了配置文件：冻结后任何一只都不得看到新值
        env.cfg["ai"]["deepseek"]["model"] = "changed-on-disk"
        env.cfg["decision"]["signal_weights"]["stop_loss"] = 99.0

    env.on_call = change_disk
    env.run()

    for cfg in env.configs():
        assert cfg["ai"]["deepseek"]["model"] == "deepseek-flash", "批中改盘不得影响本批"
        assert cfg["decision"]["signal_weights"]["stop_loss"] == 1.5
    assert len(env.loads) == 1


@pytest.mark.parametrize("mode", MODES)
def test_noai_override_is_applied_to_frozen_config(mode, monkeypatch, capsys):
    monkeypatch.setattr(start, "_no_ai", True)
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2)
    out = env.run()

    for cfg in env.configs():
        assert cfg["ai"]["enabled"] is False, "noai 覆盖必须进冻结配置"
    fp_noai = _fp(_effective_config(ai={"enabled": False}))
    fp_ai = _fp(_effective_config())
    assert fp_noai != fp_ai, "noai 是有效配置差异，指纹必须不同"
    assert fp_noai in out, "启动行指纹必须反映 noai 覆盖"


@pytest.mark.parametrize("mode", MODES)
def test_startup_line_reports_same_fingerprint_and_scope_note(mode, monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2)
    out = env.run()

    expected = _fp(_effective_config())
    assert "live 批配置已冻结" in out
    assert expected in out, "启动行必须显示规范指纹全文"
    assert "技能YAML内容/数据/提示词未核定" in out, "启动行必须带范围说明"
    assert "未冻结" not in out


def test_real_analyze_live_isolates_per_stock_config_inside_batch(monkeypatch, tmp_path,
                                                                 capsys):
    """批内真实 analyze_live：编排器看到逐股副本，第一只的覆盖不得泄漏到第二只。"""
    _wire_real_analyze(monkeypatch, tmp_path)
    cfg = _effective_config()
    loads = []
    seen_configs = []

    def fake_load(*a, **k):
        loads.append(1)
        return copy.deepcopy(cfg)

    orch = mock.MagicMock(name="orchestrator")
    orch.analyze.return_value = (mock.MagicMock(), mock.MagicMock(),
                                 mock.MagicMock(), mock.MagicMock())

    def build(config, rag_service=None):
        seen_configs.append(config)
        if len(seen_configs) == 1:
            config["skills"]["enabled"].append("POLLUTED-BY-FIRST-STOCK")
            config["ai"]["debug"] = True
        return orch

    monkeypatch.setattr(cli_main, "load_config", fake_load)
    monkeypatch.setattr(cli_main, "build_live_orchestrator", build)
    monkeypatch.setattr(ak_mod.AKShareClient, "prefetch_realtime_quotes",
                        staticmethod(lambda codes: len(codes)))
    baseline = copy.deepcopy(cfg)

    start.run_cli("live_multi", {"codes": CODES2})

    assert loads == [1], "整批只读一次盘"
    assert len(seen_configs) == len(CODES2)
    assert len({id(c) for c in seen_configs}) == len(CODES2), "每只必须是独立副本"
    assert "POLLUTED-BY-FIRST-STOCK" in seen_configs[0]["skills"]["enabled"]
    assert "POLLUTED-BY-FIRST-STOCK" not in seen_configs[1]["skills"]["enabled"], \
        "第一只的覆盖不得泄漏到第二只"
    assert seen_configs[1]["ai"]["debug"] is False, "debug 覆盖同样不得跨股泄漏"
    assert cfg == baseline, "批分析不得回写调用方配置"
    assert _latest()["exec_snapshot"]["sha256"] == _fp(baseline)


# ═══════ 2. 冻结失败：首只前显式终止、零业务 ═══════

class _BoomLoad:
    def __init__(self, exc):
        self.exc = exc
        self.calls = []

    def __call__(self, *a, **k):
        self.calls.append(1)
        raise self.exc


@pytest.mark.parametrize("mode", MODES)
def test_freeze_failure_aborts_before_first_stock_without_business(mode, monkeypatch,
                                                                  capsys, caplog):
    caplog.set_level(logging.DEBUG)
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES3)
    boom = _BoomLoad(RuntimeError("配置解析失败 " + _SECRET_KEY))
    monkeypatch.setattr(cli_main, "load_config", boom)
    out = env.run()

    assert boom.calls == [1], "失败也只尝试加载一次"
    assert env.calls == [], "冻结失败不得调用分析"
    assert env.prefetches == [], "冻结失败不得预取行情"
    env.pm.return_value.request_account_facts.assert_not_called()
    assert ss.get_recent_batch_tasks(5) == [], "冻结失败不得登记 run"
    assert "配置加载失败" in out and "本次未开始分析" in out
    assert _SECRET_KEY not in out, "失败提示不得回显异常正文/秘密"
    assert _SECRET_KEY not in caplog.text, "日志只记异常类型，不含正文"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("bad", [
    pytest.param(None, id="none"),
    pytest.param(["not", "a", "dict"], id="list"),
    pytest.param("raw-string", id="str"),
    pytest.param(42, id="int"),
])
def test_freeze_failure_on_non_dict_config(mode, bad, monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2, load_result=bad)
    out = env.run()

    assert env.calls == [] and env.prefetches == []
    env.pm.return_value.request_account_facts.assert_not_called()
    assert ss.get_recent_batch_tasks(5) == []
    assert "配置加载失败" in out


@pytest.mark.parametrize("mode", MODES)
def test_freeze_failure_on_deepcopy_error(mode, monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2)

    class _BadCopy:
        @staticmethod
        def deepcopy(value):
            raise RecursionError("deepcopy boom")

    monkeypatch.setattr(start, "copy", _BadCopy(), raising=False)
    out = env.run()

    assert env.calls == [] and env.prefetches == []
    assert ss.get_recent_batch_tasks(5) == []
    assert "配置加载失败" in out


# ═══════ 3. 预算与冻结的时序（不改变 T05-L 语义） ═══════

@pytest.mark.parametrize("mode", MODES)
def test_budget_expired_before_freeze_does_not_load_config(mode, monkeypatch, capsys):
    _clock(monkeypatch, _JumpClock)
    env = _Env(monkeypatch, capsys, mode, CODES3)
    out = env.run(budget=60)

    assert env.loads == [], "冻结前预算已到期：不得加载配置"
    assert env.calls == [], "一只也不得开始"
    if mode == "live_all":
        assert ss.get_recent_batch_tasks(5) == [], "名单未知: 不登记 run"
        assert "预算未开始" not in out
    else:
        run = _latest()
        assert run["not_started"] == {c: "budget_exhausted" for c in CODES3}


@pytest.mark.parametrize("mode", MODES)
def test_budget_expiring_during_freeze_starts_no_stock(mode, monkeypatch, capsys):
    clock = _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES3)
    env.on_load = lambda: clock.advance(1000.0)
    out = env.run(budget=60)

    assert env.loads == [1], "冻结前未到期: 仍然只加载一次"
    assert env.calls == [], "冻结期间预算耗尽: 不得启动任何股票"
    run = _latest()
    assert run["not_started"] == {c: "budget_exhausted" for c in CODES3}
    assert "预算未开始" in out


def test_freeze_happens_after_budget_start_and_noai_note(monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, "live_multi", CODES2)
    out = env.run(budget=60)
    assert out.index("启动预算：") < out.index("live 批配置已冻结") < out.index("[1/2]")


def test_l_all_freeze_happens_after_confirmation(monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, "live_scan_all", CODES2)
    env.run(budget=60)

    assert len(env.pre) == 1
    assert "live 批配置已冻结" not in env.pre[0], "确认前不得冻结/加载配置"
    assert env.loads == [1]


# ═══════ 4. main.analyze_live 的可选 config（向后兼容） ═══════

def _wire_real_analyze(monkeypatch, tmp_path):
    """真实 analyze_live 完整路径的离线替身：只替换外部叶子，返回编排器 config 记录。"""
    seen = {"configs": []}

    def fake_get_stock_data(code, *a, **k):
        return StockData(stock_code=code, stock_name="测试股", price=10.0, volume=100000,
                         change_pct=1.0, avg_volume_20=100000.0,
                         ma5=9.8, ma20=9.5, ma60=9.0, quote_as_of="2026-09-30")

    orch = mock.MagicMock(name="orchestrator")
    orch.analyze.return_value = (mock.MagicMock(), mock.MagicMock(),
                                 mock.MagicMock(), mock.MagicMock())

    def fake_build(config, rag_service=None):
        seen["configs"].append(config)
        return orch

    monkeypatch.setattr(ak_mod, "get_stock_data", fake_get_stock_data)
    monkeypatch.setattr(cli_main, "_cli_rag", lambda: None)
    monkeypatch.setattr(cli_main, "build_live_orchestrator", fake_build)
    monkeypatch.setattr(cli_main, "_watch_pool_touch", lambda *a, **k: None)
    monkeypatch.setattr(cli_main, "_print_plain_summary", lambda *a, **k: None)
    monkeypatch.setattr(cli_main, "_print_daily_research_view", lambda *a, **k: None)
    monkeypatch.setattr(cli_main._evidence, "record_evidence", lambda *a, **k: None)
    monkeypatch.setattr(analysis_service, "build_decision_packet", lambda *a, **k: None)
    monkeypatch.setattr(shadow_diff, "capture_shadow", lambda *a, **k: None)
    # 账户事实：真实 PortfolioManager（空账户），账本落 tmp
    monkeypatch.setattr(portfolio_mod, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH",
                        str(tmp_path / "proposals.json"))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "account_events.jsonl")
    return seen


def test_analyze_live_signature_has_optional_config():
    params = inspect.signature(cli_main.analyze_live).parameters
    assert "config" in params, "analyze_live 必须新增可选 config 参数"
    assert params["config"].default is None, "config 默认必须是 None（向后兼容）"


def test_analyze_live_with_config_does_not_read_disk(monkeypatch, tmp_path):
    seen = _wire_real_analyze(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise AssertionError("提供 config 时不得再读盘")

    monkeypatch.setattr(cli_main, "load_config", boom)
    cfg = _effective_config(ai={"debug": False})
    result = cli_main.analyze_live("600519", compact=True, config=cfg)

    assert result is cli_main.LiveOutcome.COMPLETE
    assert len(seen["configs"]) == 1
    used = seen["configs"][0]
    assert used["ai"]["deepseek"]["model"] == "deepseek-flash"
    assert used is not cfg, "必须逐股深拷贝，不得把批快照本体交给编排器"
    assert cfg["ai"]["debug"] is False, "不得污染调用方的配置"


def test_analyze_live_config_per_stock_copy_and_debug_override(monkeypatch, tmp_path):
    seen = _wire_real_analyze(monkeypatch, tmp_path)
    cfg = _effective_config(ai={"debug": False})
    baseline = copy.deepcopy(cfg)

    cli_main.analyze_live("600519", compact=True, config=cfg, ai_debug=True)

    assert seen["configs"][0]["ai"]["debug"] is True, "debug 覆盖生效于本只副本"
    assert cfg == baseline, "本只覆盖不得写回批快照"


def test_analyze_live_config_ai_overrides_do_not_leak(monkeypatch, tmp_path):
    seen = _wire_real_analyze(monkeypatch, tmp_path)
    cfg = _effective_config()
    baseline = copy.deepcopy(cfg)

    cli_main.analyze_live("600519", compact=True, config=cfg,
                          ai_overrides={"disable_ai": True})

    assert seen["configs"][0]["ai"]["enabled"] is False
    assert cfg == baseline, "ai_overrides 不得污染调用方配置"


def test_analyze_live_default_none_still_reads_disk(monkeypatch, tmp_path):
    seen = _wire_real_analyze(monkeypatch, tmp_path)
    loads = []
    cfg = _effective_config()

    def fake_load(*a, **k):
        loads.append(1)
        return copy.deepcopy(cfg)

    monkeypatch.setattr(cli_main, "load_config", fake_load)
    cli_main.analyze_live("600519", compact=True)

    assert loads == [1], "默认 None 必须保持既有读盘行为"
    assert seen["configs"][0]["ai"]["enabled"] is True


# ═══════ 5. 指纹派生：确定性 / 白名单 / 秘密剔除 / fail-closed ═══════

def test_fingerprint_schema_scope_and_shape():
    cfg = _effective_config()
    desc = ss.derive_live_config_descriptor(cfg)

    assert set(desc) == _CONFIG_KEYS, "只允许落 schema/scope/sha256"
    assert desc["schema"] == LIVE_FP_SCHEMA
    assert desc["scope"] == LIVE_FP_SCOPE, "scope 必须固定"
    assert isinstance(desc["sha256"], str) and _SHA_RE.match(desc["sha256"])
    assert ss.LIVE_CONFIG_SCHEMA == LIVE_FP_SCHEMA
    assert ss.LIVE_CONFIG_SCOPE == LIVE_FP_SCOPE


def test_fingerprint_is_deterministic_and_key_order_independent():
    cfg = _effective_config()
    assert _fp(cfg) == _fp(copy.deepcopy(cfg))
    assert _fp(cfg) == _fp(_reordered(cfg)), "键序不得影响指纹"


@pytest.mark.parametrize("path,value", [
    pytest.param(("decision", "signal_weights", "ma_trend"), 2.0, id="decision"),
    pytest.param(("skills", "enabled"), ["macd", "ma_trend", "rsi"], id="skills-order"),
    pytest.param(("skills", "types", "macd"), "regulator", id="skills-types"),
    pytest.param(("event", "ai_classify"), False, id="event"),
    pytest.param(("event", "keyword_scan"), False, id="event-keyword-scan"),
    pytest.param(("event", "rules_path"), "./src/scanner/other_rules.yaml", id="event-path"),
    pytest.param(("entry_exit", "atr", "period"), 20, id="entry-exit"),
    pytest.param(("fusion", "risk_profile", "per_stock_max"), 0.25, id="fusion"),
    pytest.param(("ai", "enabled"), False, id="ai-enabled"),
    pytest.param(("ai", "provider"), "kimi", id="ai-provider"),
    pytest.param(("ai", "direction_aware_adjustment"), False, id="ai-direction"),
    pytest.param(("ai", "request_timeout"), 45, id="ai-timeout"),
    pytest.param(("ai", "max_retries"), 1, id="ai-retries"),
    pytest.param(("ai", "modifier", "sentiment_weight"), 0.5, id="ai-modifier"),
    pytest.param(("ai", "deepseek", "model"), "deepseek-pro", id="ai-current-model"),
])
def test_fingerprint_changes_on_whitelisted_effective_value(path, value):
    base = _effective_config()
    changed = copy.deepcopy(base)
    node = changed
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert _fp(base) != _fp(changed), f"白名单参数 {path} 变化必须改变指纹"


@pytest.mark.parametrize("path,value", [
    pytest.param(("rag", "chunk_size"), 800, id="rag"),
    pytest.param(("chat", "max_tokens"), 1, id="chat"),
    pytest.param(("scanner", "cache_ttl"), 5, id="scanner"),
    pytest.param(("ranking", "enabled"), False, id="ranking"),
    pytest.param(("app", "name"), "别的名字", id="app"),
    pytest.param(("ai", "debug"), True, id="ai-debug"),
    pytest.param(("ai", "deepseek", "context_window"), 1, id="context-window"),
    pytest.param(("ai", "deepseek", "model_pro"), "deepseek-pro2", id="model-pro"),
    pytest.param(("ai", "deepseek", "max_output_tokens"), 1, id="max-output"),
    pytest.param(("ai", "kimi", "model"), "kimi-k3", id="other-provider-model"),
    pytest.param(("ai", "kimi", "api_key"), "rotated-kimi", id="other-provider-key"),
])
def test_fingerprint_ignores_non_whitelisted_config(path, value):
    base = _effective_config()
    changed = copy.deepcopy(base)
    node = changed
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert _fp(base) == _fp(changed), f"非白名单 {path} 不得影响指纹"


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda c: c["ai"]["deepseek"].__setitem__("api_key", "sk-rotated-0001"),
                 id="provider-key"),
    pytest.param(lambda c: c["ai"]["deepseek"].__setitem__(
        "base_url", "https://other.secret-host.invalid/v2"), id="provider-url"),
    pytest.param(lambda c: c["ai"].__setitem__("api_key", _SECRET_KEY), id="ai-key"),
    pytest.param(lambda c: c["ai"].__setitem__("access_token", _SECRET_TOKEN), id="ai-token"),
    pytest.param(lambda c: c["decision"].__setitem__("api_key", _SECRET_KEY), id="section-key"),
    pytest.param(lambda c: c["decision"].__setitem__("nested_secret",
                                                     {"token": _SECRET_TOKEN}), id="nested"),
    pytest.param(lambda c: c["event"].__setitem__("webhook_url", _SECRET_URL), id="webhook"),
    pytest.param(lambda c: c["fusion"].__setitem__("probe", "sk-probe-0001"), id="sk-value"),
])
def test_fingerprint_ignores_secret_keys_and_secret_values(mutate):
    base = _effective_config()
    changed = copy.deepcopy(base)
    mutate(changed)
    assert _fp(base) == _fp(changed), "被剔除的秘密（键自身或值）不得影响指纹"


def test_fingerprint_ignores_presence_of_secret_keys():
    base = _effective_config()
    without = copy.deepcopy(base)
    without["decision"].pop("api_key", None)
    without["ai"].pop("access_token", None)
    assert _fp(base) == _fp(without)


def test_fingerprint_does_not_strip_lookalike_legit_values():
    base = _effective_config(fusion={"regime": "risk-off"})
    other = _effective_config(fusion={"regime": "risk-on"})
    assert _fp(base) != _fp(other), "risk-off 等合法值不得被当凭据剔除"


def test_fingerprint_uses_current_provider_model_only():
    """ai 只取「当前 provider」的 model：切到 kimi 后 kimi.model 才进身份，deepseek 退出。"""
    base = {"ai": {"enabled": True, "provider": "kimi",
                   "deepseek": {"model": "deepseek-flash"},
                   "kimi": {"model": "kimi-k2.6"}}}
    other_kimi = {"ai": {"enabled": True, "provider": "kimi",
                         "deepseek": {"model": "deepseek-flash"},
                         "kimi": {"model": "kimi-k3"}}}
    back = {"ai": {"enabled": True, "provider": "deepseek",
                   "deepseek": {"model": "deepseek-flash"},
                   "kimi": {"model": "kimi-k3"}}}
    assert _fp(base) != _fp(other_kimi), "当前 provider 的 model 变化必须改变指纹"
    assert _fp(back) != _fp(base), "切换 provider 必须改变指纹"


def test_fingerprint_treats_url_valued_whitelisted_key_as_secret():
    a = _effective_config(event={"rules_path": "https://a.invalid/event_rules.yaml"})
    b = _effective_config(event={"rules_path": "https://b.invalid/event_rules.yaml"})
    plain = _effective_config(event={"rules_path": "./src/scanner/event_rules.yaml"})
    assert _fp(a) == _fp(b), "URL 形状值变化不得影响指纹"
    assert _fp(a) != _fp(plain), "被剔除的值本身换成普通路径后指纹应变"


@pytest.mark.parametrize("build", [
    pytest.param(lambda c: c["decision"]["signal_weights"].__setitem__(
        "ma_trend", float("nan")), id="nan"),
    pytest.param(lambda c: c["event"].__setitem__("max_events_display", float("inf")),
                 id="inf"),
    pytest.param(lambda c: c["decision"].__setitem__(1, 2), id="non-str-key"),
    pytest.param(lambda c: c["event"].__setitem__("blob", b"bytes"), id="bytes"),
    pytest.param(lambda c: c["fusion"].__setitem__("when", datetime.now()), id="datetime"),
    pytest.param(lambda c: c.__setitem__("decision", "not-a-dict"), id="section-non-dict"),
    pytest.param(lambda c: c.__setitem__("ai", "not-a-dict"), id="ai-non-dict"),
    pytest.param(lambda c: c["ai"].__setitem__("provider", ["deepseek"]), id="provider-list"),
    pytest.param(lambda c: c["fusion"].__setitem__("loop", c["fusion"]), id="cycle"),
])
def test_fingerprint_fails_closed_on_unsafe_config(build):
    cfg = _effective_config()
    build(cfg)
    assert ss.derive_live_config_descriptor(cfg) is None, \
        "不可安全规范化的配置不得冒称已冻结指纹（不静默用 repr）"


@pytest.mark.parametrize("bad", [
    pytest.param(None, id="none"),
    pytest.param([], id="list"),
    pytest.param("raw", id="str"),
    pytest.param(42, id="int"),
])
def test_derive_rejects_non_dict(bad):
    assert ss.derive_live_config_descriptor(bad) is None


def test_validate_accepts_only_canonical_descriptor():
    desc = ss.derive_live_config_descriptor(_effective_config())
    assert ss.validate_live_config_descriptor(desc) == desc
    assert ss.validate_live_config_descriptor(dict(desc)) == desc


@pytest.mark.parametrize("raw", [
    pytest.param(None, id="none"),
    pytest.param([], id="list"),
    pytest.param("x", id="str"),
    pytest.param({"schema": LIVE_FP_SCHEMA, "scope": LIVE_FP_SCOPE}, id="missing-sha"),
    pytest.param(dict(_live_desc(), extra=1), id="extra-key"),
    pytest.param(dict(_live_desc(), schema="muyun.live.config_fingerprint/v2"), id="schema-v2"),
    pytest.param(dict(_live_desc(), schema=batch_scorer.EXEC_SNAPSHOT_SCHEMA), id="ba-schema"),
    pytest.param(dict(_live_desc(), scope="other_scope"), id="scope"),
    pytest.param(_live_desc(sha="A" * 64), id="uppercase"),
    pytest.param(_live_desc(sha="a" * 63), id="short"),
    pytest.param(_live_desc(sha="z" * 64), id="non-hex"),
    pytest.param(_live_desc(sha=123), id="int-sha"),
])
def test_validate_rejects_bad_live_descriptors(raw):
    assert ss.validate_live_config_descriptor(raw) is None


# ═══════ 6. 账本 kind 门：写入/读取同门、跨 kind 拒绝 ═══════

def test_validate_batch_snapshot_contract_by_kind():
    ba_snap = batch_scorer.derive_exec_snapshot(_effective_config(), False)
    live = _live_desc()
    assert ss.validate_batch_snapshot("ba", ba_snap) == ba_snap
    assert ss.validate_batch_snapshot("ba", live) is None, "ba 不认 live 指纹"
    for kind in LIVE_KINDS:
        assert ss.validate_batch_snapshot(kind, live) == live
        assert ss.validate_batch_snapshot(kind, ba_snap) is None, "live 不认 ba 快照"
    for kind in ("zz", "bz", "", None):
        assert ss.validate_batch_snapshot(kind, live) is None
        assert ss.validate_batch_snapshot(kind, ba_snap) is None
    for kind in ("ba",) + tuple(LIVE_KINDS):
        assert ss.validate_batch_snapshot(kind, None) is None
        assert ss.validate_batch_snapshot(kind, "raw") is None


@pytest.mark.parametrize("kind", LIVE_KINDS)
def test_live_run_stores_canonical_fingerprint(kind, monkeypatch, tmp_path):
    path = tmp_path / "batch_tasks.json"
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", path)
    desc = _live_desc()
    rid = ss.batch_task_start(kind, "k", ["A", "B"], snapshot=dict(desc))

    assert isinstance(rid, str) and rid, "合法指纹必须随 run 登记"
    record = _latest()
    assert record["exec_snapshot"] == desc, "只落规范化的 schema/scope/sha256"
    assert set(record["exec_snapshot"]) == _CONFIG_KEYS
    text = path.read_text(encoding="utf-8")
    assert _SECRET_KEY not in text and _SECRET_URL not in text
    assert "deepseek-flash" not in text, "不得落任何配置内容"


@pytest.mark.parametrize("kind", LIVE_KINDS)
def test_live_run_rejects_ba_snapshot_and_bad_live_descriptor(kind, monkeypatch, tmp_path):
    path = tmp_path / "batch_tasks.json"
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", path)
    ba_snap = batch_scorer.derive_exec_snapshot(_effective_config(), False)
    cases = [
        ba_snap,
        dict(_live_desc(), sha256="B" * 64),
        dict(_live_desc(), scope="wrong"),
        dict(_live_desc(), api_key=_SECRET_KEY),
        "raw",
    ]
    for snapshot in cases:
        path.write_text(json.dumps({"tasks": []}), encoding="utf-8")
        rid = ss.batch_task_start(kind, "k", ["A"], snapshot=snapshot)
        assert isinstance(rid, str) and rid
        record = _latest()
        assert "exec_snapshot" not in record, f"非法/跨 kind 描述不得落盘: {snapshot!r}"
        assert _SECRET_KEY not in path.read_text(encoding="utf-8")


def test_ba_kind_still_accepts_only_ba_snapshot(monkeypatch, tmp_path):
    path = tmp_path / "batch_tasks.json"
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", path)
    ba_snap = batch_scorer.derive_exec_snapshot(_effective_config(), False)
    ss.batch_task_start("ba", "k", ["A"], snapshot=_live_desc())
    assert "exec_snapshot" not in _latest(), "ba 不接受 live 指纹"
    ss.batch_task_start("ba", "k2", ["A"], snapshot=ba_snap)
    assert _latest()["exec_snapshot"] == ba_snap, "ba 原合同不变"


def test_run_and_snapshot_persist_in_one_atomic_write(monkeypatch, tmp_path):
    calls = []
    real = ss._atomic_write_json

    def spy(path, data, indent=2):
        calls.append((path, copy.deepcopy(data)))
        return real(path, data, indent=indent)

    monkeypatch.setattr(ss, "_atomic_write_json", spy)
    path = tmp_path / "batch_tasks.json"
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", path)
    rid = ss.batch_task_start("l", "k", ["A", "B"], snapshot=_live_desc())

    assert len(calls) == 1, "run 与 exec_snapshot 必须同一原子写"
    record = calls[0][1]["tasks"][0]
    assert record["run_id"] == rid and record["exec_snapshot"] == _live_desc()


# ═══════ 7. tasks 展示：live 已冻结/未冻结、ba 不变 ═══════

def test_tasks_shows_frozen_live_config_with_scope_note(monkeypatch, tmp_path, capsys):
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [_run_record()])

    assert "live批配置已冻结" in flat
    assert _live_desc()["sha256"] in flat, "tasks 必须显示规范指纹全文"
    assert "技能YAML内容/数据/提示词未核定" in flat
    assert "本批仅ba记录执行参数快照" not in flat, "不得再把 live 一律硬编码未冻结"


@pytest.mark.parametrize("snapshot", [
    pytest.param(None, id="missing"),
    pytest.param({"schema": LIVE_FP_SCHEMA, "scope": LIVE_FP_SCOPE}, id="missing-sha"),
    pytest.param(dict(_live_desc(), sha256="F" * 64), id="uppercase"),
    pytest.param(dict(_live_desc(), scope="other"), id="bad-scope"),
    pytest.param(dict(_live_desc(), extra="x"), id="extra-key"),
    pytest.param("raw-string", id="raw"),
    pytest.param({"schema": batch_scorer.EXEC_SNAPSHOT_SCHEMA, "params": {},
                  "sha256": "a" * 64}, id="ba-shape"),
])
def test_tasks_shows_unfrozen_for_bad_or_missing_live_descriptor(snapshot, monkeypatch,
                                                               tmp_path, capsys):
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys,
                              [_run_record(snapshot=snapshot)])
    assert "未冻结" in flat
    assert "已冻结" not in flat, "坏/缺描述绝不能被渲染成已冻结"
    assert "raw-string" not in out


def test_tasks_keeps_ba_contract_wording(monkeypatch, tmp_path, capsys):
    ba_snap = batch_scorer.derive_exec_snapshot(_effective_config(), False)
    record = _run_record(kind="ba", snapshot=ba_snap)
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [record])
    assert "ba执行参数快照已冻结" in flat
    assert ba_snap["sha256"][:12] in flat
    assert "live批配置" not in flat


def test_tasks_legacy_live_record_stays_unrecorded(monkeypatch, tmp_path, capsys):
    legacy = {"kind": "l all", "key": "旧记录", "codes": ["A"], "done": {},
              "failed": {}, "ts_started": "t", "ts_updated": "t"}
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys, [legacy])
    assert "旧记录" in out
    assert "配置" in flat and "未记录" in flat, "旧记录不补造配置身份"


# ═══════ 8. 同一指纹：启动行 = 账本 = tasks ═══════

@pytest.mark.parametrize("mode", MODES)
def test_startup_ledger_and_tasks_show_same_fingerprint(mode, monkeypatch, tmp_path, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2)
    out = env.run()

    startup = _SHA_IN_TEXT.findall(out)
    assert len(set(startup)) == 1, f"启动行应只有一个规范指纹: {set(startup)}"
    sha = startup[0]
    expected = _fp(_effective_config())
    assert sha == expected, "启动行指纹必须由冻结的有效配置派生"

    record = _latest()
    assert record["exec_snapshot"] == {"schema": LIVE_FP_SCHEMA, "scope": LIVE_FP_SCOPE,
                                       "sha256": sha}
    assert record["codes"] == CODES2

    cli_main.batch_tasks_cmd()
    tasks_out = capsys.readouterr().out
    assert sha in _flat(tasks_out), "tasks 必须显示同一规范指纹（rich 折行前拉平比较）"
    assert "未冻结" not in _flat(tasks_out)


# ═══════ 9. 既有语义保持（单只/确认/预算提示/错误零业务） ═══════

def test_single_live_mode_keeps_old_call_shape(monkeypatch, capsys):
    """单只 l：不传 config、不新增确认、不冻结批配置。"""
    calls = []

    def fake_analyze(code, **kw):
        calls.append((code, kw))
        return cli_main.LiveOutcome.COMPLETE

    monkeypatch.setattr(cli_main, "analyze_live", fake_analyze)
    start.run_cli("live", {"stock_code": "600519"})

    assert [c for c, _ in calls] == ["600519"]
    assert "config" not in calls[0][1], "单只保持既有调用形状（config 缺省）"
    assert "live 批配置已冻结" not in capsys.readouterr().out


@pytest.mark.parametrize("mode", MODES)
def test_no_budget_hint_and_no_new_confirmation_preserved(mode, monkeypatch, capsys):
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2)
    out = env.run()

    hint_scope = env.pre[0] if mode == "live_scan_all" else out
    assert "可选时限" in hint_scope and "--budget" in hint_scope
    assert len(env.prompts) == (1 if mode == "live_scan_all" else 0), "不得新增确认门"
    assert "启动预算" not in out


@pytest.mark.parametrize("mode", MODES)
def test_budget_rejected_before_any_config_load(mode, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli_main, "analyze_live", lambda *a, **k: calls.append("a"))
    monkeypatch.setattr(cli_main, "load_config", lambda *a, **k: calls.append("l") or {})

    for bad in (0, -1, float("nan"), "60", True):
        args = {"live_multi": {"codes": CODES2}, "live_all": {},
                "live_scan_all": {"force": False}}[mode]
        start.run_cli(mode, dict(args, budget=bad))

    out = capsys.readouterr().out
    assert calls == [], "预算坏值必须在读取配置/分析之前拒绝"
    assert "预算参数无效" in out


@pytest.mark.parametrize("mode", MODES)
def test_three_entry_batch_still_reports_outcomes(mode, monkeypatch, capsys):
    """T05-L 五态/完成标记语义不因冻结而改变：完整分析仍计成功。"""
    _clock(monkeypatch)
    env = _Env(monkeypatch, capsys, mode, CODES2)
    out = env.run()

    assert "批量完成：2/2 成功" in out
    run = _latest()
    assert set(run["done"]) == set(CODES2)
    assert run["exec_snapshot"]["sha256"] == _fp(_effective_config())


# ═══════ 10. fix1：秘密 token / 有效默认 provider / kind 门接线 / 不可派生指纹 ═══════

_FIX1_SK_A = "sk-live-ALPHA-0001"
_FIX1_SK_B = "sk-live-BRAVO-0002"
_FIX1_URL_A = "https://endpoint-a.invalid/v1"
_FIX1_URL_B = "https://endpoint-b.invalid/v1"


@pytest.mark.parametrize("template", [
    pytest.param(" {secret}", id="leading-space"),
    pytest.param("{secret} ", id="trailing-space"),
    pytest.param("\t{secret}\n", id="tab-newline"),
    pytest.param("Bearer {secret}", id="bearer-prefix"),
    pytest.param("prefix {secret} suffix", id="in-text"),
    pytest.param("note: see {secret} now", id="text-with-punct"),
])
def test_fingerprint_ignores_standalone_sk_token_anywhere_in_text(template):
    """F1：前后空白 / Bearer 前缀 / 普通文本中的独立 sk- token 都不得影响指纹。"""
    a = _effective_config(fusion={"probe": template.format(secret=_FIX1_SK_A)})
    b = _effective_config(fusion={"probe": template.format(secret=_FIX1_SK_B)})
    assert _fp(a) == _fp(b), f"换密钥不得改变指纹: {template!r}"


@pytest.mark.parametrize("template", [
    pytest.param("{url}", id="bare-url"),
    pytest.param(" {url} ", id="url-with-spaces"),
    pytest.param("see {url} for details", id="url-in-text"),
    pytest.param("endpoint: {url}", id="url-after-label"),
])
def test_fingerprint_excludes_whole_string_containing_standalone_url(template):
    """F1：普通文本里带独立 URL 时整字符串剔除——绝不能把 URL 端点哈希进去。"""
    a = _effective_config(fusion={"probe": template.format(url=_FIX1_URL_A)})
    b = _effective_config(fusion={"probe": template.format(url=_FIX1_URL_B)})
    assert _fp(a) == _fp(b), f"URL 变化不得改变指纹: {template!r}"


def test_fingerprint_keeps_ordinary_text_changes_visible():
    """F1 反向：普通文本（无 URL/sk- token）变化必须改变指纹，不做通用秘密识别。"""
    a = _effective_config(fusion={"note": "plain text alpha"})
    b = _effective_config(fusion={"note": "plain text bravo"})
    assert _fp(a) != _fp(b)


def test_fingerprint_keeps_risk_off_visible_with_secret_neighbour():
    """F1 反向：同节里有被剔秘密时，risk-off 等合法值变化仍然可见。"""
    a = _effective_config(fusion={"regime": "risk-off", "probe": " " + _FIX1_SK_A})
    b = _effective_config(fusion={"regime": "risk-on", "probe": " " + _FIX1_SK_B})
    assert _fp(a) != _fp(b)


def test_secret_token_detection_does_not_treat_words_as_secrets():
    """F1：risk-off / 普通文本 / 相对路径不因 token 规则被剔除。"""
    base = _effective_config()
    changed = _effective_config(fusion={"regime": "risk-on"})
    assert _fp(base) != _fp(changed)
    path_a = _effective_config(event={"rules_path": "./src/scanner/a.yaml"})
    path_b = _effective_config(event={"rules_path": "./src/scanner/b.yaml"})
    assert _fp(path_a) != _fp(path_b), "相对路径仍是有效配置值"


def test_default_provider_matches_real_consumers():
    """F2：默认 provider 必须与真实消费者一致（AIModifier/EventLayer 用 deepseek）。

    只读源码断言，不实例化（实例化会改 NewsClient 全局缓存并可能读环境变量）。
    """
    from src.core.ai_modifier import AIModifier
    from src.core.event_layer import EventLayer

    for cls in (AIModifier, EventLayer):
        source = inspect.getsource(cls)
        assert 'get("provider", "deepseek")' in source, f"{cls.__name__} 默认 provider 变了"
    assert ss._DEFAULT_AI_PROVIDER == "deepseek"


def test_fingerprint_uses_default_provider_model_when_provider_omitted():
    """F2：合法省略 ai.provider 时，按默认 deepseek 取 model，model 变化必须变指纹。"""
    a = {"ai": {"enabled": True, "deepseek": {"model": "model-A"}}}
    b = {"ai": {"enabled": True, "deepseek": {"model": "model-B"}}}
    assert _fp(a) != _fp(b), "省略 provider 时 deepseek.model 必须进入指纹"
    kimi_a = {"ai": {"enabled": True, "kimi": {"model": "kimi-A"}}}
    kimi_b = {"ai": {"enabled": True, "kimi": {"model": "kimi-B"}}}
    assert _fp(kimi_a) == _fp(kimi_b), "默认 provider 下非当前 provider 的 model 不参与"


def test_fingerprint_still_rejects_non_string_explicit_provider():
    """F2：显式 provider 非字符串仍 fail-closed（不借用默认值洗白）。"""
    cfg = _effective_config()
    cfg["ai"]["provider"] = ["deepseek"]
    assert ss.derive_live_config_descriptor(cfg) is None


def test_ba_config_lines_use_shared_kind_gate(monkeypatch, tmp_path, capsys):
    """F3：ba 的 tasks 读取也必须经 session_state.validate_batch_snapshot(kind, raw)。"""
    calls = []
    real = ss.validate_batch_snapshot

    def spy(kind, raw):
        calls.append((kind, raw))
        return real(kind, raw)

    monkeypatch.setattr(ss, "validate_batch_snapshot", spy)
    ba_snap = batch_scorer.derive_exec_snapshot(_effective_config(), False)
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys,
                              [_run_record(kind="ba", snapshot=ba_snap)])

    assert calls and calls[0][0] == "ba" and calls[0][1] == ba_snap
    assert "ba执行参数快照已冻结" in flat
    assert ba_snap["sha256"][:12] in flat


@pytest.mark.parametrize("kind,snapshot", [
    pytest.param("ba", "ba", id="ba"),
    pytest.param("l", "live", id="live"),
])
def test_config_lines_gate_is_authoritative_for_reads(kind, snapshot, monkeypatch, tmp_path,
                                                     capsys):
    """F3：读侧门被替换成拒绝时，两种 kind 都只能显示未冻结（证明共用同一道门）。"""
    ba_snap = batch_scorer.derive_exec_snapshot(_effective_config(), False)
    raw = ba_snap if snapshot == "ba" else _live_desc()
    calls = []

    def deny(k, r):
        calls.append(k)
        return None

    monkeypatch.setattr(ss, "validate_batch_snapshot", deny)
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys,
                              [_run_record(kind=kind, snapshot=raw)])
    assert calls == [kind]
    assert "未冻结" in flat and "已冻结" not in flat


def test_write_and_read_share_one_kind_gate(monkeypatch, tmp_path):
    """F3：写侧（batch_task_start）与读侧（tasks 展示）共用同一道 kind 门。"""
    path = tmp_path / "batch_tasks.json"
    monkeypatch.setattr(ss, "_BATCH_TASKS_FILE", path)
    monkeypatch.setattr(ss, "validate_batch_snapshot", lambda kind, raw: None)

    live_rid = ss.batch_task_start("l", "kv", ["A"], snapshot=_live_desc())
    assert isinstance(live_rid, str) and live_rid, "门拒绝也要照常登记 run"
    assert "exec_snapshot" not in _latest()

    ba_snap = batch_scorer.derive_exec_snapshot(_effective_config(), False)
    ba_rid = ss.batch_task_start("ba", "kb", ["A"], snapshot=ba_snap)
    assert isinstance(ba_rid, str) and ba_rid
    assert "exec_snapshot" not in _latest()


def test_cross_kind_snapshot_rejected_on_read_even_when_valid_elsewhere(monkeypatch, tmp_path,
                                                                       capsys):
    """F3：跨 kind 的合法描述在另一 kind 上也必须显示未冻结。"""
    ba_snap = batch_scorer.derive_exec_snapshot(_effective_config(), False)
    out, flat = _render_tasks(monkeypatch, tmp_path, capsys,
                              [_run_record(kind="l", snapshot=ba_snap)])
    assert "未冻结" in flat and "已冻结" not in flat

    out2, flat2 = _render_tasks(monkeypatch, tmp_path, capsys,
                                [_run_record(kind="ba", snapshot=_live_desc())])
    assert "未冻结" in flat2 and "已冻结" not in flat2


def _nan_config():
    return _effective_config(decision={"signal_weights": {"ma_trend": float("nan")}})


@pytest.mark.parametrize("mode", MODES)
def test_underivable_fingerprint_keeps_frozen_analysis_without_fingerprint_claim(
        mode, monkeypatch, capsys):
    """F5：配置已深拷贝成功但指纹不可派生（NaN）→ 继续真实冻结配置分析 + 显式未冻结。"""
    _clock(monkeypatch)
    cfg = _nan_config()
    env = _Env(monkeypatch, capsys, mode, CODES2)
    env.cfg = cfg
    out = env.run()

    assert env.loads == [1], "仍只加载一次"
    assert [c for c, _ in env.calls] == CODES2, "分析照常进行（不新增确认、不隐含关闭）"
    for cfg_seen in env.configs():
        assert math.isnan(cfg_seen["decision"]["signal_weights"]["ma_trend"]), \
            "分析用的是本批冻结副本（含原值），不是重新读取"

    record = _latest()
    assert record["codes"] == CODES2
    assert "exec_snapshot" not in record, "不可派生时不得保存/虚称指纹"
    assert "无法安全派生" in out and "未冻结" in out
    assert "指纹=" not in out, "不得打印任何指纹"
    assert "已冻结" not in out, "不得声称配置身份已冻结"
    assert len(env.prompts) == (1 if mode == "live_scan_all" else 0), "不新增确认门"
    assert "批量完成：2/2 成功" in out

    cli_main.batch_tasks_cmd()
    tasks_flat = _flat(capsys.readouterr().out)
    assert "未冻结" in tasks_flat and "已冻结" not in tasks_flat


# ═══════ 11. fix2：analyze_live → capture_shadow → _load_capture_switch 真实链 ═══════
#
# P1：批冻结配置只覆盖 analyze_live 本体；影子捕获若仍拿 config=None，_load_capture_switch
# 会逐股重新 src.config.load_config，用磁盘 fusion.mode/shadow_capture 决定是否捕获与
# blocking_gates（requested/effective 审计），却继续显示冻结 fusion 指纹。
# 下面用真实 analyze_live 完整路径 + 真实 capture_shadow（绝不 mock 捕获主体），
# 只隔离行情/AI/RAG/持久化等外围；两方向 × 两股票抓"逐股重读"与"错误总是透传"。

_GATES = ("J0–J3 场景与真实入口验收",
          "配对影子有效观察证据（J4 观察协议）",
          "独立验收（J5）")


def _dr_live(code):
    from types import SimpleNamespace
    return SimpleNamespace(decision=SimpleNamespace(value="HOLD"), score=0.5,
                           stock=SimpleNamespace(stock_code=code),
                           warnings=[], data_gaps=[])


def _sd_live(day):
    from src.data.models import PositionAction, SignalType, StrategyDecision, \
        StrategyState, TradeLifecycle
    return StrategyDecision(
        analysis_as_of=day, signal_findings=[],
        decision=SignalType.HOLD, position_action=PositionAction.HOLD_POSITION,
        position_ratio=0.2, sell_path=None,
        lifecycle_before=TradeLifecycle.HOLD, lifecycle_after=TradeLifecycle.HOLD,
        strategy_reasons=["策略理由"], divergence=None, new_state=StrategyState())


def _eval_live():
    from src.core.execution_layer import ExecutionEvaluation
    from src.data.models import PositionAction
    return ExecutionEvaluation(
        original_action=PositionAction.HOLD_POSITION,
        effective_action=PositionAction.HOLD_POSITION,
        blocked=False, block_reason="", slippage_pct=0.0,
        impact_cost_pct=0.0, total_cost_pct=0.0)


def _wire_shadow_chain(monkeypatch, tmp_path, holdings, disk_config):
    """真实 analyze_live → 真实 capture_shadow；只替换行情/编排器/落盘路径等外围。"""
    import src.config as config_module
    import src.data.horizon_plans as horizon_plans_module
    import src.data.research_store as research_store_module

    store = tmp_path / "shadow_diff.jsonl"
    monkeypatch.setattr(shadow_diff, "SHADOW_STORE_PATH", store)
    monkeypatch.setattr(portfolio_mod, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml"))
    monkeypatch.setattr(proposals_module, "DEFAULT_PROPOSALS_PATH",
                        str(tmp_path / "proposals.json"))
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", tmp_path / "account_events.jsonl")
    monkeypatch.setattr(horizon_plans_module, "PLANS_FILE", tmp_path / "horizon_plans.json")
    monkeypatch.setattr(research_store_module, "RESEARCH_DIR", tmp_path / "research")
    monkeypatch.setattr(cli_main._evidence, "_EVIDENCE_FILE", tmp_path / "evidence.jsonl")
    monkeypatch.setattr(cli_main._evidence, "_REPORT_DIR", tmp_path / "cards")

    pm = portfolio_mod.PortfolioManager()
    for code, ratio in holdings:
        pm.add_position(code, stock_name="N" + code, entry_price=10.0, ratio=ratio)

    def fake_get_stock_data(code, *a, **k):
        return StockData(stock_code=code, stock_name="N" + code, price=10.0,
                         volume=100000, change_pct=1.0, avg_volume_20=100000.0,
                         ma5=9.8, ma20=9.5, ma60=9.0, quote_as_of="2026-09-30")

    day = datetime.now().strftime("%Y-%m-%d")
    orch = mock.MagicMock(name="orchestrator")

    def fake_analyze(stock_data, **kw):
        return (_dr_live(stock_data.stock_code), _sd_live(day), _eval_live(), object())

    orch.analyze.side_effect = fake_analyze
    monkeypatch.setattr(ak_mod, "get_stock_data", fake_get_stock_data)
    monkeypatch.setattr(cli_main, "_cli_rag", lambda: None)
    monkeypatch.setattr(cli_main, "build_live_orchestrator",
                        lambda config, rag_service=None: orch)
    monkeypatch.setattr(cli_main, "_watch_pool_touch", lambda *a, **k: None)
    monkeypatch.setattr(cli_main, "_print_plain_summary", lambda *a, **k: None)
    monkeypatch.setattr(cli_main, "_print_daily_research_view", lambda *a, **k: None)
    monkeypatch.setattr(cli_main._evidence, "record_evidence", lambda *a, **k: None)
    monkeypatch.setattr(cli_main, "console", mock.MagicMock(name="console"))

    reads = []

    def fake_disk_load(*a, **k):
        reads.append(1)
        return copy.deepcopy(disk_config)

    monkeypatch.setattr(config_module, "load_config", fake_disk_load)

    def records():
        if not store.exists():
            return []
        return [json.loads(ln) for ln in store.read_text(encoding="utf-8").splitlines() if ln]

    return {"reads": reads, "records": records, "store": store}


def test_batch_config_governs_shadow_switch_when_frozen_on_disk_off(monkeypatch, tmp_path):
    """方向 A：冻结 fusion.mode=opt_in（捕获+3 门），磁盘 legacy_only（关闭）。

    当前 P1：config 未传入 → _load_capture_switch 逐股读盘 → legacy_only → 不捕获：
    本用例 0 条记录（应为 2）且读盘 2 次（应为 0）。
    """
    frozen = {"ai": {"enabled": False}, "fusion": {"mode": "opt_in"}}
    env = _wire_shadow_chain(monkeypatch, tmp_path, [("600519", 0.2), ("000001", 0.2)],
                             disk_config={"fusion": {"mode": "legacy_only"}})

    for code in ("600519", "000001"):
        cli_main.analyze_live(code, compact=True, config=frozen)

    recs = env["records"]()
    assert len(recs) == 2, f"冻结开关为捕获时必须每股一条影子记录: {recs}"
    assert {r["security_id"] for r in recs} == {"600519", "000001"}
    for r in recs:
        assert r["blocking_gates"] == list(_GATES), \
            "blocking_gates 必须来自冻结 config 的 requested/effective 解析"
    assert env["reads"] == [], "批内不得逐股重新 src.config.load_config"


def test_batch_config_governs_shadow_switch_when_frozen_off_disk_on(monkeypatch, tmp_path):
    """方向 B：冻结 fusion.mode=legacy_only（关闭），磁盘 opt_in（捕获）。

    当前 P1：config 未传入 → 用磁盘 → 错误写出 2 条记录（应为 0）且读盘 2 次。
    """
    frozen = {"ai": {"enabled": False}, "fusion": {"mode": "legacy_only"}}
    env = _wire_shadow_chain(monkeypatch, tmp_path, [("600519", 0.2), ("000001", 0.2)],
                             disk_config={"fusion": {"mode": "opt_in"}})

    for code in ("600519", "000001"):
        cli_main.analyze_live(code, compact=True, config=frozen)

    assert env["records"]() == [], "冻结开关为 legacy_only 时不得写影子记录"
    assert env["reads"] == [], "批内不得逐股重新 src.config.load_config"


def test_single_live_without_config_still_reads_shadow_switch_from_disk(monkeypatch,
                                                                      tmp_path):
    """对照：单只 config=None 保留既有二次读盘路径（不能改成无条件透传）。"""
    env = _wire_shadow_chain(monkeypatch, tmp_path, [("600519", 0.2)],
                             disk_config={"fusion": {"mode": "shadow"}})
    loads = []
    monkeypatch.setattr(cli_main, "load_config", lambda *a, **k: loads.append(1) or
                        {"ai": {"enabled": False}, "fusion": {"mode": "legacy_only"}})

    cli_main.analyze_live("600519", compact=True)

    assert loads == [1], "config=None 仍由 analyze_live 正常读盘一次"
    assert env["reads"] == [1], "单只影子开关仍走 _load_capture_switch 的旧读盘路径"
    recs = env["records"]()
    assert len(recs) == 1 and recs[0]["blocking_gates"] == [], \
        "影子开关取磁盘 fusion.mode=shadow（requested=effective，无阻塞门）"


# ═══════ 12. fix3：标点包围的独立凭据/URL token（M4 原范围，非通用秘密识别）═══════════
#
# M4 只要求"独立 sk-/URL token"被剔除：它可能被括号/引号/相邻标点包围，也可能是中文
# 文本里的裸 token（"凭据=(sk-live-A)" / "详见https://…。"). value.split() 只在空白处
# 切分，会把"(sk-live-A)"当成普通文本；下面用最小真实哈希不变红锁定，再按 token 边界
# （search + ASCII 前置边界）修复。命中仍剔除**整字符串**；不改其它启发式与已知过度剔除。

_SK_A = "sk-live-ALPHA-0001"
_SK_B = "sk-live-BRAVO-0002"
_URL_A = "https://endpoint-a.invalid/v1"
_URL_B = "https://endpoint-b.invalid/v1"
_LP, _RP = chr(0x300C), chr(0x300D)      # 「 」：项目约定全角字符用码点构造
_FW_LP, _FW_RP = chr(0xFF08), chr(0xFF09)  # （ ）


@pytest.mark.parametrize("template", [
    pytest.param("({token})", id="ascii-parens"),
    pytest.param('"{token}"', id="double-quotes"),
    pytest.param("'{token}'", id="single-quotes"),
    pytest.param(_LP + "{token}" + _RP, id="cn-quotes"),
    pytest.param(_FW_LP + "{token}" + _FW_RP, id="fullwidth-parens"),
    pytest.param("凭据=({token})", id="cn-label-parens"),
    pytest.param("值,{token}.", id="adjacent-punctuation"),
])
def test_fingerprint_ignores_punctuation_wrapped_sk_token(template):
    """F1/fix3：括号/引号/相邻标点包围的独立 sk- / sk_ token 不得影响指纹。"""
    a = _effective_config(fusion={"probe": template.format(token=_SK_A)})
    b = _effective_config(fusion={"probe": template.format(token=_SK_B)})
    assert _fp(a) == _fp(b), f"换密钥不得改变指纹: {template!r}"

    underscore_a = _effective_config(fusion={"probe": template.format(token="sk_live_ALPHA")})
    underscore_b = _effective_config(fusion={"probe": template.format(token="sk_live_BRAVO")})
    assert _fp(underscore_a) == _fp(underscore_b), f"sk_ 变体同样剔除: {template!r}"


@pytest.mark.parametrize("template", [
    pytest.param("({url})", id="ascii-parens"),
    pytest.param('"{url}"', id="double-quotes"),
    pytest.param(_LP + "{url}" + _RP, id="cn-quotes"),
    pytest.param("详见{url}。", id="cn-text-adjacent"),
    pytest.param("url=({url})", id="key-eq-parens"),
])
def test_fingerprint_ignores_punctuation_wrapped_url_token(template):
    """F1/fix3：括号/引号/中文相邻文本包围的独立 URL token 也不得影响指纹。"""
    a = _effective_config(fusion={"probe": template.format(url=_URL_A)})
    b = _effective_config(fusion={"probe": template.format(url=_URL_B)})
    assert _fp(a) == _fp(b), f"URL 变化不得改变指纹: {template!r}"


@pytest.mark.parametrize("word", [
    pytest.param("task-run", id="task"),
    pytest.param("disk-usage", id="disk"),
    pytest.param("brisk-only", id="brisk"),
    pytest.param("mask-ok", id="mask"),
    pytest.param("sklearn", id="sklearn-no-separator"),
])
def test_fingerprint_keeps_word_internal_sk_as_plain_text(word):
    """F1/fix3 反向：词中普通 sk 字样（非独立 token）变化必须仍可见。"""
    a = _effective_config(fusion={"probe": f"{word}-alpha"})
    b = _effective_config(fusion={"probe": f"{word}-bravo"})
    assert _fp(a) != _fp(b), f"{word!r} 不是独立凭据 token，改动必须改变指纹"


def test_fingerprint_keeps_risk_off_and_plain_text_visible_with_punctuation_context():
    """F1/fix3 反向：紧邻标点也不误伤 risk-off / 普通文本的敏感性。"""
    a = _effective_config(fusion={"regime": "(risk-off)", "note": '"plain alpha"'})
    b = _effective_config(fusion={"regime": "(risk-on)", "note": '"plain bravo"'})
    assert _fp(a) != _fp(b)


def test_punctuation_token_rule_does_not_strip_plain_punct_text():
    """F1/fix3：纯标点/路径文本未被误判为凭据（命中仍整串剔除，但不命中不得剔除）。"""
    a = _effective_config(fusion={"probe": "(见附录 A)"})
    b = _effective_config(fusion={"probe": "(见附录 B)"})
    assert _fp(a) != _fp(b)
    path_a = _effective_config(event={"rules_path": "./src/scanner/(a).yaml"})
    path_b = _effective_config(event={"rules_path": "./src/scanner/(b).yaml"})
    assert _fp(path_a) != _fp(path_b)



