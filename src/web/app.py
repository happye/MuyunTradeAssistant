"""暮云思辨 web UI（flask + Jinja2 + Tailwind + htmx）

Phase 1 接引擎：scan + analyze（threading 后台 + htmx 轮询）。
复用 chat/tools.py 调引擎（quick_scan + _orchestrator.analyze）。
pos/backtest 后续。
"""

import logging
from pathlib import Path

from flask import Flask, render_template, request

from src.web.tasks import get_task, start_task

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))
app.secret_key = "muyun-dev"

_scanner = None
_orchestrator = None
_portfolio = None


def _init_engines() -> bool:
    """lazy 初始化引擎。成功返回 True。"""
    global _scanner, _orchestrator, _portfolio
    if _scanner is not None:
        return True
    try:
        from src.chat.tools import _orchestrator as orch, _scanner_engine, init_engines
        from src.cli.main import load_config
        from src.data.portfolio import PortfolioManager

        config = load_config()
        init_engines(config)
        _scanner = _scanner_engine
        _orchestrator = orch
        _portfolio = PortfolioManager()
        return True
    except Exception as e:
        logger.error(f"web 引擎初始化失败: {e}")
        return False


def _scan_work(rule="healthy_pullback", theme=None):
    exclude = set()
    try:
        exclude = {p.stock_code for p in _portfolio.list_positions()}
    except Exception:
        pass
    candidates, info = _scanner.quick_scan(
        rule_name=rule, market_query=theme, exclude_codes=exclude
    )
    return {"candidates": candidates, "info": info, "count": len(candidates)}


def _analyze_work(code):
    """同步：_orchestrator.analyze（后台线程，复用 chat/tools.analyze_stock 模式）"""
    from src.data.akshare_client import AKShareClient
    from src.data.models import StockData

    stock_data = AKShareClient.calculate_indicators(code)
    if not stock_data:
        quote = AKShareClient.get_realtime_quote(code)
        if quote:
            stock_data = StockData(
                stock_code=quote.get("stock_code", code),
                stock_name=quote.get("stock_name", code),
                price=quote.get("price", 0),
                change_pct=quote.get("change_pct"),
            )
    if not stock_data:
        return {"error": f"无法获取 {code} 数据"}
    pos = None
    current_ratio = 0.0
    strategy_state = None
    try:
        for p in _portfolio.list_positions():
            if p.stock_code == code:
                pos = p
                current_ratio = p.current_ratio
                strategy_state = _portfolio.to_strategy_state(code)
                break
    except Exception:
        pass
    has_position = pos is not None and pos.current_ratio > 0
    dr, sd, ee, ai = _orchestrator.analyze(
        stock_data,
        current_position_ratio=current_ratio,
        strategy_state=strategy_state,
        ai_enabled=True,
        has_position=has_position,
        entry_price=pos.entry_price if pos else None,
        high_since_entry=pos.high_since_entry if pos else None,
        trade_plan=pos.trade_plan if pos else None,
    )
    return {"stock_data": stock_data, "dr": dr, "sd": sd, "ee": ee, "ai": ai}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/ping")
def ping():
    return "pong"


@app.route("/scan", methods=["POST"])
def scan():
    if not _init_engines():
        return render_template("fragments/progress.html", status="error",
                               action="scan", target="scan-panel", error="引擎初始化失败"), 500
    rule = request.form.get("rule", "healthy_pullback")
    theme = request.form.get("theme") or None
    task_id = start_task(_scan_work, rule, theme)
    return render_template("fragments/progress.html", status="running", task_id=task_id,
                           action="scan", target="scan-panel", message="扫描中（全市场行情，约4分钟）")


@app.route("/scan/<task_id>")
def scan_status(task_id):
    task = get_task(task_id)
    if task is None:
        return render_template("fragments/progress.html", status="error",
                               action="scan", target="scan-panel", error="任务不存在"), 404
    if task["status"] == "running":
        return render_template("fragments/progress.html", status="running", task_id=task_id,
                               action="scan", target="scan-panel", message="扫描中（约4分钟）")
    if task["status"] == "error":
        return render_template("fragments/progress.html", status="error",
                               action="scan", target="scan-panel", error=task["error"])
    result = task["result"] or {}
    candidates = result.get("candidates", [])
    return render_template("fragments/scan_table.html", candidates=candidates)


@app.route("/analyze/<code>", methods=["POST"])
def analyze(code):
    """触发深度分析（后台线程）"""
    if not _init_engines():
        return render_template("fragments/progress.html", status="error",
                               action="analyze-task", target="analysis-panel", error="引擎初始化失败"), 500
    task_id = start_task(_analyze_work, code)
    return render_template("fragments/progress.html", status="running", task_id=task_id,
                           action="analyze-task", target="analysis-panel",
                           message=f"分析 {code} 中（30-60s）")


@app.route("/analyze-task/<task_id>")
def analyze_status(task_id):
    """htmx 轮询分析状态（用 /analyze-task/ 避免和 /analyze/<code> 冲突）"""
    task = get_task(task_id)
    if task is None:
        return render_template("fragments/progress.html", status="error",
                               action="analyze-task", target="analysis-panel", error="任务不存在"), 404
    if task["status"] == "running":
        return render_template("fragments/progress.html", status="running", task_id=task_id,
                               action="analyze-task", target="analysis-panel", message="分析中（30-60s）")
    if task["status"] == "error":
        return render_template("fragments/progress.html", status="error",
                               action="analyze-task", target="analysis-panel", error=task["error"])
    result = task["result"] or {}
    if result.get("error"):
        return render_template("fragments/progress.html", status="error",
                               action="analyze-task", target="analysis-panel", error=result["error"])
    return render_template("fragments/analysis_panel.html", **result)


@app.route("/pos/add", methods=["POST"])
def pos_add():
    """加仓（Phase 1 占位，下轮接 PortfolioManager）"""
    code = request.form.get("code", "")
    return f'<div class="text-green-600 p-2">加仓 {code}（功能开发中，下轮接 PortfolioManager）</div>'


if __name__ == "__main__":
    import os
    app.run(debug=True, use_reloader=False, port=int(os.environ.get("PORT", 5000)))
