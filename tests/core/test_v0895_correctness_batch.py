# -*- coding: utf-8 -*-
"""v0.8.9.5 彻查批正确性修复回归（P1-1/P1-3/A-1 + load 路径 P0 冒烟）。

- P1-1: 东财系接口成交量单位归一（手→股 ×100）+ 代码前/后缀规范化
- P1-3: Monte Carlo 临时引擎与基础回测同口径透传配置
- A-1 : 重放一致性检查器透传 entry_exit_config + has_position 还原
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd

from src.data.akshare_client import AKShareClient
from src.chat.tools import _normalize_code


# ── P1-1: 成交量单位 ─────────────────────────────────────────

def test_em_volume_to_shares():
    f = AKShareClient._em_volume_to_shares
    assert f(12345) == 1_234_500      # 手 → 股
    assert f("678.9") == 67_890
    assert f(0) == 0 and f(-5) == 0
    assert f(None) == 0 and f("nan") == 0 and f("") == 0


def test_realtime_quote_em_path_volume_x100(monkeypatch):
    """get_realtime_quote 走东财全市场降级层时，成交量必须 ×100（股口径）。"""
    import pandas as pd
    # 类级缓存清场（LRN-20260831-001）：前序网络类测试可能留有 600519 的
    # 真实行情预取/全量缓存（TTL 120s），不清会让本测试命中缓存而非 EM 桩
    AKShareClient._QUOTE_PREFETCH.pop("600519", None)
    AKShareClient._stock_data_cache.pop("600519", None)
    monkeypatch.setattr(AKShareClient, "_fetch_sina_batch", classmethod(lambda cls, codes: {}))
    monkeypatch.setattr(AKShareClient, "_fetch_baostock_realtime", classmethod(lambda cls, code: None))
    em_df = pd.DataFrame([{
        "代码": "600519", "名称": "贵州茅台", "最新价": 1500.0, "涨跌幅": 1.2,
        "成交量": 25800.0,  # 手
        "今开": 1490.0, "最高": 1510.0, "最低": 1485.0, "昨收": 1482.0,
    }])
    monkeypatch.setattr("akshare.stock_zh_a_spot_em", lambda: em_df)
    q = AKShareClient.get_realtime_quote("600519", retry=1)
    assert q is not None
    assert q["price"] == 1500.0  # 确认走的是 EM 数据而非预取/新浪桩
    assert q["volume"] == 2_580_000, f"东财源成交量应为手×100: {q['volume']}"


def test_realtime_quotes_em_batch_volume_x100(monkeypatch):
    """批量行情的东财全市场层同样 ×100。"""
    import pandas as pd
    monkeypatch.setattr(AKShareClient, "_fetch_sina_batch", classmethod(lambda cls, codes: {}))
    em_df = pd.DataFrame([{
        "代码": "600519", "名称": "贵州茅台", "最新价": 1500.0, "涨跌幅": 1.2,
        "成交量": 100.0, "今开": 1490.0, "最高": 1510.0, "最低": 1485.0, "昨收": 1482.0,
    }])
    monkeypatch.setattr("akshare.stock_zh_a_spot_em", lambda: em_df)
    monkeypatch.setattr("akshare.fund_etf_spot_em", lambda: pd.DataFrame())
    results = AKShareClient.get_realtime_quotes(["600519"])
    assert results["600519"]["volume"] == 10_000


# ── P1-1 关联: 代码前/后缀规范化 ──────────────────────────────

def test_extract_pure_code_formats():
    f = AKShareClient._extract_pure_code
    assert f("600519") == "600519"
    assert f("000001.SZ") == "000001"
    assert f("SH.600519") == "600519"
    assert f("sz000001") == "000001"
    assert f(" 600 ") == "000600"     # PowerShell 吞前导零
    assert f("000001.SZ ") == "000001"


def test_normalize_stock_code_with_suffix():
    assert AKShareClient._normalize_stock_code("000001.SZ") == ("sz", "000001")
    assert AKShareClient._normalize_stock_code("SH.600519") == ("sh", "600519")
    assert AKShareClient._normalize_stock_code("600519") == ("sh", "600519")


def test_realtime_quote_prefetch_hit_with_suffix():
    """带后缀输入必须命中预取映射（键为纯 6 位码）。

    严格性：全部降级源 stub 为空 + 预取桩带专用标记——只有预取路径能产出
    被断言的 source（修复审查发现的假绿：原时间戳恒 0 已过期，实际靠真实
    新浪网络通过，回归锁不住）。
    """
    import time as _t
    stamp_quote = {
        "stock_code": "000001", "stock_name": "平安银行", "price": 10.0,
        "open": 10.0, "high": 10.0, "low": 10.0, "close_yesterday": 10.0,
        "volume": 1000, "change_pct": 0.0, "source": "prefetch_stub",
    }
    AKShareClient._QUOTE_PREFETCH["000001"] = (_t.time(), stamp_quote)
    try:
        q = AKShareClient.get_realtime_quote("000001.SZ")
        assert q is not None and q["source"] == "prefetch_stub", (
            f"带后缀输入应命中预取，实际: {q}"
        )
        assert q["stock_code"] == "000001"
    finally:
        AKShareClient._QUOTE_PREFETCH.pop("000001", None)


def test_chat_tools_normalize_code_suffix():
    assert _normalize_code("000001.SZ") == "000001"
    assert _normalize_code("SZ.000001") == "000001"
    assert _normalize_code("600519") == "600519"
    assert _normalize_code("") == ""


# ── P1-3: Monte Carlo 配置透传 ───────────────────────────────

def test_monte_carlo_passes_full_config(monkeypatch):
    from src.core import backtest_engine as be_mod

    captured = {}

    class _FakeResult:
        total_return_pct = 0.0
        max_drawdown_pct = 0.0
        mc_simulations = 0

        def __init__(self):
            self.mc_simulations = 0

    class _SpyEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            return _FakeResult()

    weights = {"ma_trend": 9.9}
    skill_types = {"ma_trend": "base"}
    ee_cfg = {"enabled": True}
    engine = be_mod.BacktestEngine(
        stock_code="600519", start_date="2024-01-01", end_date="2024-06-01",
        skills_dir="./src/skills",
        enabled_skills=["ma_trend"], signal_weights=weights, skill_types=skill_types,
        entry_exit_config=ee_cfg,
    )
    monkeypatch.setattr(be_mod, "BacktestEngine", _SpyEngine)
    engine.run_monte_carlo(n_simulations=1, seed=1)
    assert captured.get("signal_weights") is weights
    assert captured.get("skill_types") is skill_types
    assert captured.get("enabled_skills") == ["ma_trend"]
    assert captured.get("entry_exit_config") is ee_cfg
    assert captured.get("skills_dir") == "./src/skills"


# ── P0 复发锁：DataFeeder.load() 路径冒烟（mock baostock，禁网） ────

class _FakeRS:
    """baostock result set 桩：next()/get_row_data()/fields/error_code 契约。"""

    def __init__(self, rows, fields):
        self._rows = [list(r) for r in rows]
        self._i = 0
        self.fields = fields
        self.error_code = '0'
        self.error_msg = ''

    def next(self):
        if self._i < len(self._rows):
            self._i += 1
            return True
        return False

    def get_row_data(self):
        return self._rows[self._i - 1]


def test_datafeeder_load_smoke(monkeypatch):
    """load() 全链路冒烟：真实 load 路径必须完成预计算并产出可用 bar。

    审查 P0 复发锁——load() 曾调到已拆分的不存在方法，AttributeError 被自身
    except 吞成「数据加载失败」导致全部真回测报废，而等价性测试走
    object.__new__ 注入全绿。本测试锁死 load → _build_stock_data → iterate 链。
    """
    import src.data.data_feeder as df_mod
    from src.data.data_feeder import DataFeeder

    dates = pd.bdate_range("2024-01-01", periods=60).strftime("%Y-%m-%d").tolist()
    closes = [100.0 + i for i in range(60)]
    stock_fields = ['date', 'code', 'open', 'high', 'low', 'close', 'volume', 'amount']
    stock_rows = [
        [d, "sh.600519", c - 0.5, c + 0.5, c - 1.0, c, 1_000_000.0, c * 1e6]
        for d, c in zip(dates, closes)
    ]
    index_fields = ['date', 'close', 'high', 'preclose']
    index_rows = [[d, 4000.0 + i, 4010.0 + i, 3995.0 + i] for i, d in enumerate(dates)]

    stock_rs = _FakeRS(stock_rows, stock_fields)
    index_rs = _FakeRS(index_rows, index_fields)

    def _fake_query(code, fields, start_date=None, end_date=None, frequency="d", **kw):
        if code.startswith("sh.000300") or "preclose" in fields:
            return index_rs
        return stock_rs

    class _FakeBS:
        login = staticmethod(lambda: SimpleNamespace(error_code='0', error_msg=''))
        logout = staticmethod(lambda: SimpleNamespace(error_code='0'))
        query_history_k_data_plus = staticmethod(_fake_query)

    monkeypatch.setattr(df_mod, "bs", _FakeBS)

    feeder = DataFeeder("600519", "2024-01-01", "2024-03-29")
    assert feeder.load() is True, "load() 必须成功（P0：曾因调用不存在方法静默失败）"
    assert len(feeder._dates) == 60
    bars = list(feeder.iterate())
    # 前 4 根 cutoff_len<5 返回 None（与旧实现守卫一致），60-4=56
    assert len(bars) == 56
    date, sd = bars[-1]
    assert sd.price == closes[-1]
    assert sd.ma5 is not None, "预计算列必须就位"
    assert sd.atr_14 is not None and sd.rsi_6 is not None
    assert sd.weekly is not None and sd.monthly is not None
    assert sd.index_close == 4000.0 + 59
    # get_kline_until（回测笨总 mode 用）仍可用
    k = feeder.get_kline_until(dates[-1])
    assert k is not None and len(k) == 60


# ── A-1: 重放一致性检查器配置透传 ─────────────────────────────

def test_replay_consistency_passes_entry_exit_config(monkeypatch):
    from src.core import backtest_validator as bv_mod

    captured = {}

    class _SpyOrchestrator:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        def analyze(self, *a, **k):  # 不会被走到（feeder 无 bar）
            raise AssertionError("不应触发分析")

    class _FakeFeeder:
        def __init__(self, *a, **k):
            pass

        def load(self):
            return True

        def iterate(self):
            return iter(())

    monkeypatch.setattr(bv_mod, "Orchestrator", _SpyOrchestrator)
    monkeypatch.setattr(bv_mod, "DataFeeder", _FakeFeeder)

    class _Result:
        stock_code = "600519"
        stock_name = "贵州茅台"
        start_date = "2024-01-01"
        end_date = "2024-02-01"
        diagnostics = {"daily_decisions": [{"date": "2024-01-02"}], "execution_logs": []}

    ee_cfg = {"enabled": True}
    out = bv_mod.build_replay_consistency_check(
        _Result(), "600519", "./src/skills",
        signal_weights={"ma_trend": 1.0}, skill_types={"ma_trend": "base"},
        entry_exit_config=ee_cfg,
    )
    assert captured["kwargs"].get("entry_exit_config") is ee_cfg
    assert out["checked_days"] == 0 and out["passed"] is True
