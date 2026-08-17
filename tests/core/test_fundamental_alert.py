"""ISS-053: 建仓后基本面恶化硬退出 单测

mock baostock 返回，验证 check_fundamental_alert 各场景：
- 被 ST/*ST -> 触发
- 业绩预告预亏/预减/首亏 + 建仓后发布 -> 触发
- 建仓前发布的预告 -> 不触发（已定价，假退出防护，审视点3核心）
- 利好/中性预告 -> 不触发
- ST 优先于预告
- entry_date=None -> 跳过预告检查（避假退出），仅 ST 查
- 数据缺失 -> 不崩，返回 None

外加 get_latest_forecast 的 since_date 过滤端到端验证（mock baostock ResultSet）。

跑法:
    PYTHONUTF8=1 PYTHONPATH=. uv run python tests/test_fundamental_alert.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.exit_signals.fundamental import (
    check_fundamental_alert, clear_cache, LOSS_FORECAST_TYPES,
)
from src.data import akshare_client
from src.data.akshare_client import AKShareClient


# baostock query_forecast_report 实测字段（probe_iss053_data.py 验证）
_FC_FIELDS = [
    'code', 'profitForcastExpPubDate', 'profitForcastExpStatDate',
    'profitForcastType', 'profitForcastAbstract',
    'profitForcastChgPctUp', 'profitForcastChgPctDwn',
]


class _FakeRS:
    """模拟 baostock ResultSet：fields + 多行，next()/get_row_data() 逐行读。"""

    def __init__(self, fields, rows, error_code='0'):
        self.fields = fields
        self._rows = rows
        self.error_code = error_code
        self._i = 0
        self._row = None

    def next(self):
        if self._i < len(self._rows):
            self._row = self._rows[self._i]
            self._i += 1
            return True
        return False

    def get_row_data(self):
        return self._row


def _setup():
    clear_cache()  # 每测前清缓存，避免跨测污染


def test_loss_forecast_types_set():
    """校验利空类型集覆盖预亏/预减/续亏/首亏，利好类型不在内。"""
    assert LOSS_FORECAST_TYPES == {"预亏", "预减", "续亏", "首亏"}
    assert "略增" not in LOSS_FORECAST_TYPES
    assert "续盈" not in LOSS_FORECAST_TYPES
    assert "扭亏" not in LOSS_FORECAST_TYPES
    print("✓ 利空类型集 = {预亏,预减,续亏,首亏}")


def test_st_triggers():
    _setup()
    with patch.object(AKShareClient, "get_stock_basic_name", return_value="ST星源"), \
         patch.object(AKShareClient, "get_latest_forecast", return_value=None):
        r = check_fundamental_alert("000005")
    assert r and "被ST" in r and "ST星源" in r, f"应触发 ST, 实际 {r}"
    print(f"✓ ST星源 -> 触发: {r}")


def test_star_st_triggers():
    _setup()
    with patch.object(AKShareClient, "get_stock_basic_name", return_value="*ST某某"), \
         patch.object(AKShareClient, "get_latest_forecast", return_value=None):
        r = check_fundamental_alert("000001")
    assert r and "被ST" in r, f"*ST 应触发, 实际 {r}"
    print(f"✓ *ST某某 -> 触发: {r}")


def test_normal_name_no_loss_forecast_no_trigger():
    _setup()
    with patch.object(AKShareClient, "get_stock_basic_name", return_value="贵州茅台"), \
         patch.object(AKShareClient, "get_latest_forecast",
                      return_value={"type": "略增", "abstract": "预计增长",
                                    "pub_date": "2025-01-21", "stat_date": "2024-12-31"}):
        r = check_fundamental_alert("600519", entry_date="2024-01-01")
    assert r is None, f"茅台+略增不应触发, 实际 {r}"
    print("✓ 茅台+略增 -> None (不误报)")


def test_loss_forecast_post_entry_triggers():
    _setup()
    with patch.object(AKShareClient, "get_stock_basic_name", return_value="某股"), \
         patch.object(AKShareClient, "get_latest_forecast",
                      return_value={"type": "预亏", "abstract": "预计亏损1亿元",
                                    "pub_date": "2025-01-21", "stat_date": "2024-12-31"}):
        r = check_fundamental_alert("300001", entry_date="2024-06-01")
    assert r and "预亏" in r, f"建仓后预亏应触发, 实际 {r}"
    print(f"✓ 预亏+pub_date>=entry -> 触发: {r}")


def test_loss_forecast_pre_entry_no_trigger():
    """审视点3核心：建仓前发布的预告已定价，触发是假退出，必须不报。"""
    _setup()
    # mock get_latest_forecast 模拟 since_date 过滤后：建仓前发布 -> 返回 None
    with patch.object(AKShareClient, "get_stock_basic_name", return_value="某股"), \
         patch.object(AKShareClient, "get_latest_forecast", return_value=None):
        r = check_fundamental_alert("300002", entry_date="2024-06-01")
    assert r is None, f"建仓前预告不应触发(假退出), 实际 {r}"
    print("✓ 预亏但建仓前发布 -> None (避假退出)")


def test_no_entry_date_skips_forecast():
    """entry_date=None -> 跳过预告检查（无法过滤建仓前后），仅 ST 查。
    即使有预亏预告也不触发（避假退出），ST 仍生效。"""
    _setup()
    with patch.object(AKShareClient, "get_stock_basic_name", return_value="某股"), \
         patch.object(AKShareClient, "get_latest_forecast",
                      return_value={"type": "预亏", "abstract": "预计亏损",
                                    "pub_date": "2025-01-21", "stat_date": "2024-12-31"}):
        r = check_fundamental_alert("300003")  # 无 entry_date
    assert r is None, f"无 entry_date 不应触发预告(假退出风险), 实际 {r}"
    print("✓ 无 entry_date -> 跳过预告检查 (避假退出)")


def test_first_loss_triggers():
    _setup()
    with patch.object(AKShareClient, "get_stock_basic_name", return_value="某股"), \
         patch.object(AKShareClient, "get_latest_forecast",
                      return_value={"type": "首亏", "abstract": "首次亏损预告",
                                    "pub_date": "2025-04-01", "stat_date": "2025-03-31"}):
        r = check_fundamental_alert("300004", entry_date="2025-01-01")
    assert r and "首亏" in r, f"首亏应触发, 实际 {r}"
    print(f"✓ 首亏 -> 触发: {r}")


def test_st_priority_over_forecast():
    _setup()
    with patch.object(AKShareClient, "get_stock_basic_name", return_value="ST暴雷"), \
         patch.object(AKShareClient, "get_latest_forecast",
                      return_value={"type": "预亏", "abstract": "同时预亏",
                                    "pub_date": "2025-01-21", "stat_date": "2024-12-31"}):
        r = check_fundamental_alert("300005", entry_date="2024-01-01")
    assert r and "被ST" in r, f"ST 应优先于预告, 实际 {r}"
    print(f"✓ ST+预亏同存 -> ST 优先: {r}")


def test_data_missing_no_crash():
    _setup()
    with patch.object(AKShareClient, "get_stock_basic_name", return_value=None), \
         patch.object(AKShareClient, "get_latest_forecast", return_value=None):
        r = check_fundamental_alert("600999", entry_date="2024-01-01")
    assert r is None, f"数据缺失应返回 None 不崩, 实际 {r}"
    print("✓ 数据缺失 -> None (不崩)")


def test_get_latest_forecast_filters_pre_entry():
    """端到端验证 since_date 过滤：建仓前预亏被滤，取建仓后略增（避假退出核心）。"""
    _setup()
    rows = [
        ['sz.300010', '2024-01-21', '2023-12-31', '预亏', '预计亏损', '100', '200'],  # 建仓前预亏
        ['sz.300010', '2025-01-21', '2024-12-31', '略增', '预计增长', '20', '11'],    # 建仓后略增
    ]
    fake_rs = _FakeRS(_FC_FIELDS, rows)
    with patch.object(akshare_client, "_ensure_baostock_login", return_value=True), \
         patch.object(akshare_client.bs, "query_forecast_report", return_value=fake_rs):
        fc = AKShareClient.get_latest_forecast("300010", since_date="2024-06-01")
    assert fc is not None and fc["type"] == "略增", f"应取建仓后略增, 实际 {fc}"
    print(f"✓ get_latest_forecast 滤掉建仓前预亏, 取建仓后略增: type={fc['type']}")


def test_get_latest_forecast_all_pre_entry_returns_none():
    """全部预告都在建仓前发布 -> 返回 None（不假退出）。"""
    _setup()
    rows = [
        ['sz.300011', '2024-01-21', '2023-12-31', '预亏', '预计亏损', '100', '200'],
    ]
    fake_rs = _FakeRS(_FC_FIELDS, rows)
    with patch.object(akshare_client, "_ensure_baostock_login", return_value=True), \
         patch.object(akshare_client.bs, "query_forecast_report", return_value=fake_rs):
        fc = AKShareClient.get_latest_forecast("300011", since_date="2024-06-01")
    assert fc is None, f"建仓前预告应全滤掉返回 None, 实际 {fc}"
    print("✓ 建仓前预告全被滤 -> None (假退出防护)")


def test_get_latest_forecast_no_since_date_returns_latest():
    """无 since_date（trade_plan None 路径不调，但方法本身应返回最新）。"""
    _setup()
    rows = [
        ['sz.300012', '2024-01-21', '2023-12-31', '预亏', '预计亏损', '100', '200'],
        ['sz.300012', '2025-01-21', '2024-12-31', '略增', '预计增长', '20', '11'],
    ]
    fake_rs = _FakeRS(_FC_FIELDS, rows)
    with patch.object(akshare_client, "_ensure_baostock_login", return_value=True), \
         patch.object(akshare_client.bs, "query_forecast_report", return_value=fake_rs):
        fc = AKShareClient.get_latest_forecast("300012")  # 无 since_date
    assert fc is not None and fc["type"] == "略增", f"无过滤应取最新, 实际 {fc}"
    print("✓ 无 since_date -> 取最新预告")


if __name__ == "__main__":
    print("\n=== ISS-053 check_fundamental_alert 单测 ===\n")
    test_loss_forecast_types_set()
    test_st_triggers()
    test_star_st_triggers()
    test_normal_name_no_loss_forecast_no_trigger()
    test_loss_forecast_post_entry_triggers()
    test_loss_forecast_pre_entry_no_trigger()
    test_no_entry_date_skips_forecast()
    test_first_loss_triggers()
    test_st_priority_over_forecast()
    test_data_missing_no_crash()
    test_get_latest_forecast_filters_pre_entry()
    test_get_latest_forecast_all_pre_entry_returns_none()
    test_get_latest_forecast_no_since_date_returns_latest()
    print("\n=== 全部 PASS ===")
