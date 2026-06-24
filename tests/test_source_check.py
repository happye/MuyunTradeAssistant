"""数据源连通性体检工具测试（ISS-043）

覆盖：
1. _classify_error 各类错误归类 + hint 正确
2. _probe 包装：成功/失败两条路径
3. format_report 输出格式
4. check_all_sources 结构完整性（mock，不打真实网络）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.source_check import _classify_error, _probe, format_report


def test_classify_error():
    """1. 错误归类 + hint"""
    assert "代理" in _classify_error("ProxyError: 10057")
    assert "封禁" in _classify_error("RemoteDisconnected: Connection aborted")
    assert "超时" in _classify_error("Read timeout")
    assert "SSL证书" in _classify_error("SSLError: curl: (77) certificate")
    assert "AI模型名" in _classify_error("404 Not found the model deepseek-chat")
    assert "AI认证" in _classify_error("401 invalid api key")
    assert "登录" in _classify_error("Baostock 登录失败")
    print("✓ 错误归类 7 类全部正确")


def test_probe_success():
    """2a. _probe 成功路径：计时 + detail"""
    r = _probe("测试源", lambda: "ok-detail")
    assert r["ok"] is True
    assert r["detail"] == "ok-detail"
    assert r["latency_ms"] is not None and r["latency_ms"] >= 0
    assert r["error"] is None
    print(f"✓ _probe 成功: {r['name']} {r['latency_ms']}ms")


def test_probe_failure():
    """2b. _probe 失败路径：异常捕获 + hint"""
    def boom():
        raise ConnectionError("Connection aborted by remote")
    r = _probe("坏源", boom)
    assert r["ok"] is False
    assert "Connection aborted" in r["error"]
    assert "封禁" in r["hint"]
    assert r["latency_ms"] is None
    print(f"✓ _probe 失败: error={r['error'][:40]}... hint含封禁")


def test_format_report():
    """3. format_report 输出含标记/合计行"""
    result = {
        "sources": [
            {"name": "好源", "ok": True, "latency_ms": 100, "detail": "det", "error": None, "hint": None},
            {"name": "坏源", "ok": False, "latency_ms": None, "detail": None, "error": "Err", "hint": "h"},
        ],
        "summary": {"total": 2, "ok": 1, "fail": 1},
    }
    txt = format_report(result)
    assert "✅" in txt and "❌" in txt
    assert "好源" in txt and "坏源" in txt
    assert "合计: 2 个源" in txt
    assert "✅ 1 通" in txt and "❌ 1 挂" in txt
    print("✓ format_report 输出含 ✅/❌/合计")


def test_check_all_sources_structure():
    """4. check_all_sources 结构完整（不打真实网络，只验结构）

    注意：此测试会真实调用网络源（Baostock 等）。
    若断网则 sources 仍会返回（每源独立 try），结构应完整。
    只验证 schema，不验证 ok 值。
    """
    from src.data.source_check import check_all_sources
    r = check_all_sources("600989")
    assert "sources" in r and "summary" in r
    assert isinstance(r["sources"], list)
    assert len(r["sources"]) >= 7  # 至少 3 baostock + 新浪 + ths + em + 2 AI
    for s in r["sources"]:
        assert set(s.keys()) == {"name", "ok", "latency_ms", "detail", "error", "hint"}
    sm = r["summary"]
    assert sm["total"] == len(r["sources"])
    assert sm["ok"] + sm["fail"] == sm["total"]
    print(f"✓ check_all_sources 结构完整: {sm['total']} 源 (✅{sm['ok']}/❌{sm['fail']})")


if __name__ == "__main__":
    print("\n=== 数据源连通性体检工具测试 (ISS-043) ===\n")
    test_classify_error()
    test_probe_success()
    test_probe_failure()
    test_format_report()
    test_check_all_sources_structure()
    print("\n=== 全部 5 项 PASS ===")
