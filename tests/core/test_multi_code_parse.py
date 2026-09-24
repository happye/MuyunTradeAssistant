# -*- coding: utf-8 -*-
"""ISS-087 多代码支持回归测试（v0.8.9.0）

锁死语义：
- REPL l/bz 多代码：英文逗号/中文逗号/顿号/分号/空格分隔均可混用，去重保序，
  非法 token 告警跳过（消除此前静默丢弃），#N 引用可混用
- 单代码/单主题旧行为不变；l all / l all -f 不回归；--manual 多代码降级单只
- chat analyze_stock 多代码 confirm 硬门；run_command 多代码 mode 进硬门名单

跑法：pytest tests/core/test_multi_code_parse.py 或直接 python 执行。
parse_input 是 start.py 模块级纯函数（无网络无引擎副作用），可安全导入。
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

import start
from src.chat.tools import TOOL_ERROR_MARK, analyze_stock, run_command


# ── l 多代码 ──

def test_l_single_unchanged():
    assert start.parse_input("l 600519") == ("live", {"stock_code": "600519"})


def test_l_multi_comma():
    mode, args = start.parse_input("l 600519,000001")
    assert mode == "live_multi"
    assert args["codes"] == ["600519", "000001"]


def test_l_multi_chinese_comma():
    mode, args = start.parse_input("l 600519，000001")
    assert mode == "live_multi"
    assert args["codes"] == ["600519", "000001"]


def test_l_multi_space():
    mode, args = start.parse_input("l 600519 000001")
    assert mode == "live_multi"
    assert args["codes"] == ["600519", "000001"]


def test_l_multi_mixed_separators_and_suffix():
    mode, args = start.parse_input("l 600519, 000001.SZ；300750")
    assert mode == "live_multi"
    assert args["codes"] == ["600519", "000001.SZ", "300750"]


def test_l_multi_dedup_keeps_order():
    mode, args = start.parse_input("l 600519 000001 600519")
    assert mode == "live_multi"
    assert args["codes"] == ["600519", "000001"]


def test_l_invalid_token_warned_and_skipped(capsys):
    result = start.parse_input("l 600519 abc")
    assert result == ("live", {"stock_code": "600519"})
    out = capsys.readouterr().out
    assert "abc" in out and "已跳过" in out


def test_l_all_unchanged():
    assert start.parse_input("l all")[0] == "live_scan_all"
    assert start.parse_input("l all -f")[1] == {"force": True}


def test_bare_multi_code():
    mode, args = start.parse_input("600519 000001")
    assert mode == "live_multi"
    assert args["codes"] == ["600519", "000001"]


def test_bare_single_code_unchanged():
    assert start.parse_input("600519") == ("live", {"stock_code": "600519"})


# ── bz 多代码 ──

def test_bz_single_unchanged():
    mode, args = start.parse_input("bz 600519")
    assert (mode, args["meta"]) == ("benzong", "600519")
    assert args["refresh"] is False


def test_bz_single_refresh_unchanged():
    mode, args = start.parse_input("bz 600519 --refresh")
    assert mode == "benzong"
    assert args["meta"] == "600519" and args["refresh"] is True


def test_bz_multi_comma():
    mode, args = start.parse_input("bz 600519,000001")
    assert mode == "benzong_multi"
    assert args["codes"] == ["600519", "000001"]


def test_bz_multi_chinese_comma_space_refresh():
    mode, args = start.parse_input("bz 600519，000001 --refresh 300750")
    assert mode == "benzong_multi"
    assert args["codes"] == ["600519", "000001", "300750"]
    assert args["refresh"] is True


def test_bz_multi_manual_degrades_to_first(capsys):
    mode, args = start.parse_input("bz 600519,000001 --manual")
    assert mode == "benzong"
    assert args["meta"] == "600519" and args["manual"] is True
    assert "--manual" in capsys.readouterr().out


def test_bz_theme_legacy_passthrough(capsys):
    """单主题词保持旧行为：原样传给评分器（由其现有报错路径处理），不误报跳过。"""
    mode, args = start.parse_input("bz 氮化镓")
    assert (mode, args["meta"]) == ("benzong", "氮化镓")
    assert "已跳过" not in capsys.readouterr().out


def test_bz_mixed_theme_and_code(capsys):
    """主题词+代码混输：代码生效，主题词告警跳过（此前主题词占位、代码被静默丢弃）。"""
    mode, args = start.parse_input("bz 氮化镓 600519")
    assert (mode, args["meta"]) == ("benzong", "600519")
    assert "氮化镓" in capsys.readouterr().out


def test_bz_check_no_code_unchanged():
    mode, args = start.parse_input("bz --check")
    assert mode == "benzong" and args["check"] is True and args["meta"] == ""


# ── chat 层 ──

@contextlib.contextmanager
def _swap(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


@contextlib.contextmanager
def _quiet():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield buf


def test_chat_analyze_stock_multi_requires_confirm():
    """多代码无 confirm 必须被拒，且不得触碰编排器（提示 AI 先征得用户同意）。"""
    import src.chat.tools as tools
    calls = []
    with _swap(tools, "_orchestrator", object()), \
            _swap(tools, "run_command", lambda c, confirm=False: calls.append((c, confirm)) or "执行"):
        out = analyze_stock("600519,000001")
    assert out.startswith(TOOL_ERROR_MARK)
    assert "confirm=true" in out and "600519" in out
    assert calls == [], "未确认不得执行"


def test_chat_analyze_stock_multi_with_confirm_delegates_run_command():
    import src.chat.tools as tools
    calls = []
    with _swap(tools, "_orchestrator", object()), \
            _swap(tools, "run_command", lambda c, confirm=False: calls.append((c, confirm)) or "批量结果"):
        out = analyze_stock("600519,000001", confirm=True)
    assert out == "批量结果"
    assert calls == [("l 600519 000001", True)]


def test_chat_analyze_stock_invalid_token_noted(monkeypatch):
    """单只有效码+非法片段：走单只路径（完整报告），非法片段以 skip_note 前置提示。

    M1 网络审计修复：隔离取数链（calculate_indicators/实时行情兜底）——本测试
    测的是分流与 skip_note 前置，不该真连 baostock/新浪。
    """
    import src.chat.tools as tools
    from src.data.akshare_client import AKShareClient

    def _offline(*a, **k):
        raise RuntimeError("离线测试故障注入")
    monkeypatch.setattr(AKShareClient, "calculate_indicators", _offline)
    monkeypatch.setattr(AKShareClient, "get_realtime_quote", lambda code: {})

    calls = []
    with _swap(tools, "_orchestrator", object()), \
            _swap(tools, "run_command", lambda c, confirm=False: calls.append((c, confirm)) or "批量结果"):
        out = analyze_stock("600519, abc", confirm=True)
    assert "abc" in out and "已跳过" in out
    assert "600519" in out
    assert calls == [], "单只有效码不应委托批量管道"


def test_chat_run_command_multi_mode_in_confirm_gate():
    """run_command('l 600519,000001') 落在 live_multi mode，无 confirm 必须拒绝且不执行。"""
    import start as start_mod
    executed = []
    with _swap(start_mod, "run_cli", lambda *a, **k: executed.append(a)), _quiet():
        out = run_command("l 600519,000001")
    assert out.startswith(TOOL_ERROR_MARK)
    assert executed == []


# ── run_cli 分发接线（mock 分析函数，不触网不烧 AI） ──

def test_run_cli_live_multi_dispatch_wiring(monkeypatch):
    import start as start_mod
    import src.cli.main as cli_main
    import src.cli.session_state as ss
    from src.data.akshare_client import AKShareClient
    # M1 网络审计修复：live_multi 的 _QUOTE_PREFETCH 在 analyze_live 之前跑，须隔离
    monkeypatch.setattr(AKShareClient, "get_realtime_quotes", lambda codes, retry=1: {})
    calls, marks = [], []
    with _swap(cli_main, "analyze_live",
               lambda code, ai_overrides=None, ai_debug=False, compact=False:
               calls.append((code, compact))), \
         _swap(ss, "mark_deep_analyzed", lambda codes, source="": marks.append((codes, source))), \
         _quiet():
        start_mod.run_cli("live_multi", {"codes": ["600519", "000001"]})
    assert [c for c, _ in calls] == ["600519", "000001"]
    assert all(compact for _, compact in calls), "批量必须走 compact 模式"
    assert marks == [(["600519"], "手动多代码"), (["000001"], "手动多代码")]


def test_run_cli_benzong_multi_dispatch_wiring():
    import start as start_mod
    import src.cli.main as cli_main
    import src.core.benzong.cache as bz_cache
    items_seen = []
    with _swap(cli_main, "benzong_batch_analyze",
               lambda items, force_refresh=False: items_seen.append((items, force_refresh))), \
         _swap(bz_cache, "get", lambda code, date, dim: {"hit": True}), _quiet():
        start_mod.run_cli("benzong_multi", {"codes": ["600519", "000001"], "refresh": False})
    # 全部命中缓存 → 不经 y/N 确认直接出排名表
    assert items_seen and items_seen[0][1] is False
    assert [i["code"] for i in items_seen[0][0]] == ["600519", "000001"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
