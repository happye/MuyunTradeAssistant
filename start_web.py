#!/usr/bin/env python3
"""暮云思辨 web UI 入口（Phase 1）

启动 flask + 自动开浏览器。和 start.py(REPL)/start_tui.py(TUI) 共存。
"""
import os
import sys
import threading
import webbrowser

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 金融 API 直连（与 start.py 一致）
for _k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

from src.data.source_check import fix_curl_ssl_paths
fix_curl_ssl_paths()

if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from src.web.app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # 延迟 1s 开浏览器（等 flask 起来）
    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    # use_reloader=False: flask debug 默认开 reloader 启动主+子进程，
    # 会导致 Timer 在两进程都跑 -> 开两次浏览器。关 reloader 单进程，开一次。
    app.run(debug=True, use_reloader=False, port=port)
