"""宏观层回测门控测试（2026-08-23 回测实测发现）：check_top_signals 的 macro 层
在 live=False（回测）且未显式传入成交额时必须跳过，不得实时拉取全市场快照。

背景：orchestrator 传 market_turnover_trillion=getattr(data, ..., None)，该字段
StockData 上不存在恒为 None；旧代码 None 即自动拉取 -> 回测每根持仓bar打实时接口：
拉取失败拖垮速度（每次重试~20s），拉取成功=把今天的成交额注入历史bar（未来信息）。
修法与个股层 live 门控同款：live=False 且无显式值 -> 跳过宏观层。

运行：PYTHONUTF8=1 PYTHONPATH=. ./.venv/Scripts/python.exe tests/core/test_macro_gate_backtest.py
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.models import SignalFinding
from src.core.exit_signals import check_top_signals


def _finding(signal_id, detail):
    return SignalFinding(signal_id=signal_id, detail=detail, action_scope='research', source='test')
from src.data.models import StockData


def _sd(**kw):
    base = dict(stock_code="600519", stock_name="茅台", price=10, volume=1000)
    base.update(kw)
    return StockData(**base)


def test_backtest_skips_macro_fetch():
    """live=False + 无显式成交额：macro 层不被调用（不打网络）"""
    with patch("src.core.exit_signals.check_macro_top_signal") as m:
        r = check_top_signals(_sd(), "600519", live=False)
        assert m.call_count == 0, f"回测不应调宏观拉取，实际调了 {m.call_count} 次"
        assert r == []  # v0.8.29：list[SignalFinding] 契约


def test_live_still_fetches(monkeypatch):
    """live=True + 无显式成交额：macro 层照常调用（实盘行为不变）

    M1 网络审计修复：live 个股信号（股东户数/融资余额）会真调 akshare——
    故障注入隔离，宏观层调用断言不变。
    """
    import akshare as _ak

    def _offline(*a, **k):
        raise RuntimeError("离线测试故障注入")
    monkeypatch.setattr(_ak, "stock_zh_a_gdhs_detail_em", _offline)
    monkeypatch.setattr(_ak, "stock_margin_detail_sse", _offline)
    monkeypatch.setattr(_ak, "stock_margin_detail_szse", _offline)
    with patch("src.core.exit_signals.check_macro_top_signal") as m:
        m.return_value = None
        check_top_signals(_sd(), "600519", live=True)
        assert m.call_count == 1, "live 路径应照常调宏观层"


def test_explicit_value_bypasses_gate():
    """live=False 但显式传入成交额：仍检查（显式数据非未来信息注入，如调用方自有历史序列）"""
    with patch("src.core.exit_signals.check_macro_top_signal") as m:
        m.return_value = None
        check_top_signals(_sd(), "600519", live=False, market_turnover_trillion=1.2)
        assert m.call_count == 1, "显式传值应绕过门控照常检查"


def test_macro_trigger_still_wins():
    """live=True 且宏观触发时宏观发现进入结果列表首位（优先级顺序不变；v0.8.29 起为 research 资格）"""
    with patch("src.core.exit_signals.check_macro_top_signal") as m:
        m.return_value = _finding("research.macro.turnover_10t", "宏观:测试信号")
        r = check_top_signals(_sd(), "600519", live=True)
        assert r and r[0].detail == "宏观:测试信号"


def main():
    tests = [test_backtest_skips_macro_fetch, test_live_still_fetches,
             test_explicit_value_bypasses_gate, test_macro_trigger_still_wins]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"✗ {t.__name__}: {e}")
    print(f"\n{'FAIL ' + str(failed) if failed else 'ALL PASS'}: {len(tests) - failed}/{len(tests)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
