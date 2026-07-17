#!/usr/bin/env python3
"""暮云思辨 TUI 入口（Phase 1，和 start.py REPL 共存）

双击或 `python start_tui.py` 启动 textual TUI。
REPL（start.py）保留作 fallback。
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 金融 API 直连（与 start.py 一致，绕 clash 代理）
for _k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

from src.data.source_check import fix_curl_ssl_paths
fix_curl_ssl_paths()

if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.system("chcp 65001 >nul 2>&1")

from src.tui.app import MuyunTUI

if __name__ == "__main__":
    MuyunTUI().run()
