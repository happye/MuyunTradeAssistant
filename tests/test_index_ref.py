"""#N 序号快捷解析测试（体验重构 Step 2）

测 parse_input 对 l #1 / bz #3 / pos add #N 的分流，+ 越界/无 scan/正常代码不破坏。
mock session_state.resolve_index 避免依赖真实 last_scan 文件。
"""

import start
from src.cli import session_state


def test_parse_l_index(monkeypatch):
    monkeypatch.setattr(session_state, "resolve_index",
                        lambda n: ({"code": "002230", "name": "科大讯飞"}, "") if n == 1 else None)
    monkeypatch.setattr(session_state, "last_scan_count", lambda: 1)
    assert start.parse_input("l #1") == ("live", {"stock_code": "002230"})


def test_parse_bz_index(monkeypatch):
    monkeypatch.setattr(session_state, "resolve_index",
                        lambda n: ({"code": "688256", "name": "寒武纪"}, "") if n == 3 else None)
    monkeypatch.setattr(session_state, "last_scan_count", lambda: 3)
    r = start.parse_input("bz #3")
    assert r == ("benzong", {"meta": "688256", "manual": False, "refresh": False, "check": False})


def test_parse_pos_add_index(monkeypatch):
    monkeypatch.setattr(session_state, "resolve_index",
                        lambda n: ({"code": "002230", "name": "科大讯飞"}, "") if n == 1 else None)
    monkeypatch.setattr(session_state, "last_scan_count", lambda: 1)
    assert start.parse_input("pos add #1") == ("pos_add", {"stock_code": "002230", "name": "科大讯飞"})


def test_parse_pos_add_index_with_price(monkeypatch):
    monkeypatch.setattr(session_state, "resolve_index",
                        lambda n: ({"code": "002230", "name": "科大讯飞"}, "") if n == 1 else None)
    monkeypatch.setattr(session_state, "last_scan_count", lambda: 1)
    r = start.parse_input("pos add #1 35.20 0.20")
    assert r == ("pos_add", {"stock_code": "002230", "name": "科大讯飞", "price": 35.20, "ratio": 0.20})


def test_parse_index_out_of_range(monkeypatch, capsys):
    monkeypatch.setattr(session_state, "resolve_index", lambda n: None)
    monkeypatch.setattr(session_state, "last_scan_count", lambda: 2)
    assert start.parse_input("l #5") is None
    assert "超出范围" in capsys.readouterr().out


def test_parse_index_no_scan(monkeypatch, capsys):
    monkeypatch.setattr(session_state, "resolve_index", lambda n: None)
    monkeypatch.setattr(session_state, "last_scan_count", lambda: 0)
    assert start.parse_input("l #1") is None
    assert "扫描结果" in capsys.readouterr().out


def test_parse_normal_code_still_works():
    # #N 不影响普通代码解析
    assert start.parse_input("l 600519") == ("live", {"stock_code": "600519"})
    r = start.parse_input("pos add 002192 融捷股份 35.20 0.20")
    assert r == ("pos_add", {"stock_code": "002192", "name": "融捷股份", "price": 35.20, "ratio": 0.20})


def test_parse_direct_stock_code_still_works():
    assert start.parse_input("600519") == ("live", {"stock_code": "600519"})
