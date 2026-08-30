"""l all 批量深分析最近扫描（v0.8.7.9）解析与执行路径测试

- parse_input: "l all"[-f] 分流 + 不破坏 l <代码> / l #N / la 既有语义
- run_cli("live_scan_all"): 无扫描早退 / 确认取消 / 当天已析默认跳过 / -f 强制重析 /
  成功才记账、失败跳过不中断

mock session_state 与 src.cli.main.analyze_live，无网络无 AI、不触碰真实 ~/.muyun。
"""

import start
from src.cli import session_state


# ── parse_input 分流 ──────────────────────────────────────

def test_parse_l_all():
    assert start.parse_input("l all") == ("live_scan_all", {"force": False})


def test_parse_l_all_force_flags():
    assert start.parse_input("l all -f") == ("live_scan_all", {"force": True})
    assert start.parse_input("l all --force") == ("live_scan_all", {"force": True})


def test_parse_l_all_case_insensitive():
    assert start.parse_input("L ALL") == ("live_scan_all", {"force": False})


def test_parse_l_bare_still_shows_usage(capsys):
    assert start.parse_input("l") is None
    assert "l all" in capsys.readouterr().out  # 用法提示带上新命令


def test_parse_l_others_unaffected():
    assert start.parse_input("l 600519") == ("live", {"stock_code": "600519"})
    assert start.parse_input("la") == ("live_all", {})


# ── run_cli 执行路径 ──────────────────────────────────────

_ITEMS = {
    "timestamp": "2026-08-30T10:00:00",
    "source": "bz scan 测试",
    "count": 2,
    "items": [
        {"code": "600519", "name": "贵州茅台"},
        {"code": "002475", "name": "立讯精密"},
    ],
}


def _mock_state(monkeypatch, analyzed=None):
    """mock 扫描状态与当日已析记录（绝不触碰真实 ~/.muyun）。返回 mark 收集器。"""
    monkeypatch.setattr(session_state, "get_last_scan", lambda: _ITEMS)
    monkeypatch.setattr(session_state, "get_deep_analyzed", lambda: dict(analyzed or {}))
    marked = []
    monkeypatch.setattr(session_state, "mark_deep_analyzed",
                        lambda codes, source="": marked.extend(codes) or True)
    return marked


def test_run_cli_l_all_no_scan(monkeypatch, capsys):
    monkeypatch.setattr(session_state, "get_last_scan", lambda: None)
    start.run_cli("live_scan_all", {})
    assert "还没有扫描结果" in capsys.readouterr().out


def test_run_cli_l_all_declined(monkeypatch, capsys):
    _mock_state(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    start.run_cli("live_scan_all", {})
    assert "已取消" in capsys.readouterr().out


def test_run_cli_l_all_runs_and_skips_failure(monkeypatch, capsys):
    marked = _mock_state(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    calls = []

    def _fake_analyze_live(code, ai_overrides=None, ai_debug=False, compact=False):
        calls.append((code, compact))
        if code == "600519":
            raise SystemExit  # 模拟数据获取失败（la 同款路径）
        print(f"分析{code}")

    monkeypatch.setattr("src.cli.main.analyze_live", _fake_analyze_live)
    start.run_cli("live_scan_all", {})
    out = capsys.readouterr().out
    assert calls == [("600519", True), ("002475", True)]  # 逐只 compact，失败不中断
    assert "批量完成：1/2 成功" in out
    assert marked == ["002475"]  # 失败股不记账，成功股记当日已析


def test_run_cli_l_all_skips_already_analyzed(monkeypatch, capsys):
    marked = _mock_state(monkeypatch, analyzed={"600519": {"time": "09:30", "source": "x"}})
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    calls = []
    monkeypatch.setattr("src.cli.main.analyze_live",
                        lambda code, **kw: calls.append(code))
    start.run_cli("live_scan_all", {})
    out = capsys.readouterr().out
    assert calls == ["002475"]           # 已析的 600519 被跳过
    assert "今天已深分析 1 只" in out
    assert "批量完成：1/1 成功" in out
    assert marked == ["002475"]


def test_run_cli_l_all_force_reruns_analyzed(monkeypatch, capsys):
    _mock_state(monkeypatch, analyzed={"600519": {"time": "09:30", "source": "x"}})
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    calls = []
    monkeypatch.setattr("src.cli.main.analyze_live",
                        lambda code, **kw: calls.append(code))
    start.run_cli("live_scan_all", {"force": True})
    out = capsys.readouterr().out
    assert calls == ["600519", "002475"]  # -f 强制全量重析，不跳过
    assert "今天已深分析" not in out


def test_run_cli_l_all_all_done_no_calls(monkeypatch, capsys):
    _mock_state(monkeypatch, analyzed={
        "600519": {"time": "09:30", "source": "x"},
        "002475": {"time": "09:31", "source": "x"},
    })
    calls = []
    monkeypatch.setattr("src.cli.main.analyze_live",
                        lambda code, **kw: calls.append(code))
    start.run_cli("live_scan_all", {})   # 全部跳过时应在确认前返回，不触 input
    out = capsys.readouterr().out
    assert calls == []
    assert "全都分析过了" in out
