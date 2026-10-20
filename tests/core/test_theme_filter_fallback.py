"""主题过滤失败→替代源→全市场兜底（零网络）。"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.data import board_members_alt as bma
from src.data import robust_fetch as rf

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("MUYUN_ROBUST_CACHE_DIR", str(tmp_path / "rc"))
    monkeypatch.setattr(rf, "_http_get", lambda *a, **k: (_ for _ in ()).throw(OSError("no network")))


def test_theme_mapping():
    for kw, node in [("军工", "sw1_650000"), ("电网设备", "sw2_630800"), ("煤炭", "sw1_740000"),
                     ("银行", "sw1_480000"), ("半导体", "sw2_270100"), ("油气", "sw2_750100"),
                     ("电力", "sw2_410100"), ("石油", "sw1_750000")]:
        assert node in [n for n, _ in bma.resolve_nodes([kw])]


def test_dynamic_node_match():
    nodes = lambda: [("sw2_999900", "光伏设备"), ("sw3_999901", "光伏电池组件")]
    assert bma.resolve_nodes(["光伏"], node_fetcher=nodes)[0][0] == "sw2_999900"


def test_alt_members_cache_stale_fallback():
    codes, used = bma.alt_theme_members(["电网设备"], member_fetcher=lambda n: ["600406", "000400"])
    assert codes == {"600406", "000400"} and used
    # 缓存过期 + 接口失败 → 过期兜底
    rec = rf._load("sina_sw_members", "sw2_630800"); rec["ts"] -= 2 * 86400
    rf._save("sina_sw_members", "sw2_630800", rec["value"])
    import pickle
    p = rf._path("sina_sw_members", "sw2_630800"); d = pickle.loads(p.read_bytes()); d["ts"] -= 2 * 86400
    p.write_bytes(pickle.dumps(d))
    def boom(n): raise TimeoutError("x")
    codes2, _ = bma.alt_theme_members(["电网设备"], member_fetcher=boom)
    assert codes2 == {"600406", "000400"}
    assert rf.LAST_STATUS[("sina_sw_members", "sw2_630800")] == "stale_cache"


def test_alt_members_total_failure():
    def boom(n): raise TimeoutError("x")
    assert bma.alt_theme_members(["电网设备"], member_fetcher=boom) == (set(), [])


def _engine(monkeypatch, alt_codes):
    from src.scanner.scanner_engine import ScannerEngine
    eng = ScannerEngine.__new__(ScannerEngine)
    eng.market_cache = SimpleNamespace(get_stocks_by_industry=lambda n: [], get_stocks_by_concept=lambda n: [])
    monkeypatch.setattr(eng, "_resolve_industry_names", lambda x: x, raising=False)
    monkeypatch.setattr(eng, "_resolve_concept_names", lambda x: x, raising=False)
    monkeypatch.setattr(bma, "alt_theme_members", lambda kws: (set(alt_codes), ["申万·电网设备(live)"] if alt_codes else []))
    eng._theme_query = "电网设备"
    return eng


def test_scanner_uses_alt_source(monkeypatch):
    eng = _engine(monkeypatch, {"600406"})
    df = pd.DataFrame({"代码": ["600406", "000001"]})
    out = eng._apply_market_theme_filter(df, [], ["特高压"])
    assert list(out["代码"]) == ["600406"]
    assert eng.last_theme_filter_status == "alt_source" and not eng.last_theme_filter_failed


def test_scanner_market_fallback(monkeypatch):
    eng = _engine(monkeypatch, set())
    df = pd.DataFrame({"代码": ["600406", "000001"]})
    out = eng._apply_market_theme_filter(df, [], ["特高压"])
    assert len(out) == 2 and eng.last_theme_filter_failed


def _pipeline():
    spec = importlib.util.spec_from_file_location("pipeline_mod_tf", ROOT / "scripts" / "pipeline.py")
    m = importlib.util.module_from_spec(spec); sys.path.insert(0, str(ROOT)); spec.loader.exec_module(m)
    return m


def test_diversify_and_recent(tmp_path):
    m = _pipeline()
    C = lambda code, ind: SimpleNamespace(stock_code=code, industry=ind, stock_name=code)
    cands = [C("1", "银行"), C("2", "银行"), C("3", "银行"), C("4", "煤炭"), C("5", "煤炭"), C("6", "电力")]
    out = m.diversify_fallback(cands, recent={"1"}, industry_of=lambda c: None, cap=2, penalty=5)
    codes = [c.stock_code for c, _, _ in out]
    assert codes.count("1") + codes.count("2") + codes.count("3") == 2
    assert codes == ["2", "3", "4", "5", "6"]  # 近期重复的 1 被降权后被行业上限挤出
    (tmp_path / "pipeline_a.json").write_text('{"candidates":[{"code":"9"}]}', encoding="utf-8")
    assert m.recent_pipeline_codes(tmp_path) == {"9"}


def test_report_labels():
    m = _pipeline()
    R = {"generated_at": "x", "data_source": "t", "nav": 1, "stages": [], "candidates": [
        {"code": "1", "name": "A", "price": 1, "position_action": "HOLD", "decision": "WATCH", "score": 0.1,
         "market_fallback": True, "recent_repeat": True}],
         "cand_meta": {"filter_status": "market_fallback", "filter_status_cn": m.FALLBACK_LABEL}}
    md = m.fmt_md(R)
    assert "未按板块过滤（接口失败，已退回全市场）" in md.splitlines()[0]
    assert "market_fallback" in md and "〔全市场兜底·未按板块〕" in md and "〔近期重复〕" in md
