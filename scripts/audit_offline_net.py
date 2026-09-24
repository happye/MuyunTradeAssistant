"""离线纯净性审计工具（M1 任务卡第 5 步产物，plan/TECHNICAL_HANDOFF §3）。

阻断 socket.connect 并按测试名记录调用栈——离线集合里的真实网络路径会以
失败形式暴露，触发方=需要补 mock 或标 external 的测试。

用法：
    PYTHONPATH=scripts .venv/Scripts/python.exe -m pytest -q -p audit_offline_net
报告：tests/artifacts/_audit_net_hits.txt（total=0 即离线纯净）

2026-09-25 首次审计结论（total 404→0）：修掉 6 处隐藏触网——
video_report_tier1 liquidity(None 自取数)/tier2 top_signals(live 宏观成交额)/
watch_pool K线兜底 baostock 登录/scan_review 同款/multi_code 新浪预取+取数链/
fear adversarial pkg 层 recent_trade_date 绑定；127.0.0.1 命中为 asyncio
socketpair 自唤醒（进程内 IPC，已放行）。
已知盲区：子进程测试与 curl_cffi(libcurl C 层) 不走 Python socket，审计不到。
"""
import socket
import traceback

HITS = []
CURRENT = {"test": "?"}
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex


def _is_loopback_ipc() -> bool:
    """Windows asyncio 自唤醒管道（socketpair 回退）会连 127.0.0.1——进程内 IPC 放行。"""
    for f in traceback.extract_stack():
        if f.name in ("_fallback_socketpair", "_make_self_pipe", "socketpair"):
            return True
    return False


def _blocked_connect(self, address):
    if _is_loopback_ipc():
        return _orig_connect(self, address)
    if len(HITS) < 400:
        stack = "".join(traceback.format_stack(limit=80)[-25:])
        HITS.append((CURRENT["test"], f"connect {address}", stack))
    raise OSError("[AUDIT-NET-BLOCK] 离线测试禁止真实连接")


def _blocked_connect_ex(self, address):
    if _is_loopback_ipc():
        return _orig_connect_ex(self, address)
    if len(HITS) < 400:
        stack = "".join(traceback.format_stack(limit=80)[-25:])
        HITS.append((CURRENT["test"], f"connect_ex {address}", stack))
    raise OSError("[AUDIT-NET-BLOCK] 离线测试禁止真实连接")


socket.socket.connect = _blocked_connect
socket.socket.connect_ex = _blocked_connect_ex


def pytest_runtest_logstart(nodeid, location):
    CURRENT["test"] = nodeid


def pytest_sessionfinish(session, exitstatus):
    out = [f"total={len(HITS)}"]
    for test, addr, stack in HITS:
        out.append(f"\n===== [{test}] {addr}\n{stack}")
    import os
    os.makedirs("tests/artifacts", exist_ok=True)
    with open("tests/artifacts/_audit_net_hits.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out))
