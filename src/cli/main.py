"""CLI入口 - 主命令行界面"""

import sys
import json
import logging
from pathlib import Path

# Windows PowerShell 环境下设置 UTF-8（通过环境变量，不替换sys.stdout避免与Rich冲突）
if sys.platform == 'win32':
    import os
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import yaml
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.data.models import StockData, SignalType, MarketState, StrategyState, TradeLifecycle
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


def load_config(config_path: str = "./configs/settings.yaml") -> dict:
    """加载配置文件"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


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
        "[bold cyan]暮云思辨投资助手 v0.7.3[/bold cyan]\n"
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

    console.print(f"\n[green]✓[/green] 技能目录: {skills_dir}")
    if enabled_skills:
        console.print(f"[green]✓[/green] 启用的技能: {', '.join(enabled_skills)}")
    else:
        console.print("[yellow]⚠[/yellow] 将加载所有可用技能")

    # 创建编排器
    orchestrator = Orchestrator(skills_dir, enabled_skills, weights, skill_types)

    console.print(f"[green]✓[/green] 已加载技能: {', '.join(orchestrator.get_available_skills())}")

    # 获取股票数据
    data = create_sample_data()

    # 执行分析
    console.print("\n[bold yellow]🔄 正在分析...[/bold yellow]")
    result = orchestrator.analyze(data)

    # 显示结果
    display_result(result)

    return result


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

    orchestrator = Orchestrator(skills_dir, enabled_skills, weights, skill_types)
    result = orchestrator.analyze(stock_data)

    display_result(result)
    return result


def display_result(result, strategy_decision=None, execution_eval=None):
    """格式化显示分析结果（v0.7.2 含策略层和执行层信息）"""
    """格式化显示分析结果"""
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
    pos_ratio_str = f"{result.position_ratio:.0%}" if result.position_ratio > 0 else ""

    # 主决策面板
    title_str = "[bold]分析结果[/bold]"
    content_str = (
        f"[bold]股票:[/bold] {result.stock.stock_name} ({result.stock.stock_code})\n"
        f"[bold]当前价:[/bold] {result.stock.price}\n"
        f"[bold]市场状态:[/bold] [{state_color}]{result.state.value}[/{state_color}]\n"
        f"[bold {signal_color}]决策: {result.decision.value}[/]\n"
        f"[bold]综合评分:[/bold] {result.score:.2f}\n"
        f"[bold]仓位动作:[/bold] {pos_action_cn}" +
        (f" (目标{pos_ratio_str})" if pos_ratio_str else "")
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


def analyze_portfolio():
    """一键分析当前所有持仓股"""
    from src.data.akshare_client import get_stock_data, AKShareClient, _baostock_logout

    pm = PortfolioManager()
    positions = pm.list_positions()

    if not positions:
        console.print("\n[yellow]当前无持仓记录，请先使用 --pos-add 添加持仓[/yellow]")
        return

    console.print(f"\n[bold cyan]🔍 持仓扫描模式[/bold cyan]")
    console.print(f"共 {len(positions)} 只持仓股，开始逐个分析...\n")

    # 加载配置（所有股票共用一个编排器）
    config = load_config()
    enabled_skills = config.get("skills", {}).get("enabled", None)
    skills_dir = config.get("skills", {}).get("dir", "./src/skills")
    weights = config.get("decision", {}).get("signal_weights", None)
    skill_types = config.get("skills", {}).get("types", None)
    orchestrator = Orchestrator(skills_dir, enabled_skills, weights, skill_types)

    results = []  # (pos, stock_data, decision_result, strategy_decision)

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
                results.append((pos, None, None, None))
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
            logger.warning(f"获取 {pos.stock_code} 完整数据失败: {e}")

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
            results.append((pos, stock_data, None, None))
            continue

        try:
            decision_result, strategy_decision, execution_eval = orchestrator.analyze(
                stock_data,
                current_position_ratio=strategy_state.current_position_ratio,
                strategy_state=strategy_state,
            )
            results.append((pos, stock_data, decision_result, strategy_decision))

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
            pos_ratio_str = f"→{strategy_decision.position_ratio:.0%}" if strategy_decision.position_ratio > 0 else ""

            # 浮盈计算
            pnl_str = ""
            if pos.entry_price and stock_data.price:
                pnl_pct = (stock_data.price - pos.entry_price) / pos.entry_price * 100
                pnl_color = "green" if pnl_pct >= 0 else "red"
                pnl_str = f"  浮盈:[{pnl_color}]{pnl_pct:+.2f}%[/{pnl_color}]"

            console.print(
                f"  {stock_data.stock_name} 现价 {stock_data.price}  "
                f"涨跌 {stock_data.change_pct}%{pnl_str}  "
                f"决策:[{sig_color}]{decision_result.decision.value}[/{sig_color}]  "
                f"仓位:[bold]{pos_action_cn}{pos_ratio_str}[/bold]"
            )

            # 更新持仓
            pm.suggest_update(
                pos.stock_code, stock_data.stock_name,
                strategy_decision, stock_data, console
            )

        except Exception as e:
            logger.warning(f"分析 {pos.stock_code} 失败: {e}")
            console.print(f"  [red]✗ 分析失败: {e}[/red]")
            results.append((pos, stock_data, None, None))

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
    summary_table.add_column("目标仓位", justify="right", width=8)

    signal_colors = {
        SignalType.BUY: "green", SignalType.SELL: "red",
        SignalType.HOLD: "yellow", SignalType.WATCH: "cyan"
    }
    pos_action_map = {
        "OPEN": "建仓", "ADD": "加仓", "REDUCE": "减仓",
        "CLOSE_ALL": "清仓", "HOLD_POSITION": "持仓", "STAY_OUT": "观望"
    }

    for pos, stock_data, decision_result, strategy_decision in results:
        if not stock_data:
            summary_table.add_row(
                pos.stock_code, pos.stock_name or "-",
                "-", "-", "-", f"{pos.current_ratio:.0%}",
                "-", "数据失败", "-"
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

        if decision_result and strategy_decision:
            sig_color = signal_colors.get(decision_result.decision, "white")
            pos_action_cn = pos_action_map.get(strategy_decision.position_action.value, strategy_decision.position_action.value)
            target_str = f"{strategy_decision.position_ratio:.0%}" if strategy_decision.position_ratio > 0 else "-"
            summary_table.add_row(
                pos.stock_code, stock_data.stock_name,
                f"{stock_data.price:.2f}", chg_str, pnl_str,
                f"{pos.current_ratio:.0%}",
                f"[{sig_color}]{decision_result.decision.value}[/{sig_color}]",
                pos_action_cn, target_str
            )
        else:
            summary_table.add_row(
                pos.stock_code, stock_data.stock_name,
                f"{stock_data.price:.2f}", chg_str, pnl_str,
                f"{pos.current_ratio:.0%}",
                "-", "无指标", "-"
            )

    console.print(summary_table)

    # 操作建议汇总
    actions_summary = {"建仓": 0, "加仓": 0, "持仓": 0, "减仓": 0, "清仓": 0, "观望": 0}
    for _, _, decision_result, strategy_decision in results:
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


def analyze_live(stock_code: str):
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
            if pos.entry_price:
                pnl_pct = (stock_data.price - pos.entry_price) / pos.entry_price * 100
                pnl_color = "green" if pnl_pct >= 0 else "red"
                console.print(f"  开仓价: {pos.entry_price}  浮盈: [{pnl_color}]{pnl_pct:+.2f}%[/{pnl_color}]")
            if pos.strategy_state and pos.strategy_state.get("cooldown_remaining", 0) > 0:
                console.print(f"  冷却期: 剩余{pos.strategy_state['cooldown_remaining']}天")
        else:
            console.print(f"\n[dim]📂 无持仓记录（将从FLAT状态开始分析）[/dim]")

        config = load_config()
        enabled_skills = config.get("skills", {}).get("enabled", None)
        skills_dir = config.get("skills", {}).get("dir", "./src/skills")
        weights = config.get("decision", {}).get("signal_weights", None)
        skill_types = config.get("skills", {}).get("types", None)

        orchestrator = Orchestrator(skills_dir, enabled_skills, weights, skill_types)
        result, strategy_decision, execution_eval = orchestrator.analyze(
            stock_data,
            current_position_ratio=strategy_state.current_position_ratio,
            strategy_state=strategy_state,
        )
        display_result(result, strategy_decision, execution_eval)

        # ===== 建议更新持仓 =====
        pm.suggest_update(
            stock_code, stock_data.stock_name,
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
):
    """回测模式"""
    from src.core.backtest_engine import BacktestEngine

    console.print(f"\n[bold cyan]回测模式[/bold cyan]")
    console.print(f"  股票: {stock_code}")
    console.print(f"  区间: {start_date} ~ {end_date}")
    console.print(f"  初始资金: ¥{capital:,.0f}")
    console.print(f"\n[bold yellow]正在加载数据并回测...[/bold yellow]")

    config = load_config()
    enabled_skills = config.get("skills", {}).get("enabled", None)
    skills_dir = config.get("skills", {}).get("dir", "./src/skills")
    weights = config.get("decision", {}).get("signal_weights", None)
    skill_types = config.get("skills", {}).get("types", None)

    engine = BacktestEngine(
        stock_code=stock_code,
        start_date=start_date,
        end_date=end_date,
        initial_capital=capital,
        skills_dir=skills_dir,
        enabled_skills=enabled_skills,
        signal_weights=weights,
        skill_types=skill_types,
    )

    result = engine.run()

    display_backtest_result(result)
    return result


def display_backtest_result(result):
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
    if result.max_drawdown_pct > 30:
        console.print(f"  [red]最大回撤 {result.max_drawdown_pct:.1f}% 较大，策略风险较高。[/red]")


def manage_positions(action: str, stock_code: str = "", name: str = "", price: float = 0.0, ratio: float = 0.20):
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
                pos.entry_date or "-",
                f"{pos.last_action} ({pos.last_action_date or '-'})",
            )

        console.print(table)

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


def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="暮云思辨投资助手 - AI驱动的A股交易行为约束系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python -m src.cli.main                              # 交互模式
  python -m src.cli.main sample_data.json             # JSON文件模式
  python -m src.cli.main --live 600519               # 实时行情模式(通过AKShare)
  python -m src.cli.main -l 000001                   # 实时行情模式
  python -m src.cli.main --backtest 600519           # 回测模式(默认近1年)
  python -m src.cli.main --backtest 600519 -s 2024-01-01 -e 2025-01-01  # 自定义区间
  python -m src.cli.main --backtest 600519 --capital 200000  # 自定义初始资金

持仓管理:
  python -m src.cli.main --pos-list                                     # 查看所有持仓
  python -m src.cli.main --pos-add 002192 --name 融捷股份 --price 35.20  # 添加持仓
  python -m src.cli.main --pos-add 002192 --name 融捷股份 --ratio 0.40   # 添加持仓(指定仓位)
  python -m src.cli.main --pos-remove 002192                           # 删除持仓

一键扫描:
  python -m src.cli.main --portfolio                                   # 分析所有持仓股
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
        "-v", "--version",
        action="version",
        version="%(prog)s v0.7.3 (Bug修复+持仓扫描：空仓分析修正+一键扫描所有持仓)"
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

    args = parser.parse_args()

    # 根据参数调整日志级别
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    # ===== 持仓管理子命令 =====
    if args.pos_list:
        manage_positions("list")
    elif args.pos_add:
        manage_positions("add", args.pos_add, args.name, args.price, args.ratio)
    elif args.pos_remove:
        manage_positions("remove", args.pos_remove)
    elif args.portfolio:
        analyze_portfolio()
    elif args.backtest:
        # 回测模式
        from datetime import datetime, timedelta
        start_date = args.start or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        end_date = args.end or datetime.now().strftime("%Y-%m-%d")
        run_backtest(args.backtest, start_date, end_date, args.capital)
    elif args.live:
        # 实时行情模式
        analyze_live(args.live)
    elif args.file:
        # 文件模式
        json_path = args.file
        if not Path(json_path).exists():
            console.print(f"[red]文件不存在: {json_path}[/red]")
            sys.exit(1)
        analyze_json(json_path)
    else:
        # 交互模式
        analyze_interactive()


if __name__ == "__main__":
    main()
