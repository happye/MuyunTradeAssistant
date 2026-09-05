"""ISS-082 第三轮审查小项清扫回归（v0.8.8.6，2026-09-05）

锁死语义：
1. manage_portfolio 股票代码与 H05 持仓匹配同口径规范化（SH.600519 式输入
   此前原样进写盘路径：add 建垃圾键 / update、plan 报无持仓）
2. bz 单股 flag 匹配大小写不敏感（`bz <code> -R` 此前静默不生效且不触发 confirm 门）
3. ba 白话点评 conf=0（降级未评出）维度不再拿中性 50 冒充真实打分

跑法：pytest tests/core/test_iss082_review_sweep.py
"""
import contextlib
import os
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.chat import tools as chat_tools
from src.chat.tools import manage_portfolio
from src.data import portfolio as portfolio_mod


@contextlib.contextmanager
def _swap(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


@contextlib.contextmanager
def _quiet():
    import io
    buf = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = buf, buf
    try:
        yield buf
    finally:
        sys.stdout, sys.stderr = old_out, old_err


@contextlib.contextmanager
def _isolated_portfolio():
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="iss082_")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"positions": {}}, f, allow_unicode=True)
    try:
        with _swap(portfolio_mod, "DEFAULT_PORTFOLIO_PATH", path):
            chat_tools._portfolio_manager = None
            yield path
    finally:
        if os.path.exists(path):
            os.remove(path)
        chat_tools._portfolio_manager = None


def test_manage_portfolio_normalizes_code():
    """④ SH.600519 式输入规范化后再进 CLI 写盘路径。"""
    import src.cli.main as cli_main
    calls = []

    def _fake_manage_positions(action, **kwargs):
        calls.append((action, kwargs))

    with _isolated_portfolio(), _swap(cli_main, "manage_positions", _fake_manage_positions), _quiet():
        result = manage_portfolio(action="add", stock_code="SH.600519",
                                  stock_name="贵州茅台", confirm=True)
    assert not result.startswith("[工具失败]"), f"应放行并规范化: {result[:80]}"
    assert calls, "应调用 CLI manage_positions"
    assert calls[0][1].get("stock_code") == "600519", \
        f"代码应规范化为 600519，实际: {calls[0][1].get('stock_code')}"


def test_bz_dash_uppercase_R_triggers_refresh():
    """⑤ `bz <code> -R` 此前大小写敏感不匹配 → 静默按缓存跑且绕过 confirm 门。"""
    import start
    mode, args = start.parse_input("bz 600519 -R")
    assert mode == "benzong"
    assert args.get("refresh") is True, f"-R 应识别为 refresh: {args}"


def test_ba_dim_cell_shows_unrated_for_zero_conf():
    """③ conf=0 维度显示"未评出"，不拿中性 50 冒充真实打分。"""
    from src.cli.main import _ba_dim_cell
    row = {"dim_scores": {"industry_prosperity": 50, "risk_deduction": 60},
           "dim_confidences": {"industry_prosperity": 0, "risk_deduction": 0.8}}
    assert _ba_dim_cell(row, "industry_prosperity") == "未评出"
    assert _ba_dim_cell(row, "risk_deduction") == "60"
    assert _ba_dim_cell({}, "industry_prosperity") == "未评出", "缺元数据时按未评出处理"


def test_ba_note_excludes_unrated_dims():
    """最强/最弱点评只统计已评出维度；全降级时明示。"""
    import src.cli.main as cli_main
    _BZ_DIM_CN = getattr(cli_main, "_BZ_DIM_CN", {})
    row = {"dim_scores": {"risk_deduction": 50, "industry_leader": 80},
           "dim_confidences": {"risk_deduction": 0, "industry_leader": 0.8},
           "invalidate": False}
    ds = row["dim_scores"]
    dc = row["dim_confidences"]
    rated = {k: v for k, v in ds.items() if dc.get(k, 1) != 0}
    best = max(rated, key=lambda k: rated[k])
    assert best == "industry_leader", "conf=0 的风险维不得参与最强/最弱点评"
