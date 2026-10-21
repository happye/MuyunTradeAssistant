"""T04-B 服务单测：add_position_checked / remove_position_checked 全部状态。

PortfolioManager 用显式 tmp 路径；保存失败仅桩 PortfolioManager._save。
"""
import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.portfolio import (
    PortfolioManager, PositionOpOutcome, SaveResult, add_position_checked,
    remove_position_checked,
)

CODE = "600519"


@pytest.fixture
def pm(tmp_path):
    return PortfolioManager(str(tmp_path / "portfolio.yaml"), str(tmp_path / "proposals.json"))


def _fail_save(monkeypatch):
    monkeypatch.setattr(PortfolioManager, "_save", lambda self: SaveResult(False))


def test_outcome_is_frozen(pm):
    oc = add_position_checked(pm, "")
    with pytest.raises(dataclasses.FrozenInstanceError):
        oc.status = "X"


# ── add ───────────────────────────────────────────────────

def test_add_missing_code(pm):
    oc = add_position_checked(pm, "", price=-1.0, ratio=9.0)
    assert oc.status == "MISSING_CODE" and oc.ratio == 9.0
    assert not pm.list_positions()


def test_add_already_held_checked_before_price_ratio(pm):
    assert pm.add_position(CODE, stock_name="旧", entry_price=10, ratio=0.1)
    oc = add_position_checked(pm, CODE, name="新", price=-1.0, ratio=9.0)
    assert oc.status == "ALREADY_HELD"
    pos = pm.get_position(CODE)
    assert pos.stock_name == "旧" and pos.current_ratio == 0.1


def test_add_invalid_price(pm):
    oc = add_position_checked(pm, CODE, price=-0.01, ratio=9.0)   # 价格校验先于比例
    assert oc.status == "INVALID_PRICE"
    assert not pm.has_position(CODE)


@pytest.mark.parametrize("ratio", [1.5, -0.1, float("nan")])
def test_add_invalid_ratio(pm, ratio):
    oc = add_position_checked(pm, CODE, price=10.0, ratio=ratio)
    assert oc.status == "INVALID_RATIO"
    assert not pm.has_position(CODE)


def test_add_save_failed(pm, monkeypatch):
    _fail_save(monkeypatch)
    oc = add_position_checked(pm, CODE, name="测试股", price=10.0, ratio=0.2)
    assert oc.status == "SAVE_FAILED" and oc.display_name == "测试股"
    assert oc.total_ratio is None and oc.total_ratio_error is False
    assert not os.path.exists(pm.portfolio_path)


def test_add_success_defaults_name_ratio_and_none_price(pm):
    oc = add_position_checked(pm, CODE)
    assert oc == PositionOpOutcome(
        status="ADDED", stock_code=CODE, display_name=CODE, ratio=0.20, price=0.0,
        total_ratio=0.20)
    pos = PortfolioManager(pm.portfolio_path, str(pm._proposals.path)).get_position(CODE)
    assert pos.stock_name == CODE and pos.entry_price is None and pos.current_ratio == 0.20


def test_add_success_with_price_name_ratio(pm):
    oc = add_position_checked(pm, CODE, name="测试股", price=10.5, ratio=0.3)
    assert oc.status == "ADDED" and oc.display_name == "测试股"
    assert oc.ratio == 0.3 and oc.price == 10.5 and oc.total_ratio == 0.3
    pos = PortfolioManager(pm.portfolio_path, str(pm._proposals.path)).get_position(CODE)
    assert pos.stock_name == "测试股" and pos.entry_price == 10.5


def test_add_success_total_over_80(pm):
    assert pm.add_position("000001", stock_name="压舱", entry_price=5, ratio=0.7)
    oc = add_position_checked(pm, CODE, price=10.0, ratio=0.2)
    assert oc.status == "ADDED" and oc.total_ratio == pytest.approx(0.9)


def test_add_success_total_unknown_is_none(pm):
    assert pm.add_position("000001", stock_name="待重估", entry_price=5, ratio=0.1)
    pm._data["positions"]["000001"]["ratio_stale"] = True
    pm._data["positions"]["000001"]["quantity_fact"] = {
        "quantity": 100, "as_of": "2026-09-21", "avg_cost": 5.0}
    assert pm._save().ok
    oc = add_position_checked(pm, CODE, price=10.0, ratio=0.2)
    assert oc.status == "ADDED" and oc.total_ratio is None and oc.total_ratio_error is False


def test_add_success_total_error_flag(pm, monkeypatch):
    def boom(self):
        raise RuntimeError("x")
    monkeypatch.setattr(PortfolioManager, "get_total_position_ratio", boom)
    oc = add_position_checked(pm, CODE, price=10.0, ratio=0.2)
    assert oc.status == "ADDED" and oc.total_ratio_error is True and oc.total_ratio is None
    assert pm.has_position(CODE)


def test_add_data_layer_error_propagates(pm):
    with pytest.raises(ValueError):
        add_position_checked(pm, CODE, price=float("inf"), ratio=0.2)
    assert not pm.has_position(CODE)


# ── remove ────────────────────────────────────────────────

def test_remove_missing_code(pm):
    assert remove_position_checked(pm, "").status == "MISSING_CODE"


def test_remove_not_held(pm):
    oc = remove_position_checked(pm, CODE)
    assert oc.status == "NOT_HELD" and oc.stock_code == CODE


def test_remove_save_failed_keeps_position(pm, monkeypatch):
    assert pm.add_position(CODE, stock_name="测试股", entry_price=10, ratio=0.1)
    _fail_save(monkeypatch)
    oc = remove_position_checked(pm, CODE)
    assert oc.status == "SAVE_FAILED" and oc.removed_name == "测试股"
    reloaded = PortfolioManager(pm.portfolio_path, str(pm._proposals.path))
    assert reloaded.has_position(CODE)


def test_remove_success_name_then_code_fallback(pm):
    assert pm.add_position(CODE, stock_name="测试股", entry_price=10, ratio=0.1)
    assert pm.add_position("000001", stock_name="", entry_price=10, ratio=0.1)
    oc = remove_position_checked(pm, CODE)
    assert oc.status == "REMOVED" and oc.removed_name == "测试股"
    oc = remove_position_checked(pm, "000001")
    assert oc.status == "REMOVED" and oc.removed_name == "000001"
    assert PortfolioManager(pm.portfolio_path, str(pm._proposals.path)).list_positions() == []
