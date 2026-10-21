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

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 测试用户目录隔离（必须在任何 src import 之前，无条件下最安全）──
if os.environ.get("MUYUN_TESTS_REAL_HOME") != "1":
    _TEST_HOME = tempfile.mkdtemp(prefix="muyun-test-home-")
    os.environ["HOME"] = _TEST_HOME
    os.environ["USERPROFILE"] = _TEST_HOME
    atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)

# NAV 联网估值默认关闭（零网络；需要的用例注入 quote_fn 或显式 setenv）
os.environ.setdefault("MUYUN_NAV_QUOTES", "0")

# ── 收集期排除（路径相对本文件所在 tests/ 目录）────────────────
collect_ignore = ["artifacts", "rag_eval", "backtest/test_large_matrix_20260829.py"]  # 外源长跑脚本，导入会写driver，不是离线回归
if os.environ.get("MUYUN_RUN_EXTERNAL") != "1":
    collect_ignore.append("data_sources/test_all_api.py")

# Default portfolio is repository-relative, so HOME isolation alone is insufficient.
# Set its fallback once; individual test fixtures retain precedence.
if os.environ.get("MUYUN_TESTS_REAL_HOME") != "1":
    from src.data import portfolio as _portfolio_module
    _portfolio_module.DEFAULT_PORTFOLIO_PATH = os.path.join(_TEST_HOME, "portfolio.yaml")


# ── knowledge/index 受保护文件守卫（BUG-IDX）────────────────────
# 仓库内 knowledge/index 是受保护的真实知识库索引，测试不得写。
# 此前 pipeline/CLI 路径经 get_rag_service()/init_engines 读 settings 的
# 相对 index_dir 建索引并 save，TF-IDF 兜底会把真实索引覆盖成 5000 维。
_REAL_INDEX_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge", "index")


def _index_digest() -> dict:
    import hashlib
    out = {}
    if os.path.isdir(_REAL_INDEX_DIR):
        for name in sorted(os.listdir(_REAL_INDEX_DIR)):
            path = os.path.join(_REAL_INDEX_DIR, name)
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    # 带 mtime：字节相同的重写（TF-IDF 确定性重建）也算违规
                    out[name] = (hashlib.sha256(handle.read()).hexdigest(), os.stat(path).st_mtime_ns)
    return out


@pytest.fixture(scope="session", autouse=True)
def _protect_knowledge_index():
    """兜底断言：会话结束时受保护索引的 sha256+mtime 必须与开跑前一致（不得被写）。"""
    before = _index_digest()
    yield
    after = _index_digest()
    if after != before:
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        pytest.fail(f"测试改写了受保护的 knowledge/index: {changed}", pytrace=False)


@pytest.fixture(scope="session", autouse=True)
def _isolate_rag_index_dir():
    """根因隔离：RAGService 的 index_dir 若落在仓库 knowledge/index，改指临时副本。

    index_dir 现已锚仓库根（ISS-123），但仍可能等于仓库 knowledge/index；
    pipeline 的 init_engines、CLI 的 get_cli_rag_service 等真实接线会据此 save。
    就地包装 RAGService.__init__（不替换类，不影响 by-value 导入与测试自己的
    monkeypatch 替身）；副本含原索引文件，只读加载行为不变，写落临时目录。
    """
    from src.rag.service import RAGService

    original_init = RAGService.__init__
    state = {}

    def _init(self, config):
        original_init(self, config)
        index_dir = getattr(self, "index_dir", None)
        if index_dir is None or os.path.normcase(os.path.abspath(index_dir)) != os.path.normcase(_REAL_INDEX_DIR):
            return
        if "dir" not in state:
            state["dir"] = tempfile.mkdtemp(prefix="muyun-test-rag-index-")
            if os.path.isdir(_REAL_INDEX_DIR):
                shutil.copytree(_REAL_INDEX_DIR, state["dir"], dirs_exist_ok=True)
        self.index_dir = state["dir"]

    RAGService.__init__ = _init
    yield
    RAGService.__init__ = original_init
    if "dir" in state:
        shutil.rmtree(state["dir"], ignore_errors=True)


# ── 分析报告/analysis 测试隔离（ISS-123 残余 / 测试写报告目录）────────
# evidence._REPORT_DIR 锚仓库根下「分析报告/analysis」（非 HOME），HOME 隔离无效。
# 深分析路径（l/la/pipeline→record_evidence）未逐测 monkeypatch 时会往生产目录
# 落人话证据卡（已 gitignore，非受保护，但仍污染仓库工作树）。同 BUG-IDX 手法：
# session 根因隔离 + 会话结束文件名集合守卫。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REAL_REPORT_DIR = os.path.join(_REPO_ROOT, "分析报告", "analysis")


def _report_dir_names() -> set:
    if not os.path.isdir(_REAL_REPORT_DIR):
        return set()
    return {name for name in os.listdir(_REAL_REPORT_DIR)
            if os.path.isfile(os.path.join(_REAL_REPORT_DIR, name))}


@pytest.fixture(scope="session", autouse=True)
def _isolate_evidence_report_dir():
    """根因隔离：evidence._REPORT_DIR 改指临时目录，测试不写仓库分析报告/analysis。

    就地改模块属性（不替换函数）；单测自己的 monkeypatch 仍优先，结束时恢复到
    本会话临时目录（而非生产路径）。
    """
    from pathlib import Path as _Path
    from src.cli import evidence as evidence_module

    tmp = tempfile.mkdtemp(prefix="muyun-test-analysis-report-")
    evidence_module._REPORT_DIR = _Path(tmp)
    yield
    # 不恢复生产路径：会话末仍可能有慢测/teardown 写卡；临时目录随进程结束清理
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _protect_evidence_report_dir():
    """兜底断言：会话结束时仓库 分析报告/analysis 文件名集合不得新增（不得被测写）。"""
    before = _report_dir_names()
    yield
    after = _report_dir_names()
    added = sorted(after - before)
    if added:
        preview = added[:8]
        more = f" 等{len(added)}个" if len(added) > 8 else ""
        pytest.fail(
            f"测试写入了仓库分析报告/analysis（应隔离到临时目录）: {preview}{more}",
            pytrace=False,
        )
