"""CLI入口 - 主命令行界面"""

import sys
import json
import logging
from pathlib import Path

# Windows PowerShell 环境下强制 UTF-8 编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import yaml
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.data.models import StockData, SignalType, MarketState
from src.core.orchestrator import Orchestrator

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
        "[bold cyan]暮云思辨投资助手 v0.6.1[/bold cyan]\n"
        "AI驱动的A股决策辅助工具",
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


def display_result(result):
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

        config = load_config()
        enabled_skills = config.get("skills", {}).get("enabled", None)
        skills_dir = config.get("skills", {}).get("dir", "./src/skills")
        weights = config.get("decision", {}).get("signal_weights", None)
        skill_types = config.get("skills", {}).get("types", None)

        orchestrator = Orchestrator(skills_dir, enabled_skills, weights, skill_types)
        result = orchestrator.analyze(stock_data)
        display_result(result)
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
    console.print("\n[bold dim]交易成本模型：[/bold dim]")
    console.print("  佣金 0.025%(最低5元) + 印花税 0.05%(卖出) + 过户费 0.001% + 滑点 0.1%")
    console.print("  执行方式: 前日信号 + 次日开盘价（消除前视偏差）")
    console.print("  T+1: 买入后至少下一交易日才能卖出")

    # ===== 风险提示 =====
    console.print("\n[bold yellow]⚠ 风险提示[/bold yellow]")
    console.print("  回测结果不代表未来收益。历史数据回测存在过拟合风险。")
    if result.total_trades < 5:
        console.print("  [yellow]交易次数较少，统计指标可能不具参考性。[/yellow]")
    if result.max_drawdown_pct > 30:
        console.print(f"  [red]最大回撤 {result.max_drawdown_pct:.1f}% 较大，策略风险较高。[/red]")


def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="暮云思辨投资助手 - A股技术分析辅助决策工具",
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
        version="%(prog)s v0.6.1 (SELL分层门槛：牛市0.35/震荡0.30/熊市0.25+强SELL判定)"
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

    args = parser.parse_args()

    # 根据参数调整日志级别
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    if args.backtest:
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
