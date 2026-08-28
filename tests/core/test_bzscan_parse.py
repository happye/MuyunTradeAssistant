"""bz scan 解析层规则+主题组合测试（v0.8.7.4）

锁死 parse_input 的规则候选语义（对齐 scan market）：
- 裸首参当规则候选（执行层 resolve，未命中归位主题词由 run_benzong_scan 处理）
- --rule 显式指定优先于位置参数候选
- 纯主题/纯 flag/回测参数不回归

跑法：pytest tests/core/test_bzscan_parse.py 或直接 python 执行。
parse_input 是 start.py 模块级纯函数（无网络无引擎副作用），可安全导入。
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import start


def parse(s: str):
    r = start.parse_input(s)
    assert r is not None, f"解析失败: {s}"
    assert r[0] == "benzong_scan", f"非 bz scan 分支: {s} -> {r[0]}"
    return r[1]


def test_positional_rule_plus_theme():
    """bz scan shrink_pullback 科技 -> 规则候选+剩余主题。"""
    a = parse("bz scan shrink_pullback 科技")
    assert a["rule"] == "shrink_pullback"
    assert a["rule_from_positional"] is True
    assert not a.get("rule_explicit")
    assert a["theme"] == "科技"
    print("PASS 位置参数规则+主题")


def test_positional_rule_multiple_theme_words():
    a = parse("bz scan 缩量 AI,半导体")
    assert a["rule"] == "缩量"          # 模糊规则由执行层解析
    assert a["theme"] == "AI,半导体"
    print("PASS 模糊规则词+多词主题")


def test_rule_flag_beats_positional():
    """--rule 显式时位置参数全部归主题。"""
    a = parse("bz scan --rule oversold_watch 医药")
    assert a["rule"] == "oversold_watch"
    assert a["rule_explicit"] is True
    assert a["theme"] == "医药"
    assert not a.get("rule_from_positional")
    print("PASS --rule 优先")


def test_pure_theme_not_rule():
    """纯主题词（非规则）：parse 层进规则候选位+空主题，执行层 resolve 失败后归位主题。"""
    a = parse("bz scan AI,半导体")
    assert a["rule"] == "AI,半导体"          # 候选先占规则位
    assert a["theme"] == ""                   # 主题暂空
    assert a["rule_from_positional"] is True  # 标记来自位置参数，执行层失败时归位
    print("PASS 纯主题候选归位路径")


def test_default_and_flags():
    a = parse("bz scan")
    assert a["theme"] == "" and a["rule"] == "healthy_pullback"
    b = parse("bz scan --top 5 --limit 20")
    assert b["top_n"] == 5 and b["limit"] == 20 and b["theme"] == ""
    c = parse("bz scan 主题 --backtest --start 2024-01-01 --end 2024-12-31 --capital 500000")
    assert c["rule"] == "主题" and c["theme"] == ""   # 候选进规则位，执行层归位
    assert c["backtest"] is True
    assert c["start"] == "2024-01-01" and c["capital"] == 500000.0
    print("PASS 默认与 flag 不回归")


def test_bare_rule_only():
    """单规则词（无主题）：规则候选+空主题。"""
    a = parse("bz scan value_pick")
    assert a["rule"] == "value_pick" and a["theme"] == ""
    print("PASS 单规则")


if __name__ == "__main__":
    test_positional_rule_plus_theme()
    test_positional_rule_multiple_theme_words()
    test_rule_flag_beats_positional()
    test_pure_theme_not_rule()
    test_default_and_flags()
    test_bare_rule_only()
    print("\n6/6 全部通过")
