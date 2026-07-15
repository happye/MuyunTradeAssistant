"""ISS-055: 景气度闸门降级 + 低置信度 -> 透明度警告 单测

不动 effective_grade 公式（笨总域）。仅当评级受景气度闸门降级 且 景气判定置信度低时，
auto_score 额外追加一条警告，诚实暴露"降级依赖最不可靠维度"。

cases:
1. ip=30 + 景气 conf=0.3 -> 警告触发（含"景气度闸门降级"+"置信度"）
2. ip=30 + 景气 conf=0.9 -> 不触发（降级有据，不扰民）
3. ip=85 + 景气 conf=0.3 -> 不触发（ip>50 无降级）
4. ip=0  + 景气 conf=0.3 -> precondition_warning(大前提失效) 触发，本警告不触发（ip>0 守卫，避重复）

跑法: PYTHONUTF8=1 PYTHONPATH=. uv run python tests/test_iss055_prosperity_conf_warning.py
"""

import os
import sys
import unittest.mock as mock
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.benzong import auto_score, cache


def _make_ai(prosperity_score, prosperity_conf, other_score=98, other_conf=0.9):
    """mock AI：行业景气度维度独立设 score/conf，其余维度统一高分高置信。"""
    ai = mock.MagicMock()

    def fake_create(**kw):
        sys_prompt = kw.get("messages", [{}])[0].get("content", "")
        resp = mock.MagicMock()
        resp.choices = [mock.MagicMock()]
        if "行业景气度" in sys_prompt:
            resp.choices[0].message.content = (
                '{"score": %d, "confidence": %.2f, "reasoning": "pros"}'
                % (prosperity_score, prosperity_conf))
        else:
            resp.choices[0].message.content = (
                '{"score": %d, "confidence": %.2f, "reasoning": "ok"}'
                % (other_score, other_conf))
        return resp

    ai.chat.completions.create.side_effect = fake_create
    return ai


def _full_data_summary():
    return {
        "business_intro": "测试股 99% 主营 X",
        "announcements": [{"title": "测试公告", "date": "2026-04-15", "content": "", "source": ""}],
        "industry": {"industry_name": "测试行业", "stock_name": "测试股", "total_market_cap": "1万亿"},
        "kline": pd.DataFrame({"日期": ["2023-06-01", "2026-06-20"], "收盘": [100, 130], "最低": [100, 100]}),
        "market_turnover": 1.0,
        "fetch_status": {"business_intro": True, "announcements": True, "industry": True, "kline": True, "market_turnover": True},
    }


def _has_gate_warning(r):
    return any("景气度闸门降级" in w and "置信度" in w for w in r.warnings)


def test_low_ip_low_conf_warns():
    cache.clear("ISS055_1")
    ai = _make_ai(prosperity_score=30, prosperity_conf=0.3)
    r = auto_score("ISS055_1", "测试", ai_client=ai, rag_service=None,
                   data_summary=_full_data_summary(), today="2026-06-20", force_refresh=True)
    assert r.score.effective_grade() != r.score.grade(), "ip=30 应触发降级（eff != raw）"
    assert _has_gate_warning(r), f"ip=30+低置信应警告，实际 warnings={r.warnings}"
    print(f"✓ ip=30 conf=0.3 -> 警告触发（{r.score.grade()}->{r.score.effective_grade()}）")
    cache.clear("ISS055_1")


def test_low_ip_high_conf_no_warn():
    cache.clear("ISS055_2")
    ai = _make_ai(prosperity_score=30, prosperity_conf=0.9)
    r = auto_score("ISS055_2", "测试", ai_client=ai, rag_service=None,
                   data_summary=_full_data_summary(), today="2026-06-20", force_refresh=True)
    assert r.score.effective_grade() != r.score.grade(), "ip=30 应触发降级"
    assert not _has_gate_warning(r), "ip=30+高置信不应警告（降级有据）"
    print(f"✓ ip=30 conf=0.9 -> 不警告（降级有据）")
    cache.clear("ISS055_2")


def test_high_ip_no_cap_no_warn():
    cache.clear("ISS055_3")
    ai = _make_ai(prosperity_score=85, prosperity_conf=0.3)
    r = auto_score("ISS055_3", "测试", ai_client=ai, rag_service=None,
                   data_summary=_full_data_summary(), today="2026-06-20", force_refresh=True)
    assert r.score.effective_grade() == r.score.grade(), "ip=85 无降级"
    assert not _has_gate_warning(r), "ip>50 无降级不应警告"
    print(f"✓ ip=85 conf=0.3 -> 不警告（无降级）")
    cache.clear("ISS055_3")


def test_ip_zero_precondition_only():
    """ip=0 -> precondition_warning(大前提失效) 触发，本警告不重复触发。"""
    cache.clear("ISS055_4")
    ai = _make_ai(prosperity_score=0, prosperity_conf=0.3)
    r = auto_score("ISS055_4", "测试", ai_client=ai, rag_service=None,
                   data_summary=_full_data_summary(), today="2026-06-20", force_refresh=True)
    assert any("大前提失效" in w for w in r.warnings), "ip=0 应有大前提警告"
    assert not _has_gate_warning(r), "ip=0 不应重复触发降级警告"
    print(f"✓ ip=0 -> 仅大前提警告，降级警告不重复")
    cache.clear("ISS055_4")


def test_data_missing_pros_conf_zero_warns():
    """H1 修复：景气数据缺失（pros_conf=0，ip=50 兜底）-> 无条件警告，不依赖 effective!=grade。"""
    cache.clear("ISS055_5")
    ai = _make_ai(prosperity_score=50, prosperity_conf=0.0, other_score=85)
    r = auto_score("ISS055_5", "测试", ai_client=ai, rag_service=None,
                   data_summary=_full_data_summary(), today="2026-06-20", force_refresh=True)
    assert any("景气度数据缺失" in w for w in r.warnings), f"pros_conf=0 应警告数据缺失, warnings={r.warnings}"
    print(f"✓ pros_conf=0 (数据缺失/兜底50) -> 警告（H1 修复，不依赖降级触发）")
    cache.clear("ISS055_5")


if __name__ == "__main__":
    print("\n=== ISS-055 景气度闸门降级 + 低置信度透明度警告 单测 ===\n")
    test_low_ip_low_conf_warns()
    test_low_ip_high_conf_no_warn()
    test_high_ip_no_cap_no_warn()
    test_ip_zero_precondition_only()
    test_data_missing_pros_conf_zero_warns()
    print("\n=== 全部 5 项 PASS ===")
