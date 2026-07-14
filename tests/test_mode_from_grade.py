"""ISS-051: _mode_from_grade 个股级 stage 闸门单测

验证核心改动: A 级股若个股已处 S3 顶部 / S4 下跌, 降剑宗 (不死扛下跌);
S1 筑底 / S2 上升期才气宗长持。数据不足保守保持气宗。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.trade_plan.generator import _mode_from_grade


def test_a_grade_qizong_on_s1_s2():
    # A 级 + 景气>0 + 上升/筑底期 -> 气宗
    assert _mode_from_grade("A", 60, stage="S2") == "qizong"
    assert _mode_from_grade("A", 60, stage="S1") == "qizong"
    print("✓ A+S1/S2 -> qizong (上升/筑底气宗)")


def test_a_grade_jianzong_on_s3_s4():
    # 核心改动: A 级但个股已见顶/下跌 -> 降剑宗, 不死扛
    assert _mode_from_grade("A", 60, stage="S3") == "jianzong"
    assert _mode_from_grade("A", 60, stage="S4") == "jianzong"
    print("✓ A+S3/S4 -> jianzong (顶部/下跌降剑宗, 核心改动)")


def test_a_grade_data_missing_keeps_qizong():
    # 数据不足 (stage None/"?") 保守保持原行为, 不降级
    assert _mode_from_grade("A", 60, stage=None) == "qizong"
    assert _mode_from_grade("A", 60, stage="?") == "qizong"
    assert _mode_from_grade("A", 60) == "qizong"  # stage 默认 None
    print("✓ A+数据不足 -> qizong (保守不降级)")


def test_prosperity_zero_disables_qizong():
    # 景气度<=0 大前提失效, 即使 A 级也不气宗
    assert _mode_from_grade("A", 0, stage="S2") is None
    assert _mode_from_grade("A", -10, stage="S2") is None
    # 景气缺失 (None) 不阻止 (不等于<=0)
    assert _mode_from_grade("A", None, stage="S2") == "qizong"
    print("✓ A+景气<=0 -> None (大前提失效); 景气None -> 不阻止")


def test_b_grade_always_jianzong():
    assert _mode_from_grade("B", 60, stage="S2") == "jianzong"
    assert _mode_from_grade("B", 60, stage="S4") == "jianzong"
    assert _mode_from_grade("B", 0) == "jianzong"
    print("✓ B -> jianzong (一律剑宗)")


def test_low_grade_none():
    for g in ("C", "D", "F", None):
        assert _mode_from_grade(g, 60, stage="S2") is None
    print("✓ C/D/F/None -> None")


def test_2022_coal_no_false_kill():
    # 2022 煤炭 (神华/陕煤): 大盘熊但个股牛 = 个股 S2 上升期
    # S2 闸门应保持气宗, 不误杀 (个股级而非大盘级的价值所在)
    assert _mode_from_grade("A", 70, stage="S2") == "qizong"
    # 同股若转入 S3/S4 (个股见顶/下跌) 则降剑宗
    assert _mode_from_grade("A", 70, stage="S3") == "jianzong"
    assert _mode_from_grade("A", 70, stage="S4") == "jianzong"
    print("✓ 2022煤炭场景: S2->qizong(不误杀), S3/S4->jianzong(见顶/下跌保护)")


if __name__ == "__main__":
    print("\n=== ISS-051 _mode_from_grade stage 闸门单测 ===\n")
    test_a_grade_qizong_on_s1_s2()
    test_a_grade_jianzong_on_s3_s4()
    test_a_grade_data_missing_keeps_qizong()
    test_prosperity_zero_disables_qizong()
    test_b_grade_always_jianzong()
    test_low_grade_none()
    test_2022_coal_no_false_kill()
    print("\n=== 全部 PASS ===")
