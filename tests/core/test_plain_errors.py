"""人话告警翻译层回归测试（v0.8.7.3）

覆盖：映射表命中/未知透传/record.args 清理/线程安全汇总/install 幂等。
跑法：pytest tests/core/test_plain_errors.py 或直接 python 执行。
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import logging
import unittest.mock as mock

from src.cli import plain_errors as pe


def setup_function():
    pe._recent.clear()
    logging.getLogger("test_pe_filter").handlers.clear()


def test_translate_hits_all_categories():
    cases = [
        ("data_provider.margin_table_20260825 失败: ValueError: x",
         "融资余额表没拉到", "缺失"),
        ("所有实时行情接口均失败 600519, 最后错误: x", "这只股连价格都没拿到", "终止"),
        ("公告获取失败 603125（top_signal 减持子信号降级跳过）: y", "减持公告扫描失败", "缺失"),
        ("MarketCache: 双源失败冷却中(60秒内)，本次跳过全市场拉取", "歇60秒防封禁", "缺失"),
    ]
    for raw, plain_sub, tag in cases:
        plain, got_tag = pe.translate(raw)
        assert plain is not None and plain_sub in plain and got_tag == tag, f"未命中: {raw}"
    print("PASS 四类映射命中")


def test_unknown_passthrough():
    plain, tag = pe.translate("某个全新告警 whatever")
    assert plain is None and tag is None
    print("PASS 未命中透传")


def test_filter_rewrites_and_clears_args():
    """带 %s 参数的记录改写后 args 必须清空，否则格式化炸。

    不依赖全局 root handler 状态（pytest/其他测试可能已动过它）：
    自建 logger+handler+filter 的最小闭环。
    """
    logger = logging.getLogger("test_pe_filter")
    logger.setLevel(logging.WARNING)
    logger.handlers.clear()
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    cap = Capture()
    filt = pe.PlainLanguageFilter()
    cap.addFilter(filt)
    logger.addHandler(cap)
    logger.propagate = False
    try:
        logger.warning("margin_table_%s 失败: %s", "20260825", "boom")
    finally:
        logger.removeHandler(cap)

    assert len(records) == 1
    rendered = records[0].getMessage()
    assert "⚠" in rendered and "融资余额表没拉到" in rendered, rendered
    assert "原始：" in rendered and "boom" in rendered, "原文必须保留"
    print("PASS 过滤器改写+args清理")


def test_drain_summary():
    pe._recent.extend([("A缺失", "缺失"), ("B终止", "终止"), ("A缺失", "缺失")])
    items = pe.drain_new()
    assert len(items) == 3
    assert pe.drain_new() == [], "drain 后应清空"

    note = pe.render_summary([("A缺失", "缺失"), ("A缺失", "缺失")])
    assert note is not None and "2 条数据缺失" in note
    assert pe.render_summary([]) is None
    print("PASS drain与汇总")


def test_install_idempotent():
    pe.install()
    n1 = sum(1 for h in logging.getLogger().handlers
             for f in h.filters if isinstance(f, pe.PlainLanguageFilter))
    pe.install()
    n2 = sum(1 for h in logging.getLogger().handlers
             for f in h.filters if isinstance(f, pe.PlainLanguageFilter))
    assert n1 == n2 >= 1, f"应幂等安装，实际 {n1}→{n2}"
    print("PASS install 幂等")


def test_filter_no_double_prefix_on_reentry():
    """同一 record 流经多个挂 filter 的 handler 时不得嵌套前缀（2026-09-11 修复）。

    病根：filter 无条件改写 record.msg，第二个 handler 的 filter 读到已是
    「⚠ 人话｜原始：…」的成品，又翻一次 → 「⚠ …｜原始：⚠ …｜原始：…」，
    且 _recent 同一告警记两次（末尾汇总结论数字虚高）。
    生产 REPL 只有一个 root handler 故不触发，属潜在缺陷，用本测试锁死。
    """
    pe._recent.clear()
    logger = logging.getLogger("test_pe_reentry")
    logger.setLevel(logging.WARNING)
    logger.handlers.clear()
    logger.propagate = False
    seen = []

    class Capture(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    h1, h2 = Capture(), Capture()
    h1.addFilter(pe.PlainLanguageFilter())
    h2.addFilter(pe.PlainLanguageFilter())      # 第二个 handler 也挂
    logger.addHandler(h1)
    logger.addHandler(h2)
    try:
        logger.warning("margin_table_%s 失败: %s", "20260825", "boom")
    finally:
        logger.removeHandler(h1)
        logger.removeHandler(h2)

    assert len(seen) == 2
    for msg in seen:
        assert msg.count("⚠") == 1, f"前缀被重复叠加: {msg}"
    assert seen[1].count("原始：") == 1, f"原文段被重复包裹: {seen[1]}"
    assert "原始：margin_table_20260825 失败: boom" in seen[1], "原文必须完整保留"
    assert len(pe._recent) == 1, f"同一条告警应只计一次，实际 {len(pe._recent)}"
    print("PASS 重复经过不嵌套前缀/不重复记账")


if __name__ == "__main__":
    # 直跑路径：pytest 的 setup_function 钩子不会执行，手动清场保证与 pytest 同语义
    setup_function()
    test_translate_hits_all_categories()
    setup_function()
    test_unknown_passthrough()
    setup_function()
    test_filter_rewrites_and_clears_args()
    setup_function()
    test_filter_no_double_prefix_on_reentry()
    setup_function()
    test_drain_summary()
    setup_function()
    test_install_idempotent()
    print("\n6/6 全部通过")
