#!/usr/bin/env python3
"""暮云思辨投资助手 - 交互式启动脚本
双击即可运行，自动进入交互循环模式
"""

import sys
import os

# 确保工作目录为脚本所在目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Windows 下设置 UTF-8（通过环境变量，不替换sys.stdout避免与Rich冲突）
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.system("chcp 65001 >nul 2>&1")

# 检测并使用项目虚拟环境
def find_python():
    """优先使用项目 .venv 中的 Python"""
    venv_python = os.path.join(os.path.dirname(__file__), ".venv", "Scripts", "python.exe")
    if os.path.exists(venv_python):
        return venv_python
    return sys.executable


def show_banner():
    """显示欢迎横幅"""
    print()
    print("=" * 52)
    print("  暮云思辨投资助手 v0.8.0")
    print("  AI驱动的A股交易行为约束系统")
    print("=" * 52)
    print()
    show_help()


def show_help():
    """显示用法说明"""
    print("┌──────────────────────────────────────────────────┐")
    print("│  用法                                             │")
    print("├──────────────────────────────────────────────────┤")
    print("│  输入股票代码        → 实时行情分析                │")
    print("│  scan                 → 一键扫描所有持仓股        │")
    print("│  scan market          → 全市场扫描(初筛)          │")
    print("│  scan market <规则>   → 指定规则(支持模糊匹配)    │")
    print("│  scan market -i <行业>→ 行业过滤扫描              │")
    print("│  industries           → 列出行业板块              │")
    print("│  industries <关键词>  → 搜索行业板块              │")
    print("│  events               → 事件驱动扫描(预警)        │")
    print("│  b  <代码> [起止日期] → 回测模式                  │")
    print("│  pos                  → 查看持仓列表              │")
    print("│  pos add <代码> [名称] [价格] [仓位]              │")
    print("│                       → 添加持仓记录              │")
    print("│  pos rm <代码>        → 删除持仓记录              │")
    print("│  debug                → 切换AI调试模式            │")
    print("│  h  或 help           → 显示用法                  │")
    print("│  q  或 quit           → 退出                      │")
    print("├──────────────────────────────────────────────────┤")
    print("│  示例                                             │")
    print("│    000001              分析平安银行实时行情        │")
    print("│    scan                一键扫描所有持仓            │")
    print("│    scan market         全市场放量突破扫描          │")
    print("│    scan market 缩量    缩量回调扫描(模糊匹配)     │")
    print("│    events             事件驱动扫描(预警)          │")
    print("│    industries 半导体   搜索半导体相关板块         │")
    print("│    b 000001            回测平安银行(近1年)         │")
    print("│    b 000001 2025-01-01 2026-01-01                 │")
    print("│                        回测指定区间                │")
    print("│    pos                 查看当前持仓                │")
    print("│    pos add 002192 融捷股份 35.20 0.20             │")
    print("│                        添加融捷股份持仓            │")
    print("│    pos rm 002192       删除融捷股份持仓            │")
    print("│    debug               开启/关闭AI调试模式        │")
    print("└──────────────────────────────────────────────────┘")
    print()


# 全局AI debug状态
_ai_debug = False


def parse_input(user_input: str):
    """解析用户输入，返回 (mode, args_dict) 或 None"""
    # 清理BOM和不可见字符
    text = user_input.strip().strip("\ufeff").strip()
    if not text:
        return None

    parts = text.split()

    # 退出
    if parts[0].lower() in ("q", "quit", "exit"):
        return ("quit", {})

    # 帮助
    if parts[0].lower() in ("h", "help", "?"):
        return ("help", {})

    # AI调试模式切换
    if parts[0].lower() == "debug":
        return ("debug", {})

    # 一键扫描持仓
    if parts[0].lower() in ("scan", "s"):
        # scan market [规则名] [-i 行业]
        if len(parts) >= 2 and parts[1].lower() in ("market", "m"):
            args = {"rule_name": "default"}
            i = 2
            while i < len(parts):
                if parts[i].lower() in ("-i", "--industry") and i + 1 < len(parts):
                    args["industry_filter"] = parts[i + 1].split(",")
                    i += 2
                else:
                    args["rule_name"] = parts[i]
                    i += 1
            return ("scan_market", args)
        return ("scan", {})

    # 行业板块列表（支持关键词搜索）
    if parts[0].lower() in ("industries", "industry"):
        keyword = parts[1] if len(parts) >= 2 else None
        return ("industries", {"keyword": keyword})

    # 事件驱动扫描
    if parts[0].lower() == "events":
        return ("events", {})

    # 持仓管理
    if parts[0].lower() == "pos":
        if len(parts) < 2:
            return ("pos_list", {})
        sub = parts[1].lower()
        if sub in ("list", "ls", "l"):
            return ("pos_list", {})
        elif sub in ("add", "a"):
            if len(parts) < 3:
                print("  [!] 请输入股票代码，例如: pos add 002192 融捷股份 35.20 0.20")
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
                print("  [!] 请输入股票代码，例如: pos rm 002192")
                return None
            return ("pos_remove", {"stock_code": parts[2]})
        else:
            # pos 后面不是子命令，可能是 pos 002192 的简写
            if parts[1].isdigit() and len(parts[1]) == 6:
                return ("pos_list", {})
            print(f"  [!] 未知持仓命令: {sub}  用法: pos / pos add / pos rm")
            return None

    # 回测模式
    if parts[0].lower() == "b":
        if len(parts) < 2:
            print("  [!] 请输入股票代码，例如: b 000001")
            return None
        args = {"stock_code": parts[1]}
        if len(parts) >= 3:
            args["start_date"] = parts[2]
        if len(parts) >= 4:
            args["end_date"] = parts[3]
        return ("backtest", args)

    # 纯数字 → 实时行情
    if parts[0].isdigit() and len(parts[0]) == 6:
        return ("live", {"stock_code": parts[0]})

    # 带后缀的代码 (如 000001.SZ)
    if "." in parts[0] and parts[0].split(".")[0].isdigit():
        return ("live", {"stock_code": parts[0]})

    print(f"  [!] 无法识别: {text}  输入 h 查看用法")
    return None


def run_cli(mode: str, args: dict):
    """调用 CLI 主程序执行分析"""
    global _ai_debug

    from src.cli.main import (
        analyze_live, run_backtest, manage_positions, load_config,
        analyze_portfolio, console, Orchestrator, scan_market, scan_events
    )

    if mode == "live":
        analyze_live(args["stock_code"], ai_debug=_ai_debug)

    elif mode == "scan":
        analyze_portfolio(ai_debug=_ai_debug)

    elif mode == "scan_market":
        scan_market(
            rule_name=args.get("rule_name", "default"),
            industry_filter=args.get("industry_filter"),
            ai_debug=_ai_debug,
        )

    elif mode == "industries":
        from src.scanner.scanner_engine import ScannerEngine
        engine = ScannerEngine()
        keyword = args.get("keyword")
        industries = engine.get_industry_list(keyword=keyword)
        if industries:
            if keyword:
                print(f"\n  行业板块 (搜索 \"{keyword}\", {len(industries)}个):")
            else:
                print(f"\n  行业板块 ({len(industries)}个, 输入 industries <关键词> 搜索):")
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
            if keyword:
                print(f"  未找到包含 \"{keyword}\" 的行业板块")
            else:
                print("  行业板块数据获取失败")

    elif mode == "events":
        scan_events(ai_debug=_ai_debug)

    elif mode == "debug":
        _ai_debug = not _ai_debug
        status = "开启" if _ai_debug else "关闭"
        print(f"  AI调试模式: {status}")
        print(f"  （将显示AI交互的完整输入/输出和新闻抓取详情）")

    elif mode == "backtest":
        from datetime import datetime, timedelta
        start_date = args.get("start_date") or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        end_date = args.get("end_date") or datetime.now().strftime("%Y-%m-%d")
        run_backtest(
            stock_code=args["stock_code"],
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


def main():
    global _ai_debug

    # 检查命令行参数
    if "--debug" in sys.argv:
        _ai_debug = True

    show_banner()

    if _ai_debug:
        print("  ⚡ AI调试模式已启用（命令行 --debug）")
        print()

    while True:
        try:
            # 使用内置input避免Rich console.input的编码问题
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
            print("  输入 h 查看用法，或检查股票代码是否正确")

        # 输出完毕后再次显示简短提示
        print()
        debug_tag = " [DEBUG]" if _ai_debug else ""
        print("─" * 52)
        print(f"  代码分析 | scan扫描 | events事件 | b+代码回测 | pos持仓 | debug | h帮助 | q退出{debug_tag}")
        print("─" * 52)
        print()


if __name__ == "__main__":
    main()
