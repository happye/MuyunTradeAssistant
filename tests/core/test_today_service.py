"""F7 today 工作台回归测试（plan/fusion TASKS.md F7，DESIGN ADR-F08）

锁死语义：
1. 三组分类：需要处理（待确认建议——"这是建议，尚未记为成交"）/继续持有/等待条件
2. 无操作是合法结果（无建议 → "需要处理：无"）
3. 部分成交（PARTIAL）保持待办可见
4. LEGACY_UNVERIFIED 全量/部分两种提示形态
5. REPL 接线：parse_input("today") → run_cli 派发 → 输出（真实接线测试）
6. 紧急风险不隐藏（待确认建议全量展示，不截断）

纯 mock/临时目录，无网络。跑法：pytest tests/core/test_today_service.py -q
"""
import os
import sys
import tempfile
from types import SimpleNamespace

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import contextlib


@contextlib.contextmanager
def _tmp_env(positions: dict):
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="f7_today_")
    os.close(fd)
    ledger_fd, ledger = tempfile.mkstemp(suffix=".json", prefix="f7_today_ledger_")
    os.close(ledger_fd)
    os.remove(ledger)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"positions": positions}, f, allow_unicode=True)
    try:
        yield path, ledger
    finally:
        for p in (path, ledger):
            if os.path.exists(p):
                os.remove(p)


def _pm(path, ledger):
    from src.data.portfolio import PortfolioManager
    return PortfolioManager(path, proposals_path=ledger)


def _reduce_decision(ratio=0.1):
    from types import SimpleNamespace
    return SimpleNamespace(
        position_action=SimpleNamespace(value="REDUCE"),
        action_semantic="TRIM", sell_path="take_profit_trim", position_ratio=ratio,
        strategy_reasons=["止盈减仓"],
        new_state=SimpleNamespace(lifecycle=SimpleNamespace(value="HOLD"),
                                  cooldown_remaining=0, cooldown_reason=None,
                                  current_position_ratio=ratio),
    )


# ── 1. 分组 ──────────────────────────────────────────────

def test_grouping_needs_action_and_holding():
    from src.cli.today_service import build_today_view
    with _tmp_env({
        "601318": {"stock_name": "中国平安", "current_ratio": 0.2, "lifecycle": "HOLD",
                   "strategy_state": {}},
        "000001": {"stock_name": "平安银行", "current_ratio": 0.1, "lifecycle": "HOLD",
                   "strategy_state": {}},
    }) as (path, ledger):
        pm = _pm(path, ledger)
        pm.record_proposal("601318", "中国平安", _reduce_decision(), source="l")
        view = build_today_view(pm, watch_count=3)
        assert [c.stock_code for c in view.needs_action] == ["601318"], "有待办的进需要处理"
        assert "尚未记为成交" in view.needs_action[0].lines[-1], "建议≠成交主线文案"
        assert [c.stock_code for c in view.holding] == ["000001"], "无待办的继续持有"
        assert view.waiting and "观察池 3 只" in view.waiting[0].lines[0]


def test_no_pending_means_nothing_to_do():
    """DESIGN ADR-F08：无操作是合法且清晰的结果。"""
    from src.cli.today_service import build_today_view
    with _tmp_env({
        "000001": {"stock_name": "平安银行", "current_ratio": 0.1, "lifecycle": "HOLD",
                   "strategy_state": {}},
    }) as (path, ledger):
        view = build_today_view(_pm(path, ledger), watch_count=0)
        assert view.needs_action == []
        assert len(view.holding) == 1


def test_partial_proposal_stays_visible():
    """部分成交后剩余待办保持可见（F1 PARTIAL 语义在 today 的呈现）。"""
    from src.cli.today_service import build_today_view
    with _tmp_env({
        "601318": {"stock_name": "中国平安", "entry_date": "2026-08-10",
                   "entry_price": 50.0, "current_ratio": 0.2, "lifecycle": "HOLD",
                   "strategy_state": {}},
    }) as (path, ledger):
        pm = _pm(path, ledger)
        from types import SimpleNamespace
        prop = pm.record_proposal("601318", "中国平安", SimpleNamespace(
            position_action=SimpleNamespace(value="CLOSE_ALL"), action_semantic="EXIT",
            sell_path="x", position_ratio=0.0, strategy_reasons=["止损"],
            new_state=SimpleNamespace(lifecycle=SimpleNamespace(value="FLAT"),
                                      cooldown_remaining=5, cooldown_reason="close_all",
                                      current_position_ratio=0.0)), source="l")
        pm.confirm_fill("601318", "SELL", 0.1, date="2026-09-25")  # 部分成交
        view = build_today_view(pm, watch_count=0)
        assert len(view.needs_action) == 1, "PARTIAL 剩余待办可见"
        assert "PARTIAL" in view.needs_action[0].lines[0], "状态如实标注"


# ── 2. LEGACY 提示两形态 ─────────────────────────────────

def test_legacy_notice_all_vs_partial():
    from src.cli.today_service import build_today_view
    with _tmp_env({
        "000001": {"stock_name": "平安银行", "current_ratio": 0.1, "lifecycle": "HOLD",
                   "strategy_state": {}},
    }) as (path, ledger):
        view = build_today_view(_pm(path, ledger), watch_count=0)
        assert any("全部 1 条旧持仓" in n for n in view.notices), "全量 LEGACY 提示"
    # 部分确认（CONFIRMED_FILL）+ 部分 LEGACY
    with _tmp_env({
        "000001": {"stock_name": "A", "current_ratio": 0.1, "lifecycle": "HOLD",
                   "strategy_state": {}},
        "000002": {"stock_name": "B", "current_ratio": 0.1, "lifecycle": "HOLD",
                   "holding_verification": "CONFIRMED_FILL", "strategy_state": {}},
    }) as (path, ledger):
        view = build_today_view(_pm(path, ledger), watch_count=0)
        assert any("1/2 条旧持仓" in n for n in view.notices), "部分 LEGACY 提示"


# ── 3. REPL 真实接线（parse → run_cli）──────────────────

def test_today_repl_wiring(monkeypatch, tmp_path, capsys):
    """真实接线：parse_input("today") 派发到 today_command 输出工作台（不 mock
    render——走真实 build_today_view/render_today）。P2-10 修复：monkeypatch
    默认持仓路径（DEFAULT_PORTFOLIO_PATH 按 __file__ 推导，不受 conftest HOME
    隔离影响——密闭注入临时空持仓）。"""
    import start
    from src.data import portfolio as _pf
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="f7_wiring_")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"positions": {}}, f)
    monkeypatch.setattr(_pf, "DEFAULT_PORTFOLIO_PATH", path)
    mode, args = start.parse_input("today")
    assert (mode, args) == ("today", {})
    mode2, _ = start.parse_input("今日")
    assert mode2 == "today"
    start.run_cli("today", {})
    out = capsys.readouterr().out
    assert "今日工作台" in out and "需要处理" in out
    os.remove(path)


def test_render_contains_all_groups(monkeypatch, tmp_path):
    from src.cli.today_service import build_today_view, render_today
    with _tmp_env({
        "601318": {"stock_name": "中国平安", "current_ratio": 0.2, "lifecycle": "HOLD",
                   "strategy_state": {}},
        "000001": {"stock_name": "平安银行", "current_ratio": 0.1, "lifecycle": "HOLD",
                   "strategy_state": {}},
    }) as (path, ledger):
        pm = _pm(path, ledger)
        pm.record_proposal("601318", "中国平安", _reduce_decision(), source="l")
        view = build_today_view(pm, watch_count=2)
        text = render_today(view)
        assert "需要处理" in text and "继续持有" in text and "等待条件" in text
        assert "没有信号就不动" in text
