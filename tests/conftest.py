"""pytest 根 conftest：项目根入 sys.path + 测试用户目录隔离 + 收集期排除。

M1（plan/TECHNICAL_HANDOFF.md §3）三件事：
- HOME/USERPROFILE → 一次性临时目录：必须在产品模块 import 计算路径之前生效，
  src 各模块 import 时经 Path.home() 解析出的状态文件全部落进临时目录，
  全程不碰真实 ~/.muyun。测试自身的 monkeypatch/_swap 替身依然优先（本文件
  不做任何 fixture 级路径重设，避免覆盖 test_chat_command_bridge 等的模块级替身）。
  逃生口 MUYUN_TESTS_REAL_HOME=1 仅诊断隔离问题用（会碰真实 home，勿常开）。
- artifacts / rag_eval 是产物与评估工具目录，不参与收集（等价旧命令 --ignore）。
- test_all_api.py import 期有副作用（清代理环境变量、reconfigure stdout、写证书），
  必须收集前排除——-m 事后过滤挡不住 import 副作用；显式启用见 tests/README.md。
"""
import atexit
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 测试用户目录隔离（必须在任何 src import 之前，无条件下最安全）──
if os.environ.get("MUYUN_TESTS_REAL_HOME") != "1":
    _TEST_HOME = tempfile.mkdtemp(prefix="muyun-test-home-")
    os.environ["HOME"] = _TEST_HOME
    os.environ["USERPROFILE"] = _TEST_HOME
    atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

# ── 收集期排除（路径相对本文件所在 tests/ 目录）────────────────
collect_ignore = ["artifacts", "rag_eval"]
if os.environ.get("MUYUN_RUN_EXTERNAL") != "1":
    collect_ignore.append("data_sources/test_all_api.py")
