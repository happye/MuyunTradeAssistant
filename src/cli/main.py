"""CLI入口 - 主命令行界面"""

import sys
import os
import json
import logging
from pathlib import Path

# Windows PowerShell 环境下设置 UTF-8（通过环境变量，不替换sys.stdout避免与Rich冲突）
if sys.platform == 'win32':
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# 清代理 + NO_PROXY=*：开clash等代理时，金融API直连更稳（代理会拦截返回456）
# 命令行带参数直接走 main.py 不经 start.py，这里也要设
for _k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import yaml
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.data.models import StockData, SignalType, MarketState, StrategyState, TradeLifecycle, AIModifierResult, MarketEvent
from src.core.orchestrator import Orchestrator
from src.data.portfolio import PortfolioManager

# 配置日志（默认WARNING，只显示警告及以上；--verbose 开启INFO；--debug 开启DEBUG）
# 注意：必须在 import 其他模块前设置，否则子模块的 getLogger 会继承根 logger 级别
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Windows PowerShell 环境下禁用 legacy_windows 模式避免编码问题
console = Console(legacy_windows=False)


def normalize_stock_code(stock_code: str) -> str:
    """规范化股票代码，修复 PowerShell 数字参数吞掉前导零的问题。"""
    code = str(stock_code).strip()
    return code.zfill(6) if code.isdigit() and len(code) < 6 else code


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并 override 到 base（override 同名键覆盖，dict 则继续深合并）。"""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    merged = dict(base)
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_config(config_path: str = "./configs/settings.yaml") -> dict:
    """加载配置文件。

    若存在 configs/settings.local.yaml（gitignored），深合并覆盖到基础配置之上——
    用于存放 API key 等敏感项（ISS-042）。环境变量 DEEPSEEK_API_KEY / KIMI_API_KEY
    仍由各 AI 入口自行兜底。
    """
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    local_path = "./configs/settings.local.yaml"
    if os.path.exists(local_path):
        with open(local_path, 'r', encoding='utf-8') as f:
            local = yaml.safe_load(f) or {}
        if local:
            config = _deep_merge(config, local)
    return config


def load_pyramid_config(config: dict, position_tiers_path: str = "./configs/position_tiers.yaml") -> dict | None:
    """加载金字塔仓位配置（ISS-032）。

    优先级：settings.yaml 的 pyramid 段 > position_tiers.yaml 兜底。
    返回的 dict 直接传给 PyramidPositionManager（期望含 position_tiers 顶层 key）。
    缺失返回 None（StrategyLayer 走默认仓位逻辑，行为与改动前等价）。
    """
    if config and config.get("pyramid"):
        return config["pyramid"]
    try:
        with open(position_tiers_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return None


def apply_ai_overrides(config: dict, ai_overrides: dict) -> dict:
    """应用AI参数覆盖到配置（v0.8.0）

    Args:
        config: 原始配置
        ai_overrides: 覆盖参数，如 {'disable_ai': True, 'provider': 'kimi'}

    Returns:
        修改后的配置
    """
    if not ai_overrides:
        return config

    ai_cfg = config.setdefault("ai", {})
    if ai_overrides.get('disable_ai'):
        ai_cfg['enabled'] = False
    if ai_overrides.get('provider'):
        ai_cfg['provider'] = ai_overrides['provider']

    return config


def create_sample_data() -> StockData:
    """创建示例股票数据"""
    console.print("\n[bold cyan]📊 手工数据输入模式[/bold cyan]")
    console.print("请输入以下数据（直接回车使用默认值示例）：\n")

    defaults = {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "price": 1850.0,
        "ma5": 1820.0,
        "ma10": 1800.0,
        "ma20": 1780.0,
        "ma60": 1750.0,
        "volume": 85000000,
        "avg_volume_20": 72000000,
        "change_pct": 1.5,
        "high_60d": 1950.0,
        "low_60d": 1680.0
    }

    data = {}
    for key, default_val in defaults.items():
        if key in ["stock_code", "stock_name"]:
            val = console.input(f"  {key} [{default_val}]: ").strip()
            data[key] = val or default_val
        else:
            val = console.input(f"  {key} [{default_val}]: ").strip()
            data[key] = float(val) if val else default_val

    return StockData(**data)


def analyze_interactive():
    """交互式分析模式"""
    console.print(Panel.fit(
        "[bold cyan]暮云思辨投资助手 v0.8.6.6[/bold cyan]\n"
        "AI驱动的A股交易行为约束系统",
        border_style="cyan"
    ))

    # 加载配置和初始化编排器
    try:
        config = load_config()
    except Exception as e:
        console.print(f"[red]配置文件加载失败: {e}[/red]")
        config = {"skills": {"dir": "./src/skills", "enabled": None}}

    enabled_skills = config.get("skills", {}).get("enabled", None)
    skills_dir = config.get("skills", {}).get("dir", "./src/skills")
    weights = config.get("decision", {}).get("signal_weights", None)
    skill_types = config.get("skills", {}).get("types", None)
    ai_config = config.get("ai", None)

    # v0.8.0 Phase 3: 事件层配置
    event_config = config.get("event", None)

    console.print(f"\n[green]✓[/green] 技能目录: {skills_dir}")
    if enabled_skills:
        console.print(f"[green]✓[/green] 启用的技能: {', '.join(enabled_skills)}")
    else:
        console.print("[yellow]⚠[/yellow] 将加载所有可用技能")

    entry_exit_config = config.get("entry_exit", None)
    pyramid_config = load_pyramid_config(config)

    # 创建编排器（含AI调节层+事件层）
    orchestrator = Orchestrator(skills_dir, enabled_skills, weights, skill_types, ai_config=ai_config, event_config=event_config, entry_exit_config=entry_exit_config, pyramid_config=pyramid_config)

    console.print(f"[green]✓[/green] 已加载技能: {', '.join(orchestrator.get_available_skills())}")
    if orchestrator.ai_modifier and orchestrator.ai_modifier.is_available():
        console.print(f"[green]✓[/green] AI调节层: 已启用 ({config.get('ai', {}).get('provider', 'deepseek')})")
    else:
        console.print(f"[dim]AI调节层: 未配置（可在 configs/settings.yaml 中启用）[/dim]")

    # 获取股票数据
    try:
        data = create_sample_data()
    except KeyboardInterrupt:
        console.print("\n[yellow]已取消输入[/yellow]")
        return None

    # 执行分析
    console.print("\n[bold yellow]🔄 正在分析...[/bold yellow]")
    decision_result, strategy_decision, execution_eval, ai_result = orchestrator.analyze(data, ai_enabled=True)

    # 显示结果（支持策略层与执行层信息）
    display_result(decision_result, strategy_decision, execution_eval, ai_result)

    return decision_result


def analyze_json(json_path: str):
    """从JSON文件分析股票数据"""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    stock_data = StockData(**data)

    config = load_config()
    enabled_skills = config.get("skills", {}).get("enabled", None)
    skills_dir = config.get("skills", {}).get("dir", "./src/skills")
    weights = config.get("decision", {}).get("signal_weights", None)
    skill_types = config.get("skills", {}).get("types", None)
    ai_config = config.get("ai", None)
    event_config = config.get("event", None)
    entry_exit_config = config.get("entry_exit", None)
    pyramid_config = load_pyramid_config(config)

    orchestrator = Orchestrator(skills_dir, enabled_skills, weights, skill_types, ai_config=ai_config, event_config=event_config, entry_exit_config=entry_exit_config, pyramid_config=pyramid_config)

    decision_result, strategy_decision, execution_eval, ai_result = orchestrator.analyze(stock_data, ai_enabled=False)

    display_result(decision_result, strategy_decision, execution_eval, ai_result)
    return decision_result


def display_result(result, strategy_decision=None, execution_eval=None, ai_result=None):
    """格式化显示分析结果（v0.8.0 含AI调节层信息）"""
    # 信号颜色映射
    signal_colors = {
        SignalType.BUY: "green",
        SignalType.SELL: "red",
        SignalType.HOLD: "yellow",
        SignalType.WATCH: "cyan"
    }

    # 状态颜色映射
    state_colors = {
        MarketState.RISK_ON: "green",
        MarketState.RISK_OFF: "yellow",
        MarketState.PANIC: "red",
        MarketState.TRANSITION: "blue"
    }

    signal_color = signal_colors.get(result.decision, "white")
    state_color = state_colors.get(result.state, "white")

    # 仓位动作中文映射
    pos_action_map = {
        "OPEN": "试探建仓", "ADD": "加仓", "REDUCE": "减仓",
        "CLOSE_ALL": "全部清仓", "HOLD_POSITION": "维持仓位", "STAY_OUT": "空仓观望"
    }
    pos_action_cn = pos_action_map.get(result.position_action.value, result.position_action.value)

    # 主决策面板
    title_str = "[bold]分析结果[/bold]"
    content_str = (
        f"[bold]股票:[/bold] {result.stock.stock_name} ({result.stock.stock_code})\n"
        f"[bold]当前价:[/bold] {result.stock.price}\n"
        f"[bold]市场状态:[/bold] [{state_color}]{result.state.value}[/{state_color}]\n"
        f"[bold {signal_color}]决策: {result.decision.value}[/]\n"
        f"[bold]综合评分:[/bold] {result.score:.2f}\n"
        f"[bold]仓位动作:[/bold] {pos_action_cn}"
    )
    console.print(Panel.fit(content_str, title=title_str, border_style=signal_color))

    # 各技能信号表格
    table = Table(title="各技能信号")
    table.add_column("技能", style="cyan")
    table.add_column("信号", style=signal_color)
    table.add_column("置信度", justify="right")
    table.add_column("理由", style="dim")

    for sig in result.signals:
        sig_color = signal_colors.get(sig.signal, "white")
        table.add_row(
            sig.skill_alias,
            f"[{sig_color}]{sig.signal.value}[/{sig_color}]",
            f"{sig.confidence:.0%}",
            ", ".join(sig.reason[:2]) if sig.reason else "-"
        )

    console.print(table)

    # 决策理由
    if result.reason:
        console.print("\n[bold]决策理由：[/bold]")
        for i, reason in enumerate(result.reason, 1):
            console.print(f"  {i}. {reason}")

    # v0.8.0: AI情绪信息
    if ai_result and ai_result.adjusted:
        sentiment_cn = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
        sentiment_color = {"bullish": "green", "bearish": "red", "neutral": "yellow"}
        sent_cn = sentiment_cn.get(ai_result.sentiment, ai_result.sentiment)
        sent_color = sentiment_color.get(ai_result.sentiment, "white")
        event_cn = {"policy": "政策", "war": "地缘冲突", "earnings": "财报", "macro": "宏观", "black_swan": "黑天鹅", "none": ""}
        evt_cn = event_cn.get(ai_result.event_type, ai_result.event_type)

        ai_line = f"🤖 AI情绪: [{sent_color}]{sent_cn}[/{sent_color}] (置信度: {ai_result.confidence:.0%})"
        if evt_cn:
            ai_line += f" | 事件: {evt_cn}"
        if ai_result.score_adjustment != 0:
            pct = abs(ai_result.score_adjustment) * 100
            direction = "下调" if ai_result.score_adjustment < 0 else "上调"
            ai_line += f" | 信号{direction}{pct:.0f}%"
        if ai_result.position_cap < 1.0:
            ai_line += f" | 仓位上限{ai_result.position_cap:.0%}"
        if ai_result.summary:
            ai_line += f"\n    {ai_result.summary}"

        console.print(f"\n[bold]AI调节：[/bold]")
        console.print(f"  {ai_line}")

        if ai_result.force_state:
            console.print(f"  [bold red]⚠ 状态干预: {ai_result.force_state}[/bold red]")

        # v0.8.3: 技术面感知状态
        if ai_result.tech_context_awareness:
            used_items = ", ".join(ai_result.tech_context_used) if ai_result.tech_context_used else "无具体要素"
            console.print(f"  [dim]📊 技术面感知: ✅ ({used_items})[/dim]")
        elif ai_result.adjusted:
            console.print(f"  [dim]📊 技术面感知: ❌ 未引用技术面[/dim]")

    # v0.8.3: 买卖点信息
    if strategy_decision and hasattr(strategy_decision, "entry_exit") and strategy_decision.entry_exit:
        ee = strategy_decision.entry_exit
        console.print(f"\n[bold yellow]买卖点：[/bold yellow]")
        if ee.get("entry_triggered"):
            entry_ratio_pct = int(ee.get('entry_ratio', 0) * 100)
            console.print(f"  [green]▶ 买点·建仓{entry_ratio_pct}%:[/green] {ee.get('entry_reason', '')}")
        if ee.get("exit_triggered"):
            action_cn = _exit_action_cn(ee.get('exit_action', ''), ee.get('exit_ratio', 0), ee.get('exit_type', ''))
            exit_reason = ee.get('exit_reason', '')
            chandelier_stop = ee.get('chandelier_stop_price')
            if chandelier_stop:
                console.print(f"  [red]◀ 卖点·{action_cn}:[/red] 止损价 {chandelier_stop} | {exit_reason}")
            else:
                console.print(f"  [red]◀ 卖点·{action_cn}:[/red] {exit_reason}")
        if ee.get("override_decision"):
            action = ee.get('override_action', '')
            console.print(f"  [bold]买卖点覆盖决策 → {action}[/bold]")
        if not ee.get("entry_triggered") and not ee.get("exit_triggered"):
            console.print(f"  [dim]无触发 (价格在买卖点之间)[/dim]")
    
    # v0.8.3 ISS-030: 买卖点与AI情绪分歧
    if strategy_decision and strategy_decision.divergence:
        d = strategy_decision.divergence
        console.print(f"\n[bold yellow]⚠ 分歧提示：[/bold yellow]")
        console.print(f"  [yellow]技术面: {d['technical_signal']} | AI情绪: {d['ai_sentiment']}[/yellow]")
        console.print(f"  [dim]{d['warning']}[/dim]")

# v0.7.2: 策略层信息
    if strategy_decision:
        console.print(f"\n[bold magenta]策略层：[/bold magenta]")
        lifecycle_colors = {
            TradeLifecycle.FLAT: "dim",
            TradeLifecycle.OPEN: "green",
            TradeLifecycle.HOLD: "cyan",
            TradeLifecycle.EXIT: "yellow",
            TradeLifecycle.COOLDOWN: "red",
        }
        lc_color = lifecycle_colors.get(strategy_decision.lifecycle_after, "white")
        console.print(f"  生命周期: [{lc_color}]{strategy_decision.lifecycle_before.value}[/{lc_color}] → [{lc_color}]{strategy_decision.lifecycle_after.value}[/{lc_color}]")
        if strategy_decision.action_semantic:
            console.print(f"  动作语义: {strategy_decision.action_semantic}")
        if strategy_decision.sell_path:
            console.print(f"  卖出路径: {strategy_decision.sell_path}")
        console.print(f"  信号稳定性: {strategy_decision.new_state.signal_stability_score:.0%}")
        console.print(f"  惯性: {strategy_decision.new_state.inertia_counter}天")
        if strategy_decision.strategy_reasons:
            console.print(f"  策略理由: {'; '.join(strategy_decision.strategy_reasons[:3])}")

    # v0.7.2: 执行层信息
    if execution_eval and execution_eval.blocked:
        console.print(f"\n[bold red]执行约束：[/bold red]")
        console.print(f"  ⚠ {execution_eval.block_reason}")
    elif execution_eval:
        console.print(f"\n[bold dim]执行评估：[/bold dim]")
        console.print(f"  滑点: {execution_eval.slippage_pct:.3%}  冲击成本: {execution_eval.impact_cost_pct:.3%}  总成本: {execution_eval.total_cost_pct:.3%}")

    # 风险提示
    if result.warnings:
        console.print("\n[bold yellow]风险提示：[/bold yellow]")
        for warning in result.warnings:
            console.print(f"  - {warning}")

    # 决策追溯
    if result.trace:
        console.print("\n[bold dim]决策追溯：[/bold dim]")
        for t in result.trace:
            console.print(f"  [{t.step}] {t.description}")
            # 展示关键数据
            for k, v in t.data.items():
                if isinstance(v, dict):
                    items = ", ".join(f"{kk}={vv}" for kk, vv in v.items())
                    console.print(f"    [dim]{k}: {items}[/dim]")
                elif isinstance(v, list) and len(v) <= 8:
                    console.print(f"    [dim]{k}: {', '.join(str(i) for i in v)}[/dim]")
                elif isinstance(v, list):
                    console.print(f"    [dim]{k}: ({len(v)} items)[/dim]")
                else:
                    console.print(f"    [dim]{k}: {v}[/dim]")

    # 详细数据
    console.print("\n[bold dim]当前数据：[/bold dim]")
    console.print(
        f"  MA5: {result.stock.ma5}, "
        f"MA20: {result.stock.ma20}, "
        f"MA60: {result.stock.ma60}"
    )
    console.print(
        f"  成交量: {result.stock.volume:,.0f}, "
        f"20日均量: {result.stock.avg_volume_20:,.0f}"
    )
    console.print(
        f"  60日高: {result.stock.high_60d}, "
        f"60日低: {result.stock.low_60d}, "
        f"涨跌幅: {result.stock.change_pct}%"
    )


def analyze_portfolio(ai_overrides: dict = None, ai_debug: bool = False):
    """一键分析当前所有持仓股"""
    from src.data.akshare_client import get_stock_data, AKShareClient, _baostock_logout

    pm = PortfolioManager()
    positions = pm.list_positions()

    if not positions:
        console.print("\n[yellow]当前无持仓记录，请先使用 --pos-add 添加持仓[/yellow]")
        return

    console.print(f"\n[bold cyan]🔍 持仓扫描模式[/bold cyan]")
    console.print(f"共 {len(positions)} 只持仓股，开始逐个分析...")

    # 数据时效性提示
    from datetime import datetime
    now = datetime.now()
    is_trading = (now.weekday() < 5
                  and ((9 <= now.hour < 12) or (13 <= now.hour < 15)))
    if is_trading:
        console.print("[dim]  ⏱ 盘中模式：数据来自最近交易日K线（Baostock约30秒延迟），技术指标准确[/dim]")
    else:
        console.print("[dim]  ⏱ 盘后模式：数据为最近交易日收盘数据，技术指标准确[/dim]")
    console.print()

    # 加载配置（所有股票共用一个编排器）
    config = load_config()
    config = apply_ai_overrides(config, ai_overrides or {})
    enabled_skills = config.get("skills", {}).get("enabled", None)
    skills_dir = config.get("skills", {}).get("dir", "./src/skills")
    weights = config.get("decision", {}).get("signal_weights", None)
    skill_types = config.get("skills", {}).get("types", None)
    ai_config = config.get("ai", None)
    # debug模式：覆盖配置中的debug开关
    if ai_debug and ai_config:
        ai_config["debug"] = True
    event_config = config.get("event", None)
    entry_exit_config = config.get("entry_exit", None)
    pyramid_config = load_pyramid_config(config)
    orchestrator = Orchestrator(skills_dir, enabled_skills, weights, skill_types, ai_config=ai_config, event_config=event_config, entry_exit_config=entry_exit_config, pyramid_config=pyramid_config)

    results = []  # (pos, stock_data, decision_result, strategy_decision, ai_result)

    for i, pos in enumerate(positions, 1):
        console.print(f"[dim]─── [{i}/{len(positions)}] {pos.stock_name or pos.stock_code} ({pos.stock_code}) ───[/dim]")

        # 获取数据（带超时保护，防止单只股票卡死整个扫描）
        stock_data = None
        has_indicators = False
        try:
            import threading
            result_container = [None]
            error_container = [None]

            def _fetch():
                try:
                    result_container[0] = get_stock_data(pos.stock_code)
                except Exception as e:
                    error_container[0] = e

            t = threading.Thread(target=_fetch, daemon=True)
            t.start()
            t.join(timeout=30)  # 30秒超时

            if t.is_alive():
                logger.warning(f"获取 {pos.stock_code} 超时(30s)，跳过")
                console.print(f"  [yellow]⚠ 获取超时，跳过[/yellow]")
                results.append((pos, None, None, None, None))
                continue

            if error_container[0]:
                raise error_container[0]

            stock_data = result_container[0]
            if stock_data:
                has_indicators = any([
                    stock_data.ma5, stock_data.macd_dif, stock_data.rsi_6,
                    stock_data.boll_upper, stock_data.kdj_k
                ])
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"获取 {pos.stock_code} 完整数据失败: {e}")
            # 连接超时/网络错误的友好提示
            if '10060' in error_msg or 'timeout' in error_msg.lower() or '连接' in error_msg:
                console.print(f"  [yellow]⚠ 网络连接超时，正在尝试备用数据源...[/yellow]")

        # 降级：只获取实时行情
        if not stock_data:
            try:
                quote = AKShareClient.get_realtime_quote(pos.stock_code)
                if quote:
                    stock_data = StockData(
                        stock_code=quote["stock_code"],
                        stock_name=quote["stock_name"],
                        price=quote["price"],
                        open=quote.get("open"),
                        high=quote.get("high"),
                        low=quote.get("low"),
                        change_pct=quote.get("change_pct"),
                        volume=quote.get("volume"),
                    )
            except Exception as e:
                logger.warning(f"获取 {pos.stock_code} 实时行情也失败: {e}")

        if not stock_data:
            console.print(f"  [red]✗ 数据获取失败，跳过[/red]")
            results.append((pos, None, None, None))
            continue

        # 分析
        strategy_state = pm.to_strategy_state(pos.stock_code)

        if not has_indicators:
            # 无技术指标，只显示行情
            console.print(f"  {stock_data.stock_name} 现价 {stock_data.price}  涨跌 {stock_data.change_pct}%  [yellow]⚠ 技术指标不可用，跳过分析[/yellow]")
            results.append((pos, stock_data, None, None, None))
            continue

        try:
            has_position = pos is not None and pos.current_ratio > 0
            decision_result, strategy_decision, execution_eval, ai_result = orchestrator.analyze(
                stock_data,
                current_position_ratio=strategy_state.current_position_ratio,
                strategy_state=strategy_state,
                ai_enabled=True,
                has_position=has_position,
                entry_price=pos.entry_price if pos else None,
                    high_since_entry=pos.high_since_entry if pos else None,
                trade_plan=pos.trade_plan if pos else None,  # v0.8.5: PlanGuard 守卫
            )
            results.append((pos, stock_data, decision_result, strategy_decision, ai_result))
            if hasattr(strategy_decision,'entry_exit') and strategy_decision.entry_exit:
                ee = strategy_decision.entry_exit
                if ee.get('highest_since_entry'):
                    if pos.high_since_entry is None or ee['highest_since_entry'] > pos.high_since_entry:
                        pos.high_since_entry = ee['highest_since_entry']

            # 单只股票简要输出
            signal_colors = {
                SignalType.BUY: "green", SignalType.SELL: "red",
                SignalType.HOLD: "yellow", SignalType.WATCH: "cyan"
            }
            sig_color = signal_colors.get(decision_result.decision, "white")
            pos_action_map = {
                "OPEN": "建仓", "ADD": "加仓", "REDUCE": "减仓",
                "CLOSE_ALL": "清仓", "HOLD_POSITION": "持仓", "STAY_OUT": "观望"
            }
            pos_action_cn = pos_action_map.get(strategy_decision.position_action.value, strategy_decision.position_action.value)
            # 不显示具体目标仓位数值——当前算法未考虑组合总仓位，
            # 单股目标仓位叠加后容易超过100%，展示这个数字会误导决策

            # 浮盈计算
            pnl_str = ""
            if pos.entry_price and stock_data.price:
                pnl_pct = (stock_data.price - pos.entry_price) / pos.entry_price * 100
                pnl_color = "green" if pnl_pct >= 0 else "red"
                pnl_str = f"  浮盈:[{pnl_color}]{pnl_pct:+.2f}%[/{pnl_color}]"

            # AI情绪行（v0.8.0）
            ai_str = ""
            if ai_result and ai_result.adjusted:
                sentiment_cn = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
                sentiment_color = {"bullish": "green", "bearish": "red", "neutral": "yellow"}
                sent_cn = sentiment_cn.get(ai_result.sentiment, ai_result.sentiment)
                sent_color = sentiment_color.get(ai_result.sentiment, "white")
                ai_str = f"  🤖[{sent_color}]{sent_cn}[/{sent_color}]({ai_result.confidence:.0%})"
                if ai_result.summary:
                    ai_str += f" {ai_result.summary}"
                if ai_result.tech_context_awareness:
                    used_short = ",".join(ai_result.tech_context_used[:3]) if ai_result.tech_context_used else ""
                    if used_short:
                        ai_str += f" [dim]📊{used_short}[/dim]"

            # 优先用portfolio.yaml中的stock_name（用户手动维护），备选stock_data
            display_name = pos.stock_name or stock_data.stock_name or stock_data.stock_code
            console.print(
                f"  {display_name} 现价 {stock_data.price}  "
                f"涨跌 {stock_data.change_pct}%{pnl_str}  "
                f"决策:[{sig_color}]{decision_result.decision.value}[/{sig_color}]  "
                f"仓位:[bold]{pos_action_cn}[/bold]"
            )
            if ai_str:
                console.print(f"  {ai_str}")

            # 买卖点信息
            if strategy_decision and strategy_decision.entry_exit:
                ee = strategy_decision.entry_exit
                # 持仓分析里直接输出“建仓/清仓/减仓”的中文语义，不再把内部字段原样暴露给用户。
                # 用户关心的是“现在该怎么做”，不是内部动作枚举叫什么。
                if ee.get("entry_triggered"):
                    entry_ratio_pct = int(ee.get('entry_ratio', 0) * 100)
                    console.print(f"  [green]▶ 买点·建仓{entry_ratio_pct}%:[/green] {ee.get('entry_reason', '')}")
                elif ee.get("exit_triggered"):
                    action_cn = _exit_action_cn(ee.get('exit_action', ''), ee.get('exit_ratio', 0), ee.get('exit_type', ''))
                    chandelier_stop = ee.get('chandelier_stop_price')
                    if chandelier_stop:
                        console.print(f"  [red]◀ 卖点·{action_cn}:[/red] 止损价{chandelier_stop} | {ee.get('exit_reason', '')}")
                    else:
                        console.print(f"  [red]◀ 卖点·{action_cn}:[/red] {ee.get('exit_reason', '')}")

            # v0.8.3 ISS-030: 分歧提示（持仓扫描路径）
            if strategy_decision and strategy_decision.divergence:
                d = strategy_decision.divergence
                console.print(f"  [yellow]⚠ 分歧: 技术面{d['technical_signal']} vs AI情绪{d['ai_sentiment']} — AI仅作风险提示[/yellow]")

            # v0.8.5 阶段 1.4: TradePlan 动态调整建议
            if pos.trade_plan is not None:
                from src.core.trade_plan import TradePlanAdjuster
                adj = TradePlanAdjuster()
                suggestions = adj.suggest(
                    pos.trade_plan, stock_data,
                    high_since_entry=pos.high_since_entry,
                )
                if suggestions:
                    console.print(f"  [bold cyan]💡 计划调整建议（{len(suggestions)} 条）[/bold cyan]")
                    for i, s in enumerate(suggestions, 1):
                        marker = "📋" if s.get("actionable") else "⚠"
                        console.print(f"    {marker} [{s['trigger']}] {s['reason']}")
                    console.print(f"  [dim]💡 跑 `pos plan {pos.stock_code}` 查看完整计划，编辑 portfolio.yaml 应用调整[/dim]")

            # 更新持仓
            # 名称优先用 portfolio.yaml 中用户维护的 stock_name（Baostock 行情 stock_data.stock_name 常是代码本身，
            # 直接传会把持仓名称覆盖成代码）。与上方 display_name 逻辑一致。
            pm.suggest_update(
                pos.stock_code, pos.stock_name or stock_data.stock_name,
                strategy_decision, stock_data, console
            )

        except Exception as e:
            logger.warning(f"分析 {pos.stock_code} 失败: {e}")
            console.print(f"  [red]✗ 分析失败: {e}[/red]")
            results.append((pos, stock_data, None, None, None))

    # ===== 汇总表格 =====
    # 扫描结束，清理baostock连接
    try:
        _baostock_logout()
    except Exception:
        pass

    console.print(f"\n[bold cyan]📊 持仓扫描汇总[/bold cyan]")

    summary_table = Table()
    summary_table.add_column("代码", style="cyan", width=8)
    summary_table.add_column("名称", width=10)
    summary_table.add_column("现价", justify="right", width=8)
    summary_table.add_column("涨跌%", justify="right", width=7)
    summary_table.add_column("浮盈%", justify="right", width=7)
    summary_table.add_column("仓位", justify="right", width=6)
    summary_table.add_column("决策", width=6)
    summary_table.add_column("动作", width=8)
    summary_table.add_column("语义", width=6)
    summary_table.add_column("AI情绪", width=12)

    signal_colors = {
        SignalType.BUY: "green", SignalType.SELL: "red",
        SignalType.HOLD: "yellow", SignalType.WATCH: "cyan"
    }
    pos_action_map = {
        "OPEN": "建仓", "ADD": "加仓", "REDUCE": "减仓",
        "CLOSE_ALL": "清仓", "HOLD_POSITION": "持仓", "STAY_OUT": "观望"
    }

    for pos, stock_data, decision_result, strategy_decision, ai_result in results:
        if not stock_data:
            summary_table.add_row(
                pos.stock_code, pos.stock_name or "-",
                "-", "-", "-", f"{pos.current_ratio:.0%}",
                "-", "数据失败", "-", "-"
            )
            continue

        # 浮盈
        pnl_str = "-"
        if pos.entry_price and stock_data.price:
            pnl_pct = (stock_data.price - pos.entry_price) / pos.entry_price * 100
            pnl_color = "green" if pnl_pct >= 0 else "red"
            pnl_str = f"[{pnl_color}]{pnl_pct:+.2f}[/{pnl_color}]"

        # 涨跌幅颜色
        chg = stock_data.change_pct or 0
        chg_color = "green" if chg >= 0 else "red"
        chg_str = f"[{chg_color}]{chg:+.2f}[/{chg_color}]"

        # AI情绪
        ai_str = "[dim]无数据[/dim]"
        if ai_result and ai_result.adjusted:
            sentiment_cn = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
            sentiment_color = {"bullish": "green", "bearish": "red", "neutral": "yellow"}
            sent_cn = sentiment_cn.get(ai_result.sentiment, ai_result.sentiment)
            sent_color = sentiment_color.get(ai_result.sentiment, "white")
            ai_str = f"[{sent_color}]{sent_cn}[/{sent_color}]({ai_result.confidence:.0%})"
        elif ai_result and not ai_result.adjusted:
            ai_str = f"[dim]未生效({ai_result.summary or '未知'})[/dim]"
        if ai_result and ai_result.tech_context_awareness:
            ai_str += " [dim]📊[/dim]"

        if decision_result and strategy_decision:
            sig_color = signal_colors.get(decision_result.decision, "white")
            pos_action_cn = pos_action_map.get(strategy_decision.position_action.value, strategy_decision.position_action.value)
            # 优先用portfolio.yaml中的stock_name（用户手动维护），备选stock_data
            display_name = pos.stock_name or stock_data.stock_name or stock_data.stock_code
            summary_table.add_row(
                pos.stock_code, display_name,
                f"{stock_data.price:.2f}", chg_str, pnl_str,
                f"{pos.current_ratio:.0%}",
                f"[{sig_color}]{decision_result.decision.value}[/{sig_color}]",
                pos_action_cn,
                strategy_decision.action_semantic or "-",
                ai_str
            )
        else:
            display_name = pos.stock_name or stock_data.stock_name or stock_data.stock_code
            summary_table.add_row(
                pos.stock_code, display_name,
                f"{stock_data.price:.2f}", chg_str, pnl_str,
                f"{pos.current_ratio:.0%}",
                "-", "无指标", "-", "-"
            )

    console.print(summary_table)

    # 操作建议汇总
    actions_summary = {"建仓": 0, "加仓": 0, "持仓": 0, "减仓": 0, "清仓": 0, "观望": 0}
    for _, _, decision_result, strategy_decision, _ in results:
        if strategy_decision:
            pos_action_cn = pos_action_map.get(strategy_decision.position_action.value, "观望")
            if pos_action_cn in actions_summary:
                actions_summary[pos_action_cn] += 1
            else:
                actions_summary["观望"] += 1

    action_parts = []
    action_style = {"建仓": "green", "加仓": "green", "减仓": "yellow", "清仓": "red", "持仓": "cyan", "观望": "dim"}
    for action, count in actions_summary.items():
        if count > 0:
            style = action_style.get(action, "white")
            action_parts.append(f"[{style}]{action}×{count}[/{style}]")

    if action_parts:
        console.print(f"\n  操作建议: {'  '.join(action_parts)}")


def analyze_live(stock_code: str, ai_overrides: dict = None, ai_debug: bool = False):
    """实时行情分析模式（通过AKShare）"""
    from src.data.akshare_client import get_stock_data, AKShareClient

    console.print(f"\n[bold cyan]实时行情模式[/bold cyan]")
    console.print(f"正在获取 [{stock_code}] 的实时数据...\n")

    # 尝试获取完整数据（包含技术指标）
    stock_data = None
    has_indicators = False
    try:
        stock_data = get_stock_data(stock_code)
        if stock_data:
            has_indicators = any([
                stock_data.ma5, stock_data.macd_dif, stock_data.rsi_6,
                stock_data.boll_upper, stock_data.kdj_k
            ])
    except Exception as e:
        logger.warning(f"完整数据获取失败: {e}")

    # 降级：只获取实时行情（如果没有获取到完整数据）
    if not stock_data:
        try:
            quote = AKShareClient.get_realtime_quote(stock_code)
            if not quote:
                console.print(f"[red]获取实时行情失败，请检查股票代码是否正确或稍后重试[/red]")
                sys.exit(1)
            stock_data = StockData(
                stock_code=quote["stock_code"],
                stock_name=quote["stock_name"],
                price=quote["price"],
                open=quote.get("open"),
                high=quote.get("high"),
                low=quote.get("low"),
                change_pct=quote.get("change_pct"),
                volume=quote.get("volume"),
            )
            console.print(f"[yellow]⚠[/yellow] 实时数据获取成功，但历史K线获取失败（技术指标不可用）")
        except Exception as e:
            console.print(f"[red]获取数据失败: {e}[/red]")
            console.print(f"[yellow]提示: AKShare数据源不稳定，可尝试：[/yellow]")
            console.print(f"  1. 稍后重试")
            console.print(f"  2. 使用交互模式手工输入数据")
            sys.exit(1)
    else:
        console.print(f"[green]✓[/green] 数据获取成功!")

    console.print(f"  股票: {stock_data.stock_name} ({stock_data.stock_code})")
    console.print(f"  当前价: {stock_data.price}")
    console.print(f"  涨跌幅: {stock_data.change_pct}%")

    # 有技术指标时执行完整分析
    if has_indicators:
        indicators = []
        if stock_data.ma5:
            indicators.append(f"MA5={stock_data.ma5}")
        if stock_data.macd_dif:
            indicators.append(f"MACD_DIF={stock_data.macd_dif}")
        if stock_data.rsi_6:
            indicators.append(f"RSI-6={stock_data.rsi_6}")
        if stock_data.boll_upper:
            indicators.append(f"BOLL={stock_data.boll_lower:.1f}~{stock_data.boll_upper:.1f}")
        if stock_data.kdj_k:
            indicators.append(f"KDJ={stock_data.kdj_k:.1f}/{stock_data.kdj_d:.1f}/{stock_data.kdj_j:.1f}")

        if indicators:
            console.print(f"  技术指标: {', '.join(indicators)}")

        # ===== 读取持仓记录 =====
        pm = PortfolioManager()
        strategy_state = pm.to_strategy_state(stock_code)
        pos = pm.get_position(stock_code)
        if pos:
            console.print(f"\n[bold green]📂 持仓记录[/bold green]")
            console.print(f"  生命周期: {pos.lifecycle}")
            console.print(f"  当前仓位: {pos.current_ratio:.0%}")
            if pos.last_action_semantic:
                console.print(f"  上次动作语义: {pos.last_action_semantic}")
            if pos.last_sell_path:
                console.print(f"  上次卖出路径: {pos.last_sell_path}")
            if pos.entry_price:
                pnl_pct = (stock_data.price - pos.entry_price) / pos.entry_price * 100
                pnl_color = "green" if pnl_pct >= 0 else "red"
                console.print(f"  开仓价: {pos.entry_price}  浮盈: [{pnl_color}]{pnl_pct:+.2f}%[/{pnl_color}]")
            if pos.strategy_state and pos.strategy_state.get("cooldown_remaining", 0) > 0:
                console.print(f"  冷却期: 剩余{pos.strategy_state['cooldown_remaining']}天")
        else:
            console.print(f"\n[dim]📂 无持仓记录（将从FLAT状态开始分析）[/dim]")

        config = load_config()
        config = apply_ai_overrides(config, ai_overrides or {})
        enabled_skills = config.get("skills", {}).get("enabled", None)
        skills_dir = config.get("skills", {}).get("dir", "./src/skills")
        weights = config.get("decision", {}).get("signal_weights", None)
        skill_types = config.get("skills", {}).get("types", None)
        ai_config = config.get("ai", None)
        # debug模式：覆盖配置中的debug开关
        if ai_debug and ai_config:
            ai_config["debug"] = True
        event_config = config.get("event", None)
        entry_exit_config = config.get("entry_exit", None)
        pyramid_config = load_pyramid_config(config)

        orchestrator = Orchestrator(skills_dir, enabled_skills, weights, skill_types, ai_config=ai_config, event_config=event_config, entry_exit_config=entry_exit_config, pyramid_config=pyramid_config)
        has_position = pos is not None and pos.current_ratio > 0
        result, strategy_decision, execution_eval, ai_result = orchestrator.analyze(
            stock_data,
            current_position_ratio=strategy_state.current_position_ratio,
            strategy_state=strategy_state,
            ai_enabled=True,
            has_position=has_position,
            entry_price=pos.entry_price if pos else None,
                    high_since_entry=pos.high_since_entry if pos else None,
            trade_plan=pos.trade_plan if pos else None,  # v0.8.5: PlanGuard 守卫
        )
        display_result(result, strategy_decision, execution_eval, ai_result)

        # v0.8.6.2: l 命令末尾追加笨总评分摘要（缓存优先，避免每次 l 都 6 次 AI 调用）
        _print_benzong_summary(stock_code, stock_data.stock_name)

        # ===== 建议更新持仓 =====
        # 名称优先用 portfolio.yaml 中已维护的（Baostock stock_data.stock_name 常是代码本身，会覆盖正确名称）
        _existing_pos = pm.get_position(stock_code)
        _update_name = (_existing_pos.stock_name if _existing_pos and _existing_pos.stock_name
                         else stock_data.stock_name)
        pm.suggest_update(
            stock_code, _update_name,
            strategy_decision, stock_data, console
        )
    else:
        # 无技术指标时，只显示基本信息
        console.print(f"\n[bold yellow]仅实时数据可用，无法进行技术分析[/bold yellow]")
        console.print("原因: 历史K线数据获取失败（网络不稳定或API限制）")
        console.print("建议: 使用交互模式手工输入数据进行分析")


def run_backtest(
    stock_code: str,
    start_date: str,
    end_date: str,
    capital: float = 100000.0,
    backtest_mode: str = "framework_strict",
    export_analysis_json_path: str | None = None,
    export_analysis_txt_path: str | None = None,
    export_layer_comparison_json_path: str | None = None,
    export_layer_comparison_txt_path: str | None = None,
    export_validation_json_path: str | None = None,
    export_validation_txt_path: str | None = None,
    layer_mode: str = "decision_strategy_execution",
):
    """回测模式"""
    from src.core.backtest_engine import BacktestEngine
    from src.core.backtest_reporter import (
        export_analysis_json,
        export_analysis_txt,
        export_layer_comparison_json,
        export_layer_comparison_txt,
    )
    from src.core.backtest_validator import (
        build_replay_consistency_check,
        build_validation_payload,
        build_validation_windows,
        export_validation_json,
        export_validation_txt,
        load_trading_dates,
    )

    console.print(f"\n[bold cyan]回测模式[/bold cyan]")
    console.print(f"  分层模式: {layer_mode}")
    console.print(f"  股票: {stock_code}")
    console.print(f"  区间: {start_date} ~ {end_date}")
    console.print(f"  初始资金: ¥{capital:,.0f}")
    mode_cn = "严格框架" if backtest_mode == "framework_strict" else "兼容模式"
    console.print(f"  回测模式: {mode_cn} ({backtest_mode})")
    console.print(f"\n[bold yellow]正在加载数据并回测...[/bold yellow]")

    config = load_config()
    enabled_skills = config.get("skills", {}).get("enabled", None)
    skills_dir = config.get("skills", {}).get("dir", "./src/skills")
    weights = config.get("decision", {}).get("signal_weights", None)
    skill_types = config.get("skills", {}).get("types", None)
    entry_exit_config = config.get("entry_exit", None)
    pyramid_config = load_pyramid_config(config)

    def build_engine(target_layer_mode: str, target_start_date: str | None = None, target_end_date: str | None = None) -> BacktestEngine:
        return BacktestEngine(
            stock_code=stock_code,
            start_date=target_start_date or start_date,
            end_date=target_end_date or end_date,
            initial_capital=capital,
            execution_mode=backtest_mode,
            layer_mode=target_layer_mode,
            skills_dir=skills_dir,
            signal_weights=weights,
            skill_types=skill_types,
            entry_exit_config=entry_exit_config,
            pyramid_config=pyramid_config,
        )

    engine = build_engine(layer_mode)

    result = engine.run()

    display_backtest_result(result, backtest_mode, layer_mode)

    if export_analysis_json_path:
        output = export_analysis_json(result, backtest_mode, export_analysis_json_path, layer_mode)
        console.print(f"[green]✓[/green] 已导出分析 JSON: {output}")

    if export_analysis_txt_path:
        output = export_analysis_txt(result, backtest_mode, export_analysis_txt_path, layer_mode)
        console.print(f"[green]✓[/green] 已导出分析文本: {output}")

    if export_layer_comparison_json_path or export_layer_comparison_txt_path:
        comparison_results = {layer_mode: result}
        for compare_layer in ("decision_only", "decision_strategy", "decision_strategy_execution"):
            if compare_layer in comparison_results:
                continue
            console.print(f"[bold yellow]正在生成三层对照: {compare_layer}[/bold yellow]")
            comparison_results[compare_layer] = build_engine(compare_layer).run()
        if export_layer_comparison_json_path:
            output = export_layer_comparison_json(comparison_results, backtest_mode, export_layer_comparison_json_path)
            console.print(f"[green]✓[/green] 已导出三层对照 JSON: {output}")
        if export_layer_comparison_txt_path:
            output = export_layer_comparison_txt(comparison_results, backtest_mode, export_layer_comparison_txt_path)
            console.print(f"[green]✓[/green] 已导出三层对照文本: {output}")

    if export_validation_json_path or export_validation_txt_path:
        console.print("[bold yellow]正在执行回测验证基线...[/bold yellow]")
        trading_dates = load_trading_dates(stock_code, start_date, end_date)
        windows = build_validation_windows(trading_dates)
        consistency_check = build_replay_consistency_check(
            result,
            stock_code=stock_code,
            skills_dir=skills_dir,
            signal_weights=weights,
            skill_types=skill_types,
        )
        in_sample = windows["in_sample"]
        out_of_sample = windows["out_of_sample"]
        in_sample_result = build_engine(layer_mode, in_sample["start_date"], in_sample["end_date"]).run()
        out_of_sample_result = build_engine(layer_mode, out_of_sample["start_date"], out_of_sample["end_date"]).run()
        walk_forward_results = []
        for window in windows["walk_forward_windows"]:
            wf_result = build_engine(layer_mode, window["test_start"], window["test_end"]).run()
            walk_forward_results.append({"window": window, "result": wf_result})
        validation_payload = build_validation_payload(
            full_result=result,
            backtest_mode=backtest_mode,
            windows=windows,
            in_sample_result=in_sample_result,
            out_of_sample_result=out_of_sample_result,
            walk_forward_results=walk_forward_results,
            consistency_check=consistency_check,
        )
        if export_validation_json_path:
            output = export_validation_json(validation_payload, export_validation_json_path)
            console.print(f"[green]✓[/green] 已导出验证 JSON: {output}")
        if export_validation_txt_path:
            output = export_validation_txt(validation_payload, export_validation_txt_path)
            console.print(f"[green]✓[/green] 已导出验证文本: {output}")
    return result


def run_batch_validation(
    stock_codes: list[str],
    start_date: str,
    end_date: str,
    capital: float = 100000.0,
    backtest_mode: str = "framework_strict",
    export_batch_validation_json_path: str | None = None,
    export_batch_validation_txt_path: str | None = None,
    layer_mode: str = "decision_strategy_execution",
):
    """多标的批量回测验证。"""
    from src.core.backtest_engine import BacktestEngine
    from src.core.backtest_validator import (
        build_batch_validation_payload,
        build_replay_consistency_check,
        build_validation_payload,
        build_validation_windows,
        export_batch_validation_json,
        export_batch_validation_txt,
        load_trading_dates,
    )

    stock_codes = [normalize_stock_code(code) for code in stock_codes]

    console.print(f"\n[bold cyan]批量回测验证模式[/bold cyan]")
    console.print(f"  股票数: {len(stock_codes)}")
    console.print(f"  区间: {start_date} ~ {end_date}")
    console.print(f"  初始资金: ¥{capital:,.0f}")
    console.print(f"  分层模式: {layer_mode}")
    console.print(f"  回测模式: {backtest_mode}")

    config = load_config()
    skills_dir = config.get("skills", {}).get("dir", "./src/skills")
    weights = config.get("decision", {}).get("signal_weights", None)
    skill_types = config.get("skills", {}).get("types", None)
    entry_exit_config = config.get("entry_exit", None)
    pyramid_config = load_pyramid_config(config)

    payloads: list[dict] = []
    failures: list[dict[str, str]] = []

    for index, stock_code in enumerate(stock_codes, start=1):
        console.print(f"[bold yellow]({index}/{len(stock_codes)}) 正在验证 {stock_code}...[/bold yellow]")

        def build_engine(target_layer_mode: str, target_start_date: str | None = None, target_end_date: str | None = None) -> BacktestEngine:
            return BacktestEngine(
                stock_code=stock_code,
                start_date=target_start_date or start_date,
                end_date=target_end_date or end_date,
                initial_capital=capital,
                execution_mode=backtest_mode,
                layer_mode=target_layer_mode,
                skills_dir=skills_dir,
                signal_weights=weights,
                skill_types=skill_types,
                entry_exit_config=entry_exit_config,
                pyramid_config=pyramid_config,
            )

        try:
            result = build_engine(layer_mode).run()
            trading_dates = load_trading_dates(stock_code, start_date, end_date)
            windows = build_validation_windows(trading_dates)
            consistency_check = build_replay_consistency_check(
                result,
                stock_code=stock_code,
                skills_dir=skills_dir,
                signal_weights=weights,
                skill_types=skill_types,
            )
            in_sample = windows["in_sample"]
            out_of_sample = windows["out_of_sample"]
            in_sample_result = build_engine(layer_mode, in_sample["start_date"], in_sample["end_date"]).run()
            out_of_sample_result = build_engine(layer_mode, out_of_sample["start_date"], out_of_sample["end_date"]).run()
            walk_forward_results = []
            for window in windows["walk_forward_windows"]:
                wf_result = build_engine(layer_mode, window["test_start"], window["test_end"]).run()
                walk_forward_results.append({"window": window, "result": wf_result})
            validation_payload = build_validation_payload(
                full_result=result,
                backtest_mode=backtest_mode,
                windows=windows,
                in_sample_result=in_sample_result,
                out_of_sample_result=out_of_sample_result,
                walk_forward_results=walk_forward_results,
                consistency_check=consistency_check,
            )
            payloads.append(validation_payload)
            console.print(
                f"[green]✓[/green] {stock_code} | 收益 {validation_payload['slices']['full_period']['total_return_pct']:+.2f}% | "
                f"一致性 {'通过' if validation_payload['checks']['replay_consistency'].get('passed') else '未通过'}"
            )
        except Exception as exc:
            failures.append({"stock_code": stock_code, "error": str(exc)})
            console.print(f"[red]✗[/red] {stock_code} 验证失败: {exc}")

    batch_payload = build_batch_validation_payload(
        validation_payloads=payloads,
        requested_codes=stock_codes,
        start_date=start_date,
        end_date=end_date,
        backtest_mode=backtest_mode,
        layer_mode=layer_mode,
        failures=failures,
    )

    if export_batch_validation_json_path:
        output = export_batch_validation_json(batch_payload, export_batch_validation_json_path)
        console.print(f"[green]✓[/green] 已导出批量验证 JSON: {output}")
    if export_batch_validation_txt_path:
        output = export_batch_validation_txt(batch_payload, export_batch_validation_txt_path)
        console.print(f"[green]✓[/green] 已导出批量验证文本: {output}")
    return batch_payload


def display_backtest_result(result, backtest_mode: str = "framework_strict", layer_mode: str = "decision_strategy_execution"):
    """展示回测结果"""
    from src.data.models import BacktestResult

    if not isinstance(result, BacktestResult):
        console.print("[red]回测结果异常[/red]")
        return

    # ===== 核心指标面板 =====
    # 收益率颜色
    return_color = "green" if result.total_return_pct > 0 else "red"
    benchmark_color = "green" if result.benchmark_return_pct > 0 else "red"
    beat_benchmark = result.total_return_pct > result.benchmark_return_pct
    beat_icon = "✓" if beat_benchmark else "✗"
    beat_color = "green" if beat_benchmark else "red"

    content = (
        f"[bold]股票:[/bold] {result.stock_code}\n"
        f"[bold]区间:[/bold] {result.start_date} ~ {result.end_date}\n"
        f"[bold]回测模式:[/bold] {backtest_mode}\n"
        f"[bold]分层模式:[/bold] {layer_mode}\n"
        f"[bold]初始资金:[/bold] ¥{result.initial_capital:,.0f}\n"
        f"[bold]最终资产:[/bold] ¥{result.final_value:,.2f}\n"
        f"\n[bold {return_color}]总资金收益率: {result.total_return_pct:+.2f}%[/]\n"
        f"[bold]投入资金收益率: {result.invested_return_pct:+.2f}%[/bold]  [dim](盈利/实际投入成本，反映选股能力)[/dim]\n"
        f"[bold]年化收益率:[/bold] {result.annualized_return_pct:+.2f}%\n"
        f"[bold]最大回撤:[/bold] [red]-{result.max_drawdown_pct:.2f}%[/red]\n"
        f"[bold]夏普比率:[/bold] {result.sharpe_ratio:.2f}\n"
        f"\n[bold]基准收益(买入持有):[/bold] [{benchmark_color}]{result.benchmark_return_pct:+.2f}%[/{benchmark_color}]"
        f"  [{beat_color}]{beat_icon} {'跑赢' if beat_benchmark else '跑输'}基准[/{beat_color}]"
    )

    border = "green" if result.total_return_pct > 0 else "red"
    console.print(Panel.fit(content, title="[bold]回测结果[/bold]", border_style=border))

    # ===== 交易统计 =====
    # 计算投入成本和盈利
    total_buy_amount = sum(t.amount for t in result.trades if t.action == "BUY")
    total_sell_amount = sum(t.amount for t in result.trades if t.action == "SELL")
    net_profit = total_sell_amount - total_buy_amount
    profit_color = "green" if net_profit >= 0 else "red"

    stats_table = Table(title="交易统计")
    stats_table.add_column("指标", style="cyan")
    stats_table.add_column("值", justify="right")

    stats_table.add_row("总交易次数", str(result.total_trades))
    stats_table.add_row("回测模式", backtest_mode)
    stats_table.add_row("分层模式", layer_mode)
    stats_table.add_row("买入次数", str(result.buy_count))
    stats_table.add_row("卖出次数", str(result.sell_count))
    stats_table.add_row("总投入成本", f"¥{total_buy_amount:,.0f}")
    stats_table.add_row("总卖出回款", f"¥{total_sell_amount:,.0f}")
    stats_table.add_row("买卖盈利", f"[{profit_color}]¥{net_profit:,.0f}[/{profit_color}]")
    stats_table.add_row("胜率", f"{result.win_rate:.1f}%")
    stats_table.add_row("盈亏比", f"{result.profit_loss_ratio:.2f}")

    # v0.7.2: 稳定性指标
    if result.decision_stability > 0 or result.drawdown_stability > 0:
        stats_table.add_row("─── v0.7.2 稳定性 ───", "")
        stats_table.add_row("决策稳定性", f"{result.decision_stability:.0%}")
        stats_table.add_row("回撤稳定性", f"{result.drawdown_stability:.2f}")
        if result.mc_simulations > 0:
            stats_table.add_row("最差收益", f"{result.worst_case_return_pct:+.2f}%")
            stats_table.add_row("结果方差", f"{result.result_variance:.2f}")
            stats_table.add_row("MC模拟次数", str(result.mc_simulations))
        if result.blocked_by_limit_up > 0 or result.blocked_by_limit_down > 0 or result.blocked_by_liquidity > 0:
            stats_table.add_row("─── 执行约束 ───", "")
            if result.blocked_by_limit_up > 0:
                stats_table.add_row("涨停阻止买入", str(result.blocked_by_limit_up))
            if result.blocked_by_limit_down > 0:
                stats_table.add_row("跌停阻止卖出", str(result.blocked_by_limit_down))
            if result.blocked_by_liquidity > 0:
                stats_table.add_row("流动性不足阻止", str(result.blocked_by_liquidity))

    console.print(stats_table)

    # ===== 交易记录 =====
    if result.trades:
        trade_table = Table(title="交易记录")
        trade_table.add_column("日期", style="cyan")
        trade_table.add_column("操作", style="bold")
        trade_table.add_column("仓位动作", style="dim")
        trade_table.add_column("价格", justify="right")
        trade_table.add_column("股数", justify="right")
        trade_table.add_column("金额", justify="right")
        trade_table.add_column("仓位%", justify="right")
        trade_table.add_column("原因", style="dim", max_width=28)

        for t in result.trades:
            action_color = "green" if t.action == "BUY" else "red"
            pos_action_display = t.position_action if t.position_action else ""
            # 仓位动作中文映射
            pos_action_map = {
                "OPEN": "建仓", "ADD": "加仓", "REDUCE": "减仓",
                "CLOSE_ALL": "清仓", "HOLD_POSITION": "持仓", "STAY_OUT": "观望"
            }
            pos_action_cn = pos_action_map.get(pos_action_display, pos_action_display)
            ratio_str = f"{t.position_ratio_after:.0%}" if t.position_ratio_after > 0 else ""

            trade_table.add_row(
                t.date,
                f"[{action_color}]{t.action}[/{action_color}]",
                pos_action_cn,
                f"{t.price:.2f}",
                str(t.shares),
                f"¥{t.amount:,.0f}",
                ratio_str,
                t.reason[:28] if t.reason else "",
            )

        console.print(trade_table)

    # ===== 收益曲线摘要 =====
    if result.daily_snapshots and len(result.daily_snapshots) > 1:
        console.print("\n[bold]收益曲线摘要：[/bold]")

        # 每隔N天取一个点展示
        snap_count = len(result.daily_snapshots)
        step = max(1, snap_count // 10)
        summary_table = Table()
        summary_table.add_column("日期", style="cyan")
        summary_table.add_column("收盘价", justify="right")
        summary_table.add_column("总资产", justify="right")
        summary_table.add_column("收益率", justify="right")
        summary_table.add_column("持仓", justify="right")

        for i in range(0, snap_count, step):
            snap = result.daily_snapshots[i]
            ret_color = "green" if snap.return_pct >= 0 else "red"
            summary_table.add_row(
                snap.date,
                f"{snap.price:.2f}",
                f"¥{snap.total_value:,.0f}",
                f"[{ret_color}]{snap.return_pct:+.2f}%[/{ret_color}]",
                f"{snap.position}股" if snap.position > 0 else "-",
            )

        # 最后一天
        if snap_count > 1 and (snap_count - 1) % step != 0:
            snap = result.daily_snapshots[-1]
            ret_color = "green" if snap.return_pct >= 0 else "red"
            summary_table.add_row(
                snap.date,
                f"{snap.price:.2f}",
                f"¥{snap.total_value:,.0f}",
                f"[{ret_color}]{snap.return_pct:+.2f}%[/{ret_color}]",
                f"{snap.position}股" if snap.position > 0 else "-",
            )

        console.print(summary_table)

    # ===== 交易成本说明 =====
    console.print("\n[bold dim]交易成本模型（v0.7.2）：[/bold dim]")
    console.print("  佣金 0.025%(最低5元) + 印花税 0.05%(卖出) + 过户费 0.001%")
    console.print("  滑点: 与波动率挂钩（高波动=大滑点）| 冲击成本: 与量比相关")
    console.print("  涨停不买/跌停不卖 | 流动性不足(量比<0.3)禁止交易")
    console.print("  执行方式: 前日信号+次日开盘价（消除前视偏差）| T+1硬限制")
    console.print("  策略层: 交易生命周期+决策惯性+信号确认+冷却期+反转成本")

    # ===== 风险提示 =====
    console.print("\n[bold yellow]⚠ 风险提示[/bold yellow]")
    console.print("  回测结果不代表未来收益。历史数据回测存在过拟合风险。")
    if result.total_trades < 5:
        console.print("  [yellow]交易次数较少，统计指标可能不具参考性。[/yellow]")
    if result.total_trades == 0 and result.daily_snapshots:
        min_price = min(s.price for s in result.daily_snapshots if s.price and s.price > 0)
        min_lot_cost = min_price * 100  # A股最小交易单位：1手=100股
        if result.initial_capital < min_lot_cost:
            console.print(
                f"  [yellow]零交易诊断：初始资金不足以买入1手（最低约¥{min_lot_cost:,.0f}），"
                "策略信号即使触发也无法成交。[/yellow]"
            )
        else:
            # ISS-034：用 reject 计数器精确定位拒单原因
            rej_funds = result.rejected_insufficient_funds
            rej_below = result.rejected_below_target
            rej_zero = result.rejected_zero_shares
            total_rej = rej_funds + rej_below + rej_zero
            if total_rej > 0:
                # 高价股典型场景：try 买入但目标增量 < 1 手成本
                console.print(
                    f"  [yellow]零交易诊断：BUY 信号触发后被拒单 {total_rej} 次"
                    f"（资金不足{rej_funds} / 已达目标{rej_below} / 手数为0 {rej_zero}）。[/yellow]"
                )
                if rej_funds > 0:
                    suggested_capital = int(min_lot_cost / 0.20 / 10000) * 10000  # 按 20% 试探仓反推建议资金
                    console.print(
                        f"  [yellow]提示：本股最小手数 ¥{min_lot_cost:,.0f}，按 20% 试探仓需 ≥¥{suggested_capital:,.0f} 才能建仓。"
                        f"建议 --capital ¥{suggested_capital:,.0f} 或更高。[/yellow]"
                    )
            else:
                console.print(
                    "  [yellow]零交易诊断：资金可买入1手且无拒单记录，可能由策略信号、"
                    "执行约束或样本区间共同导致。建议拉长区间或更换标的复核。[/yellow]"
                )
    if result.max_drawdown_pct > 30:
        console.print(f"  [red]最大回撤 {result.max_drawdown_pct:.1f}% 较大，策略风险较高。[/red]")


def _try_attach_trade_plan(pm, stock_code: str, stock_name: str, entry_price: float, ratio: float):
    """v0.8.5 阶段 1.2：建仓后调用 TradePlanGenerator 生成草稿 + Y/n 交互。

    设计（E1-B1 强化）：
    - 优先尝试拉实时 StockData（akshare），让 generator 用真实 ATR/MA 算
    - **拉数据失败 → 默认跳过 plan 生成**（兜底数据不能用作真实交易依据）
      用户可强制用 --force-plan-fallback 走规则版兜底（仅用于流程测试）
    - 用户 n 跳过 → 不附加 plan，提示 pos plan 命令后续手动补
    """
    from src.core.trade_plan import TradePlanGenerator

    console.print(f"\n[bold cyan]🤖 正在为 {stock_code} {stock_name} 生成交易计划草稿...[/bold cyan]")

    # 尝试拉实时数据（含 ATR/MA）
    stock_data = None
    data_error = None
    try:
        from src.data.akshare_client import get_stock_data
        stock_data = get_stock_data(stock_code)
        if stock_data:
            atr_str = f"{stock_data.atr_14:.2f}" if stock_data.atr_14 else "?"
            ma20_str = f"{stock_data.ma20:.2f}" if stock_data.ma20 else "?"
            ma60_str = f"{stock_data.ma60:.2f}" if stock_data.ma60 else "?"
            console.print(f"   [green]✓[/green] 读取技术面（ATR={atr_str}, MA20={ma20_str}, MA60={ma60_str}）")
    except Exception as e:
        data_error = e
        logger.warning(f"TradePlan 数据拉取失败: {e}")

    # E1-B1：数据不可用时强警告 + 直接跳过（不再用兜底值生成误导性 plan）
    if stock_data is None:
        console.print(f"\n[bold red]⚠ 无法获取技术面数据[/bold red]")
        if data_error:
            console.print(f"   错误: [red]{type(data_error).__name__}: {str(data_error)[:100]}[/red]")
        console.print(f"\n[bold yellow]TradePlan 自动生成已跳过[/bold yellow]")
        console.print(f"   原因：没有 ATR / MA / 趋势数据时生成的 plan 全是硬编码兜底值（8% 止损 / 10%-20% 止盈），")
        console.print(f"   [bold]不能作为真实交易决策依据[/bold]，建议：")
        console.print(f"   [cyan]1.[/cyan] 检查网络/代理（系统代理 127.0.0.1:7890 会干扰金融 API，可临时关闭）")
        console.print(f"   [cyan]2.[/cyan] 等数据源恢复后跑 [bold]pos plan {stock_code}[/bold] 查看是否需要重建 plan")
        console.print(f"   [cyan]3.[/cyan] 或手动编辑 portfolio.yaml 的 trade_plan 字段（参考 portfolio.yaml.template）")
        console.print(f"\n[dim]持仓本身已添加成功，仅 trade_plan 子系统未启用此股 PlanGuard 守卫[/dim]")
        return

    # RAG（可选）
    rag_service = None
    try:
        from src.rag.service import get_rag_service
        rag_service = get_rag_service()
    except Exception:
        pass

    # 跳法A 阶段1.2：笨总评分定 mode（建仓时一次评分，A→气宗/B→剑宗/其他→不设）
    benzong_grade = None
    industry_prosperity = None
    try:
        from src.core.benzong.auto_scorer import auto_score
        console.print(f"   [cyan]🧮 笨总评分中（定气宗/剑宗模式）...[/cyan]")
        bz = auto_score(stock_code, name=stock_name)
        benzong_grade = bz.score.effective_grade()
        industry_prosperity = bz.score.industry_prosperity
        console.print(f"   [green]✓[/green] 笨总评分 {bz.score.normalized_score():.0f}/100 级别 {benzong_grade}（行业景气={industry_prosperity:.0f}, conf={bz.overall_confidence:.2f}）")
    except Exception as e:
        logger.warning(f"建仓笨总评分失败: {e}")
        console.print(f"   [yellow]⚠ 笨总评分未获取，本计划不设气宗/剑宗纪律（mode=None）[/yellow]")

    gen = TradePlanGenerator(rag_service=rag_service, ai_modifier=None)
    # market_state 已不参与 mode 判定（ISS-051 回退阶段4牛市闸门，改用个股级 Weinstein stage，
    # gen.generate 内部 _detect_weinstein_stage 自算）。原 _ms 计算与 StateMachine 调用是死代码，已删。
    try:
        plan, meta = gen.generate(
            stock_code=stock_code, stock_name=stock_name,
            entry_price=entry_price, ratio=ratio, stock_data=stock_data,
            benzong_grade=benzong_grade, industry_prosperity=industry_prosperity,
        )
    except Exception as e:
        logger.error(f"TradePlan 生成失败: {e}")
        console.print(f"   [red]✗ 生成失败: {e}[/red]")
        console.print(f"   [dim]可后续手动编辑 portfolio.yaml 的 trade_plan 字段[/dim]")
        return

    # 展示草稿
    outlook_color = {"bullish": "green", "neutral": "yellow", "bearish": "red"}.get(plan.fundamental_outlook, "white")
    console.print(f"\n[bold]📋 计划草稿[/bold]")
    console.print(f"  why_buy: {plan.why_buy}")
    console.print(f"  when_buy: {plan.when_buy}")
    console.print(f"  how_much: {plan.how_much:.0%} 仓位")
    console.print(f"  止盈分档: {' / '.join(f'¥{t:.2f}' for t in plan.when_sell_targets)}")
    console.print(f"  失效条件:")
    for c in plan.when_sell_invalidate:
        console.print(f"    - {c}")
    console.print(f"  [red]locked_initial_stop: ¥{plan.locked_initial_stop:.2f}[/red]")
    console.print(f"  max_hold_days: {plan.max_hold_days} 天")
    console.print(f"  fundamental_outlook: [{outlook_color}]{plan.fundamental_outlook}[/{outlook_color}]")
    mode_label = {"qizong": "气宗(长期格局,持有期长,压制技术卖出)", "jianzong": "剑宗(一波流,破线即走)"}.get(plan.mode, "未设定(仅压 weak_sell)")
    mode_color = {"qizong": "green", "jianzong": "yellow"}.get(plan.mode, "dim")
    console.print(f"  笨总模式 mode: [{mode_color}]{mode_label}[/{mode_color}]" + (f"  [dim](笨总 {benzong_grade} 级)[/dim]" if benzong_grade else ""))
    console.print(f"  [dim]thesis_sources: {len(plan.thesis_sources)} 条锚点[/dim]")

    # 元数据提示
    if meta.get("fallback_reasons"):
        console.print(f"  [dim]⚠ 降级提示: {' / '.join(meta['fallback_reasons'])}[/dim]")

    # Y/N 交互
    try:
        answer = input("\n是否采用此计划草稿？[Y/n]（n=跳过，可后续编辑 portfolio.yaml 补） ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    if answer in ("", "y", "yes"):
        if pm.attach_plan(stock_code, plan):
            console.print(f"[green]✓ 交易计划已保存到 portfolio.yaml[/green]")
            console.print(f"  [dim]后续跑 `scan` 时 PlanGuard 会按此计划压制 weak_sell（计划未失效时不卖）[/dim]")
        else:
            console.print(f"[red]✗ 附加失败（持仓不存在？）[/red]")
    else:
        console.print(f"[dim]已跳过 TradePlan 生成。可后续编辑 portfolio.yaml 的 trade_plan 字段补[/dim]")


def _print_benzong_summary(stock_code: str, stock_name: str = ""):
    """v0.8.6.2: l 命令末尾打印笨总评分摘要（缓存优先，不强制 AI 调用）。

    设计：
    - 优先读当日缓存（同股同日已跑过 bz 则直接显示）
    - 缓存缺失 → 只显示提示"跑 bz <code> 获取完整评分"，不自动跑 6 次 AI
      （避免每次 l 都 30-60s + 6 次 API 调用）
    """
    from datetime import datetime
    from src.core.benzong import cache

    today = datetime.now().strftime("%Y-%m-%d")
    console.print(f"\n[bold cyan]📊 笨总评分摘要[/bold cyan]")

    # 检查缓存是否有当日评分
    dims_to_check = ["industry_prosperity", "business_purity", "valuation_position",
                     "industry_leader", "market_recognition", "risk_deduction"]
    cached = {}
    for dim in dims_to_check:
        r = cache.get(stock_code, today, dim)
        if r is not None:
            cached[dim] = r

    if len(cached) == 6:
        # 全缓存命中 → 算总分显示
        from src.core.benzong import score_one
        # 流动性用缓存里的（或默认 1.0）。审查修复 L-B：market_turnover 不在维度结果 dict 里
        # （维度结果只有 score/confidence/sources/...），.get(key,1.0) 在 key 缺失时返 1.0，
        # 但若将来 key 存在且为 None 会传 None 给 liquidity_coefficient 崩溃。用 `or 1.0` 兜底 None/0。
        mt = cached.get("industry_prosperity", {}).get("market_turnover") or 1.0
        bs = score_one(
            industry_prosperity=cached["industry_prosperity"]["score"],
            business_purity=cached["business_purity"]["score"],
            valuation_position=cached["valuation_position"]["score"],
            industry_leader=cached["industry_leader"]["score"],
            market_recognition=cached["market_recognition"]["score"],
            risk_deduction=cached["risk_deduction"]["score"],
            market_turnover_trillion=mt,
            stock_code=stock_code, stock_name=stock_name,
        )
        grade_colors = {"A": "green", "B": "green", "C": "yellow", "D": "red", "F": "red"}
        eff = bs.effective_grade()
        gc = grade_colors.get(eff, "white")
        dg = f" [dim](原{bs.grade()})[/dim]" if eff != bs.grade() else ""
        console.print(f"  评分 [bold]{bs.normalized_score():.0f}[/bold]/100 → [{gc}]{eff} 级[/{gc}]{dg}")
        console.print(f"  [dim]（今日已评分，缓存命中。跑 bz {stock_code} --refresh 可重算）[/dim]")
    elif len(cached) > 0:
        console.print(f"  [yellow]部分维度已评分（{len(cached)}/6），跑 bz {stock_code} 完成剩余维度[/yellow]")
    else:
        console.print(f"  [dim]今日未评分。跑 [bold]bz {stock_code}[/bold] 获取 6 维 AI 自动评分（约 30-60s）[/dim]")


def _display_trade_plan(pos):
    """展示某持仓的完整 TradePlan（从 plan 分支抽出复用）。"""
    tp = pos.trade_plan
    if tp is None:
        console.print(f"[yellow]⚠ {pos.stock_code} 暂无 TradePlan[/yellow]")
        return
    outlook_color = {"bullish": "green", "neutral": "yellow", "bearish": "red"}.get(tp.fundamental_outlook, "white")
    console.print(f"\n[bold cyan]📋 {pos.stock_code} {pos.stock_name} — 交易计划[/bold cyan]")
    console.print(f"  plan_id: {tp.plan_id}")
    console.print(f"  opened_at: {tp.opened_at}")
    if tp.mode:
        mode_cn = {"qizong": "⛰️气宗(长期格局)", "jianzong": "🗡️剑宗(一波流)"}.get(tp.mode, tp.mode)
        console.print(f"  mode: {mode_cn}")
    console.print(f"  why_buy: {tp.why_buy}")
    console.print(f"  when_buy: {tp.when_buy}")
    console.print(f"  how_much: {tp.how_much:.0%} 仓位")
    console.print(f"  止盈分档: {' / '.join(f'¥{t:.2f}' for t in tp.when_sell_targets) or '-'}")
    console.print(f"  失效条件:")
    for c in tp.when_sell_invalidate:
        console.print(f"    - {c}")
    console.print(f"  [red]locked_initial_stop: ¥{tp.locked_initial_stop:.2f}[/red]")
    console.print(f"  [red]current_stop: ¥{tp.current_stop:.2f}[/red] (单向上移)")
    console.print(f"  max_hold_days: {tp.max_hold_days} 天")
    console.print(f"  fundamental_outlook: [{outlook_color}]{tp.fundamental_outlook}[/{outlook_color}]")
    if tp.thesis_sources:
        console.print(f"  [dim]thesis_sources: {len(tp.thesis_sources)} 条锚点[/dim]")
    if tp.adjustments:
        console.print(f"  [dim]adjustments: {len(tp.adjustments)} 条调整记录[/dim]")


def _generate_or_update_plan(pm, stock_code: str, update: bool = False) -> bool:
    """为单只持仓生成或更新 TradePlan。返回是否成功。

    - 生成（update=False 或无现有计划）：用 generator 按当前行情生成
    - 更新（update=True 且有现有计划）：重新生成但保留 opened_at（建仓日期不变），
      记录调整审计，current_stop 单向上移（不低于旧值）
    """
    from src.core.trade_plan import TradePlanGenerator

    pos = pm.get_position(stock_code)
    if pos is None:
        console.print(f"[red]✗ {stock_code} 无持仓记录[/red]")
        return False

    old_plan = pos.trade_plan if update else None
    action_label = "更新" if (update and old_plan) else "生成"

    # 拉当前行情（同 _try_attach_trade_plan）
    stock_data = None
    try:
        from src.data.akshare_client import get_stock_data
        stock_data = get_stock_data(stock_code)
        if stock_data:
            atr_str = f"{stock_data.atr_14:.2f}" if stock_data.atr_14 else "?"
            console.print(f"   [green]✓[/green] 读取当前技术面（ATR={atr_str}, 价格=¥{stock_data.price:.2f}）")
    except Exception as e:
        console.print(f"   [red]✗ 技术面数据拉取失败: {type(e).__name__}: {str(e)[:80]}[/red]")
        console.print(f"   [dim]检查网络/代理，或跑 bz --check 体检数据源[/dim]")
        return False

    if stock_data is None:
        console.print(f"   [red]✗ 无法获取技术面数据，{action_label}已跳过[/red]")
        return False

    # 入场价：有持仓用持仓价，更新时用当前价重算止损基准
    entry_price = pos.entry_price if pos.entry_price else stock_data.price
    ratio = pos.current_ratio if pos.current_ratio else 0.20

    rag_service = None
    try:
        from src.rag.service import get_rag_service
        rag_service = get_rag_service()
    except Exception:
        pass

    # 跳法A 阶段1.2：笨总评分定 mode。
    # 更新模式下不重跑笨总评分（避免 mode 摇摆），由旧 mode 反推 grade 保持持有参数一致；
    # 仅新生成时跑评分。
    benzong_grade = None
    industry_prosperity = None
    if old_plan and old_plan.mode:
        benzong_grade = {"qizong": "A", "jianzong": "B"}.get(old_plan.mode)
        # 审查修复 chat-M1：原 industry_prosperity=None 绕过 _mode_from_grade 大前提守卫
        # （ip<=0 检查被 None 跳过 -> prosperity 崩到 0 仍保持气宗压制 exit）。取今日缓存的景气度
        # （不重跑评分，符合"更新不重跑"意图），让大前提守卫生效；缓存无则回退 None（原行为）。
        try:
            from src.core.benzong import cache as bz_cache
            from datetime import datetime as _dt
            cached_ip = bz_cache.get(stock_code, _dt.now().strftime("%Y-%m-%d"), "industry_prosperity")
            if cached_ip and "score" in cached_ip:
                industry_prosperity = cached_ip["score"]
                console.print(f"   [dim]沿用旧计划 mode={old_plan.mode}（更新不重跑评分，景气度取缓存={industry_prosperity:.0f}）[/dim]")
            else:
                console.print(f"   [dim]沿用旧计划 mode={old_plan.mode}（更新不重跑笨总评分，景气度未知）[/dim]")
        except Exception:
            console.print(f"   [dim]沿用旧计划 mode={old_plan.mode}（更新不重跑笨总评分）[/dim]")
    else:
        try:
            from src.core.benzong.auto_scorer import auto_score
            console.print(f"   [cyan]🧮 笨总评分中（定气宗/剑宗模式）...[/cyan]")
            bz = auto_score(stock_code, name=pos.stock_name or stock_code)
            benzong_grade = bz.score.effective_grade()
            industry_prosperity = bz.score.industry_prosperity
            console.print(f"   [green]✓[/green] 笨总评分 {bz.score.normalized_score():.0f}/100 级别 {benzong_grade}（行业景气={industry_prosperity:.0f}）")
        except Exception as e:
            logger.warning(f"{action_label}时笨总评分失败: {e}")
            console.print(f"   [yellow]⚠ 笨总评分未获取，mode 不设定[/yellow]")

    gen = TradePlanGenerator(rag_service=rag_service, ai_modifier=None)
    # market_state 已不参与 mode 判定（ISS-051 回退阶段4牛市闸门，改用个股级 Weinstein stage，
    # gen.generate 内部 _detect_weinstein_stage 自算）。原 _ms 计算与 StateMachine 调用是死代码，已删。
    try:
        plan, meta = gen.generate(
            stock_code=stock_code, stock_name=pos.stock_name or stock_code,
            entry_price=entry_price, ratio=ratio, stock_data=stock_data,
            benzong_grade=benzong_grade, industry_prosperity=industry_prosperity,
        )
    except Exception as e:
        console.print(f"   [red]✗ {action_label}失败: {e}[/red]")
        return False

    # 更新模式：保留 opened_at + current_stop 单向上移 + 记审计
    if old_plan:
        plan.opened_at = old_plan.opened_at  # 建仓日期不变
        plan.plan_id = old_plan.plan_id
        # current_stop 单向上移（不低于旧值，trailing 纪律）
        if old_plan.current_stop and plan.current_stop < old_plan.current_stop:
            plan.locked_initial_stop = old_plan.locked_initial_stop
            plan.current_stop = old_plan.current_stop
            console.print(f"   [dim]current_stop 保持 ¥{old_plan.current_stop:.2f}（单向上移，不降）[/dim]")
        # 保留旧调整历史 + 追加本次
        plan.adjustments = list(old_plan.adjustments or [])
        from datetime import datetime
        plan.adjustments.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "field": "plan_refresh",
            "old": "按旧行情",
            "new": "按当前行情重算",
            "reason": f"pos plan --update 按当前行情刷新（价格¥{stock_data.price:.2f}）",
            "source": "user",
        })

    # 写回
    if pm.attach_plan(stock_code, plan):
        console.print(f"   [green]✓ {action_label}成功[/green]")
        if meta.get("fallback_reasons"):
            console.print(f"   [dim]⚠ 降级提示: {' / '.join(meta['fallback_reasons'])}[/dim]")
        return True
    else:
        console.print(f"   [red]✗ 写回失败[/red]")
        return False


def manage_positions(action: str, stock_code: str = "", name: str = "", price: float = 0.0, ratio: float = 0.20, update: bool = False):
    """持仓管理子命令"""
    pm = PortfolioManager()

    if action == "list":
        positions = pm.list_positions()
        if not positions:
            console.print("\n[dim]当前无持仓记录[/dim]")
            return

        console.print(f"\n[bold cyan]📂 持仓列表[/bold cyan]")
        table = Table()
        table.add_column("代码", style="cyan")
        table.add_column("名称", style="white")
        table.add_column("仓位", justify="right")
        table.add_column("开仓价", justify="right")
        table.add_column("生命周期", style="yellow")
        table.add_column("动作语义", style="magenta")
        table.add_column("卖出路径", style="dim")
        table.add_column("开仓日期")
        table.add_column("上次操作")

        for pos in positions:
            ratio_str = f"{pos.current_ratio:.0%}"
            price_str = f"{pos.entry_price:.2f}" if pos.entry_price else "-"
            table.add_row(
                pos.stock_code,
                pos.stock_name or "-",
                ratio_str,
                price_str,
                pos.lifecycle,
                pos.last_action_semantic or "-",
                pos.last_sell_path or "-",
                pos.entry_date or "-",
                f"{pos.last_action} ({pos.last_action_date or '-'})",
            )

        console.print(table)

        # v0.8.5 TradePlan：列出所有有计划的持仓，让用户跑 pos 时看见计划核心字段
        with_plan = [p for p in positions if p.trade_plan is not None]
        if with_plan:
            console.print(f"\n[bold cyan]📋 交易计划摘要（{len(with_plan)} 只有计划）[/bold cyan]")
            for pos in with_plan:
                tp = pos.trade_plan
                outlook_color = {"bullish": "green", "neutral": "yellow", "bearish": "red"}.get(tp.fundamental_outlook, "white")
                targets = " / ".join(f"¥{t:.2f}" for t in tp.when_sell_targets) if tp.when_sell_targets else "-"
                console.print(
                    f"  [cyan]{pos.stock_code}[/cyan] {pos.stock_name}: "
                    f"止损 [red]¥{tp.current_stop:.2f}[/red] (初始 ¥{tp.locked_initial_stop:.2f}) | "
                    f"止盈 {targets} | "
                    f"持有上限 {tp.max_hold_days} 天 | "
                    f"前景 [{outlook_color}]{tp.fundamental_outlook}[/{outlook_color}]"
                )
                console.print(f"    [dim]thesis: {tp.why_buy[:60]}{'...' if len(tp.why_buy) > 60 else ''}[/dim]")
        else:
            console.print(f"\n[dim]💡 提示：持仓尚无 TradePlan。下次跑 `pos add` 时系统会自动 AI 辅助生成交易计划（v0.8.5 新增）[/dim]")

    elif action == "add":
        if not stock_code:
            console.print("[red]请指定股票代码[/red]")
            return
        if pm.has_position(stock_code):
            console.print(f"[yellow]⚠ {stock_code} 已有持仓记录，请先 --pos-remove 删除[/yellow]")
            return

        pm.add_position(
            stock_code=stock_code,
            stock_name=name or stock_code,
            entry_price=price if price > 0 else None,
            ratio=ratio,
        )
        console.print(f"[green]✓ 已添加持仓: {name or stock_code} ({stock_code})[/green]")
        console.print(f"  仓位: {ratio:.0%}" + (f"  开仓价: {price:.2f}" if price > 0 else ""))

        # v0.8.5 阶段 1.2：AI 辅助建仓引导器
        # 入场价存在时自动生成 TradePlan 草稿，缺失则跳过（用户可后续手动补）
        if price > 0:
            _try_attach_trade_plan(pm, stock_code, name or stock_code, price, ratio)

    elif action == "remove":
        if not stock_code:
            console.print("[red]请指定股票代码[/red]")
            return
        if not pm.has_position(stock_code):
            console.print(f"[yellow]⚠ {stock_code} 无持仓记录[/yellow]")
            return

        pos = pm.get_position(stock_code)
        pm.remove_position(stock_code)
        console.print(f"[green]✓ 已删除持仓: {pos.stock_name or stock_code} ({stock_code})[/green]")

    elif action == "plan":
        # v0.8.5 查看/生成单只；v0.8.6.3 --update 更新 + all 批量
        # stock_code 可能是 "all"（批量）
        if stock_code.lower() in ("all", "*", "全部"):
            positions = pm.list_positions()
            if not positions:
                console.print("[yellow]⚠ 当前无持仓记录[/yellow]")
                return
            console.print(f"\n[bold cyan]📋 批量{'更新' if update else '生成'}交易计划（{len(positions)} 只持仓）[/bold cyan]")
            ok, skip, fail = 0, 0, 0
            for pos in positions:
                if not update and pos.trade_plan is not None:
                    skip += 1
                    console.print(f"  [dim]⏭ {pos.stock_code} {pos.stock_name} 已有计划，跳过（用 pos plan all --update 更新）[/dim]")
                    continue
                console.print(f"\n[bold]▶ {pos.stock_code} {pos.stock_name}[/bold]")
                if _generate_or_update_plan(pm, pos.stock_code, update=update):
                    ok += 1
                else:
                    fail += 1
            console.print(f"\n[bold]批量完成：✓ {ok} 成功 / ⏭ {skip} 跳过 / ✗ {fail} 失败[/bold]")
            return

        # 单只
        if not pm.has_position(stock_code):
            console.print(f"[yellow]⚠ {stock_code} 无持仓记录（先 pos add {stock_code}）[/yellow]")
            return
        pos = pm.get_position(stock_code)
        has_plan = pos.trade_plan is not None

        if not update and has_plan:
            # 仅查看（原行为）
            _display_trade_plan(pos)
            console.print(f"\n[dim]💡 行情变动后可 `pos plan {stock_code} --update` 按当前行情更新计划[/dim]")
            return

        # 生成（无计划）或更新（有计划 + --update）
        if _generate_or_update_plan(pm, stock_code, update=update):
            pos = pm.get_position(stock_code)  # 重新读
            _display_trade_plan(pos)


def scan_market(
    rule_name: str = "healthy_pullback",
    market_query: str = None,
    ai_debug: bool = False,
    deep: bool = False,
    ai_enabled: bool = True,
):
    """全市场扫描 — 初筛候选股 + 可选深度分析

    两步走模式：
    1. quick_scan() → 展示候选池
    2. 用户选择后 → deep_analyze() 逐只深度分析

    Args:
        rule_name: 扫描规则名称
        market_query: 统一主题词，自动匹配相关行业/概念
        ai_debug: 是否开启AI调试模式
        deep: 是否自动执行深度分析（跳过用户选择）
        ai_enabled: 深度分析时是否启用AI
    """
    from src.scanner.scanner_engine import ScannerEngine
    from src.data.portfolio import PortfolioManager

    config = load_config()
    scanner_cfg = config.get("scanner", {})
    ai_config = config.get("ai", None)
    skills_dir = config.get("skills", {}).get("dir", "./src/skills")
    enabled_skills = config.get("skills", {}).get("enabled", [])
    signal_weights = config.get("decision", {}).get("signal_weights", {})
    skill_types = config.get("skills", {}).get("types", {})

    if ai_debug and ai_config:
        ai_config["debug"] = True

    # 排除已持仓股票
    exclude_codes = set()
    if scanner_cfg.get("auto_exclude_holdings", True):
        try:
            pm = PortfolioManager()
            positions = pm.list_positions()
            exclude_codes = {pos.stock_code for pos in positions}
        except Exception:
            pass

    # 创建Scanner引擎
    entry_exit_config = config.get("entry_exit", None)
    engine = ScannerEngine(
        rules_path=scanner_cfg.get("rules_path", "./src/scanner/scan_rules.yaml"),
        skills_dir=skills_dir,
        enabled_skills=enabled_skills,
        signal_weights=signal_weights,
        skill_types=skill_types,
        ai_config=ai_config,
        cache_ttl=scanner_cfg.get("cache_ttl", 300),
        entry_exit_config=entry_exit_config,
    )

    theme_query = market_query
    effective_rule = rule_name or "healthy_pullback"
    if not theme_query:
        resolved_rule = engine.resolve_rule_name(effective_rule)
        if resolved_rule:
            effective_rule = resolved_rule
        else:
            theme_query = effective_rule
            effective_rule = "healthy_pullback"

    # ===== Step 1: 快速初筛 =====
    # 规则名模糊匹配
    resolved_name = engine.resolve_rule_name(effective_rule)
    if not resolved_name:
        console.print(f"[red]未找到规则 '{effective_rule}'[/red]")
        console.print("\n可用规则:")
        for r in engine.get_available_rules():
            console.print(f"  [cyan]{r['name']:20s}[/cyan] ({r['display_name']}) - {r['description']}")
        console.print("\n[dim]提示: 支持模糊匹配，如 '缩量' → 缩量回调, '动量' → 强势动量[/dim]")
        return

    rule_info = None
    for r in engine.get_available_rules():
        if r["name"] == resolved_name:
            rule_info = r
            break

    rule_display = rule_info["display_name"] if rule_info else resolved_name
    console.print(f"\n[bold cyan]全市场扫描[/bold cyan] — {rule_display}")

    # v0.8.4: 大盘环境前置检查
    try:
        from src.data.akshare_client import AKShareClient
        idx = AKShareClient._get_index_trend()
        if idx:
            trend = idx.get("trend", "NEUTRAL")
            trend_cn = {"BULLISH": "多头(🟢 适合积极选股)", "BEARISH": "空头(🔴 控制仓位)", "NEUTRAL": "中性(🟡 谨慎操作)"}.get(trend, trend)
            trend_color = {"BULLISH": "green", "BEARISH": "red", "NEUTRAL": "yellow"}.get(trend, "white")
            idx_info = f"沪深300: MA20={idx.get('ma20', 0):.0f} MA60={idx.get('ma60', 0):.0f} 趋势={trend_cn}"
            if idx.get("change_pct") is not None:
                idx_info += f" 涨跌={idx['change_pct']:+.2f}%"
            console.print(f"  [{trend_color}]大盘: {idx_info}[/{trend_color}]")
            if trend == "BEARISH":
                console.print(f"  [red]⚠ 大盘空头环境，选股需严格风控，建议轻仓或空仓[/red]")
            elif trend == "NEUTRAL":
                console.print(f"  [yellow]⚠ 大盘方向不明，建议只选最强趋势的股票[/yellow]")
    except Exception as e:
        logger.debug(f"大盘环境检查失败: {e}")

    # 显示缓存状态
    cache_status = engine.market_cache.get_cache_status()
    if cache_status["stocks"]["cached"] and not cache_status["stocks"]["expired"]:
        console.print(f"  行情缓存: [green]命中[/green] ({cache_status['stocks']['count']}只, {cache_status['stocks']['age_seconds']}秒前)")
    else:
        console.print("  行情缓存: [yellow]未命中，正在获取全市场数据（约4分钟）...[/yellow]")

    # v0.8.4: 初筛仅有当日快照，深度分析的 Weinstein 阶段(S2/S4)做趋势确认
    console.print("  [dim]提示: 初筛基于当日数据，深度分析可补充趋势判断(Weinstein S1-S4)[/dim]")

    if theme_query:
        console.print(f"  主题词: {theme_query}")

    with console.status("扫描中..."):
        candidates, scan_info = engine.quick_scan(
            rule_name=effective_rule,
            market_query=theme_query,
            exclude_codes=exclude_codes,
        )

    if "error" in scan_info:
        console.print(f"[red]扫描失败: {scan_info['error']}[/red]")
        if "available_keys" in scan_info:
            console.print("\n可用规则:")
            for k, n in zip(scan_info["available_keys"], scan_info["available_names"]):
                console.print(f"  [cyan]{k:20s}[/cyan] ({n})")
        return

    if not candidates:
        # 给出详细信息帮助用户理解为什么没有结果
        total = scan_info.get("total_stocks", "?")
        after_exclude = scan_info.get("after_exclude", "?")
        console.print(f"\n[yellow]未找到符合条件的股票[/yellow]")
        console.print(f"  过滤过程: 全市场 {total} 只 → 排除ST/停牌/北交所后 {after_exclude} 只", highlight=False)
        if theme_query:
            console.print(f"  主题匹配后: {scan_info.get('after_theme', '?')} 只", highlight=False)
        console.print(f"  规则「{rule_display}」过滤后: 0 只", highlight=False)
        console.print(f"\n  [dim]可能原因:[/dim]")
        console.print(f"  [dim]1. 当前非交易时段，量比/换手率等实时指标可能为0或无效[/dim]")
        console.print(f"  [dim]2. 筛选条件较严格，可尝试其他规则（如 scan market 低估 或 scan market 超跌）[/dim]")
        console.print(f"  [dim]3. 盘中/盘后数据完整度不同，建议交易时段或收盘后使用[/dim]")
        console.print(f"\n  [dim]可用规则:[/dim]")
        for r in engine.get_available_rules():
            console.print(f"  [dim]  scan market {r['name']:20s} ({r['display_name']}) - {r['description']}[/dim]")
        return

    if scan_info.get("market_query"):
        matched_industries = scan_info.get("matched_industries") or []
        matched_concepts = scan_info.get("matched_concepts") or []
        source_label = {
            "ai": "AI",
            "direct": "直连",
        }.get(scan_info.get("match_source"), "本地")
        if matched_industries:
            console.print(f"  行业匹配({source_label}): {', '.join(matched_industries)}")
        if matched_concepts:
            console.print(f"  概念匹配({source_label}): {', '.join(matched_concepts)}")
        if scan_info.get("fallback_unfiltered"):
            console.print("  [yellow]主题匹配失败，已回退为全市场规则扫描[/yellow]")

    # 展示候选池
    elapsed = scan_info.get("elapsed_seconds", 0)
    console.print(
        f"  初筛: {scan_info.get('total_stocks', '?')}只 → "
        f"[green]{len(candidates)}只[/green] 匹配 "
        f"({elapsed}秒)"
    )

    # 候选股表格
    table = Table(title=f"候选股票池 — {rule_display}", show_lines=False)
    table.add_column("代码", style="cyan", width=8)
    table.add_column("名称", style="white", width=10)
    table.add_column("最新价", justify="right", width=8)
    table.add_column("涨跌幅%", justify="right", width=8)
    table.add_column("换手率%", justify="right", width=8)
    table.add_column("量比", justify="right", width=6)
    table.add_column("振幅%", justify="right", width=6)
    table.add_column("成交额(亿)", justify="right", width=10)

    for c in candidates:
        # 中国股市惯例：涨红跌绿
        change_style = "red" if (c.change_pct or 0) > 0 else "green" if (c.change_pct or 0) < 0 else "white"
        amount_yi = f"{c.amount / 1e8:.1f}" if c.amount else "-"
        table.add_row(
            c.stock_code,
            c.stock_name,
            f"{c.price:.2f}" if c.price else "-",
            f"[{change_style}]{c.change_pct:+.2f}[/{change_style}]" if c.change_pct is not None else "-",
            f"{c.turnover_rate:.2f}" if c.turnover_rate else "-",
            f"{c.volume_ratio:.2f}" if c.volume_ratio else "-",
            f"{c.amplitude:.2f}" if c.amplitude else "-",
            amount_yi,
        )

    console.print(table)

    # ===== Step 2: 深度分析 =====
    # 已分析的代码集合，支持多轮选择
    analyzed_results = []
    remaining_candidates = list(candidates)  # 可分析的候选股

    while remaining_candidates:
        if deep:
            # 自动全量深度分析
            selected_codes = [c.stock_code for c in remaining_candidates]
            console.print(f"\n[bold]自动深度分析 {len(selected_codes)} 只候选股...[/bold]")
            remaining_candidates = []
        else:
            # 两步走：让用户选择
            remaining_display = ", ".join(c.stock_code for c in remaining_candidates[:10])
            if len(remaining_candidates) > 10:
                remaining_display += f" ... 等{len(remaining_candidates)}只"
            console.print(
                f"\n  [dim]待分析: {remaining_display}[/dim]"
            )
            console.print(
                "  [dim]输入代码深度分析(如: 600546 002192) 或 all(全选) 或 q(跳过)[/dim]"
            )
            try:
                choice = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if choice.lower() in ("q", "quit", ""):
                console.print("  跳过剩余深度分析")
                break

            if choice.lower() == "all":
                selected_codes = [c.stock_code for c in remaining_candidates]
                console.print(f"\n  [bold]深度分析 {len(selected_codes)} 只候选股...[/bold]")
                remaining_candidates = []
            else:
                selected_codes = [c.strip() for c in choice.split() if c.strip()]
                # 从剩余候选中移除已选择的
                remaining_candidates = [c for c in remaining_candidates if c.stock_code not in selected_codes]

        if not selected_codes:
            break

        # 执行深度分析
        ranking_config = config.get("ranking", {})
        batch_results = engine.deep_analyze(
            stock_codes=selected_codes,
            candidates=candidates,
            ai_enabled=ai_enabled,
            ai_debug=ai_debug,
            progress_callback=_scan_progress_callback,
            ranking_config=ranking_config,
        )
        analyzed_results.extend(batch_results)

        # 非自动模式且还有候选，展示本轮结果后继续循环
        if not deep and remaining_candidates:
            _display_scan_deep_results(batch_results)
            console.print(f"\n  [dim]还剩 {len(remaining_candidates)} 只候选股待分析[/dim]")

    # 展示汇总结果
    if analyzed_results:
        _display_scan_deep_results(analyzed_results)


def _scan_progress_callback(step: str, current: int, total: int, message: str):
    """扫描进度回调"""
    if step == "deep_analyze":
        console.print(f"  [dim][{current}/{total}] {message}[/dim]")


def _display_scan_deep_results(results: list[dict]):
    """展示深度分析结果排名表（v0.8.0 Phase 4 按综合评分排序）"""
    if not results:
        return

    success_results = [r for r in results if r.get("success")]

    if not success_results:
        console.print("[red]所有股票深度分析均失败[/red]")
        for r in results:
            if r.get("error"):
                console.print(f"  {r['stock_code']}: {r['error']}")
        return

    # 检查是否有排名数据
    has_ranking = any(r.get("ranking") for r in success_results)

    if has_ranking:
        _display_ranked_results(success_results)
    else:
        _display_unranked_results(success_results)

    # 失败提示
    failed = [r for r in results if not r.get("success")]
    if failed:
        console.print(f"\n  [dim]{len(failed)}只分析失败[/dim]")


def _display_ranked_results(success_results: list[dict]):
    """展示带排名的深度分析结果"""
    # 排名表格
    table = Table(title="深度分析排名（按综合评分排序）", show_lines=False)
    table.add_column("排名", justify="center", width=4)
    table.add_column("代码", style="cyan", width=8)
    table.add_column("名称", style="white", width=10)
    table.add_column("综合分", justify="right", width=6)
    table.add_column("技术", justify="right", width=5)
    table.add_column("情绪", justify="right", width=5)
    table.add_column("流动", justify="right", width=5)
    table.add_column("波动", justify="right", width=5)
    table.add_column("决策", style="bold", width=6)
    table.add_column("仓位", width=8)
    table.add_column("语义", width=6)
    table.add_column("阶段", width=6)
    table.add_column("买卖点", width=18)
    table.add_column("AI情绪", width=10)

    buy_count = 0
    sell_count = 0
    watch_count = 0
    hold_count = 0
    top3_stocks = []

    for r in success_results:
        dr = r.get("decision_result")
        sd = r.get("strategy_decision")
        ai_r = r.get("ai_result")
        ranking = r.get("ranking")

        decision = dr.decision.value if dr else "?"
        pos_action = sd.position_action.value if sd else "?"
        action_semantic = sd.action_semantic if sd and sd.action_semantic else "-"

        # AI情绪（排名表格）
        ai_str = "无数据"
        if ai_r and ai_r.adjusted:
            sentiment_map = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
            ai_str = f"{sentiment_map.get(ai_r.sentiment, '?')}({ai_r.confidence:.0%})"
        elif ai_r and not ai_r.adjusted:
            ai_str = f"未生效({ai_r.summary or '未知'})"

        # 决策颜色
        decision_style = {
            "BUY": "green", "HOLD": "cyan", "SELL": "red", "WATCH": "yellow"
        }.get(decision, "white")

        # 统计
        if decision == "BUY":
            buy_count += 1
        elif decision == "SELL":
            sell_count += 1
        elif decision == "WATCH":
            watch_count += 1
        else:
            hold_count += 1

        # 排名数据
        if ranking:
            rank_str = str(ranking.rank)
            total_score = ranking.total_score

            # 综合分颜色
            if total_score >= 70:
                score_style = "green"
            elif total_score >= 50:
                score_style="yellow"
            else:
                score_style = "dim"

            # 各维度分
            dim_map = {d.name: d for d in ranking.dimensions}
            tech_str = f"{dim_map['technical'].score:.0f}" if 'technical' in dim_map else "-"
            sent_str = f"{dim_map['sentiment'].score:.0f}" if 'sentiment' in dim_map else "-"
            liq_str = f"{dim_map['liquidity'].score:.0f}" if 'liquidity' in dim_map else "-"
            vol_str = f"{dim_map['volatility'].score:.0f}" if 'volatility' in dim_map else "-"

            # TOP3 加粗
            if ranking.rank <= 3:
                rank_str = f"[bold]{rank_str}[/bold]"
                top3_stocks.append(f"{r['stock_code']} {r.get('stock_name', '')}({total_score}分)")

            # 买卖点摘要
            ee_str = _format_entry_exit_brief(sd)
            # Weinstein 阶段
            stock_d3 = dr.stock if dr and hasattr(dr, 'stock') else None
            stage_str = _weinstein_stage(stock_d3) if stock_d3 else "[dim]?[/dim]"

            table.add_row(
                rank_str,
                r["stock_code"],
                r.get("stock_name", ""),
                f"[{score_style}]{total_score:.0f}[/{score_style}]",
                tech_str,
                sent_str,
                liq_str,
                vol_str,
                f"[{decision_style}]{decision}[/{decision_style}]",
                pos_action,
                action_semantic,
                stage_str,
                ee_str,
                ai_str,
            )
        else:
            # 无排名数据时用旧格式
            score = dr.score if dr else 0
            ee_str = _format_entry_exit_brief(sd)
            stock_d4 = dr.stock if dr and hasattr(dr, 'stock') else None
            stage_str = _weinstein_stage(stock_d4) if stock_d4 else "[dim]?[/dim]"
            table.add_row(
                "-",
                r["stock_code"],
                r.get("stock_name", ""),
                f"{score:.2f}",
                "-",
                "-",
                "-",
                "-",
                f"[{decision_style}]{decision}[/{decision_style}]",
                pos_action,
                action_semantic,
                stage_str,
                ee_str,
                ai_str,
            )

    console.print(table)

    # 操作建议
    action_parts = []
    action_style = {"BUY": "green", "HOLD": "cyan", "SELL": "red", "WATCH": "yellow"}
    for action, count in [("BUY", buy_count), ("SELL", sell_count), ("WATCH", watch_count), ("HOLD", hold_count)]:
        if count > 0:
            style = action_style.get(action, "white")
            action_parts.append(f"[{style}]{action}×{count}[/{style}]")

    if action_parts:
        console.print(f"\n  操作建议: {'  '.join(action_parts)}")

    # TOP3推荐
    if top3_stocks:
        console.print(f"  [bold green]TOP3推荐[/bold green]: {' | '.join(top3_stocks)}")

    # ISS-031: 触发了买卖点的股票，补完整理由（短摘要在表格里截断会丢关键数值/MA偏离/盈利%）
    triggered = []
    for r in success_results:
        sd = r.get("strategy_decision")
        if not sd or not getattr(sd, "entry_exit", None):
            continue
        ee = sd.entry_exit
        if ee.get("entry_triggered") or ee.get("exit_triggered"):
            triggered.append((r, ee))
    if triggered:
        console.print(f"\n  [bold]买卖点触发详情：[/bold]")
        for r, ee in triggered:
            code = r["stock_code"]
            name = r.get("stock_name", "")
            if ee.get("entry_triggered"):
                ratio_pct = int(ee.get("entry_ratio", 0) * 100)
                reason = ee.get("entry_reason", "")
                console.print(f"  [green]▶[/green] {code} {name} 建仓{ratio_pct}%: {reason}")
            elif ee.get("exit_triggered"):
                action_cn = _exit_action_cn(ee.get('exit_action', ''), ee.get('exit_ratio', 0), ee.get('exit_type', ''))
                reason = ee.get("exit_reason", "")
                stop = ee.get("chandelier_stop_price")
                metric_str = f"止损价 ¥{stop:.2f} | " if stop else ""
                console.print(f"  [red]◀[/red] {code} {name} {action_cn}: {metric_str}{reason}")

    # 评分解读
    console.print(
        "\n  [dim]评分解读: "
        "综合分=加权总分(技术40%+情绪20%+流动20%+波动20%) | "
        "技术=核心信号(看这个最重要) | "
        "情绪=AI判断(50=中性,>50看多,<50看空) | "
        "流动=成交活跃度 | 波动=振幅适度性[/dim]"
    )


def _exit_action_cn(exit_action: str, exit_ratio: float, exit_type: str = "") -> str:
    """把内部动作枚举翻译成中文执行语义+子类型标签。"""
    # exit_type 子语义映射
    type_labels = {
        "chandelier_stop": "Chandelier止损",
        "trend_break": "趋势破坏",
        "take_profit": "止盈",
        "capital_outflow": "资金流出",
    }
    type_tag = type_labels.get(exit_type, exit_type.replace("_", " ").title()) if exit_type else ""

    if exit_action == "EXIT" or exit_ratio >= 1.0:
        base = "清仓"
    else:
        pct = int(exit_ratio * 100)
        base = f"减仓{pct}%"
    
    if type_tag and type_tag not in base:
        return f"{base}({type_tag})"
    return base


def _weinstein_stage(sd) -> str:
    """Weinstein四阶段分类（基于StockData技术指标）
    
    阶段1(筑底): MA20与MA60缠绕，价格在两者附近
    阶段2(上升): MA20>MA60，价格>MA20
    阶段3(顶部): 价格高位，MA走平或即将死叉
    阶段4(下跌): MA20<MA60，价格<MA20
    """
    if not sd:
        return "[dim]?[/dim]"
    try:
        ma20 = getattr(sd, 'ma20', None)
        ma60 = getattr(sd, 'ma60', None)
        price = getattr(sd, 'price', None)
        if ma20 is None or ma60 is None or price is None:
            return "[dim]?[/dim]"
        
        if ma20 > ma60:
            if price > ma20:
                return "[green]S2↑[/green]"
            elif price > ma60:
                return "[yellow]S2-[/yellow]"
            else:
                return "[yellow]S1[/yellow]"
        elif ma20 < ma60:
            if price < ma20:
                return "[red]S4↓[/red]"
            elif price < ma60:
                return "[red]S3[/red]"
            else:
                return "[yellow]S3[/yellow]"
        else:
            if price > ma20:
                return "[yellow]S1+[/yellow]"
            else:
                return "[yellow]S1[/yellow]"
    except Exception:
        return "[dim]?[/dim]"


def _format_entry_exit_brief(sd) -> str:
    """买卖点简要摘要，用于扫描表格列。

    这里不是要把所有细节都塞进一列，而是给 scan 结果提供一个高密度、低噪音的摘要：
    - 买点：建仓比例 + 关键理由截断
    - 卖点：动作+子语义 + 关键指标（Chandelier 用结构化止损价，其他用 reason 截断）
    - 无触发：统一显示为空，避免让用户误以为漏了信号

    ISS-031 收尾：尽量从结构化字段（chandelier_stop_price 等）取关键指标，
    避免对 reason 文本做正则提取（脆弱，随模板改写易 break）。
    """
    if not sd or not getattr(sd, "entry_exit", None):
        return "[dim]-[/dim]"
    ee = sd.entry_exit
    if ee.get("entry_triggered"):
        ratio_pct = int(ee.get("entry_ratio", 0) * 100)
        reason = ee.get("entry_reason", "")[:14]
        prefix = f"建{ratio_pct}%" if ratio_pct else "建仓"
        return f"[green]▶{prefix}: {reason}[/green]"
    if ee.get("exit_triggered"):
        # exit_action 是内部枚举（EXIT/TRIM），翻成中文加子语义（清仓/减仓+原因类型）
        action_cn = _exit_action_cn(ee.get('exit_action', ''), ee.get('exit_ratio', 0), ee.get('exit_type', ''))
        exit_type = ee.get("exit_type", "")
        # Chandelier 止损：从结构化字段拼"止损¥X"，比正则切 reason 稳
        if exit_type == "chandelier_stop":
            stop = ee.get("chandelier_stop_price")
            if stop:
                return f"[red]◀{action_cn}: 止损¥{stop:.2f}[/red]"
        # 其他类型回退到 reason 截断
        reason = ee.get("exit_reason", "")[:16]
        return f"[red]◀{action_cn}: {reason}[/red]"
    return "[dim]未触发[/dim]"


def _display_unranked_results(success_results: list[dict]):
    """展示无排名的深度分析结果（兜底，排名层未启用时使用）"""
    table = Table(title="深度分析结果", show_lines=False)
    table.add_column("代码", style="cyan", width=8)
    table.add_column("名称", style="white", width=10)
    table.add_column("决策", style="bold", width=6)
    table.add_column("评分", justify="right", width=6)
    table.add_column("仓位", width=8)
    table.add_column("语义", width=6)
    table.add_column("状态", width=10)
    table.add_column("阶段", width=6)
    table.add_column("买卖点", width=18)
    table.add_column("AI情绪", width=10)

    buy_count = 0
    sell_count = 0
    watch_count = 0
    hold_count = 0

    for r in success_results:
        dr = r.get("decision_result")
        sd = r.get("strategy_decision")
        ai_r = r.get("ai_result")

        decision = dr.decision.value if dr else "?"
        score = dr.score if dr else 0
        pos_action = sd.position_action.value if sd else "?"
        action_semantic = sd.action_semantic if sd and sd.action_semantic else "-"
        lifecycle = sd.lifecycle_after.value if sd else "?"

        # AI情绪（unranked表格）
        ai_str = "无数据"
        if ai_r and ai_r.adjusted:
            sentiment_map = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
            ai_str = f"{sentiment_map.get(ai_r.sentiment, '?')}({ai_r.confidence:.0%})"
        elif ai_r and not ai_r.adjusted:
            ai_str = f"未生效({ai_r.summary or '未知'})"

        # 决策颜色
        decision_style = {
            "BUY": "green", "HOLD": "cyan", "SELL": "red", "WATCH": "yellow"
        }.get(decision, "white")

        # 统计
        if decision == "BUY":
            buy_count += 1
        elif decision == "SELL":
            sell_count += 1
        elif decision == "WATCH":
            watch_count += 1
        else:
            hold_count += 1

        # Weinstein 阶段判断
        stock_d2 = dr.stock if dr and hasattr(dr, 'stock') else None
        stage_str = _weinstein_stage(stock_d2) if stock_d2 else "[dim]?[/dim]"
        
        table.add_row(
            r["stock_code"],
            r.get("stock_name", ""),
            f"[{decision_style}]{decision}[/{decision_style}]",
            f"{score:.2f}",
            pos_action,
            action_semantic,
            lifecycle,
            stage_str,
            _format_entry_exit_brief(sd),
            ai_str,
        )

    console.print(table)

    # 操作建议
    action_parts = []
    action_style = {"BUY": "green", "HOLD": "cyan", "SELL": "red", "WATCH": "yellow"}
    for action, count in [("BUY", buy_count), ("SELL", sell_count), ("WATCH", watch_count), ("HOLD", hold_count)]:
        if count > 0:
            style = action_style.get(action, "white")
            action_parts.append(f"[{style}]{action}×{count}[/{style}]")

    if action_parts:
        console.print(f"\n  操作建议: {'  '.join(action_parts)}")


def scan_events(ai_debug: bool = False):
    """事件驱动扫描 - 检测重大市场事件并预警"""
    from src.core.event_layer import EventLayer
    from src.data.portfolio import PortfolioManager

    config = load_config()
    event_config = config.get("event", {})
    ai_config = config.get("ai", None)
    if ai_debug and ai_config:
        ai_config["debug"] = True

    if not event_config.get("enabled", False):
        console.print("[yellow]事件驱动层未启用[/yellow]（在 configs/settings.yaml 的 event.enabled 中开启）")
        return

    # 创建EventLayer
    event_layer = EventLayer(event_config, ai_config=ai_config)

    console.print(f"\n[bold cyan]⚡ 事件驱动扫描[/bold cyan]")
    console.print(f"  关键词扫描: {'✓' if event_layer.keyword_scan_enabled else '✗'}")
    console.print(f"  AI分类: {'✓' if event_layer._ai_available else '✗ (API不可用)'}")
    console.print(f"  持仓扫描: {'✓' if event_layer.portfolio_scan_enabled else '✗'}")
    console.print()

    # 获取持仓列表
    positions = []
    if event_layer.portfolio_scan_enabled:
        try:
            pm = PortfolioManager()
            positions = pm.list_positions()
            if positions:
                console.print(f"  持仓: {len(positions)}只")
        except Exception:
            pass

    # 执行完整扫描
    with console.status("扫描事件中..."):
        events = event_layer.detect_all(positions=positions if positions else None)

    # 展示结果
    if not events:
        console.print(f"\n[green]✓ 未检测到重大市场事件[/green]")
        return

    # 事件预警展示
    console.print(f"\n[bold]⚡ 事件预警 ({len(events)}条)[/bold]")

    # 事件表格
    table = Table(show_lines=False, expand=True)
    table.add_column("等级", width=4, justify="center")
    table.add_column("类型", width=8)
    table.add_column("情绪", width=6)
    table.add_column("范围", width=6)
    table.add_column("摘要", style="white", overflow="fold")
    table.add_column("四要素", style="dim", overflow="fold")
    table.add_column("来源", style="dim", overflow="fold")
    table.add_column("检测方式", style="dim", overflow="fold")

    impact_icons = {1: "·", 2: "🟡", 3: "🟠", 4: "🔴", 5: "🔴"}
    event_type_cn = {
        "policy": "政策", "war": "地缘", "earnings": "财报",
        "macro": "宏观", "black_swan": "黑天鹅", "market_crash": "暴跌", "none": ""
    }
    sentiment_cn = {"bullish": "利好", "bearish": "利空", "neutral": "中性"}
    sentiment_colors = {"bullish": "green", "bearish": "red", "neutral": "yellow"}
    scope_cn = {"market": "全市场", "sector": "行业", "stock": "个股"}
    method_cn = {"keyword": "关键词", "ai": "AI", "rule": "规则"}

    for event in events:
        icon = impact_icons.get(event.impact_level, "·")
        evt_cn = event_type_cn.get(event.event_type, event.event_type)
        sent_cn = sentiment_cn.get(event.sentiment, event.sentiment)
        sent_color = sentiment_colors.get(event.sentiment, "white")
        scp_cn = scope_cn.get(event.scope, event.scope)
        mth_cn = method_cn.get(event.detection_method, event.detection_method)

        # 受影响标的
        affected = ""
        if event.affected_codes:
            affected = f" [{','.join(event.affected_codes[:3])}]"

        # 四要素（仅 AI 检测的事件有）
        fe = event.four_elements
        if fe:
            auth = fe.get("authenticity", 0)
            auth_color = "red" if auth < 30 else ("yellow" if auth < 60 else "green")
            fe_str = (f"真[{auth_color}]{auth:.0f}[/{auth_color}] "
                      f"传{fe.get('virality', 0):.0f} "
                      f"规{fe.get('scale', 0):.0f} "
                      f"时{fe.get('timeliness', 0):.0f}")
        else:
            fe_str = "-"

        table.add_row(
            f"{icon}{event.impact_level}",
            evt_cn,
            f"[{sent_color}]{sent_cn}[/{sent_color}]",
            scp_cn,
            f"{event.summary}{affected}",
            fe_str,
            (event.source or "")[:30],
            mth_cn,
        )

    console.print(table)

    # 真实性存疑事件提示（笨总四要素，真实性<30 已降级，提示用户核实）
    suspect = [e for e in events
               if e.four_elements and e.four_elements.get("authenticity", 100) < 30]
    if suspect:
        console.print(f"\n[bold red]⚠ 真实性存疑事件 ({len(suspect)}条，已降级，请人工核实):[/bold red]")
        for event in suspect:
            console.print(f"  ❓ {event.summary} — 真实性仅 {event.four_elements['authenticity']:.0f}/100（单一来源/疑似伪信号）")

    # 高影响事件预警
    high_impact = [e for e in events if e.impact_level >= 4]
    if high_impact:
        console.print(f"\n[bold red]⚠ 高影响事件 ({len(high_impact)}条):[/bold red]")
        for event in high_impact:
            evt_cn = event_type_cn.get(event.event_type, event.event_type)
            console.print(f"  🔴 [{evt_cn}] {event.summary} — {event.source}")

    # 事件对策略的影响
    if events:
        top_event = max(events, key=lambda e: e.impact_level)
        if top_event.impact_level >= 3:
            ai_result = event_layer.to_ai_modifier_result(top_event)
            console.print(f"\n[bold]策略影响预判：[/bold]")
            if ai_result.score_adjustment != 0:
                direction = "压制" if ai_result.score_adjustment < 0 else "增强"
                console.print(f"  信号{direction}: {abs(ai_result.score_adjustment):.1%}")
            if ai_result.position_cap < 1.0:
                console.print(f"  仓位上限: {ai_result.position_cap:.0%}")
            if ai_result.force_state:
                console.print(f"  [bold red]状态干预: {ai_result.force_state}[/bold red]")


def main():
    """主入口"""
    import argparse
    from src.data.source_check import fix_curl_ssl_paths

    fix_curl_ssl_paths()

    parser = argparse.ArgumentParser(
        description="暮云思辨投资助手 - AI驱动的A股交易行为约束系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python -m src.cli.main                              # 交互模式
  python -m src.cli.main sample_data.json             # JSON文件模式
  python -m src.cli.main --live 600519               # 实时行情模式(通过AKShare)
  python -m src.cli.main -l 000001                   # 实时行情模式
  python -m src.cli.main -l 000001 --no-ai           # 实时行情但禁用AI调节
  python -m src.cli.main -l 000001 --ai-provider kimi # 临时切换为Kimi API
  python -m src.cli.main --backtest 600519           # 回测模式(默认近1年)
  python -m src.cli.main --backtest 600519 -s 2024-01-01 -e 2025-01-01  # 自定义区间
  python -m src.cli.main --backtest 600519 --capital 200000  # 自定义初始资金
    python -m src.cli.main --backtest 600519 --backtest-mode legacy_compatible  # 旧版兼容门控

持仓管理:
  python -m src.cli.main --pos-list                                     # 查看所有持仓
  python -m src.cli.main --pos-add 002192 --name 融捷股份 --price 35.20  # 添加持仓
  python -m src.cli.main --pos-add 002192 --name 融捷股份 --ratio 0.40   # 添加持仓(指定仓位)
  python -m src.cli.main --pos-remove 002192                           # 删除持仓

一键扫描:
  python -m src.cli.main --portfolio                                   # 分析所有持仓股
    python -m src.cli.main --scan                                        # 全市场默认扫描
    python -m src.cli.main --scan 缩量                                   # 按规则扫描（规则模糊匹配）
    python -m src.cli.main --scan AI                                     # 单个主题词扫描
    python -m src.cli.main --scan AI,半导体,机器人                        # 多个主题词扫描（英文逗号分隔）

AI配置:
  在 configs/settings.yaml 的 ai 节填写 API Key
  DeepSeek: ai.deepseek.api_key 或环境变量 DEEPSEEK_API_KEY
  Kimi: ai.kimi.api_key 或环境变量 KIMI_API_KEY
        """
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="JSON数据文件路径"
    )
    parser.add_argument(
        "-l", "--live",
        metavar="STOCK_CODE",
        help="实时行情模式，通过AKShare获取股票数据"
    )
    parser.add_argument(
        "-b", "--backtest",
        metavar="STOCK_CODE",
        help="回测模式，用历史数据验证策略"
    )
    parser.add_argument(
        "--batch-backtest-codes",
        metavar="CODES",
        help="批量回测验证股票代码，使用逗号分隔，例如 002192,600519,000001"
    )
    parser.add_argument(
        "-s", "--start",
        metavar="DATE",
        help="回测起始日期 (YYYY-MM-DD)，默认为1年前"
    )
    parser.add_argument(
        "-e", "--end",
        metavar="DATE",
        help="回测结束日期 (YYYY-MM-DD)，默认为今天"
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=100000.0,
        help="回测初始资金 (默认: 100000)"
    )
    parser.add_argument(
        "--backtest-mode",
        choices=["framework_strict", "legacy_compatible"],
        default="framework_strict",
        help="回测执行模式：framework_strict(默认，按最新框架) / legacy_compatible(保留旧门控)"
    )
    parser.add_argument(
        "--layer-mode",
        choices=["decision_only", "decision_strategy", "decision_strategy_execution"],
        default="decision_strategy_execution",
        help="回测分层模式：decision_only / decision_strategy / decision_strategy_execution(默认)"
    )
    parser.add_argument(
        "--export-analysis-json",
        metavar="PATH",
        help="导出 AI 分析用 JSON 载荷（含汇总指标、交易记录、每日快照）"
    )
    parser.add_argument(
        "--export-analysis-txt",
        metavar="PATH",
        help="导出 AI 分析用文本摘要"
    )
    parser.add_argument(
        "--export-layer-comparison-json",
        metavar="PATH",
        help="导出 decision_only / decision_strategy / decision_strategy_execution 三层对照 JSON"
    )
    parser.add_argument(
        "--export-layer-comparison-txt",
        metavar="PATH",
        help="导出三层对照文本分析报告（含逐日层间分歧明细）"
    )
    parser.add_argument(
        "--export-validation-json",
        metavar="PATH",
        help="导出 lookahead / 样本内外 / walk-forward 验证 JSON"
    )
    parser.add_argument(
        "--export-validation-txt",
        metavar="PATH",
        help="导出 lookahead / 样本内外 / walk-forward 验证文本"
    )
    parser.add_argument(
        "--export-batch-validation-json",
        metavar="PATH",
        help="导出多标的批量验证 JSON 汇总"
    )
    parser.add_argument(
        "--export-batch-validation-txt",
        metavar="PATH",
        help="导出多标的批量验证文本汇总"
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version="%(prog)s v0.8.6.6 (笨总评分+跳法A气宗/剑宗+PlanGuard+买卖点精确触发)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="显示详细日志（INFO级别），默认只显示警告"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="显示调试日志（DEBUG级别），包含所有内部信息"
    )

    # ===== AI调节层参数（v0.8.0）=====
    ai_group = parser.add_argument_group("AI调节层")
    ai_group.add_argument(
        "--no-ai",
        action="store_true",
        help="禁用AI调节层（仅使用技术面分析）"
    )
    ai_group.add_argument(
        "--ai-provider",
        metavar="PROVIDER",
        choices=["deepseek", "kimi"],
        help="临时切换AI提供商（deepseek/kimi）"
    )

    # ===== 持仓管理子命令 =====
    pos_group = parser.add_argument_group("持仓管理")
    pos_group.add_argument(
        "--pos-add",
        metavar="STOCK_CODE",
        help="手动添加持仓记录（如：--pos-add 002192 --name 融捷股份 --price 35.20 --ratio 0.20）"
    )
    pos_group.add_argument(
        "--pos-list",
        action="store_true",
        help="列出所有持仓记录"
    )
    pos_group.add_argument(
        "--pos-remove",
        metavar="STOCK_CODE",
        help="删除持仓记录"
    )
    pos_group.add_argument(
        "--name",
        metavar="STOCK_NAME",
        help="持仓添加时的股票名称"
    )
    pos_group.add_argument(
        "--price",
        type=float,
        metavar="PRICE",
        help="持仓添加时的开仓价格"
    )
    pos_group.add_argument(
        "--ratio",
        type=float,
        default=0.20,
        metavar="RATIO",
        help="持仓添加时的仓位比例（默认0.20即20%%）"
    )
    pos_group.add_argument(
        "-p", "--portfolio",
        action="store_true",
        help="一键扫描所有持仓股，逐个分析并汇总"
    )

    # ===== 全市场扫描参数（v0.8.0 Phase 2）=====
    scan_group = parser.add_argument_group("全市场扫描")
    scan_group.add_argument(
        "--scan",
        nargs="?",
        const="default",
        metavar="RULE_OR_QUERY",
        help="全市场扫描。可传规则名/规则关键词，也可直接传单个或多个主题词；多个主题请用英文逗号分隔，如 AI,半导体,机器人"
    )
    scan_group.add_argument(
        "--scan-deep",
        action="store_true",
        help="扫描时自动执行深度分析（跳过用户选择）"
    )
    scan_group.add_argument(
        "--scan-list-rules",
        action="store_true",
        help="列出所有可用的扫描规则"
    )
    scan_group.add_argument(
        "--scan-list-industries",
        action="store_true",
        help="列出所有行业板块（含涨跌幅）"
    )
    scan_group.add_argument(
        "--scan-list-concepts",
        action="store_true",
        help="列出所有概念板块（含涨跌幅）"
    )

    # ===== 事件驱动参数（v0.8.0 Phase 3）=====
    event_group = parser.add_argument_group("事件驱动")
    event_group.add_argument(
        "--events",
        action="store_true",
        help="扫描重大市场事件并预警（宏观+持仓+规则）"
    )

    # Chat Agent参数
    chat_group = parser.add_argument_group("Chat Agent")
    chat_group.add_argument(
        "--chat",
        action="store_true",
        help="进入Chat Agent对话模式（自然语言交互）"
    )

    args = parser.parse_args()

    # 根据参数调整日志级别
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    # ===== AI参数处理（v0.8.0）=====
    # --no-ai 全局禁用AI；--ai-provider 临时切换提供商
    # 这通过修改 load_config 返回的 dict 中的 ai 配置来实现
    _ai_override = {}
    if hasattr(args, 'no_ai') and args.no_ai:
        _ai_override['disable_ai'] = True
    if hasattr(args, 'ai_provider') and args.ai_provider:
        _ai_override['provider'] = args.ai_provider

    # ===== 持仓管理子命令 =====
    if args.pos_list:
        manage_positions("list")
    elif args.pos_add:
        manage_positions("add", args.pos_add, args.name, args.price, args.ratio)
    elif args.pos_remove:
        manage_positions("remove", args.pos_remove)
    elif args.portfolio:
        analyze_portfolio(ai_overrides=_ai_override)
    elif hasattr(args, 'scan_list_rules') and args.scan_list_rules:
        # 列出可用扫描规则
        from src.scanner.scanner_engine import ScannerEngine
        config = load_config()
        scanner_cfg = config.get("scanner", {})
        engine = ScannerEngine(rules_path=scanner_cfg.get("rules_path", "./src/scanner/scan_rules.yaml"))
        rules = engine.get_available_rules()
        table = Table(title="可用扫描规则")
        table.add_column("规则名", style="cyan")
        table.add_column("中文名", style="white")
        table.add_column("描述")
        for r in rules:
            table.add_row(r["name"], r["display_name"], r["description"])
        console.print(table)
    elif hasattr(args, 'scan_list_industries') and args.scan_list_industries:
        # 列出行业板块
        from src.scanner.scanner_engine import ScannerEngine
        engine = ScannerEngine()
        industries = engine.get_industry_list()
        if not industries:
            console.print("[yellow]行业板块数据获取失败[/yellow]")
        else:
            table = Table(title=f"行业板块 ({len(industries)}个)")
            table.add_column("行业", style="cyan")
            table.add_column("涨跌幅%", justify="right")
            for ind in industries[:50]:  # 只显示前50个
                change = ind.get("change_pct", 0)
                style = "red" if change > 0 else "green" if change < 0 else "white"
                table.add_row(ind["name"], f"[{style}]{change:+.2f}[/{style}]")
            console.print(table)
            if len(industries) > 50:
                console.print(f"  [dim]... 共{len(industries)}个行业，仅显示前50个[/dim]")
    elif hasattr(args, 'scan_list_concepts') and args.scan_list_concepts:
        from src.scanner.scanner_engine import ScannerEngine
        engine = ScannerEngine()
        concepts = engine.get_concept_list()
        if not concepts:
            console.print("[yellow]概念板块数据获取失败[/yellow]")
        else:
            table = Table(title=f"概念板块 ({len(concepts)}个)")
            table.add_column("概念", style="cyan")
            table.add_column("涨跌幅%", justify="right")
            for item in concepts[:50]:
                change = item.get("change_pct", 0)
                style = "red" if change > 0 else "green" if change < 0 else "white"
                table.add_row(item["name"], f"[{style}]{change:+.2f}[/{style}]")
            console.print(table)
            if len(concepts) > 50:
                console.print(f"  [dim]... 共{len(concepts)}个概念，仅显示前50个[/dim]")
    elif hasattr(args, 'scan') and args.scan:
        # 全市场扫描
        scan_market(
            rule_name=args.scan or "healthy_pullback",
            market_query=None,
            deep=getattr(args, 'scan_deep', False),
            ai_enabled=not _ai_override.get('disable_ai', False),
        )
    elif hasattr(args, 'events') and args.events:
        # 事件驱动扫描
        scan_events(ai_debug=getattr(args, 'debug', False))
    elif getattr(args, 'batch_backtest_codes', None):
        # 批量回测验证模式
        from datetime import datetime, timedelta
        stock_codes = [normalize_stock_code(item) for item in args.batch_backtest_codes.split(",") if item.strip()]
        if not stock_codes:
            console.print("[red]批量回测验证至少需要一个股票代码[/red]")
            sys.exit(1)
        start_date = args.start or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        end_date = args.end or datetime.now().strftime("%Y-%m-%d")
        run_batch_validation(
            stock_codes=stock_codes,
            start_date=start_date,
            end_date=end_date,
            capital=args.capital,
            backtest_mode=args.backtest_mode,
            export_batch_validation_json_path=args.export_batch_validation_json,
            export_batch_validation_txt_path=args.export_batch_validation_txt,
            layer_mode=args.layer_mode,
        )
    elif args.backtest:
        # 回测模式
        from datetime import datetime, timedelta
        start_date = args.start or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        end_date = args.end or datetime.now().strftime("%Y-%m-%d")
        run_backtest(
            args.backtest,
            start_date,
            end_date,
            args.capital,
            args.backtest_mode,
            args.export_analysis_json,
            args.export_analysis_txt,
            args.export_layer_comparison_json,
            args.export_layer_comparison_txt,
            args.export_validation_json,
            args.export_validation_txt,
            args.layer_mode,
        )
    elif args.live:
        # 实时行情模式
        analyze_live(args.live, ai_overrides=_ai_override)
    elif args.file:
        # 文件模式
        json_path = args.file
        if not Path(json_path).exists():
            console.print(f"[red]文件不存在: {json_path}[/red]")
            sys.exit(1)
        analyze_json(json_path)
    elif args.chat:
        # Chat Agent对话模式
        from src.chat.agent import run_chat_repl
        config = load_config()
        run_chat_repl(config)
    else:
        # 交互模式
        try:
            analyze_interactive()
        except KeyboardInterrupt:
            console.print("\n[yellow]已取消操作[/yellow]")


if __name__ == "__main__":
    main()
