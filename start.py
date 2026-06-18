#!/usr/bin/env python3
"""暮云思辨投资助手 - 交互式启动脚本
双击即可运行，自动进入交互循环模式。
命令行带参数时直接代理给 src.cli.main（支持所有 argparse 参数）。
"""

import sys
import os
import subprocess

# 确保工作目录为脚本所在目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Windows 下设置 UTF-8（通过环境变量，不替换sys.stdout避免与Rich冲突）
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.system("chcp 65001 >nul 2>&1")

VERSION = "v0.8.4"

# ── 全局状态 ──────────────────────────────────────────────
_ai_debug = False   # AI 调试模式（显示完整 AI 交互日志）
_no_ai = False      # 禁用 AI 调节层（纯技术面分析）


def find_python():
    """优先使用项目 .venv 中的 Python"""
    venv_python = os.path.join(os.path.dirname(__file__), ".venv", "Scripts", "python.exe")
    if os.path.exists(venv_python):
        return venv_python
    return sys.executable


# ── 帮助 ──────────────────────────────────────────────────
def show_banner():
    print()
    print("=" * 56)
    print(f"  暮云思辨投资助手 {VERSION}")
    print("  AI驱动的A股交易行为约束系统")
    print("=" * 56)
    print()
    show_help()


def show_help():
    print("┌────────────────────────────────────────────────────┐")
    print("│  ★ 股票分析                                        │")
    print("│    l <代码>               实时分析（如 l 600519）   │")
    print("│    <6位代码>              同上（直接输代码也行）    │")
    print("│                                                    │")
    print("│  ★ 回测                                            │")
    print("│    b  <代码> [起 止 资金] 单只回测（默认近1年10万） │")
    print("│    bb <代码,代码> [起 止] 批量回测验证              │")
    print("│                                                    │")
    print("│  ★ 扫描 (v0.8.4 趋势选股)                          │")
    print("│    scan                   一键扫描所有持仓          │")
    print("│    scan market [规则]     全市场扫描                │")
    print("│    scan market deep       全市场深度分析            │")
    print("│    可用规则:                                        │")
    print("│      healthy_pullback/ 健康回调   ★默认，缩量小跌    │")
    print("│      steady_advance  / 温和上涨   温和放量上涨       │")
    print("│      shrink_pullback / 缩量回调   缩量下跌企稳      │")
    print("│      value_pick      / 低估值筛选  PE/PB估值        │")
    print("│    rules                  列出所有扫描规则          │")
    print("│    industries [关键词]    行业板块                  │")
    print("│    concepts   [关键词]    概念板块                  │")
    print("│    events                 事件驱动预警              │")
    print("│                                                    │")
    print("│  ★ 持仓                                            │")
    print("│    pos                    查看持仓列表              │")
    print("│    pos add <代码> [名称] [价格] [仓位]              │")
    print("│    pos rm  <代码>         删除持仓记录              │")
    print("│                                                    │")
    print("│  ★ 其他                                            │")
    print("│    noai                   切换 AI 开关（纯技术面）  │")
    print("│    debug                  切换 AI 调试模式          │")
    print("│    chat                   AI 对话模式               │")
    print("│    h                      显示帮助                  │")
    print("│    q                      退出                      │")
    print("├────────────────────────────────────────────────────┤")
    print("│  示例                                              │")
    print("│    l 600519               分析贵州茅台实时行情      │")
    print("│    b 000001               回测平安银行（近1年）     │")
    print("│    b 000001 2025-01-01 2026-01-01 200000            │")
    print("│                           自定义区间+资金           │")
    print("│    bb 002192,600519,000001                          │")
    print("│                           批量回测验证              │")
    print("│    scan market            健康回调(默认，推荐)     │")
    print("│    scan market 低估值筛选   价值投资扫描            │")
    print("│    scan market AI,半导体   多主题扫描(英文逗号)     │")
    print("│    scan market deep        全市场自动深度分析       │")
    print("│    pos add 002192 融捷股份 35.20 0.20               │")
    print("└────────────────────────────────────────────────────┘")
    print()


# ── 输入解析 ──────────────────────────────────────────────
def _is_stock_code(s: str) -> bool:
    """判断是否为6位纯数字股票代码或带后缀格式 (000001.SZ)"""
    if s.isdigit() and len(s) == 6:
        return True
    if "." in s:
        base = s.split(".")[0]
        return base.isdigit() and len(base) == 6
    return False


def parse_input(user_input: str):
    """解析用户输入，返回 (mode, args_dict) 或 None"""
    text = user_input.strip().strip("\ufeff").strip()
    if not text:
        return None

    parts = text.split()
    cmd = parts[0].lower()

    # ── 基础命令 ──
    if cmd in ("q", "quit", "exit"):
        return ("quit", {})
    if cmd in ("h", "help", "?"):
        return ("help", {})

    # ── 实时分析：l/live + 代码，或直接输入6位代码 ──
    if cmd in ("l", "live"):
        if len(parts) < 2 or not _is_stock_code(parts[1]):
            print("  [!] 用法: l <6位股票代码>  例如: l 600519")
            return None
        return ("live", {"stock_code": parts[1]})

    if _is_stock_code(parts[0]):
        return ("live", {"stock_code": parts[0]})

    # ── 回测 ──
    if cmd == "b":
        if len(parts) < 2:
            print("  [!] 用法: b <代码> [起始日期 结束日期 初始资金]")
            return None
        args = {"stock_code": parts[1]}
        if len(parts) >= 3:
            args["start_date"] = parts[2]
        if len(parts) >= 4:
            args["end_date"] = parts[3]
        if len(parts) >= 5:
            try:
                args["capital"] = float(parts[4])
            except ValueError:
                print(f"  [!] 资金参数无效: {parts[4]}  应为数字")
                return None
        return ("backtest", args)

    # ── 批量回测 ──
    if cmd == "bb":
        if len(parts) < 2:
            print("  [!] 用法: bb <代码1,代码2,...> [起始日期 结束日期]")
            return None
        codes = [c.strip() for c in parts[1].split(",") if c.strip()]
        if not codes:
            print("  [!] 请提供至少一个股票代码")
            return None
        args = {"codes": codes}
        if len(parts) >= 3:
            args["start_date"] = parts[2]
        if len(parts) >= 4:
            args["end_date"] = parts[3]
        return ("batch_backtest", args)

    # ── 扫描 ──
    if cmd in ("scan", "s"):
        if len(parts) >= 2 and parts[1].lower() in ("market", "m"):
            args = {"rule_name": "healthy_pullback", "market_query": None, "deep": False}
            rest = parts[2:]
            # scan market deep [主题]
            if rest and rest[0].lower() == "deep":
                args["deep"] = True
                rest = rest[1:]
            if rest:
                args["market_query"] = " ".join(rest).strip()
            return ("scan_market", args)
        return ("scan", {})

    # ── 扫描规则列表 ──
    if cmd == "rules":
        return ("rules", {})

    # ── 板块列表 ──
    if cmd in ("industries", "industry"):
        keyword = parts[1] if len(parts) >= 2 else None
        return ("industries", {"keyword": keyword})
    if cmd in ("concepts", "concept"):
        keyword = parts[1] if len(parts) >= 2 else None
        return ("concepts", {"keyword": keyword})

    # ── 事件驱动 ──
    if cmd == "events":
        return ("events", {})

    # ── 持仓管理 ──
    if cmd == "pos":
        if len(parts) < 2:
            return ("pos_list", {})
        sub = parts[1].lower()
        if sub in ("list", "ls"):
            return ("pos_list", {})
        elif sub in ("add", "a"):
            if len(parts) < 3:
                print("  [!] 用法: pos add <代码> [名称] [价格] [仓位]")
                return None
            args = {"stock_code": parts[2]}
            if len(parts) >= 4:
                args["name"] = parts[3]
            if len(parts) >= 5:
                try:
                    args["price"] = float(parts[4])
                except ValueError:
                    pass
            if len(parts) >= 6:
                try:
                    args["ratio"] = float(parts[5])
                except ValueError:
                    pass
            return ("pos_add", args)
        elif sub in ("remove", "rm", "r", "del", "d"):
            if len(parts) < 3:
                print("  [!] 用法: pos rm <代码>")
                return None
            return ("pos_remove", {"stock_code": parts[2]})
        else:
            print(f"  [!] 未知: pos {sub}  用法: pos / pos add / pos rm")
            return None

    # ── 开关 ──
    if cmd == "noai":
        return ("noai", {})
    if cmd == "debug":
        return ("debug", {})
    if cmd == "chat":
        return ("chat", {})

    print(f"  [!] 无法识别: {text}  输入 h 查看用法")
    return None


# ── 命令执行 ──────────────────────────────────────────────
def run_cli(mode: str, args: dict):
    """调用 CLI 主程序执行分析"""
    global _ai_debug, _no_ai

    from src.cli.main import (
        analyze_live, run_backtest, run_batch_validation,
        manage_positions, load_config, analyze_portfolio,
        console, scan_market, scan_events,
    )

    ai_overrides = {}
    if _no_ai:
        ai_overrides["disable_ai"] = True

    if mode == "live":
        analyze_live(args["stock_code"], ai_overrides=ai_overrides, ai_debug=_ai_debug)

    elif mode == "scan":
        analyze_portfolio(ai_overrides=ai_overrides, ai_debug=_ai_debug)

    elif mode == "scan_market":
        scan_market(
            rule_name=args.get("rule_name", "healthy_pullback"),
            market_query=args.get("market_query"),
            ai_debug=_ai_debug,
            deep=args.get("deep", False),
            ai_enabled=not _no_ai,
        )

    elif mode == "rules":
        from src.scanner.scanner_engine import ScannerEngine
        from rich.table import Table
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

    elif mode == "industries":
        from src.scanner.scanner_engine import ScannerEngine
        engine = ScannerEngine()
        keyword = args.get("keyword")
        industries = engine.get_industry_list(keyword=keyword)
        if industries:
            label = f"搜索 \"{keyword}\", " if keyword else ""
            print(f"\n  行业板块 ({label}{len(industries)}个):")
            for ind in industries[:50]:
                change = ind.get("change_pct", 0)
                arrow = "↑" if change > 0 else "↓" if change < 0 else "→"
                up = ind.get("up_count", "?")
                down = ind.get("down_count", "?")
                lead = ind.get("lead_stock", "")
                lead_str = f" 领涨:{lead}" if lead else ""
                print(f"    {ind['name']:12s} {change:+6.2f}% {arrow}  涨{up}/跌{down}{lead_str}")
            if len(industries) > 50:
                print(f"    ... 共{len(industries)}个，仅显示前50个")
            print()
        else:
            msg = f"未找到包含 \"{keyword}\" 的行业板块" if keyword else "行业板块数据获取失败"
            print(f"  {msg}")

    elif mode == "concepts":
        from src.scanner.scanner_engine import ScannerEngine
        engine = ScannerEngine()
        keyword = args.get("keyword")
        concepts = engine.get_concept_list(keyword=keyword)
        if concepts:
            label = f"搜索 \"{keyword}\", " if keyword else ""
            print(f"\n  概念板块 ({label}{len(concepts)}个):")
            for concept in concepts[:50]:
                change = concept.get("change_pct", 0)
                arrow = "↑" if change > 0 else "↓" if change < 0 else "→"
                lead = concept.get("lead_stock", "")
                lead_str = f" 领涨:{lead}" if lead else ""
                print(f"    {concept['name']:16s} {change:+6.2f}% {arrow}{lead_str}")
            if len(concepts) > 50:
                print(f"    ... 共{len(concepts)}个，仅显示前50个")
            print()
        else:
            msg = f"未找到包含 \"{keyword}\" 的概念板块" if keyword else "概念板块数据获取失败"
            print(f"  {msg}")

    elif mode == "events":
        scan_events(ai_debug=_ai_debug)

    elif mode == "noai":
        _no_ai = not _no_ai
        status = "已禁用（纯技术面）" if _no_ai else "已启用"
        print(f"  AI调节层: {status}")

    elif mode == "debug":
        _ai_debug = not _ai_debug
        status = "开启" if _ai_debug else "关闭"
        print(f"  AI调试模式: {status}")

    elif mode == "backtest":
        from datetime import datetime, timedelta
        start_date = args.get("start_date") or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        end_date = args.get("end_date") or datetime.now().strftime("%Y-%m-%d")
        capital = args.get("capital", 100000.0)
        run_backtest(
            stock_code=args["stock_code"],
            start_date=start_date,
            end_date=end_date,
            capital=capital,
        )

    elif mode == "batch_backtest":
        from datetime import datetime, timedelta
        start_date = args.get("start_date") or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        end_date = args.get("end_date") or datetime.now().strftime("%Y-%m-%d")
        run_batch_validation(
            stock_codes=args["codes"],
            start_date=start_date,
            end_date=end_date,
        )

    elif mode == "pos_list":
        manage_positions("list")

    elif mode == "pos_add":
        manage_positions(
            "add",
            stock_code=args.get("stock_code", ""),
            name=args.get("name", ""),
            price=args.get("price", 0.0),
            ratio=args.get("ratio", 0.20),
        )

    elif mode == "pos_remove":
        manage_positions("remove", stock_code=args.get("stock_code", ""))

    elif mode == "chat":
        from src.chat.agent import run_chat_repl
        config = load_config()
        run_chat_repl(config)


# ── 主循环 ────────────────────────────────────────────────
def main():
    global _ai_debug

    # 带命令行参数时直接代理给 src.cli.main（支持全部 argparse 参数）
    cli_args = sys.argv[1:]
    if cli_args and cli_args != ["--debug"]:
        python_exec = find_python()
        completed = subprocess.run([python_exec, "-m", "src.cli.main", *cli_args], cwd=os.getcwd())
        raise SystemExit(completed.returncode)

    if "--debug" in sys.argv:
        _ai_debug = True

    show_banner()

    if _ai_debug:
        print("  ⚡ AI调试模式已启用（命令行 --debug）")
        print()

    while True:
        try:
            user_input = input("暮云> ")
        except (EOFError, KeyboardInterrupt):
            print("\n  再见！")
            break

        parsed = parse_input(user_input)
        if parsed is None:
            continue

        mode, args = parsed

        if mode == "quit":
            print("  再见！")
            break
        if mode == "help":
            show_help()
            continue

        try:
            run_cli(mode, args)
        except KeyboardInterrupt:
            print("\n  [已中断]")
        except Exception as e:
            print(f"\n  [错误] {e}")
            print("  输入 h 查看用法")

        # 底栏提示
        tags = []
        if _no_ai:
            tags.append("[NO-AI]")
        if _ai_debug:
            tags.append("[DEBUG]")
        tag_str = " " + " ".join(tags) if tags else ""
        print()
        print("─" * 56)
        print(f"  l+代码 分析 | b+代码 回测 | scan 扫描 | pos 持仓 | h 帮助{tag_str}")
        print("─" * 56)
        print()


if __name__ == "__main__":
    main()
