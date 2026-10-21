"""T04-E-0 黄金测试：锁住 start.parse_input / start.run_cli 分发行为

目的：在拆分 start.py 大函数（parse_input ~540 行 / run_cli ~500 行）之前，
先用表驱动用例锁死当前分发契约——
  1) parse_input(命令串) -> (mode, args) 或 None（逐字段相等）
  2) run_cli(mode, args) -> 调用的 CLI 处理器名 + 关键 kwargs（桩拦截，零网络零 AI）

改造后同一文件必须全绿；任何 mode 名/参数键/处理器路由变化 = 回滚
（DELIVERY_PLAN 卡片 T04-E 停止条件）。

范围：关键命令覆盖（含中文别名、pos 子命令、scan/bz/fear/watch 等）；
不做控制台全文黄金（那是 T04-A/B 路径）；不触评分/MID/G7/v43/Baostock。
本卡第二提交抽出 pos_* 连贯分发助手 _dispatch_pos_cli；其余大分支仍待后续卡。

跑法：pytest tests/core/test_t04e_dispatch_golden.py -q

T04-F0 extend: lock benzong_multi (all-cached batch) / benzong_all (last-scan
all-cached) / benzong --check; pave for _dispatch_benzong_cli extract.
Still skip scoring product / MID / G7 / v43 / Baostock.
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from unittest.mock import MagicMock

import pytest

import start as start_module
import src.cli.main as cli_main


# parse_input golden table (frozen from HEAD dump)
PARSE_CASES = [
    {
        "in": "q",
        "out": [
            "quit",
            {}
        ]
    },
    {
        "in": "quit",
        "out": [
            "quit",
            {}
        ]
    },
    {
        "in": "exit",
        "out": [
            "quit",
            {}
        ]
    },
    {
        "in": "h",
        "out": [
            "help",
            {}
        ]
    },
    {
        "in": "help",
        "out": [
            "help",
            {}
        ]
    },
    {
        "in": "?",
        "out": [
            "help",
            {}
        ]
    },
    {
        "in": "la",
        "out": [
            "live_all",
            {}
        ]
    },
    {
        "in": "lall",
        "out": [
            "live_all",
            {}
        ]
    },
    {
        "in": "ba",
        "out": [
            "benzong_all",
            {}
        ]
    },
    {
        "in": "bzall",
        "out": [
            "benzong_all",
            {}
        ]
    },
    {
        "in": "l 600519",
        "out": [
            "live",
            {
                "stock_code": "600519"
            }
        ]
    },
    {
        "in": "l 600519,000001",
        "out": [
            "live_multi",
            {
                "codes": [
                    "600519",
                    "000001"
                ]
            }
        ]
    },
    {
        "in": "l 600519 000001",
        "out": [
            "live_multi",
            {
                "codes": [
                    "600519",
                    "000001"
                ]
            }
        ]
    },
    {
        "in": "l all",
        "out": [
            "live_scan_all",
            {
                "force": False
            }
        ]
    },
    {
        "in": "l all -f",
        "out": [
            "live_scan_all",
            {
                "force": True
            }
        ]
    },
    {
        "in": "l all --force",
        "out": [
            "live_scan_all",
            {
                "force": True
            }
        ]
    },
    {
        "in": "600519",
        "out": [
            "live",
            {
                "stock_code": "600519"
            }
        ]
    },
    {
        "in": "600519 000001",
        "out": [
            "live_multi",
            {
                "codes": [
                    "600519",
                    "000001"
                ]
            }
        ]
    },
    {
        "in": "b 600519",
        "out": [
            "backtest",
            {
                "stock_code": "600519"
            }
        ]
    },
    {
        "in": "b 600519 2024-01-01 2024-12-31 100000",
        "out": [
            "backtest",
            {
                "stock_code": "600519",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "capital": 100000.0
            }
        ]
    },
    {
        "in": "bb 600519,000001",
        "out": [
            "batch_backtest",
            {
                "codes": [
                    "600519",
                    "000001"
                ]
            }
        ]
    },
    {
        "in": "bb 600519,000001 2024-01-01 2024-12-31",
        "out": [
            "batch_backtest",
            {
                "codes": [
                    "600519",
                    "000001"
                ],
                "start_date": "2024-01-01",
                "end_date": "2024-12-31"
            }
        ]
    },
    {
        "in": "scan",
        "out": [
            "scan",
            {}
        ]
    },
    {
        "in": "s",
        "out": [
            "scan",
            {}
        ]
    },
    {
        "in": "scan market",
        "out": [
            "scan_market",
            {
                "rule_name": "healthy_pullback",
                "market_query": None,
                "deep": False
            }
        ]
    },
    {
        "in": "scan market deep",
        "out": [
            "scan_market",
            {
                "rule_name": "healthy_pullback",
                "market_query": None,
                "deep": True
            }
        ]
    },
    {
        "in": "scan market deep 科技",
        "out": [
            "scan_market",
            {
                "rule_name": "科技",
                "market_query": None,
                "deep": True
            }
        ]
    },
    {
        "in": "scan market healthy_pullback 科技",
        "out": [
            "scan_market",
            {
                "rule_name": "healthy_pullback",
                "market_query": "科技",
                "deep": False
            }
        ]
    },
    {
        "in": "scan review",
        "out": [
            "scan_review",
            {
                "days": 7
            }
        ]
    },
    {
        "in": "scan review 14",
        "out": [
            "scan_review",
            {
                "days": 14
            }
        ]
    },
    {
        "in": "scan review import",
        "out": [
            "scan_review_import",
            {}
        ]
    },
    {
        "in": "scan 复盘",
        "out": [
            "scan_review",
            {
                "days": 7
            }
        ]
    },
    {
        "in": "rules",
        "out": [
            "rules",
            {}
        ]
    },
    {
        "in": "industries",
        "out": [
            "industries",
            {
                "keyword": None
            }
        ]
    },
    {
        "in": "industry 银行",
        "out": [
            "industries",
            {
                "keyword": "银行"
            }
        ]
    },
    {
        "in": "concepts",
        "out": [
            "concepts",
            {
                "keyword": None
            }
        ]
    },
    {
        "in": "concept AI",
        "out": [
            "concepts",
            {
                "keyword": "AI"
            }
        ]
    },
    {
        "in": "events",
        "out": [
            "events",
            {}
        ]
    },
    {
        "in": "expect",
        "out": [
            "expect",
            {
                "days": 30
            }
        ]
    },
    {
        "in": "expect 60",
        "out": [
            "expect",
            {
                "days": 60
            }
        ]
    },
    {
        "in": "doctor",
        "out": [
            "doctor",
            {}
        ]
    },
    {
        "in": "诊断",
        "out": [
            "doctor",
            {}
        ]
    },
    {
        "in": "diff",
        "out": [
            "diff_list",
            {}
        ]
    },
    {
        "in": "对比",
        "out": [
            "diff_list",
            {}
        ]
    },
    {
        "in": "diff 600519",
        "out": [
            "diff_evidence",
            {
                "code": "600519",
                "ai": False
            }
        ]
    },
    {
        "in": "tasks",
        "out": [
            "batch_tasks",
            {}
        ]
    },
    {
        "in": "任务",
        "out": [
            "batch_tasks",
            {}
        ]
    },
    {
        "in": "today",
        "out": [
            "today",
            {}
        ]
    },
    {
        "in": "今日",
        "out": [
            "today",
            {}
        ]
    },
    {
        "in": "shadow",
        "out": [
            "shadow",
            {}
        ]
    },
    {
        "in": "影子",
        "out": [
            "shadow",
            {}
        ]
    },
    {
        "in": "plan2",
        "out": [
            "plan2",
            {
                "rest": []
            }
        ]
    },
    {
        "in": "计划2",
        "out": [
            "plan2",
            {
                "rest": []
            }
        ]
    },
    {
        "in": "research 600519",
        "out": [
            "research",
            {
                "rest": [
                    "600519"
                ]
            }
        ]
    },
    {
        "in": "fear",
        "out": [
            "fear",
            {
                "action": "overview",
                "scope": "",
                "refresh": False,
                "chart": True
            }
        ]
    },
    {
        "in": "fear history",
        "out": [
            "fear",
            {
                "action": "history",
                "scope": "",
                "refresh": False,
                "chart": True
            }
        ]
    },
    {
        "in": "fear backfill",
        "out": [
            "fear",
            {
                "action": "backfill",
                "scope": "",
                "refresh": False,
                "chart": True
            }
        ]
    },
    {
        "in": "watch",
        "out": [
            "watch_pool",
            {
                "action": "list",
                "code": "",
                "name": "",
                "price": None
            }
        ]
    },
    {
        "in": "观察池",
        "out": [
            "watch_pool",
            {
                "action": "list",
                "code": "",
                "name": "",
                "price": None
            }
        ]
    },
    {
        "in": "watch add 600519",
        "out": [
            "watch_pool",
            {
                "action": "add",
                "code": "600519",
                "name": "",
                "price": None
            }
        ]
    },
    {
        "in": "watch rm 600519",
        "out": [
            "watch_pool",
            {
                "action": "rm",
                "code": "600519",
                "name": "",
                "price": None
            }
        ]
    },
    {
        "in": "pos",
        "out": [
            "pos_list",
            {}
        ]
    },
    {
        "in": "pos list",
        "out": [
            "pos_list",
            {}
        ]
    },
    {
        "in": "pos add 600519 测试股 10 0.1",
        "out": [
            "pos_add",
            {
                "stock_code": "600519",
                "name": "测试股",
                "price": 10.0,
                "ratio": 0.1
            }
        ]
    },
    {
        "in": "pos rm 600519",
        "out": [
            "pos_remove",
            {
                "stock_code": "600519"
            }
        ]
    },
    {
        "in": "pos remove 600519",
        "out": [
            "pos_remove",
            {
                "stock_code": "600519"
            }
        ]
    },
    {
        "in": "pos confirm 600519 --qty 100 --price 12 --fee 5",
        "out": [
            "pos_confirm",
            {
                "stock_code": "600519",
                "ratio": None,
                "price": 12.0,
                "qty": 100.0,
                "fee": 5.0,
                "trade_date": "",
                "note": ""
            }
        ]
    },
    {
        "in": "pos verify",
        "out": None
    },
    {
        "in": "pos opening",
        "out": None
    },
    {
        "in": "pos plan",
        "out": None
    },
    {
        "in": "pos plan 600519",
        "out": [
            "pos_plan",
            {
                "stock_code": "600519",
                "update": False
            }
        ]
    },
    {
        "in": "pos plan 600519 --update",
        "out": [
            "pos_plan",
            {
                "stock_code": "600519",
                "update": True
            }
        ]
    },
    {
        "in": "pos plan all",
        "out": [
            "pos_plan",
            {
                "stock_code": "all",
                "update": False
            }
        ]
    },
    {
        "in": "pos plan all --update",
        "out": [
            "pos_plan",
            {
                "stock_code": "all",
                "update": True
            }
        ]
    },
    {
        "in": "pos overweight 600519",
        "out": [
            "pos_overweight",
            {
                "stock_code": "600519",
                "name": ""
            }
        ]
    },
    {
        "in": "noai",
        "out": [
            "noai",
            {}
        ]
    },
    {
        "in": "debug",
        "out": [
            "debug",
            {}
        ]
    },
    {
        "in": "chat",
        "out": [
            "chat",
            {}
        ]
    },
    {
        "in": "bz 600519",
        "out": [
            "benzong",
            {
                "meta": "600519",
                "manual": False,
                "refresh": False,
                "check": False
            }
        ]
    },
    {
        "in": "bz 600519 --refresh",
        "out": [
            "benzong",
            {
                "meta": "600519",
                "manual": False,
                "refresh": True,
                "check": False
            }
        ]
    },
    {
        "in": "bz --check",
        "out": [
            "benzong",
            {
                "meta": "",
                "manual": False,
                "refresh": False,
                "check": True
            }
        ]
    },
    {
        "in": "bz 600519,000001",
        "out": [
            "benzong_multi",
            {
                "codes": [
                    "600519",
                    "000001"
                ],
                "refresh": False
            }
        ]
    },
    {
        "in": "bz scan",
        "out": [
            "benzong_scan",
            {
                "theme": "",
                "top_n": 10,
                "backtest": False,
                "start": "",
                "end": "",
                "capital": 100000.0,
                "rule": "healthy_pullback",
                "limit": 15,
                "refresh": False
            }
        ]
    },
    {
        "in": "bz scan 科技",
        "out": [
            "benzong_scan",
            {
                "theme": "",
                "top_n": 10,
                "backtest": False,
                "start": "",
                "end": "",
                "capital": 100000.0,
                "rule": "科技",
                "limit": 15,
                "refresh": False,
                "rule_from_positional": True
            }
        ]
    },
    {
        "in": "notacommand",
        "out": None
    }
]


@pytest.mark.parametrize("case", PARSE_CASES, ids=lambda c: repr(c["in"])[:60])
def test_parse_input_dispatch_golden(case):
    """parse_input (mode, args)/None contract for key commands."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        got = start_module.parse_input(case["in"])
    expected = case["out"]
    if expected is None:
        assert got is None
    else:
        assert got is not None
        assert got[0] == expected[0]
        assert got[1] == expected[1]


def _spy_env(monkeypatch):
    spies = {}
    names = [
        "analyze_live", "run_backtest", "run_batch_validation",
        "manage_positions", "analyze_portfolio", "scan_market", "scan_events",
        "show_expect", "show_fear", "scan_review", "scan_review_import",
        "watch_pool", "doctor", "diff_evidence_cmd", "diff_list_cmd",
        "batch_tasks_cmd", "today_command", "shadow_command", "plan2_command",
        "research_command", "benzong_batch_analyze",
    ]
    for n in names:
        m = MagicMock(name=n)
        spies[n] = m
        monkeypatch.setattr(cli_main, n, m)
    monkeypatch.setattr(start_module, "run_benzong_scoring", MagicMock(name="run_benzong_scoring"))
    monkeypatch.setattr(start_module, "run_benzong_scan", MagicMock(name="run_benzong_scan"))
    spies["run_benzong_scoring"] = start_module.run_benzong_scoring
    spies["run_benzong_scan"] = start_module.run_benzong_scan
    return spies


RUN_CLI_CASES = [
    ("live", {"stock_code": "600519"}, "fn", "analyze_live", {"ai_debug": False}),
    ("scan", {}, "fn", "analyze_portfolio", {}),
    ("scan_market", {"rule_name": "healthy_pullback", "market_query": None, "deep": False},
     "fn", "scan_market", {"rule_name": "healthy_pullback", "deep": False}),
    ("events", {}, "fn", "scan_events", {}),
    ("expect", {"days": 60}, "fn", "show_expect", {"days": 60}),
    ("fear", {"action": "overview", "scope": "", "refresh": False, "chart": True},
     "fn", "show_fear", {}),
    ("scan_review", {"days": 14}, "fn", "scan_review", {"days": 14}),
    ("scan_review_import", {}, "fn", "scan_review_import", {}),
    ("watch_pool", {"action": "list", "code": "", "name": "", "price": None},
     "fn", "watch_pool", {}),
    ("doctor", {}, "fn", "doctor", {}),
    ("diff_evidence", {"code": "600519", "ai": False},
     "fn", "diff_evidence_cmd", {}),
    ("diff_list", {}, "fn", "diff_list_cmd", {}),
    ("batch_tasks", {}, "fn", "batch_tasks_cmd", {}),
    ("today", {}, "fn", "today_command", {}),
    ("shadow", {}, "fn", "shadow_command", {}),
    ("plan2", {"rest": []}, "fn", "plan2_command", {}),
    ("research", {"rest": ["600519"]}, "fn", "research_command", {}),
    ("backtest", {"stock_code": "600519"}, "fn", "run_backtest", {"stock_code": "600519"}),
    ("batch_backtest", {"codes": ["600519", "000001"]},
     "fn", "run_batch_validation", {"stock_codes": ["600519", "000001"]}),
    ("pos_list", {}, "manage", "list", {}),
    ("pos_add", {"stock_code": "600519", "name": "测试股", "price": 10.0, "ratio": 0.1},
     "manage", "add", {"stock_code": "600519", "name": "测试股", "price": 10.0, "ratio": 0.1}),
    ("pos_remove", {"stock_code": "600519"}, "manage", "remove", {"stock_code": "600519"}),
    ("pos_confirm", {"stock_code": "600519", "ratio": None, "price": 12.0,
                     "qty": 100.0, "fee": 5.0, "trade_date": "", "note": ""},
     "manage", "confirm", {"stock_code": "600519", "qty": 100.0, "fee": 5.0, "price": 12.0}),
    ("pos_verify", {"stock_code": "600519"}, "manage", "verify", {"stock_code": "600519"}),
    ("pos_opening", {"cash": 50000.0}, "manage", "opening", {"cash": 50000.0}),
    ("pos_openinglot", {"security_id": "600519", "quantity": 100, "cost_price": 10.0,
                        "acquired_at": "2026-09-20"},
     "manage", "openinglot", {"security_id": "600519", "qty": 100, "cost_price": 10.0}),
    ("pos_plan", {"stock_code": "600519", "update": True},
     "manage", "plan", {"stock_code": "600519", "update": True}),
    ("pos_overweight", {"stock_code": "600519", "name": ""},
     "manage", "overweight", {"stock_code": "600519", "name": ""}),
    ("benzong", {"meta": "600519", "manual": False, "refresh": False, "check": False},
     "start", "run_benzong_scoring", {"meta": "600519", "manual": False, "refresh": False}),
    ("benzong_scan", {"theme": "", "top_n": 10, "backtest": False, "start": "", "end": "",
                      "capital": 100000.0, "rule": "healthy_pullback", "limit": 15, "refresh": False},
     "start", "run_benzong_scan", {}),
]


@pytest.mark.parametrize(
    "mode,args,kind,name,kw",
    RUN_CLI_CASES,
    ids=[c[0] for c in RUN_CLI_CASES],
)
def test_run_cli_dispatch_golden(monkeypatch, mode, args, kind, name, kw):
    """run_cli routes mode to the right handler (mocked)."""
    spies = _spy_env(monkeypatch)
    buf = io.StringIO()
    with redirect_stdout(buf):
        start_module.run_cli(mode, dict(args))

    if kind == "fn":
        spy = spies[name]
        assert spy.called, f"{mode} should call cli_main.{name}"
        called_kwargs = spy.call_args.kwargs or {}
        for k, v in kw.items():
            if k in called_kwargs:
                assert called_kwargs[k] == v, f"{mode}.{name} kwargs[{k}]"
        assert not spies["manage_positions"].called, f"{mode} must not hit manage_positions"
    elif kind == "manage":
        spy = spies["manage_positions"]
        assert spy.called, f"{mode} should call manage_positions({name})"
        assert spy.call_args.args[0] == name
        for k, v in kw.items():
            assert spy.call_args.kwargs.get(k) == v, f"{mode} manage kwargs[{k}]"
    elif kind == "start":
        spy = spies[name]
        assert spy.called, f"{mode} should call start.{name}"
        called_kwargs = spy.call_args.kwargs or {}
        for k, v in kw.items():
            if k in called_kwargs:
                assert called_kwargs[k] == v
        if name == "run_benzong_scan" and spy.call_args.args:
            assert spy.call_args.args[0] == args
    else:
        raise AssertionError(kind)


def test_run_cli_unknown_mode_warns(monkeypatch, capsys):
    """Unknown mode must warn (chat bridge protocol)."""
    spies = _spy_env(monkeypatch)
    start_module.run_cli("__no_such_mode__", {})
    out = capsys.readouterr().out
    assert "未知执行模式" in out
    assert not spies["manage_positions"].called
    assert not spies["analyze_live"].called


def test_noai_debug_toggle(monkeypatch):
    """noai/debug flip module flags and skip business handlers."""
    spies = _spy_env(monkeypatch)
    start_module._no_ai = False
    start_module._ai_debug = False
    start_module.run_cli("noai", {})
    assert start_module._no_ai is True
    start_module.run_cli("debug", {})
    assert start_module._ai_debug is True
    start_module._no_ai = False
    start_module._ai_debug = False
    assert not spies["manage_positions"].called
    assert not spies["analyze_live"].called


def test_parse_then_run_cli_pos_confirm_chain(monkeypatch):
    """LRN-L005: parse_input -> run_cli full chain for pos confirm."""
    spies = _spy_env(monkeypatch)
    parsed = start_module.parse_input("pos confirm 600519 --qty 100 --price 12 --fee 5")
    assert parsed is not None
    mode, args = parsed
    assert mode == "pos_confirm"
    start_module.run_cli(mode, args)
    spy = spies["manage_positions"]
    assert spy.called
    assert spy.call_args.args[0] == "confirm"
    assert spy.call_args.kwargs["qty"] == 100.0
    assert spy.call_args.kwargs["fee"] == 5.0


def test_run_cli_benzong_multi_all_cached(monkeypatch):
    """T04-F0: benzong_multi all-dims cached -> benzong_batch_analyze, no y/N prompt."""
    spies = _spy_env(monkeypatch)
    import src.core.benzong.cache as bz_cache

    monkeypatch.setattr(bz_cache, "get", lambda *a, **k: {"score": 1})
    start_module.run_cli(
        "benzong_multi",
        {"codes": ["600519", "000001"], "refresh": False},
    )
    spy = spies["benzong_batch_analyze"]
    assert spy.called, "benzong_multi should call benzong_batch_analyze"
    items = spy.call_args.args[0]
    assert [it["code"] for it in items] == ["600519", "000001"]
    assert spy.call_args.kwargs.get("force_refresh") is False
    assert not spies["run_benzong_scoring"].called
    assert not spies["run_benzong_scan"].called


def test_run_cli_benzong_all_all_cached(monkeypatch):
    """T04-F0: benzong_all last-scan + all cached -> benzong_batch_analyze(src_label)."""
    spies = _spy_env(monkeypatch)
    from datetime import datetime
    from src.cli import session_state
    import src.core.benzong.cache as bz_cache

    now = datetime.now().isoformat(timespec="seconds")
    monkeypatch.setattr(
        session_state,
        "get_last_scan",
        lambda: {
            "items": [{"code": "600519"}, {"code": "000001"}],
            "source": "bzscan",
            "timestamp": now,
        },
    )
    monkeypatch.setattr(bz_cache, "get", lambda *a, **k: {"score": 1})
    start_module.run_cli("benzong_all", {})
    spy = spies["benzong_batch_analyze"]
    assert spy.called, "benzong_all should call benzong_batch_analyze"
    items = spy.call_args.args[0]
    assert [it["code"] for it in items] == ["600519", "000001"]
    assert spy.call_args.kwargs.get("src_label") == "bzscan"
    assert not spies["run_benzong_scoring"].called


def test_run_cli_benzong_check(monkeypatch, capsys):
    """T04-F0: benzong --check uses source_check; skips scoring entry."""
    spies = _spy_env(monkeypatch)
    import src.data.source_check as sc

    monkeypatch.setattr(sc, "check_all_sources", lambda: {"ok": True})
    monkeypatch.setattr(sc, "format_report", lambda r: "CHECK_OK_T04F")
    start_module.run_cli(
        "benzong",
        {"check": True, "meta": "", "manual": False, "refresh": False},
    )
    out = capsys.readouterr().out
    assert "CHECK_OK_T04F" in out
    assert not spies["run_benzong_scoring"].called
    assert not spies["benzong_batch_analyze"].called
