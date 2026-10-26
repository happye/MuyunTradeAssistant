"""T05-K 缓存 payload 资格门的真实消费方接线回归。

覆盖真实临时 JSON → 共用 cache.get → auto_score / start.py 展示与报告 /
CLI 两个缓存摘要 / plan update 准备 / 批量费用预览。AI、data_summary、
sector annotation 与宏观展示分别 mock，维度调用计数只统计维度评分器。
不碰真实 ~/.muyun、无网络、无产品 AI。
"""
import copy
import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.benzong import auto_scorer, cache
from src.core.trade_plan import plan_update_prep
from src.data.models import TradePlan
import src.cli.main as cli_main
import start as start_module

DIMS = [
    "industry_prosperity",
    "business_purity",
    "valuation_position",
    "industry_leader",
    "market_recognition",
    "risk_deduction",
]
DATE = "2026-10-27"


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "get_cache_dir", lambda: tmp_path)
    return tmp_path


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _raw_write(root, code, today, dim, payload):
    data = dict(payload)
    data["_cache_version"] = cache.CACHE_VERSION
    data["_cached_at"] = "2026-10-27T09:00:00"
    path = root / f"{code}_{today}_{dim}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _seed_valid(root, code, today, *, scores=None, confidence=0.9,
                sources=("行业数据", "公告"), reasoning="推理", market_turnover=None):
    scores = scores or {}
    for dim in DIMS:
        payload = {
            "score": scores.get(dim, 60),
            "confidence": confidence,
            "sources": list(sources),
            "reasoning": reasoning,
            "warnings": [],
        }
        if dim == "industry_prosperity" and market_turnover is not None:
            payload["market_turnover"] = market_turnover
        _raw_write(root, code, today, dim, payload)


def _summary():
    return {
        "business_intro": "测试业务",
        "announcements": [],
        "industry": {"industry_name": "测试行业", "stock_name": "测试股"},
        "kline": None,
        "market_turnover": 1.0,
        "fetch_status": {},
    }


def _patch_offline(monkeypatch):
    """data_summary / sector annotation / client 分别独立 mock（不冒充维度调用）。"""
    monkeypatch.setattr(auto_scorer.data_provider, "get_data_summary",
                        lambda *a, **k: _summary())
    monkeypatch.setattr(auto_scorer, "_annotate_sector_meta",
                        lambda *a, **k: (None, None))
    monkeypatch.setattr(auto_scorer, "_build_ai_client",
                        lambda config=None: (object(), "t05k-mock-model"))


def _patch_macro(monkeypatch):
    from src.core.exit_signals import macro
    monkeypatch.setattr(macro, "assess_liquidity_state", lambda *a, **k: None)
    monkeypatch.setattr(macro, "assess_market_breadth", lambda *a, **k: None)


def _install_dims(monkeypatch, implementations):
    """只替换维度评分器；未提供的维度一旦被调用即证明缓存门失效。"""
    counts = {dim: 0 for dim in DIMS}
    dims = {}
    for dim in DIMS:
        if dim not in implementations:
            def _unexpected(*args, _name=dim, **kwargs):
                raise AssertionError(f"{_name} 不应调用：应命中缓存")
            dims[dim] = _unexpected
            continue
        result = implementations[dim]

        def _score(*args, _name=dim, _result=result, **kwargs):
            counts[_name] += 1
            return copy.deepcopy(_result)

        dims[dim] = _score
    monkeypatch.setattr(auto_scorer, "_lazy_load_dimensions", lambda: dims)
    return counts


def _run_auto(code, today, force_refresh=False):
    return auto_scorer.auto_score(
        code, "测试股", force_refresh=force_refresh,
        ai_client=object(), ai_model="t05k-mock-model", rag_service=None,
        data_summary=_summary(), today=today,
    )


# ── auto_score：坏一维 + 五维复用 ───────────────────────────────────

def test_bad_dimension_retried_once_then_reused(cache_root, monkeypatch):
    code = "T05K10"
    _seed_valid(cache_root, code, DATE, scores={d: 60.0 for d in DIMS})
    _raw_write(cache_root, code, DATE, "business_purity",
               {"score": 88.0, "confidence": 0, "sources": [], "reasoning": "malformed"})
    _patch_offline(monkeypatch)
    counts = _install_dims(monkeypatch, {
        "business_purity": {"score": 33.0, "confidence": 0.6, "sources": [],
                            "reasoning": "fresh retry", "warnings": []},
    })

    r1 = _run_auto(code, DATE)
    assert set(r1.cache_hits) == set(DIMS) - {"business_purity"}
    assert counts["business_purity"] == 1
    assert sum(counts.values()) == 1, "坏一维只重算一次，其他五维必须复用"
    assert r1.dimensions_meta["business_purity"]["score"] == 33.0
    assert r1.score.business_purity == 33.0, "总分不得采用坏缓存里的 88"
    assert r1.score.business_purity != 88.0

    # 重试成功且 confidence>0 → 按既有规则落盘；下一次 6/6 复用，不再重算
    assert cache.get(code, DATE, "business_purity") is not None
    r2 = _run_auto(code, DATE)
    assert set(r2.cache_hits) == set(DIMS)
    assert sum(counts.values()) == 1


def test_zero_confidence_failure_not_persisted_and_only_that_dim_retried(
        cache_root, monkeypatch):
    code = "T05K11"
    _seed_valid(cache_root, code, DATE, scores={d: 60.0 for d in DIMS})
    bad = _raw_write(cache_root, code, DATE, "business_purity",
                     {"score": 77.0, "confidence": 0, "sources": [],
                      "reasoning": "malformed"})
    bad_before = bad.read_bytes()
    _patch_offline(monkeypatch)
    counts = _install_dims(monkeypatch, {
        "business_purity": {"score": 50.0, "confidence": 0.0, "sources": [],
                            "reasoning": "degraded retry", "warnings": ["降级"]},
    })

    r1 = _run_auto(code, DATE)
    assert set(r1.cache_hits) == set(DIMS) - {"business_purity"}
    assert counts["business_purity"] == 1
    assert bad.read_bytes() == bad_before, "conf=0 的失败结果不得写缓存"
    assert cache.get(code, DATE, "business_purity") is None

    r2 = _run_auto(code, DATE)
    assert set(r2.cache_hits) == set(DIMS) - {"business_purity"}
    assert counts["business_purity"] == 2, "第二次只应重试坏维一次"
    assert sum(counts.values()) == 2
    assert bad.read_bytes() == bad_before


def test_force_refresh_recomputes_all_six(cache_root, monkeypatch):
    code = "T05K12"
    _seed_valid(cache_root, code, DATE, scores={d: 60.0 for d in DIMS})
    _patch_offline(monkeypatch)
    counts = _install_dims(monkeypatch, {
        d: {"score": 42.0, "confidence": 0.8, "sources": [], "reasoning": "refresh"}
        for d in DIMS
    })

    r = _run_auto(code, DATE, force_refresh=True)
    assert r.cache_hits == [], "force_refresh 不得复用任何维度缓存"
    assert counts == {d: 1 for d in DIMS}
    assert r.score.industry_prosperity == 42.0
    for dim in DIMS:
        data = cache.get(code, DATE, dim)
        assert data is not None and data["score"] == 42.0


def test_weak_positive_confidence_and_boundary_scores_are_reused(cache_root, monkeypatch):
    code = "T05K13"
    scores = {
        "industry_prosperity": 0,
        "business_purity": 100,
        "valuation_position": 50,
        "industry_leader": 100,
        "market_recognition": 0,
        "risk_deduction": 100,
    }
    _seed_valid(cache_root, code, DATE, scores=scores, confidence=0.3)
    _patch_offline(monkeypatch)
    _install_dims(monkeypatch, {})  # 任一维被调用都会 AssertionError

    r = _run_auto(code, DATE)
    assert len(r.cache_hits) == 6
    assert r.min_confidence == 0.3
    for dim, expected in scores.items():
        assert r.dimensions_meta[dim]["score"] == expected
        assert r.dimensions_meta[dim]["confidence"] == 0.3
    assert r.score.industry_prosperity == 0


# ── start.py 真实展示与报告路径 ─────────────────────────────────────

@pytest.mark.parametrize("analysis_day", ["2026-10-28", "2031-01-03"])
def test_start_display_and_report_render_cached_sources_and_reasoning(
        cache_root, monkeypatch, capsys, tmp_path, analysis_day):
    code = "T05K20"
    _seed_valid(cache_root, code, analysis_day, sources=("行业数据:A", "公告:B"),
                reasoning="第一行推理\n第二行推理")
    _patch_offline(monkeypatch)
    _install_dims(monkeypatch, {})
    _patch_macro(monkeypatch)

    class _FrozenNow:
        @staticmethod
        def now(tz=None):
            return datetime.fromisoformat(f"{analysis_day}T09:00:00")

    monkeypatch.setattr(auto_scorer, "datetime", _FrozenNow)
    monkeypatch.setattr(start_module, "__file__", str(tmp_path / "start.py"))

    start_module._run_benzong_auto(code)

    out = capsys.readouterr().out
    assert "来源：行业数据:A / 公告:B" in out
    assert "第一行推理" in out and "第二行推理" in out
    assert "缓存命中：6/6" in out
    reports = list((tmp_path / "分析报告" / "bz").glob("*.md"))
    assert len(reports) == 1, "真实报告路径应生成一份 Markdown"
    text = reports[0].read_text(encoding="utf-8")
    assert "- 数据来源：" in text
    assert "行业数据:A" in text and "公告:B" in text
    assert "第一行推理" in text and "第二行推理" in text


@pytest.mark.parametrize("analysis_day", ["2026-10-28", "2031-01-03"])
def test_start_display_excludes_malformed_dimension(
        cache_root, monkeypatch, capsys, tmp_path, analysis_day):
    code = "T05K21"
    _seed_valid(cache_root, code, analysis_day, scores={d: 60.0 for d in DIMS},
                sources=("正常来源",), reasoning="正常推理")
    _raw_write(cache_root, code, analysis_day, "business_purity",
               {"score": 88.0, "confidence": 0.9,
                "sources": "RAW_SOURCE_LEAK", "reasoning": "RAW_REASON_LEAK"})
    _patch_offline(monkeypatch)
    counts = _install_dims(monkeypatch, {
        "business_purity": {"score": 44.0, "confidence": 0.0, "sources": [],
                            "reasoning": "重算失败", "warnings": ["坏维降级"]},
    })
    _patch_macro(monkeypatch)

    class _FrozenNow:
        @staticmethod
        def now(tz=None):
            return datetime.fromisoformat(f"{analysis_day}T09:00:00")

    monkeypatch.setattr(auto_scorer, "datetime", _FrozenNow)
    monkeypatch.setattr(start_module, "__file__", str(tmp_path / "start.py"))

    start_module._run_benzong_auto(code)

    out = capsys.readouterr().out
    assert counts["business_purity"] == 1
    assert "缓存命中：5/6" in out
    assert "44.0" in out, "坏维应展示本次重试结果"
    assert "88.0" not in out, "不得展示坏缓存里的分数"
    assert "RAW_SOURCE_LEAK" not in out and "RAW_REASON_LEAK" not in out
    text = list((tmp_path / "分析报告" / "bz").glob("*.md"))[0].read_text(encoding="utf-8")
    assert "44.0" in text
    assert "88.0" not in text
    assert "RAW_SOURCE_LEAK" not in text and "RAW_REASON_LEAK" not in text


# ── CLI 两个缓存摘要 ────────────────────────────────────────────────

def test_cli_cached_brief_and_summary_reject_bad_dimension(
        cache_root, monkeypatch, capsys):
    code = "T05K30"
    today = _today()
    _seed_valid(cache_root, code, today, scores={d: 60.0 for d in DIMS},
                market_turnover=0)  # 显式 0 合法；消费方沿用 or 1.0 回落
    assert cli_main._cached_benzong_brief(code)[0] == "full"

    _raw_write(cache_root, code, today, "business_purity",
               {"score": 88.0, "confidence": 0, "sources": [], "reasoning": "bad"})
    brief = cli_main._cached_benzong_brief(code)
    assert brief == ("partial", "5/6"), "坏维必须从摘要计数里排除"
    _patch_macro(monkeypatch)
    cli_main._print_benzong_summary(code, "测试股")
    out = capsys.readouterr().out
    assert "部分维度已评分（5/6）" in out
    assert "/100" not in out, "缺一维时不得拼出总分"
    assert "88" not in out, "不得展示坏缓存分数"

    for dim in DIMS:
        _raw_write(cache_root, code, today, dim,
                   {"score": 50, "confidence": 0, "sources": [], "reasoning": "bad"})
    assert cli_main._cached_benzong_brief(code) is None
    cli_main._print_benzong_summary(code, "测试股")
    out = capsys.readouterr().out
    assert "今日未评分" in out


def test_cli_summary_full_cache_uses_qualified_payloads(
        cache_root, monkeypatch, capsys):
    code = "T05K31"
    today = _today()
    _seed_valid(cache_root, code, today, scores={d: 60.0 for d in DIMS},
                market_turnover=0)
    _patch_macro(monkeypatch)
    cli_main._print_benzong_summary(code, "测试股")
    out = capsys.readouterr().out
    assert "评分" in out and "/100" in out
    assert "缓存命中" in out


# ── plan update 准备：坏景气度必须保持缺失 ──────────────────────────

def _plan():
    return TradePlan(
        plan_id="p1", opened_at="2020-01-01", why_buy="w", when_buy="t",
        how_much=0.2, when_sell_targets=[10.0], when_sell_invalidate=["x"],
        locked_initial_stop=8.0, current_stop=9.0, max_hold_days=300,
        fundamental_outlook="neutral", mode="qizong",
    )


def _sd():
    return SimpleNamespace(atr_14=1.5, ma20=10.0, ma60=9.0, price=11.0,
                           change_pct=1.0, macd=None, low_60d=8.0)


def _prepare_update(code, today):
    pos = SimpleNamespace(trade_plan=_plan(), entry_price=10,
                          current_ratio=0.2, stock_name="测试股")
    pm = SimpleNamespace(get_position=lambda c: pos)

    class FakeGen:
        def generate(self, **kwargs):
            return _plan(), {}

    def score_boom(*args, **kwargs):
        raise AssertionError("update 路径不得重跑评分")

    return plan_update_prep.prepare_generate_or_update_plan(
        pm, code, update=True,
        get_stock_data=lambda c: _sd(),
        auto_score_fn=score_boom,
        get_rag_service=lambda: None,
        generator_factory=lambda rag: FakeGen(),
        today=today,
    )


def test_plan_update_bad_industry_stays_missing(cache_root):
    code = "T05K40"
    _seed_valid(cache_root, code, DATE, scores={d: 60.0 for d in DIMS})
    bad = _raw_write(cache_root, code, DATE, "industry_prosperity",
                     {"score": 55.0, "confidence": 0, "sources": [],
                      "reasoning": "bad"})
    bad_before = bad.read_bytes()

    oc = _prepare_update(code, DATE)

    assert oc.status == plan_update_prep.PU_READY_UPDATE
    assert oc.industry_prosperity is None, "坏景气度不得作为计划更新输入"
    assert oc.prosperity_note == "unknown"
    assert oc.prosperity_from_cache is False
    assert bad.read_bytes() == bad_before


def test_plan_update_valid_industry_from_qualified_cache(cache_root):
    code = "T05K41"
    _seed_valid(cache_root, code, DATE, scores={d: 60.0 for d in DIMS})
    _raw_write(cache_root, code, DATE, "industry_prosperity",
               {"score": 55.0, "confidence": 0.8, "sources": [],
                "reasoning": "ok"})

    oc = _prepare_update(code, DATE)

    assert oc.status == plan_update_prep.PU_READY_UPDATE
    assert oc.industry_prosperity == 55.0
    assert oc.prosperity_note == "cache"
    assert oc.prosperity_from_cache is True


# ── 批量费用预览：坏缓存必须计为未命中，无隐藏刷新 ──────────────────

def test_fee_preview_requires_all_six_qualified_dimensions(
        cache_root, monkeypatch, capsys):
    code = "T05K50"
    today = _today()
    _seed_valid(cache_root, code, today, scores={d: 60.0 for d in DIMS})
    analyzed = []
    monkeypatch.setattr(cli_main, "benzong_batch_analyze",
                        lambda items, **kw: analyzed.append((items, kw)))
    prompts = []
    monkeypatch.setattr("builtins.input",
                        lambda prompt="": prompts.append(prompt) or "n")

    start_module.run_cli("benzong_multi", {"codes": [code], "refresh": False})
    out = capsys.readouterr().out
    assert "全部命中今日缓存" in out
    assert prompts == [], "全命中时不得多出确认"
    assert len(analyzed) == 1
    assert analyzed[0][1].get("force_refresh") is False

    bad = _raw_write(cache_root, code, today, "business_purity",
                     {"score": 88.0, "confidence": 0, "sources": [],
                      "reasoning": "bad"})
    before = {p.name: p.read_bytes() for p in cache_root.glob("*.json")}
    start_module.run_cli("benzong_multi", {"codes": [code], "refresh": False})
    out = capsys.readouterr().out
    assert "其中 1 只今天未评分" in prompts[0], "坏 payload 必须在费用预览里计为未命中"
    assert len(prompts) == 1, "沿用既有一个 y/N 确认，不新增确认"
    assert "已取消" in out
    assert len(analyzed) == 1, "取消后不得进入批量评分"
    after = {p.name: p.read_bytes() for p in cache_root.glob("*.json")}
    assert after == before, "费用预览/取消不得隐藏刷新或改写缓存"
    assert bad.read_bytes() == before[bad.name]
