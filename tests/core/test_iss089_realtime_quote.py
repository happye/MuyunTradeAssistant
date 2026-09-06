# -*- coding: utf-8 -*-
"""ISS-089 盘中实时价回归：单只行情新浪优先（真实盘中价），失败落回既有降级链

跑法：pytest tests/core/test_iss089_realtime_quote.py 或直接 python 执行。
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

import src.data.akshare_client as akc
from src.data.akshare_client import AKShareClient


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


def test_quote_sina_first_realtime_price(monkeypatch):
    """新浪命中即返回，且不得再调 baostock（盘中真实价优先于昨收）。"""
    monkeypatch.setattr(AKShareClient, "_fetch_sina_batch",
                        classmethod(lambda cls, codes: {"600519": {"stock_code": "600519", "price": 123.4, "source": "sina_batch"}}))

    def _boom(cls, code):
        raise AssertionError("新浪命中时不得再调 baostock")

    monkeypatch.setattr(AKShareClient, "_fetch_baostock_realtime", classmethod(_boom))
    with _quiet():
        q = AKShareClient.get_realtime_quote("600519")
    assert q["source"] == "sina_batch" and q["price"] == 123.4


def test_quote_sina_miss_falls_back_to_baostock(monkeypatch):
    """新浪失败（空 dict）→ 落回 baostock 既有降级链。"""
    monkeypatch.setattr(AKShareClient, "_fetch_sina_batch", classmethod(lambda cls, codes: {}))
    monkeypatch.setattr(AKShareClient, "_fetch_baostock_realtime",
                        classmethod(lambda cls, code: {"stock_code": code, "price": 9.9, "source": "baostock"}))
    with _quiet():
        q = AKShareClient.get_realtime_quote("600519")
    assert q["source"] == "baostock"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
