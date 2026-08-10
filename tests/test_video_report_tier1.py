"""笨总视频理念优化报告 第一档 测试（2026-08-10）

覆盖：
1.1 宏观流动性状态 assess_liquidity_state（笨总实操阈值 0.8/1.3/1.5 万亿）
1.2 事件真实性一票否决（to_ai_modifier_result 真实性<30 -> neutral 空操作）
1.3 自主可控强制剑宗（_mode_from_grade is_self_reliance gate + detect_self_reliance）

运行：PYTHONUTF8=1 ./.venv/Scripts/python.exe tests/test_video_report_tier1.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.exit_signals.macro import assess_liquidity_state, check_macro_top_signal
from src.core.benzong.self_reliance import detect_self_reliance, detect_self_reliance_from_summary
from src.core.trade_plan.generator import _mode_from_grade
from src.data.models import MarketEvent, AIModifierResult


def test_liquidity_state_tiers():
    """1.1 笨总实操流动性分档：0.8枯竭/1.3风险线/1.5充沛"""
    assert assess_liquidity_state(0.7)[0] == "枯竭", "<0.8 应判枯竭"
    assert assess_liquidity_state(0.8)[0] == "偏紧", "0.8 边界不算枯竭(非<)"
    assert assess_liquidity_state(1.0)[0] == "偏紧", "0.8-1.3 偏紧"
    assert assess_liquidity_state(1.3)[0] == "正常", "1.3 边界算正常(非<)"
    assert assess_liquidity_state(1.4)[0] == "正常", "1.3-1.5 正常"
    assert assess_liquidity_state(1.5)[0] == "充沛", "1.5 边界算充沛"
    assert assess_liquidity_state(2.0)[0] == "充沛", ">1.5 充沛"
    print("✓ 流动性分档 0.8/1.3/1.5 阈值正确")


def test_liquidity_none_on_failure():
    """1.1 获取失败返回 None 不崩"""
    assert assess_liquidity_state(None) is None or isinstance(assess_liquidity_state(None), tuple)
    print("✓ 流动性 None 输入不崩")


def test_macro_top_signal_unchanged():
    """1.1 10万亿极端顶信号保留不动（笨总原话，不擅改）"""
    assert check_macro_top_signal(11.0) is not None, "破10万亿仍触发"
    assert check_macro_top_signal(3.5) is None, "3.5万亿不触发极端顶"
    print("✓ 10万亿极端顶信号保留(笨总原话)")


def test_self_reliance_detection():
    """1.3 自主可控（追赶类）关键词检测"""
    assert detect_self_reliance("半导体", "中芯国际") is True
    assert detect_self_reliance("集成电路", "寒武纪") is True
    assert detect_self_reliance("", "紫光国微") is False, "名字不含关键词+无行业 -> False（heuristic 局限，需行业）"
    assert detect_self_reliance("芯片设计", "海光信息") is True
    assert detect_self_reliance("煤炭开采", "中国神华") is False, "煤炭不算自主可控"
    assert detect_self_reliance("光模块", "中际旭创") is False, "光模块是遥遥领先非自主可控"
    assert detect_self_reliance("白酒", "贵州茅台") is False
    assert detect_self_reliance("", "") is False
    print("✓ 自主可控检测：半导体/芯片 True，煤炭/光模块/白酒 False")


def test_self_reliance_from_summary():
    """1.3 从 data_summary 检测"""
    assert detect_self_reliance_from_summary({"industry": {"industry_name": "半导体"}}) is True
    assert detect_self_reliance_from_summary({"industry": {"industry_name": "白酒"}}) is False
    assert detect_self_reliance_from_summary({}) is False
    assert detect_self_reliance_from_summary(None) is False
    print("✓ data_summary 自主可控检测")


def test_mode_self_reliance_gate():
    """1.3 自主可控 A+S2 强制剑宗（核心改动，治气宗死扛）"""
    # A 级 + S2 上升期，正常 -> 气宗
    assert _mode_from_grade("A", 70, stage="S2", is_self_reliance=False) == "qizong"
    # A 级 + S2 上升期 + 自主可控 -> 强制剑宗（笨总教学九：不长持）
    assert _mode_from_grade("A", 70, stage="S2", is_self_reliance=True) == "jianzong", \
        "自主可控 A+S2 必须强制剑宗"
    # A 级 + S1 筑底 + 自主可控 -> 剑宗
    assert _mode_from_grade("A", 70, stage="S1", is_self_reliance=True) == "jianzong"
    # 自主可控不影响大前提失效（景气<=0 仍 None）
    assert _mode_from_grade("A", 0, stage="S2", is_self_reliance=True) is None, \
        "大前提失效优先于自主可控闸门"
    # 自主可控不影响 S3/S4（已是剑宗）
    assert _mode_from_grade("A", 70, stage="S3", is_self_reliance=True) == "jianzong"
    # B 级无视自主可控（本来就剑宗）
    assert _mode_from_grade("B", 70, is_self_reliance=True) == "jianzong"
    print("✓ 自主可控 A+S2 强制剑宗（大前提/S3闸门优先级正确）")


def test_authenticity_veto_neutral():
    """1.2 真实性<30 -> to_ai_modifier_result 返回 neutral（信号一票否决）"""
    # 构造一个真实性极低的事件
    ev = MarketEvent(
        event_type="policy", sentiment="bullish", impact_level=5,
        scope="market", duration="long", source="小道消息", summary="某重大利好传闻",
        four_elements={"authenticity": 15, "virality": 80, "scale": 70, "timeliness": 60},
    )
    from src.core.event_layer import EventLayer
    # 不需要完整初始化，直接调方法
    layer = EventLayer.__new__(EventLayer)
    r = layer.to_ai_modifier_result(ev)
    assert r.sentiment == "neutral", f"真实性<30 应返回 neutral，实际 {r.sentiment}"
    assert r.score_adjustment == 0.0, "真实性存疑事件不应调整分数"
    assert r.position_cap == 1.0, "真实性存疑事件不应限仓(1.0=不限制)"
    assert r.adjusted is False, "真实性存疑事件不应标记为已调整"
    assert "真实性存疑" in (r.summary or ""), "summary 应含真实性存疑标记"
    print("✓ 真实性<30 -> neutral 空操作（信号一票否决，保留展示提示）")


def test_authenticity_pass_through():
    """1.2 真实性>=30 正常流转（不一票否决）"""
    ev = MarketEvent(
        event_type="policy", sentiment="bearish", impact_level=4,
        scope="market", duration="medium", source="权威媒体",
        four_elements={"authenticity": 80, "virality": 70, "scale": 60, "timeliness": 50},
    )
    from src.core.event_layer import EventLayer
    layer = EventLayer.__new__(EventLayer)
    r = layer.to_ai_modifier_result(ev)
    assert r.sentiment == "bearish", "真实性达标应保留原 sentiment"
    assert r.adjusted is True, "应正常调整"
    print("✓ 真实性>=30 正常流转（不一票否决）")


def main():
    tests = [
        test_liquidity_state_tiers,
        test_liquidity_none_on_failure,
        test_macro_top_signal_unchanged,
        test_self_reliance_detection,
        test_self_reliance_from_summary,
        test_mode_self_reliance_gate,
        test_authenticity_veto_neutral,
        test_authenticity_pass_through,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
    print()
    if failed:
        print(f"=== 失败 {failed} / {len(tests)} ===")
        sys.exit(1)
    print(f"=== 全部 {len(tests)} 项 PASS ===")


if __name__ == "__main__":
    main()
