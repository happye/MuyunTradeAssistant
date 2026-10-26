"""人话告警翻译层回归测试（v0.8.7.3）

覆盖：映射表命中/未知透传/record.args 清理/线程安全汇总/install 幂等。
跑法：pytest tests/core/test_plain_errors.py 或直接 python 执行。
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import logging
import json
import unittest.mock as mock

import pytest

from src.cli import plain_errors as pe
from src.core.benzong import cache


def setup_function():
    pe._recent.clear()
    logging.getLogger("test_pe_filter").handlers.clear()


def test_translate_hits_all_categories():
    cases = [
        ("财务返回身份核对失败 query_profit_data sh.600519 2024Q1: 返回证券不符", "已拒收", "缺失"),
        ("MarketCache: 缓存时间异常，已忽略该缓存 (industry)", "缓存的时间无效或晚于当前时间", "缺失"),
        ("最新分析无操作建议，旧待办过期写入失败；请重跑分析复核待办", "持仓和成交记录不变", "缺失"),
        ("data_provider.margin_table_20260825 失败: ValueError: x",
         "融资余额表没拉到", "缺失"),
        ("所有实时行情接口均失败 600519, 最后错误: x", "这只股连价格都没拿到", "终止"),
        ("公告获取失败 603125（top_signal 减持子信号降级跳过）: y", "减持公告扫描失败", "缺失"),
        ("MarketCache: 双源失败冷却中(60秒内)，本次跳过全市场拉取", "歇60秒防封禁", "缺失"),
        ("web 账户资格不可核(600519)，本次不生成分析建议: broken", "不会按空仓给建议", "终止"),
        ("web 策略状态元数据读取失败(600519)，保留账户事实和独立计划: broken", "独立交易计划", "缺失"),
        ("持仓读取失败，本次扫描无法排除已持仓股: broken", "读取失败不能证明空仓", "缺失"),
        # T05-K：缓存 payload 资格门（新增告警必须同步人话映射与速查手册）
        # fix1：读拒/写拒/写失败必须可区分，且写拒不得承诺重算
        ("笨总缓存读取 payload 资格校验失败（字段 confidence 非法），当未命中: "
         "600519_2026-10-27_business_purity.json",
         "该维按未命中处理", "缺失"),
        ("笨总缓存写入 payload 资格校验失败（字段 sources 非法），拒绝写入",
         "本次评分结果未写入缓存", "缺失"),
        ("笨总缓存写入失败（OSError），缓存未更新",
         "后续按正常规则命中或重算", "缺失"),
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


def _capture_cache_warnings(fn, caplog):
    """真实 logger → PlainLanguageFilter 接线：返回过滤后消息文本。

    只做局部隔离：caplog.at_level 自行保存/恢复正常 level；本 helper 只临时挂
    Capture handler 并恢复 handlers/propagate，绝不把 cache logger 永久留在 WARNING。
    """
    logger = logging.getLogger("src.core.benzong.cache")
    saved_handlers = list(logger.handlers)
    saved_propagate = logger.propagate
    seen = []

    class Capture(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    cap = Capture()
    cap.addFilter(pe.PlainLanguageFilter())
    logger.addHandler(cap)
    logger.propagate = True  # 让 pytest caplog（root）也拿到同一条真实 record
    try:
        with caplog.at_level(logging.WARNING, logger="src.core.benzong.cache"):
            fn()
    finally:
        logger.removeHandler(cap)
        logger.handlers[:] = saved_handlers
        logger.propagate = saved_propagate
    return "\n".join(seen)


def test_cache_read_rejection_real_filter_wiring(tmp_path, monkeypatch, caplog):
    """读拒：真实 cache.get → 日志 → 人话过滤；只表示该维未命中，不宣称重算。"""
    monkeypatch.setattr(cache, "get_cache_dir", lambda: tmp_path)
    path = tmp_path / "600519_2026-10-27_business_purity.json"
    path.write_text(json.dumps({
        "score": "bad", "confidence": 0.5,
        "_cache_version": cache.CACHE_VERSION,
        "leak_marker": "RAW_READ_CANARY",
    }, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()

    rendered = _capture_cache_warnings(
        lambda: cache.get("600519", "2026-10-27", "business_purity"), caplog)

    assert "该维按未命中处理" in rendered
    assert "坏文件保留" in rendered
    assert "本次评分结果未写入缓存" not in rendered, "读拒不得套用写拒文案"
    assert "RAW_READ_CANARY" not in rendered
    assert "RAW_READ_CANARY" not in caplog.text
    assert "笨总缓存读取 payload 资格校验失败" in caplog.text
    assert path.read_bytes() == before, "读拒不得动坏文件"


def test_cache_write_rejection_real_filter_wiring(tmp_path, monkeypatch, caplog):
    """写拒：真实 cache.set → 日志 → 人话过滤；只表示不更新缓存，不承诺重算。"""
    monkeypatch.setattr(cache, "get_cache_dir", lambda: tmp_path)
    payload = {"score": 50, "confidence": 0,
               "leak_marker": "RAW_WRITE_CANARY"}

    rendered = _capture_cache_warnings(
        lambda: cache.set("600519", "2026-10-27", "business_purity", payload), caplog)

    assert "本次评分结果未写入缓存" in rendered
    assert "不代表旧缓存已损坏" in rendered
    assert "该维按未命中处理" not in rendered, "写拒不得套用读拒文案"
    assert "RAW_WRITE_CANARY" not in rendered
    assert "RAW_WRITE_CANARY" not in caplog.text
    assert "笨总缓存写入 payload 资格校验失败" in caplog.text
    assert list(tmp_path.glob("*.json")) == [], "写拒必须在路径/目录之前返回"


def test_cache_write_failure_real_filter_wiring(tmp_path, monkeypatch, caplog):
    """写失败：真实异常正文含 canary，不得进入 logger 或人话过滤输出。"""
    monkeypatch.setattr(cache, "get_cache_dir", lambda: tmp_path)
    old = tmp_path / "600519_2026-10-27_business_purity.json"
    old.write_text(json.dumps({
        "score": 61, "confidence": 0.6,
        "_cache_version": cache.CACHE_VERSION,
    }), encoding="utf-8")
    before = old.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("secret_canary_write_boom")

    monkeypatch.setattr(cache.os, "replace", boom)
    rendered = _capture_cache_warnings(
        lambda: cache.set("600519", "2026-10-27", "business_purity",
                          {"score": 50, "confidence": 0.5}), caplog)

    assert "缓存未更新" in rendered
    assert "后续按正常规则命中或重算" in rendered
    assert "secret_canary_write_boom" not in rendered, "人话/原始段都不得回显异常正文"
    assert "secret_canary_write_boom" not in caplog.text
    assert "笨总缓存写入失败" in caplog.text
    assert old.read_bytes() == before, "写失败必须保留旧缓存字节"


@pytest.mark.parametrize("initial_level", [logging.INFO, logging.DEBUG])
@pytest.mark.parametrize("warning_path", ["get", "set"])
def test_capture_cache_warnings_restores_logger_state(
        tmp_path, monkeypatch, caplog, initial_level, warning_path):
    """helper 只做局部隔离：真实 get/set 告警后 level/handlers/propagate 全恢复。

    红例：修复前 helper 在 with caplog.at_level 之前 setLevel(WARNING)，finally
    只还原 handlers/propagate，调用后 cache logger 被永久留在 WARNING。
    """
    logger = logging.getLogger("src.core.benzong.cache")
    monkeypatch.setattr(cache, "get_cache_dir", lambda: tmp_path)
    original_level = logger.level
    original_handlers = list(logger.handlers)
    original_propagate = logger.propagate
    try:
        logger.setLevel(initial_level)
        logger.propagate = False
        # helper 进入前的状态（也就是它必须恢复到的状态）
        expected_handlers = list(logger.handlers)
        expected_propagate = logger.propagate
        if warning_path == "get":
            path = tmp_path / "600519_2026-10-27_business_purity.json"
            path.write_text(json.dumps({
                "score": "bad", "confidence": 0.5,
                "_cache_version": cache.CACHE_VERSION,
            }, ensure_ascii=False), encoding="utf-8")
            action = lambda: cache.get("600519", "2026-10-27", "business_purity")
            expected = "该维按未命中处理"
        else:
            action = lambda: cache.set(
                "600519", "2026-10-27", "business_purity",
                {"score": 50, "confidence": 0})
            expected = "本次评分结果未写入缓存"

        rendered = _capture_cache_warnings(action, caplog)

        assert expected in rendered, "既有真实 filter 接线不得回退"
        assert logger.level == initial_level, "helper 不得永久改写 logger level"
        assert logger.handlers == expected_handlers, "helper 必须恢复 handlers"
        assert logger.propagate is expected_propagate, "helper 必须恢复 propagate"
    finally:
        # 红例自身也必须清理，避免污染后续用例
        logger.setLevel(original_level)
        logger.handlers[:] = original_handlers
        logger.propagate = original_propagate


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
