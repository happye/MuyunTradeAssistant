"""暮云思辨 web UI（flask + Jinja2 + Tailwind + htmx）

Phase 1+2: scan + analyze + pos + backtest（threading 后台 + htmx 轮询）。
_init_engines 在 work 函数内（不阻塞请求，progress 立即显示）。
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
    """lazy 初始化引擎（线程安全：多线程调时只 init 一次，后续 return True）"""
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
    """后台：_init_engines + quick_scan（不阻塞请求）"""
    if not _init_engines():
        return {"error": "引擎初始化失败"}
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
    """后台：_init_engines + _orchestrator.analyze"""
    if not _init_engines():
        return {"error": "引擎初始化失败"}
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


def _backtest_work(code, start, end, capital):
    """后台：_init_engines + BacktestEngine.run"""
    if not _init_engines():
        return {"error": "引擎初始化失败"}
    from src.core.backtest_engine import BacktestEngine
    from src.cli.main import load_config, load_pyramid_config, normalize_stock_code

    config = load_config()
    eng = BacktestEngine(
        stock_code=normalize_stock_code(code),
        start_date=start, end_date=end, initial_capital=capital,
        execution_mode="framework_strict",
        layer_mode="decision_strategy_execution",
        skills_dir=config.get("skills", {}).get("dir", "./src/skills"),
        signal_weights=config.get("decision", {}).get("signal_weights"),
        skill_types=config.get("skills", {}).get("types"),
        entry_exit_config=config.get("entry_exit"),
        pyramid_config=load_pyramid_config(config),
    )
    res = eng.run()
    return {
        "code": code,
        "total_return": res.total_return_pct,
        "benchmark": res.benchmark_return_pct,
        "trades": getattr(res, "total_trades", 0) or 0,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/ping")
def ping():
    return "pong"


@app.route("/scan", methods=["POST"])
def scan():
    """触发扫描（后台线程，progress 立即返回，_init_engines 在后台）"""
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
    if result.get("error"):
        return render_template("fragments/progress.html", status="error",
                               action="scan", target="scan-panel", error=result["error"])
    candidates = result.get("candidates", [])
    return render_template("fragments/scan_table.html", candidates=candidates)


@app.route("/analyze/<code>", methods=["POST"])
def analyze(code):
    """触发深度分析（后台，progress 立即）"""
    task_id = start_task(_analyze_work, code)
    return render_template("fragments/progress.html", status="running", task_id=task_id,
                           action="analyze-task", target="analysis-panel",
                           message=f"分析 {code} 中（30-60s）")


@app.route("/analyze-task/<task_id>")
def analyze_status(task_id):
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


@app.route("/backtest/<code>", methods=["POST"])
def backtest(code):
    """触发回测（后台，progress 立即，结果去 #backtest-panel 不覆盖分析）"""
    from datetime import datetime, timedelta
    end = request.form.get("end") or datetime.now().strftime("%Y-%m-%d")
    start = request.form.get("start") or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    capital = float(request.form.get("capital", "100000"))
    task_id = start_task(_backtest_work, code, start, end, capital)
    return render_template("fragments/progress.html", status="running", task_id=task_id,
                           action="backtest-task", target="backtest-panel",
                           message=f"回测 {code} 中（1-3分钟）")


@app.route("/backtest-task/<task_id>")
def backtest_status(task_id):
    task = get_task(task_id)
    if task is None:
        return render_template("fragments/progress.html", status="error",
                               action="backtest-task", target="backtest-panel", error="任务不存在"), 404
    if task["status"] == "running":
        return render_template("fragments/progress.html", status="running", task_id=task_id,
                               action="backtest-task", target="backtest-panel", message="回测中（1-3分钟）")
    if task["status"] == "error":
        return render_template("fragments/progress.html", status="error",
                               action="backtest-task", target="backtest-panel", error=task["error"])
    result = task["result"] or {}
    if result.get("error"):
        return render_template("fragments/progress.html", status="error",
                               action="backtest-task", target="backtest-panel", error=result["error"])
    return render_template("fragments/backtest_result.html", **result)


@app.route("/pos/add", methods=["POST"])
def pos_add():
    """加仓（接 PortfolioManager.add_position）"""
    code = request.form.get("code", "")
    name = request.form.get("name", "")
    price_str = request.form.get("price", "")
    if not code:
        return '<div class="text-red-600 p-2">加仓失败：无代码</div>'
    if not _init_engines():
        return '<div class="text-red-600 p-2">引擎未初始化</div>'
    try:
        price = float(price_str) if price_str else None
        _portfolio.add_position(stock_code=code, stock_name=name,
                                entry_price=price, ratio=0.20)
        positions = _portfolio.list_positions()
        return render_template("fragments/positions.html", positions=positions,
                               msg=f"已加仓 {code}（20%）")
    except Exception as e:
        return f'<div class="text-red-600 p-2">加仓失败: {e}</div>'


@app.route("/pos", methods=["GET"])
def pos_list():
    if not _init_engines():
        return '<div class="text-red-600 p-2">引擎未初始化</div>'
    positions = _portfolio.list_positions()
    return render_template("fragments/positions.html", positions=positions)


@app.route("/events")
def events():
    """事件预警（宏观新闻）"""
    if not _init_engines():
        return '<div class="text-red-600 p-2">引擎未初始化</div>'
    try:
        from src.data.news_client import NewsClient
        news = NewsClient.get_macro_news(max_count=15)
        return render_template("fragments/events.html", events=news)
    except Exception as e:
        return f'<div class="text-red-600 p-2">事件获取失败: {e}</div>'


def _bz_work(code):
    """后台：笨总6维评分 auto_score"""
    if not _init_engines():
        return {"error": "引擎初始化失败"}
    from src.core.benzong import auto_score
    result = auto_score(code, force_refresh=False)
    bs = result.score
    return {
        "code": code,
        "name": getattr(bs, "stock_name", code) or code,
        "norm": bs.normalized_score(),
        "grade": bs.effective_grade(),
        "raw_grade": bs.grade(),
        "confidence": result.overall_confidence,
        "dims": result.dimensions_meta,
        "warnings": result.warnings,
    }


@app.route("/bz", methods=["POST"])
def bz():
    """触发笨总评分（code 在 form，结果去 #bz-panel）"""
    code = request.form.get("code", "")
    if not code:
        return '<div class="text-red-600 p-2">无代码</div>'
    task_id = start_task(_bz_work, code)
    return render_template("fragments/progress.html", status="running", task_id=task_id,
                           action="bz-task", target="bz-panel",
                           message=f"笨总评分 {code} 中（30-60s，6维AI）")


@app.route("/bz-task/<task_id>")
def bz_status(task_id):
    task = get_task(task_id)
    if task is None:
        return render_template("fragments/progress.html", status="error",
                               action="bz-task", target="bz-panel", error="任务不存在"), 404
    if task["status"] == "running":
        return render_template("fragments/progress.html", status="running", task_id=task_id,
                               action="bz-task", target="bz-panel", message="评分中（30-60s）")
    if task["status"] == "error":
        return render_template("fragments/progress.html", status="error",
                               action="bz-task", target="bz-panel", error=task["error"])
    result = task["result"] or {}
    if result.get("error"):
        return render_template("fragments/progress.html", status="error",
                               action="bz-task", target="bz-panel", error=result["error"])
    return render_template("fragments/bz_result.html", **result)


if __name__ == "__main__":
    import os
    app.run(debug=True, use_reloader=False, port=int(os.environ.get("PORT", 5000)))
