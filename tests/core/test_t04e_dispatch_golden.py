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

T04-G0 extend: lock live_multi / live_all / live_scan_all dispatch (analyze_live
compact batch + early exits); pave for _dispatch_live_cli extract.

T04-H0 extend: lock rules / industries / concepts run_cli dispatch (ScannerEngine
stubs + empty/keyword paths); pave for _dispatch_scan_cli extract of scan_market /
rules / industries / concepts / events / scan_review / scan_review_import.

T04-I0 extend: lock expect / fear / watch_pool run_cli arg passthrough
(show_expect days kwarg incl. default 30; show_fear/watch_pool positional args);
pave for _dispatch_observe_cli extract. Still skip scoring / MID / G7 / v43 / Baostock.

T04-J0 extend: lock doctor / diff_evidence / diff_list / batch_tasks / today
run_cli arg passthrough (doctor/diff_list/batch_tasks/today zero-arg;
diff_evidence code positional + use_ai kwarg incl. ai True/False);
pave for _dispatch_ops_cli extract. Still skip scoring / MID / G7 / v43 / Baostock.

T04-K0 extend: lock shadow / plan2 / research run_cli arg passthrough
(shadow zero-arg; plan2/research rest list positional incl. empty/missing
and non-empty; parse plan2 rest + research --cash-materials shlex path);
pave for _dispatch_fusion_cli extract. Still skip scoring / MID / G7 / v43 / Baostock.
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
        "in": "plan2 600519 mid thesis-text",
        "out": [
            "plan2",
            {
                "rest": [
                    "600519",
                    "mid",
                    "thesis-text"
                ]
            }
        ]
    },
    {
        "in": "research",
        "out": [
            "research",
            {
                "rest": []
            }
        ]
    },
    {
        "in": "research 600519 --cash-materials bag.json",
        "out": [
            "research",
            {
                "rest": [
                    "600519",
                    "--cash-materials",
                    "bag.json"
                ]
            }
        ]
    },
    {
        "in": "research 600519 --cash-materials \"path with space.json\"",
        "out": [
            "research",
            {
                "rest": [
                    "600519",
                    "--cash-materials",
                    "path with space.json"
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


def _fake_acct_facts(entries=None, incomplete=False):
    """Minimal request_account_facts stand-in for live_* golden."""
    facts = MagicMock(name="acct_facts")
    facts.holding_entries.return_value = (list(entries or []), incomplete)
    return facts


def _patch_portfolio_facts(monkeypatch, facts):
    import src.data.portfolio as portfolio_mod

    pm = MagicMock(name="PortfolioManager")
    pm.return_value.request_account_facts.return_value = facts
    monkeypatch.setattr(portfolio_mod, "PortfolioManager", pm)
    return pm


def test_run_cli_live_multi_dispatches_analyze_live(monkeypatch):
    """T04-G0: live_multi codes -> prefetch + analyze_live(compact) per code."""
    spies = _spy_env(monkeypatch)
    from src.cli import session_state
    import src.data.akshare_client as ak_mod

    facts = _fake_acct_facts()
    _patch_portfolio_facts(monkeypatch, facts)
    monkeypatch.setattr(
        ak_mod.AKShareClient, "prefetch_realtime_quotes", staticmethod(lambda codes: len(codes))
    )
    monkeypatch.setattr(session_state, "batch_task_start", MagicMock())
    monkeypatch.setattr(session_state, "batch_task_mark", MagicMock())
    monkeypatch.setattr(session_state, "mark_deep_analyzed", MagicMock())

    start_module.run_cli("live_multi", {"codes": ["600519", "000001"]})
    spy = spies["analyze_live"]
    assert spy.call_count == 2
    codes = [c.args[0] for c in spy.call_args_list]
    assert codes == ["600519", "000001"]
    for c in spy.call_args_list:
        assert c.kwargs.get("compact") is True
        assert c.kwargs.get("account_facts") is facts
    assert not spies["manage_positions"].called
    assert not spies["analyze_portfolio"].called


def test_run_cli_live_multi_empty_codes(monkeypatch, capsys):
    """T04-G0: live_multi empty codes -> early message, no analyze_live."""
    spies = _spy_env(monkeypatch)
    start_module.run_cli("live_multi", {"codes": []})
    out = capsys.readouterr().out
    assert "没有可分析的代码" in out
    assert not spies["analyze_live"].called


def test_run_cli_live_all_with_holdings(monkeypatch):
    """T04-G0: live_all holdings -> analyze_live(compact) per entry."""
    spies = _spy_env(monkeypatch)
    from src.cli import session_state

    e1 = MagicMock(stock_code="600519", stock_name="茅台", from_ledger=False)
    e2 = MagicMock(stock_code="000001", stock_name="平安", from_ledger=False)
    facts = _fake_acct_facts([e1, e2], incomplete=False)
    _patch_portfolio_facts(monkeypatch, facts)
    monkeypatch.setattr(session_state, "batch_task_start", MagicMock())
    monkeypatch.setattr(session_state, "batch_task_mark", MagicMock())

    start_module.run_cli("live_all", {})
    spy = spies["analyze_live"]
    assert spy.call_count == 2
    assert [c.args[0] for c in spy.call_args_list] == ["600519", "000001"]
    for c in spy.call_args_list:
        assert c.kwargs.get("compact") is True
        assert c.kwargs.get("account_facts") is facts


def test_run_cli_live_all_no_holdings(monkeypatch, capsys):
    """T04-G0: live_all empty complete list -> 当前无持仓; no analyze_live."""
    spies = _spy_env(monkeypatch)
    facts = _fake_acct_facts([], incomplete=False)
    _patch_portfolio_facts(monkeypatch, facts)
    start_module.run_cli("live_all", {})
    out = capsys.readouterr().out
    assert "当前无持仓记录" in out
    assert not spies["analyze_live"].called


def test_run_cli_live_scan_all_no_scan(monkeypatch, capsys):
    """T04-G0: live_scan_all without last scan -> tip; no analyze_live."""
    spies = _spy_env(monkeypatch)
    from src.cli import session_state

    monkeypatch.setattr(session_state, "get_last_scan", lambda: None)
    start_module.run_cli("live_scan_all", {"force": False})
    out = capsys.readouterr().out
    assert "还没有扫描结果" in out
    assert not spies["analyze_live"].called


def test_run_cli_live_scan_all_force_confirm(monkeypatch):
    """T04-G0: live_scan_all force + y -> analyze_live(compact) for scan items."""
    spies = _spy_env(monkeypatch)
    from datetime import datetime
    from src.cli import session_state

    now = datetime.now().isoformat(timespec="seconds")
    monkeypatch.setattr(
        session_state,
        "get_last_scan",
        lambda: {
            "items": [{"code": "600519", "name": "茅台"}, {"code": "000001", "name": "平安"}],
            "source": "bzscan",
            "timestamp": now,
        },
    )
    monkeypatch.setattr(session_state, "get_deep_analyzed", lambda: {})
    monkeypatch.setattr(session_state, "batch_task_start", MagicMock())
    monkeypatch.setattr(session_state, "batch_task_mark", MagicMock())
    monkeypatch.setattr(session_state, "mark_deep_analyzed", MagicMock())
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    facts = _fake_acct_facts()
    _patch_portfolio_facts(monkeypatch, facts)

    start_module.run_cli("live_scan_all", {"force": True})
    spy = spies["analyze_live"]
    assert spy.call_count == 2
    assert [c.args[0] for c in spy.call_args_list] == ["600519", "000001"]
    for c in spy.call_args_list:
        assert c.kwargs.get("compact") is True
        assert c.kwargs.get("account_facts") is facts


def _patch_scanner_engine(monkeypatch, *, rules=None, industries=None, concepts=None):
    """Stub ScannerEngine used by rules/industries/concepts CLI branches."""
    import src.scanner.scanner_engine as se_mod

    eng = MagicMock(name="ScannerEngine")
    eng.get_available_rules.return_value = rules if rules is not None else [
        {"name": "healthy_pullback", "display_name": "健康回踩", "description": "desc"},
    ]
    eng.get_industry_list.return_value = industries if industries is not None else []
    eng.get_concept_list.return_value = concepts if concepts is not None else []
    monkeypatch.setattr(se_mod, "ScannerEngine", MagicMock(return_value=eng))
    return eng


def test_run_cli_rules_lists_available_rules(monkeypatch):
    """T04-H0: rules -> ScannerEngine.get_available_rules + console.print(table)."""
    spies = _spy_env(monkeypatch)
    eng = _patch_scanner_engine(monkeypatch)
    printed = []
    monkeypatch.setattr(cli_main.console, "print", lambda *a, **k: printed.append(a[0] if a else None))
    monkeypatch.setattr(cli_main, "load_config", lambda: {"scanner": {"rules_path": "./src/scanner/scan_rules.yaml"}})

    start_module.run_cli("rules", {})
    eng.get_available_rules.assert_called_once_with()
    assert printed, "expected console.print(table)"
    assert not spies["scan_market"].called
    assert not spies["analyze_live"].called


def test_run_cli_industries_with_keyword(monkeypatch, capsys):
    """T04-H0: industries keyword -> get_industry_list + print rows."""
    spies = _spy_env(monkeypatch)
    rows = [
        {"name": "半导体", "change_pct": 1.25, "up_count": 10, "down_count": 3, "lead_stock": "600584"},
        {"name": "软件开发", "change_pct": -0.5, "up_count": 2, "down_count": 8, "lead_stock": ""},
    ]
    eng = _patch_scanner_engine(monkeypatch, industries=rows)
    start_module.run_cli("industries", {"keyword": "半导"})
    eng.get_industry_list.assert_called_once_with(keyword="半导")
    out = capsys.readouterr().out
    assert "半导体" in out
    assert "行业板块" in out
    assert not spies["scan_market"].called


def test_run_cli_industries_empty(monkeypatch, capsys):
    """T04-H0: industries empty list -> failure / not-found message."""
    spies = _spy_env(monkeypatch)
    eng = _patch_scanner_engine(monkeypatch, industries=[])
    start_module.run_cli("industries", {"keyword": None})
    eng.get_industry_list.assert_called_once_with(keyword=None)
    out = capsys.readouterr().out
    assert "行业板块数据获取失败" in out
    assert not spies["scan_events"].called


def test_run_cli_concepts_with_keyword(monkeypatch, capsys):
    """T04-H0: concepts keyword -> get_concept_list + print rows."""
    spies = _spy_env(monkeypatch)
    rows = [
        {"name": "人工智能", "change_pct": 2.0, "lead_stock": "002230"},
        {"name": "固态电池", "change_pct": 0.0, "lead_stock": ""},
    ]
    eng = _patch_scanner_engine(monkeypatch, concepts=rows)
    start_module.run_cli("concepts", {"keyword": "AI"})
    eng.get_concept_list.assert_called_once_with(keyword="AI")
    out = capsys.readouterr().out
    assert "人工智能" in out
    assert "概念板块" in out
    assert not spies["scan_review"].called


def test_run_cli_concepts_empty_with_keyword(monkeypatch, capsys):
    """T04-H0: concepts empty + keyword -> not-found message."""
    spies = _spy_env(monkeypatch)
    eng = _patch_scanner_engine(monkeypatch, concepts=[])
    start_module.run_cli("concepts", {"keyword": "不存在概念"})
    eng.get_concept_list.assert_called_once_with(keyword="不存在概念")
    out = capsys.readouterr().out
    assert "未找到包含" in out
    assert "概念板块" in out
    assert not spies["analyze_portfolio"].called

def test_run_cli_expect_passes_days(monkeypatch):
    """T04-I0: expect -> show_expect(days=...) kwargs; default 30 when days absent."""
    spies = _spy_env(monkeypatch)
    start_module.run_cli("expect", {"days": 60})
    spies["show_expect"].assert_called_once_with(days=60)
    assert not spies["show_fear"].called
    assert not spies["watch_pool"].called

    spies["show_expect"].reset_mock()
    start_module.run_cli("expect", {})
    spies["show_expect"].assert_called_once_with(days=30)


def test_run_cli_fear_passes_args_positional(monkeypatch):
    """T04-I0: fear -> show_fear(args) as first positional (not kwargs-only)."""
    spies = _spy_env(monkeypatch)
    args = {"action": "history", "scope": "", "refresh": True, "chart": False}
    start_module.run_cli("fear", args)
    spies["show_fear"].assert_called_once_with(args)
    assert not spies["show_expect"].called
    assert not spies["watch_pool"].called


def test_run_cli_watch_pool_passes_args_positional(monkeypatch):
    """T04-I0: watch_pool -> watch_pool(args) as first positional."""
    spies = _spy_env(monkeypatch)
    args = {"action": "add", "code": "600519", "name": "maotai", "price": 1800.0}
    start_module.run_cli("watch_pool", args)
    spies["watch_pool"].assert_called_once_with(args)
    assert not spies["show_expect"].called
    assert not spies["show_fear"].called


def test_run_cli_doctor_zero_arg(monkeypatch):
    """T04-J0: doctor -> doctor() with no args."""
    spies = _spy_env(monkeypatch)
    start_module.run_cli("doctor", {})
    spies["doctor"].assert_called_once_with()
    assert not spies["diff_evidence_cmd"].called
    assert not spies["today_command"].called


def test_run_cli_diff_evidence_passes_code_and_use_ai(monkeypatch):
    """T04-J0: diff_evidence -> diff_evidence_cmd(code, use_ai=...); ai True/False."""
    spies = _spy_env(monkeypatch)
    start_module.run_cli("diff_evidence", {"code": "600519", "ai": False})
    spies["diff_evidence_cmd"].assert_called_once_with("600519", use_ai=False)
    assert not spies["diff_list_cmd"].called
    assert not spies["doctor"].called

    spies["diff_evidence_cmd"].reset_mock()
    start_module.run_cli("diff_evidence", {"code": "000001", "ai": True})
    spies["diff_evidence_cmd"].assert_called_once_with("000001", use_ai=True)

    spies["diff_evidence_cmd"].reset_mock()
    start_module.run_cli("diff_evidence", {})
    spies["diff_evidence_cmd"].assert_called_once_with("", use_ai=False)


def test_run_cli_diff_list_zero_arg(monkeypatch):
    """T04-J0: diff_list -> diff_list_cmd() with no args."""
    spies = _spy_env(monkeypatch)
    start_module.run_cli("diff_list", {})
    spies["diff_list_cmd"].assert_called_once_with()
    assert not spies["diff_evidence_cmd"].called
    assert not spies["batch_tasks_cmd"].called


def test_run_cli_batch_tasks_zero_arg(monkeypatch):
    """T04-J0: batch_tasks -> batch_tasks_cmd() with no args."""
    spies = _spy_env(monkeypatch)
    start_module.run_cli("batch_tasks", {})
    spies["batch_tasks_cmd"].assert_called_once_with()
    assert not spies["today_command"].called
    assert not spies["doctor"].called


def test_run_cli_today_zero_arg(monkeypatch):
    """T04-J0: today -> today_command() with no args."""
    spies = _spy_env(monkeypatch)
    start_module.run_cli("today", {})
    spies["today_command"].assert_called_once_with()
    assert not spies["shadow_command"].called
    assert not spies["doctor"].called


def test_run_cli_shadow_zero_arg(monkeypatch):
    """T04-K0: shadow -> shadow_command() with no args."""
    spies = _spy_env(monkeypatch)
    start_module.run_cli("shadow", {})
    spies["shadow_command"].assert_called_once_with()
    assert not spies["plan2_command"].called
    assert not spies["research_command"].called


def test_run_cli_plan2_passes_rest_positional(monkeypatch):
    """T04-K0: plan2 -> plan2_command(rest); empty/missing -> []."""
    spies = _spy_env(monkeypatch)
    start_module.run_cli("plan2", {"rest": ["600519", "mid", "thesis-text"]})
    spies["plan2_command"].assert_called_once_with(["600519", "mid", "thesis-text"])
    assert not spies["shadow_command"].called
    assert not spies["research_command"].called

    spies["plan2_command"].reset_mock()
    start_module.run_cli("plan2", {"rest": []})
    spies["plan2_command"].assert_called_once_with([])

    spies["plan2_command"].reset_mock()
    start_module.run_cli("plan2", {})
    spies["plan2_command"].assert_called_once_with([])


def test_run_cli_research_passes_rest_positional(monkeypatch):
    """T04-K0: research -> research_command(rest); empty/missing -> []."""
    spies = _spy_env(monkeypatch)
    start_module.run_cli("research", {"rest": ["600519", "--capture"]})
    spies["research_command"].assert_called_once_with(["600519", "--capture"])
    assert not spies["shadow_command"].called
    assert not spies["plan2_command"].called

    spies["research_command"].reset_mock()
    start_module.run_cli("research", {"rest": []})
    spies["research_command"].assert_called_once_with([])

    spies["research_command"].reset_mock()
    start_module.run_cli("research", {})
    spies["research_command"].assert_called_once_with([])

