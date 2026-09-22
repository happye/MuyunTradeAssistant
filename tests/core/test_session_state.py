"""session_state 模块测试（体验重构 Step 1）

覆盖：save/get 往返、resolve_index 正常/越界/无scan、persist 落盘、count、过期提示。
用 tmp_path monkeypatch 避免污染真实 ~/.muyun/last_scan.json 与 scan_history.jsonl
（v0.8.11 起 save_last_scan 同时追加历史，重定向必须两者都盖）。
"""

from datetime import datetime, timedelta
from pathlib import Path

from src.cli import session_state


def test_save_and_get(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(session_state, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    items = [
        {"code": "002230", "name": "科大讯飞", "score": 82, "grade": "B", "confidence": 0.8},
        {"code": "688256", "name": "寒武纪", "score": 78, "grade": "B", "confidence": 0.75},
    ]
    assert session_state.save_last_scan(items, "bz scan AI,半导体")
    data = session_state.get_last_scan()
    assert data is not None
    assert data["count"] == 2
    assert data["source"] == "bz scan AI,半导体"
    assert data["items"][0]["code"] == "002230"
    assert data["items"][1]["name"] == "寒武纪"
    assert "timestamp" in data


def test_resolve_index_normal(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(session_state, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    items = [{"code": "A", "name": "a"}, {"code": "B", "name": "b"}, {"code": "C", "name": "c"}]
    session_state.save_last_scan(items, "test")
    r = session_state.resolve_index(1)
    assert r is not None
    item, warning = r
    assert item["code"] == "A"
    assert warning == ""  # 刚存，不过期
    r2 = session_state.resolve_index(3)
    assert r2 is not None
    assert r2[0]["code"] == "C"


def test_resolve_index_out_of_range(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(session_state, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    session_state.save_last_scan([{"code": "X"}], "test")
    assert session_state.resolve_index(0) is None   # 1-based，0 越界
    assert session_state.resolve_index(2) is None   # 超出
    assert session_state.resolve_index(-1) is None


def test_resolve_index_no_scan(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "none.json")
    assert session_state.resolve_index(1) is None
    assert session_state.last_scan_count() == 0


def test_persist_report(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_REPORT_DIR", tmp_path / "scan")
    items = [
        {"code": "002230", "name": "科大讯飞", "score": 82, "grade": "B",
         "confidence": 0.8, "dims": {"industry_prosperity": 90}},
    ]
    path = session_state.persist_scan_report(items, "bz scan AI,半导体")
    assert path
    content = Path(path).read_text(encoding="utf-8")
    assert "002230" in content
    assert "科大讯飞" in content
    assert "| 1 |" in content
    assert "industry_prosperity" in content  # 明细
    # 文件名含 safe source
    assert "AI" in path or "AI_" in path


def test_persist_report_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_REPORT_DIR", tmp_path / "scan")
    path = session_state.persist_scan_report([], "bz scan test")
    assert path  # 空列表也应能落盘（共0只）


def test_persist_report_custom_columns(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_REPORT_DIR", tmp_path / "scan")
    items = [
        {"code": "600519", "name": "贵州茅台", "price": 1800.0, "change_pct": 2.5,
         "turnover_rate": 0.8, "volume_ratio": 1.2, "amplitude": 3.1, "amount_yi": 25.6},
    ]
    cols = [("price", "价"), ("change_pct", "涨跌%"), ("turnover_rate", "换手%"),
            ("volume_ratio", "量比"), ("amplitude", "振幅%"), ("amount_yi", "额(亿)")]
    path = session_state.persist_scan_report(items, "scan market", columns=cols)
    assert path
    content = Path(path).read_text(encoding="utf-8")
    assert "600519" in content
    assert "贵州茅台" in content
    assert "1800.00" in content
    assert "2.50" in content  # change_pct
    # 自定义列表头出现
    assert "涨跌%" in content
    assert "额(亿)" in content


def test_count(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "_LAST_SCAN_FILE", tmp_path / "last_scan.json")
    monkeypatch.setattr(session_state, "_SCAN_HISTORY_FILE", tmp_path / "scan_history.jsonl")
    assert session_state.last_scan_count() == 0
    session_state.save_last_scan([{"code": str(i)} for i in range(5)], "test")
    assert session_state.last_scan_count() == 5


def test_stale_warning():
    old = (datetime.now() - timedelta(minutes=60)).isoformat()
    assert "过期" in session_state._stale_warning(old)
    fresh = datetime.now().isoformat()
    assert session_state._stale_warning(fresh) == ""
    assert session_state._stale_warning(None) == ""
    assert session_state._stale_warning("not-a-date") == ""
