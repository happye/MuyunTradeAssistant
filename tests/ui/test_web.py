"""web UI 测试（flask test client）

Phase 1 scan + analyze：mock 引擎验证 threading 后台 + htmx 轮询。
不接真引擎（避免真 scan 4分钟 / analyze 60s）。
"""

import re
import time

import pytest


class _FakeCand:
    def __init__(self, code, name):
        self.stock_code = code
        self.stock_name = name
        self.price = 100.0
        self.change_pct = 2.5
        self.turnover_rate = 1.5
        self.volume_ratio = 1.2
        self.amplitude = 3.0
        self.amount = 10e8


class _FakeScanner:
    def quick_scan(self, **kw):
        return [_FakeCand("600519", "贵州茅台"), _FakeCand("002230", "科大讯飞")], {
            "total_stocks": 3
        }


class _FakePM:
    def __init__(self):
        self._positions = []

    def list_positions(self):
        return self._positions

    def add_position(self, stock_code, stock_name="", entry_price=None, ratio=0.20, lifecycle="OPEN"):
        # M5 起真实现返回 bool（保存成败），替身对齐返回 True=已保存
        class _Pos:
            def __init__(self, code, name, ratio):
                self.stock_code = code
                self.stock_name = name
                self.current_ratio = ratio
                self.entry_price = None
                self.lifecycle = "OPEN"
        self._positions.append(_Pos(stock_code, stock_name, ratio))
        return True

    def to_strategy_state(self, code):
        return None


# analyze mock 对象
class _FakeStockData:
    stock_code = "600519"
    stock_name = "贵州茅台"
    price = 1800.0
    change_pct = 2.5
    ma5 = 1790.0
    ma20 = 1750.0
    ma60 = 1700.0


class _FakeDR:
    class state:
        value = "bullish"
    class decision:
        value = "BUY"
    score = 82.5
    signals = []


class _FakeSD:
    class position_action:
        value = "OPEN"


class _FakeAI:
    adjusted = False


@pytest.fixture
def client(monkeypatch):
    from src.web import app as app_mod

    monkeypatch.setattr(app_mod, "_scanner", _FakeScanner())
    monkeypatch.setattr(app_mod, "_orchestrator", None)
    monkeypatch.setattr(app_mod, "_portfolio", _FakePM())

    # mock _analyze_work 返回假结果（避免真 AKShareClient/orchestrator）
    def _fake_analyze(code):
        return {"stock_data": _FakeStockData(), "dr": _FakeDR(),
                "sd": _FakeSD(), "ee": None, "ai": _FakeAI()}
    monkeypatch.setattr(app_mod, "_analyze_work", _fake_analyze)

    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "暮云".encode("utf-8") in r.data
    assert b"hx-post" in r.data
    assert b"scan-panel" in r.data
    assert b"analysis-panel" in r.data
    assert b"/static/htmx.min.js" in r.data


def test_static_htmx_served(client):
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200
    assert b"htmx" in r.data


def test_scan_returns_progress(client):
    r = client.post("/scan")
    assert r.status_code == 200
    assert "扫描中".encode("utf-8") in r.data
    assert b"hx-get" in r.data
    assert b"every 2s" in r.data


def test_scan_completes_to_table(client):
    r = client.post("/scan")
    assert r.status_code == 200
    time.sleep(0.3)
    m = re.search(r"/scan/([a-f0-9]+)", r.data.decode("utf-8"))
    assert m, f"task_id 未找到: {r.data}"
    task_id = m.group(1)
    r2 = client.get(f"/scan/{task_id}")
    assert r2.status_code == 200
    assert "贵州茅台".encode("utf-8") in r2.data
    assert "600519".encode("utf-8") in r2.data
    assert b"hx-post" in r2.data  # 行联动 /analyze


def test_analyze_returns_progress(client):
    r = client.post("/analyze/600519")
    assert r.status_code == 200
    assert "分析".encode("utf-8") in r.data  # progress running
    assert b"analyze-task" in r.data  # 轮询 /analyze-task/<id>
    assert b"analysis-panel" in r.data  # target


def test_analyze_completes_to_panel(client):
    r = client.post("/analyze/600519")
    assert r.status_code == 200
    time.sleep(0.3)
    m = re.search(r"/analyze-task/([a-f0-9]+)", r.data.decode("utf-8"))
    assert m, f"task_id 未找到: {r.data}"
    task_id = m.group(1)
    r2 = client.get(f"/analyze-task/{task_id}")
    assert r2.status_code == 200
    assert "贵州茅台".encode("utf-8") in r2.data  # 分析栏含股票名
    assert "BUY".encode("utf-8") in r2.data  # 决策
    assert "试探建仓".encode("utf-8") in r2.data  # 仓位动作（OPEN 映射）
    assert b"hx-post" in r2.data  # 加仓按钮联动


def test_pos_add(client):
    r = client.post("/pos/add", data={"code": "600519", "name": "贵州茅台", "price": "1800"})
    assert r.status_code == 200
    assert "已加仓".encode("utf-8") in r.data
    assert "600519".encode("utf-8") in r.data  # positions 片段含


def test_pos_list(client):
    r = client.get("/pos")
    assert r.status_code == 200


def test_reports(client):
    r = client.get("/reports")
    assert r.status_code == 200  # 空或列表都 200


def test_events(client, monkeypatch):
    from src.data import news_client

    monkeypatch.setattr(
        news_client.NewsClient, "get_macro_news",
        classmethod(lambda cls, max_count=10: [
            {"title": "测试事件", "time": "2026-07-18", "summary": "测试摘要"}
        ])
    )
    r = client.get("/events")
    assert r.status_code == 200
    assert "测试事件".encode("utf-8") in r.data


def test_bz(client, monkeypatch):
    from src.web import app as app_mod

    def _fake_bz(code):
        return {"code": code, "name": "贵州茅台", "norm": 82.0, "grade": "B",
                "raw_grade": "B", "confidence": 0.75, "dims": {}, "warnings": []}
    monkeypatch.setattr(app_mod, "_bz_work", _fake_bz)

    r = client.post("/bz", data={"code": "600519"})
    assert r.status_code == 200
    assert "评分".encode("utf-8") in r.data
    time.sleep(0.3)
    m = re.search(r"/bz-task/([a-f0-9]+)", r.data.decode("utf-8"))
    assert m, f"task_id 未找到: {r.data}"
    r2 = client.get(f"/bz-task/{m.group(1)}")
    assert r2.status_code == 200
    assert "贵州茅台".encode("utf-8") in r2.data
    assert b"B" in r2.data  # grade


def test_backtest(client, monkeypatch):
    from src.web import app as app_mod

    def _fake_bt(code, start, end, capital):
        return {"code": code, "total_return": 15.5, "benchmark": 8.2, "trades": 5}
    monkeypatch.setattr(app_mod, "_backtest_work", _fake_bt)

    r = client.post("/backtest/600519")
    assert r.status_code == 200
    assert "回测".encode("utf-8") in r.data
    time.sleep(0.3)
    m = re.search(r"/backtest-task/([a-f0-9]+)", r.data.decode("utf-8"))
    assert m, f"task_id 未找到: {r.data}"
    r2 = client.get(f"/backtest-task/{m.group(1)}")
    assert r2.status_code == 200
    assert "15.50".encode("utf-8") in r2.data  # total_return
    assert "8.20".encode("utf-8") in r2.data  # benchmark
