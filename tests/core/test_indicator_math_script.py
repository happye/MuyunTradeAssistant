"""把技术指标数学正确性验证脚本拉回回归体系（2026-09-11）。

## 为什么需要这个文件

`scripts/verify_indicator_math.py` 是第三轮对抗审查 C 区块的成果：用"已知输入算已知输出"
跟权威参考实现（通达信/同花顺口径）逐项比对 RSI(RMA)/ATR/BOLL/KDJ，并验证除零等边界防护。

问题在于：2026-09-05 瘦身重构把它从 `tests/core/` 移到了 `scripts/`，而它
**0 个 `def test_*`**、`pytest tests/` 也扫不到 `scripts/` —— **从此不再被任何全量命令执行**。
`.workbuddy/memory/MEMORY.md` 里「回归脚本务必纳入回归」这条纪律事实上失效了。

本文件用**子进程**把它拉回（零侵入，不改脚本一行）：
脚本本身是"可直跑"设计（`sys.exit(main())`），子进程方式还能真实覆盖它的入口路径。

判定口径由脚本自身定义：与权威口径/回测路径不一致 = FAIL（退出码 1）；边界 NaN/inf = WARN。
"""
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "scripts", "verify_indicator_math.py")

# 汇总行形如：通过 11 项 | 警告 3 项 | 失败 0 项
_SUMMARY_RE = re.compile(r"通过\s*(\d+)\s*项\s*\|\s*警告\s*(\d+)\s*项\s*\|\s*失败\s*(\d+)\s*项")


@pytest.fixture(scope="module")
def verify_run():
    """跑一次脚本（模块级缓存，避免同一份断言重复付 2 秒）。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, SCRIPT], cwd=ROOT, env=env,
        capture_output=True, text=True, errors="replace", timeout=300)


def test_script_exists():
    assert os.path.exists(SCRIPT), "指标数学验证脚本不见了——回归资产被移走后本测试会立刻红"


def test_verify_indicator_math_exits_zero(verify_run):
    out = (verify_run.stdout or "") + (verify_run.stderr or "")
    assert verify_run.returncode == 0, (
        f"指标数学验证失败，退出码 {verify_run.returncode}：\n{out[-2000:]}")


def test_verify_indicator_math_actually_ran(verify_run):
    """防"空跑通过"：必须解析到汇总行、失败 0 项、且通过项数不为 0。"""
    out = (verify_run.stdout or "") + (verify_run.stderr or "")
    m = _SUMMARY_RE.search(out)
    assert m, f"未解析到汇总行（脚本输出结构变了？）：\n{out[-2000:]}"
    passed, warned, failed = (int(g) for g in m.groups())
    assert failed == 0, f"脚本报出 {failed} 项 FAIL：\n{out[-2000:]}"
    assert passed >= 10, (
        f"通过项只有 {passed} 项（历史基线 11 通过 / 3 警告），疑似脚本被削弱成空跑")
