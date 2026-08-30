"""v0.8.7.9 缓存层回归：磁盘 L2（market_cache）+ 法C当日文件缓存（theme_locator）
+ session_state 当日已析记录。

全部 mock/临时目录，无网络、不写真实 ~/.muyun。
"""

import os
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from src.scanner import market_cache as mc
from src.core.benzong import theme_locator as tl
from src.cli import session_state as ss


# ── 磁盘 L2：market_cache ─────────────────────────────────

@pytest.fixture
def disk_dir(tmp_path, monkeypatch):
    """磁盘缓存重定向到临时目录 + 清空类级内存缓存。

    生产代码在 pytest 进程下整体禁用磁盘缓存（防假数据写真实缓存），
    本组测试专门测磁盘层，故在此显式重新开启。
    """
    d = tmp_path / "market_cache"
    monkeypatch.setattr(mc, "_DISK_CACHE_DIR", d)
    monkeypatch.setattr(mc, "_disk_enabled", lambda: True)
    mc.MarketCache._shared_stock_df = None
    mc.MarketCache._shared_stock_ts = 0.0
    mc.MarketCache._shared_stock_count = 0
    mc.MarketCache._shared_fail_ts = 0.0
    mc.MarketCache._shared_industry_stocks.clear()
    mc.MarketCache._shared_industry_stocks_ts.clear()
    mc.MarketCache._shared_concept_stocks.clear()
    mc.MarketCache._shared_concept_stocks_ts.clear()
    return d


def _valid_df(n=20):
    return pd.DataFrame({"代码": [f"{600000 + i}" for i in range(n)],
                         "最新价": [10.0 + i for i in range(n)]})


def _boom(*a, **k):
    raise AssertionError("缓存命中时不应触网")


def test_disk_roundtrip_list(disk_dir):
    assert mc._disk_save("industry_stocks_半导体", ["688981", "002049"])
    d = mc._disk_load("industry_stocks_半导体")
    assert d["data"] == ["688981", "002049"] and d["ts"] > 0
    assert (disk_dir / "industry_stocks_半导体.pkl").exists()  # 中文键转安全文件名


def test_disk_roundtrip_dataframe(disk_dir):
    assert mc._disk_save("stocks_df", _valid_df())
    d = mc._disk_load("stocks_df")
    assert isinstance(d["data"], pd.DataFrame) and len(d["data"]) == 20


def test_disk_disabled_by_env(disk_dir, monkeypatch):
    # 专测环境开关语义：开关开启时读写全禁；关闭时恢复。
    # （pytest 进程下生产 _disk_enabled 恒 False，故这里替换为"只看环境变量"的等价实现）
    monkeypatch.setattr(mc, "_disk_enabled",
                        lambda: os.environ.get("MUYUN_DISABLE_DISK_CACHE", "") != "1")
    monkeypatch.setenv("MUYUN_DISABLE_DISK_CACHE", "1")
    assert mc._disk_save("stocks_df", _valid_df()) is False
    assert mc._disk_load("stocks_df") is None
    monkeypatch.delenv("MUYUN_DISABLE_DISK_CACHE")
    assert mc._disk_save("stocks_df", _valid_df()) is True
    assert mc._disk_load("stocks_df") is not None


def test_disk_disabled_under_pytest_by_default(tmp_path, monkeypatch):
    # 生产 _disk_enabled 在 pytest 进程下必须恒禁用（防测试假数据污染真实 ~/.muyun）
    monkeypatch.setattr(mc, "_DISK_CACHE_DIR", tmp_path / "market_cache")
    monkeypatch.delenv("MUYUN_DISABLE_DISK_CACHE", raising=False)
    assert mc._disk_enabled() is False
    assert mc._disk_save("stocks_df", _valid_df()) is False


def test_get_all_stocks_disk_hit_no_network(disk_dir, monkeypatch):
    mc._disk_save("stocks_df", _valid_df(30))
    monkeypatch.setattr(mc.MarketCache, "_fetch_sina_market", _boom)
    monkeypatch.setattr(mc.MarketCache, "_fetch_efinance_market", _boom)
    df = mc.MarketCache().get_all_stocks()
    assert len(df) == 30
    assert mc.MarketCache._shared_stock_df is not None  # 磁盘命中同时复活内存缓存


def test_get_all_stocks_success_saves_disk(disk_dir, monkeypatch):
    monkeypatch.setattr(mc.MarketCache, "_fetch_sina_market", lambda self: _valid_df(25))
    df = mc.MarketCache().get_all_stocks()
    assert len(df) == 25
    d = mc._disk_load("stocks_df")  # 成功路径（_accept_snapshot 唯一入口）落盘
    assert d is not None and len(d["data"]) == 25


def test_get_all_stocks_both_fail_disk_stale_fallback(disk_dir, monkeypatch):
    mc._disk_save("stocks_df", _valid_df(18))
    p = disk_dir / "stocks_df.pkl"
    payload = pd.read_pickle(p)
    payload["ts"] = time.time() - 99999  # 改旧：模拟已过期
    pd.to_pickle(payload, p)
    monkeypatch.setattr(mc.MarketCache, "_fetch_sina_market", lambda self: None)
    monkeypatch.setattr(mc.MarketCache, "_fetch_efinance_market", lambda self: None)
    df = mc.MarketCache().get_all_stocks()
    assert len(df) == 18  # 双源失败 → 磁盘过期快照兜底（旧行为返回空表）


def test_industry_stocks_disk_hit_no_crawl(disk_dir, monkeypatch):
    mc._disk_save("industry_stocks_半导体", ["688981"])
    monkeypatch.setattr(mc.MarketCache, "_get_ths_board_stocks_by_name", _boom)
    assert mc.MarketCache().get_stocks_by_industry("半导体") == ["688981"]


def test_industry_stocks_fetch_saves_disk_then_memory_hit(disk_dir, monkeypatch):
    monkeypatch.setattr(mc.MarketCache, "_get_ths_board_stocks_by_name",
                        lambda self, name, t: ["002049"])
    assert mc.MarketCache().get_stocks_by_industry("半导体") == ["002049"]
    assert mc._disk_load("industry_stocks_半导体")["data"] == ["002049"]
    monkeypatch.setattr(mc.MarketCache, "_get_ths_board_stocks_by_name", _boom)
    # 二次调用走内存缓存，不再碰 THS 爬虫
    assert mc.MarketCache().get_stocks_by_industry("半导体") == ["002049"]


def test_industry_stocks_timeout_falls_back_to_stale_disk(disk_dir, monkeypatch):
    mc._disk_save("industry_stocks_半导体", ["688981"])
    # 内存为空 + 爬虫抛异常 → 磁盘过期数据兜底（旧行为返回 []）
    monkeypatch.setattr(mc.MarketCache, "_get_ths_board_stocks_by_name",
                        lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("ths down")))
    assert mc.MarketCache().get_stocks_by_industry("半导体") == ["688981"]


# ── 法C 当日文件缓存：theme_locator ───────────────────────

_JSON = '[{"code": "002475", "name": "立讯精密", "term": "消费电子", "why": "主业聚焦"}]'


def _ai_reply(content):
    """可计数的假 AI client：resp.choices[0].message.content / finish_reason。"""
    resp = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content), finish_reason="stop")])

    class _FakeAI:
        def __init__(self):
            self.calls = 0
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create))

        def _create(self, **kw):
            self.calls += 1
            return resp

    return _FakeAI()


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    d = tmp_path / "tl_cache"
    monkeypatch.setattr(tl, "_CACHE_DIR", d)
    return d


def test_locate_saves_and_reuses_same_day(cache_dir, monkeypatch):
    monkeypatch.setattr(tl, "_verify_code", lambda code, name: {"code": code, "name": name})
    ai = _ai_reply(_JSON)
    r1 = tl.locate_theme_stocks(["消费电子"], ai_client=ai, use_cache=True)
    assert len(r1["stocks"]) == 1 and not r1.get("cached")
    assert ai.calls == 1
    assert len(list(cache_dir.glob("*.json"))) == 1
    ai2 = _ai_reply(_JSON)
    r2 = tl.locate_theme_stocks(["消费电子"], ai_client=ai2, use_cache=True)
    assert r2["stocks"] == r1["stocks"] and r2.get("cached") is True
    assert ai2.calls == 0  # 命中当日缓存，AI 零调用


def test_locate_empty_result_not_cached(cache_dir, monkeypatch):
    monkeypatch.setattr(tl, "_verify_code", lambda code, name: None)  # 验证全失败
    ai = _ai_reply(_JSON)
    r1 = tl.locate_theme_stocks(["虚拟主题"], ai_client=ai, use_cache=True)
    assert r1["stocks"] == []
    assert list(cache_dir.glob("*.json")) == []  # 空结果不落缓存（防放大临时故障）
    ai2 = _ai_reply(_JSON)
    tl.locate_theme_stocks(["虚拟主题"], ai_client=ai2, use_cache=True)
    assert ai2.calls == 1  # 第二次仍走 AI 重试


def test_locate_use_cache_false_bypasses(cache_dir, monkeypatch):
    monkeypatch.setattr(tl, "_verify_code", lambda code, name: {"code": code, "name": name})
    ai = _ai_reply(_JSON)
    tl.locate_theme_stocks(["氮化镓"], ai_client=ai, use_cache=True)
    ai2 = _ai_reply(_JSON)
    tl.locate_theme_stocks(["氮化镓"], ai_client=ai2, use_cache=False)  # bz scan --refresh 旁路
    assert ai2.calls == 1


# ── session_state 当日已析记录 ────────────────────────────

def test_deep_analyzed_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "_DEEP_ANALYZED_FILE", tmp_path / "deep_analyzed.json")
    monkeypatch.setattr(ss, "_STATE_DIR", tmp_path)
    assert ss.get_deep_analyzed() == {}
    assert ss.mark_deep_analyzed(["600519", "002475"], source="bz scan 测试")
    rec = ss.get_deep_analyzed()
    assert set(rec) == {"600519", "002475"}
    assert rec["600519"]["source"] == "bz scan 测试"
    assert "time" in rec["600519"]


def test_deep_analyzed_corrupt_file_returns_empty(tmp_path, monkeypatch):
    f = tmp_path / "deep_analyzed.json"
    f.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(ss, "_DEEP_ANALYZED_FILE", f)
    assert ss.get_deep_analyzed() == {}
