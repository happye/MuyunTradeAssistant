# -*- coding: utf-8 -*-
"""ISS-091 杂项加固回归：日期告警/无价告警/缓存清理/新闻磁盘缓存

跑法：pytest tests/core/test_iss091_robustness.py 或直接 python 执行。
"""
import contextlib
import io
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

import akshare as ak
import src.data.news_client as nc
from src.core.plan_guard import _days_since
from src.data.portfolio import PortfolioManager


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
        yield


def test_days_since_bad_date_warns(caplog):
    from src.core.plan_guard import _days_since
    with caplog.at_level(logging.WARNING, logger="src.core.plan_guard"):
        assert _days_since("not-a-date", "2026-09-07") == 0
    assert any("日期解析失败" in r.message for r in caplog.records)


def test_add_position_without_price_warns(caplog):
    from src.data.portfolio import PortfolioManager
    pm = PortfolioManager()
    with caplog.at_level(logging.WARNING, logger="src.data.portfolio"):
        pm.add_position("999999", stock_name="测试无价", entry_price=None, ratio=0.1)
    assert any("未提供开仓价" in r.message for r in caplog.records)
    pm.remove_position("999999")


def test_benzong_cache_90day_cleanup(tmp_path, monkeypatch, capsys):
    """90 天前的评分缓存文件在 bz 入口被自动清理。"""
    import src.core.benzong.cache as bz_cache
    old_file = tmp_path / "600519_2026-06-01_business_purity.json"
    old_file.write_text("{}", encoding="utf-8")
    new_file = tmp_path / "600519_2099-01-01_business_purity.json"
    new_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bz_cache, "get_cache_dir", lambda: tmp_path)
    import start as start_mod

    with _swap(start_mod, "_run_benzong_manual", lambda meta="": None), _quiet():
        start_mod.run_benzong_scoring(meta="", manual=True)
    assert not old_file.exists(), "90 天前缓存应被清理"
    assert new_file.exists(), "新缓存不应被误删"


def test_news_disk_cache_roundtrip(monkeypatch, tmp_path):
    """新闻当日磁盘缓存：首次抓取落盘，进程内缓存清空后第二次命中磁盘不再抓。"""
    import src.data.news_client as nc
    monkeypatch.setattr(nc.NewsClient, "_news_disk_path", classmethod(lambda cls, code: tmp_path / f"{code}_2026-09-07.json"))
    fetches = []

    class FakeDF(list):
        empty = False
        def head(self, n):
            return self
        def iterrows(self):
            return iter([("t", {"新闻标题": "标题A", "新闻内容": "内容"})])

    def fake_news_em(symbol):
        fetches.append(symbol)
        return FakeDF([("t", {})])

    import akshare as ak
    monkeypatch.setattr(ak, "stock_news_em", fake_news_em)
    # 隔离类级内存缓存：全量跑中其他测试（chat/web 真实链）可能已真实抓取并
    # 缓存 600519——本测试依赖"缓存未命中"初态，须显式清空（第九轮全量实证）
    monkeypatch.setattr(nc.NewsClient, "_stock_news_cache", {})
    with _quiet():
        first = nc.NewsClient.get_stock_news("600519")
        assert len(first) == 1 and fetches == ["600519"]
        assert (tmp_path / "600519_2026-09-07.json").exists(), "首次抓取应落盘"
        # 模拟进程重启：清空内存缓存
        nc.NewsClient._stock_news_cache.clear()
        second = nc.NewsClient.get_stock_news("600519")
    assert second == first
    assert fetches == ["600519"], "磁盘命中不得重抓"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
