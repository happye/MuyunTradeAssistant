"""笨总自动评分端到端测试（v0.8.6.2）

不依赖真网络/真 AI — 全部 mock。
覆盖：
1. auto_score 正常流程（6 维 mock + data_summary mock）
2. 缓存命中跳过 AI
3. 数据缺失降级（confidence=0 + warning）
4. AI 不可用降级
5. 一票否决（risk_deduction invalidate）
6. RAG 加载失败不影响评分
"""

import os
import sys
import unittest.mock as mock
import pandas as pd

# 确保项目根在 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.benzhong import auto_score, cache


def _make_mock_ai(scores_by_dim_keyword):
    """构造 mock AI client，按 system prompt 关键词返回不同 score"""
    ai = mock.MagicMock()
    def fake_create(**kw):
        sys_prompt = kw.get("messages", [{}])[0].get("content", "")
        for keyword, score in scores_by_dim_keyword.items():
            if keyword in sys_prompt:
                resp = mock.MagicMock()
                resp.choices = [mock.MagicMock()]
                resp.choices[0].message.content = (
                    '{"score": %d, "confidence": 0.9, "reasoning": "mock %s"}'
                    % (score, keyword)
                )
                return resp
        # 兜底
        resp = mock.MagicMock()
        resp.choices = [mock.MagicMock()]
        resp.choices[0].message.content = '{"score": 50, "confidence": 0.5, "reasoning": "fallback"}'
        return resp
    ai.chat.completions.create.side_effect = fake_create
    return ai


def _make_full_data_summary():
    return {
        "business_intro": "茅台 99% 主营白酒",
        "announcements": [{"title": "茅台Q1+15%", "date": "2026-04-15", "content": "", "source": ""}],
        "industry": {"industry_name": "白酒", "stock_name": "贵州茅台", "total_market_cap": "2.1万亿"},
        "kline": pd.DataFrame({"日期": ["2023-06-01", "2026-06-20"], "收盘": [1300, 1500], "最低": [1300, 1300]}),
        "market_turnover": 1.0,
        "fetch_status": {"business_intro": True, "announcements": True, "industry": True, "kline": True, "market_turnover": True},
    }


def test_normal_flow():
    """1. 正常流程：6 维 mock + 全数据 → 总分 + grade"""
    cache.clear("TEST001")
    ai = _make_mock_ai({
        "行业景气度": 85, "业务纯度": 95, "细分行业龙头": 90,
        "市场辨识度": 100, "个股风险值": 10,
    })
    r = auto_score("TEST001", "测试股", ai_client=ai, rag_service=None,
                   data_summary=_make_full_data_summary(), today="2026-06-20", force_refresh=True)
    assert r.score.total_score > 0
    assert r.overall_confidence > 0.5
    assert len(r.dimensions_meta) == 6
    print(f"✓ 正常流程: 总分={r.score.total_score} 级别={r.score.grade()} conf={r.overall_confidence}")
    cache.clear("TEST001")


def test_cache_hit():
    """2. 缓存命中：第二次跑应 6/6 命中，AI 不再调用"""
    cache.clear("TEST002")
    ai = _make_mock_ai({"行业景气度": 80, "业务纯度": 90, "细分行业龙头": 85,
                        "市场辨识度": 95, "个股风险值": 5})
    # 第一次：全跑
    auto_score("TEST002", "测试", ai_client=ai, rag_service=None,
               data_summary=_make_full_data_summary(), today="2026-06-20", force_refresh=True)
    call_count_1 = ai.chat.completions.create.call_count
    # 第二次：应命中缓存
    r2 = auto_score("TEST002", "测试", ai_client=ai, rag_service=None,
                    data_summary=_make_full_data_summary(), today="2026-06-20", force_refresh=False)
    call_count_2 = ai.chat.completions.create.call_count
    assert len(r2.cache_hits) == 6, f"应 6/6 命中, 实际 {len(r2.cache_hits)}"
    assert call_count_2 == call_count_1, "缓存命中时不应再调 AI"
    print(f"✓ 缓存命中: {len(r2.cache_hits)}/6, AI 调用次数 {call_count_1}→{call_count_2} (未增加)")
    cache.clear("TEST002")


def test_data_missing_degradation():
    """3. 数据缺失：fetch_status 全 False → 各维度 confidence=0 + warning"""
    cache.clear("TEST003")
    ai = _make_mock_ai({"行业景气度": 70})
    empty_summary = {
        "business_intro": None, "announcements": [], "industry": None,
        "kline": None, "market_turnover": None,
        "fetch_status": {"business_intro": False, "announcements": False, "industry": False,
                         "kline": False, "market_turnover": False},
    }
    r = auto_score("TEST003", "测试", ai_client=ai, rag_service=None,
                   data_summary=empty_summary, today="2026-06-20", force_refresh=True)
    # 多数维度应 confidence=0
    low_conf_dims = [d for d, m in r.dimensions_meta.items() if m.get("confidence", 0) < 0.5]
    assert len(low_conf_dims) >= 3, f"数据缺失应有 >=3 维度 conf<0.5, 实际 {len(low_conf_dims)}"
    assert len(r.warnings) > 0, "应有警告"
    print(f"✓ 数据缺失降级: {len(low_conf_dims)} 维度 conf<0.5, 警告 {len(r.warnings)} 条")
    cache.clear("TEST003")


def test_ai_unavailable():
    """4. AI 不可用：ai_client=None → 5 个 AI 维度全降级 conf=0"""
    cache.clear("TEST004")
    r = auto_score("TEST004", "测试", ai_client=None, rag_service=None,
                   data_summary=_make_full_data_summary(), today="2026-06-20", force_refresh=True)
    # valuation_position 是纯算法不依赖 AI，conf 应=1.0
    assert r.dimensions_meta["valuation_position"]["confidence"] == 1.0
    # 其他 5 个 AI 维度 conf 应=0
    ai_dims = ["industry_prosperity", "business_purity", "industry_leader",
               "market_recognition", "risk_deduction"]
    for d in ai_dims:
        assert r.dimensions_meta[d]["confidence"] == 0.0, f"{d} 应 conf=0"
    print(f"✓ AI 不可用: valuation conf=1.0 (纯算法), 5 个 AI 维度 conf=0")
    cache.clear("TEST004")


def test_invalidate_redline():
    """5. 一票否决：risk_deduction 返回 invalidate=true"""
    cache.clear("TEST005")
    ai = mock.MagicMock()
    def fake_create(**kw):
        sys_prompt = kw.get("messages", [{}])[0].get("content", "")
        resp = mock.MagicMock()
        resp.choices = [mock.MagicMock()]
        if "个股风险值" in sys_prompt:
            resp.choices[0].message.content = (
                '{"score": 100, "confidence": 0.95, "reasoning": "财务造假立案", "invalidate": true}'
            )
        else:
            resp.choices[0].message.content = '{"score": 80, "confidence": 0.9, "reasoning": "ok"}'
        return resp
    ai.chat.completions.create.side_effect = fake_create
    r = auto_score("TEST005", "测试", ai_client=ai, rag_service=None,
                   data_summary=_make_full_data_summary(), today="2026-06-20", force_refresh=True)
    assert r.invalidate is True, "应触发一票否决"
    assert any("一票否决" in w for w in r.warnings), "警告应含一票否决"
    print(f"✓ 一票否决: invalidate={r.invalidate}, 警告含红线提示")
    cache.clear("TEST005")


def test_rag_failure_tolerant():
    """6. RAG 加载失败不影响评分（industry_prosperity 仍能跑）"""
    cache.clear("TEST006")
    ai = _make_mock_ai({"行业景气度": 75, "业务纯度": 85, "细分行业龙头": 80,
                        "市场辨识度": 90, "个股风险值": 5})
    # rag_service 是个会抛异常的 mock
    bad_rag = mock.MagicMock()
    bad_rag.get_context.side_effect = Exception("RAG 故障")
    r = auto_score("TEST006", "测试", ai_client=ai, rag_service=bad_rag,
                   data_summary=_make_full_data_summary(), today="2026-06-20", force_refresh=True)
    # industry_prosperity 应仍能出分（RAG 失败被吞）
    assert r.dimensions_meta["industry_prosperity"]["score"] == 75
    assert r.dimensions_meta["industry_prosperity"]["confidence"] > 0
    print(f"✓ RAG 故障容忍: industry_prosperity 仍 score={r.dimensions_meta['industry_prosperity']['score']}")
    cache.clear("TEST006")


if __name__ == "__main__":
    print("\n=== 笨总自动评分 v0.8.6.2 端到端测试 ===\n")
    test_normal_flow()
    test_cache_hit()
    test_data_missing_degradation()
    test_ai_unavailable()
    test_invalidate_redline()
    test_rag_failure_tolerant()
    print("\n=== 全部 6 项 PASS ===")
