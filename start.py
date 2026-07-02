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

# 清理代理环境变量：金融数据API(新浪/东财/同花顺)直连更稳，走代理会被拦截返回456/HTML
# 必须在import数据模块前清，和 tests/test_all_api.py 保持一致，否则实跑会因代理失败但测试通过
for _k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_k, None)

# Windows 下设置 UTF-8（通过环境变量，不替换sys.stdout避免与Rich冲突）
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.system("chcp 65001 >nul 2>&1")

VERSION = "v0.8.5"

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
    print("│      ↑ 自动 AI 辅助生成 TradePlan 草稿（v0.8.5）    │")
    print("│    pos plan <代码>        查看/生成单只交易计划      │")
    print("│    pos plan <代码> --u    按当前行情更新计划         │")
    print("│    pos plan all           批量生成（无计划的持仓）   │")
    print("│    pos plan all --u       批量更新所有持仓计划       │")
    print("│    pos rm  <代码>         删除持仓记录              │")
    print("│                                                    │")
    print("│  ★ 笨总「超景气价值投机」(v0.8.6.1)                │")
    print("│    bz <代码>             笨总 AI 自动 6 维评分       │")
    print("│    bz <代码> --refresh   强制刷新（跳过缓存）        │")
    print("│    bz --manual           旧交互式手动打分（兜底）    │")
    print("│    bz --check            数据源连通性体检(ISS-043)  │")
    print("│    bz scan [主题] --top N  笨总选股初筛+批量评分    │")
    print("│    bz scan --allrules    四规则全跑合并(各Top5)⭐     │")
    print("│    bz scan 规则简述(无主题词时按规则初筛):          │")
    print("│      healthy_pullback 健康回调 缩量小跌(-3%~-0.1%)   │")
    print("│      steady_advance  温和上涨 放量上涨(0.1%~5%)     │")
    print("│      shrink_pullback  缩量回调 深跌缩量(-5%~-0.1%)   │")
    print("│      value_pick      低估值   PE<20/PB<2            │")
    print("│      (来自 B站「笨笨的韭菜」up 主教学体系)         │")
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
        elif sub in ("plan", "p"):
            # v0.8.5：pos plan <代码> 查看/生成计划；v0.8.6.3：--update 更新，all 批量
            if len(parts) < 3:
                print("  [!] 用法: pos plan <代码|all> [--update]")
                print("      pos plan 600519          查看/生成单只计划")
                print("      pos plan 600519 --update 按当前行情更新计划")
                print("      pos plan all             批量生成（无计划的持仓）")
                print("      pos plan all --update    批量更新所有持仓计划")
                return None
            target = parts[2]
            update = "--update" in parts[3:] or "-u" in parts[3:]
            return ("pos_plan", {"stock_code": target, "update": update})
        else:
            print(f"  [!] 未知: pos {sub}  用法: pos / pos add / pos rm / pos plan")
            return None

    # ── 开关 ──
    if cmd == "noai":
        return ("noai", {})
    if cmd == "debug":
        return ("debug", {})
    if cmd == "chat":
        return ("chat", {})
    if cmd in ("bz", "benzong", "笨总"):
        # v0.8.6.2: bz <code> 自动 AI 评分；bz --manual 走旧交互式；bz --refresh 强制刷新
        # v0.8.6.3: bz --check 数据源连通性体检（ISS-043）
        # v0.8.6.3: bz scan 选股初筛→笨总批量评分→可选回测（ISS-041 方向 A）
        if len(parts) >= 2 and parts[1].lower() in ("scan", "筛选", "选股"):
            scan_args = {"theme": "", "top_n": 10, "backtest": False,
                         "start": "", "end": "", "capital": 100000.0,
                         "rule": "healthy_pullback", "limit": 15, "refresh": False}
            rest = parts[2:]
            i = 0
            theme_parts = []
            while i < len(rest):
                p = rest[i]
                low = p.lower()
                if low in ("--top",) and i + 1 < len(rest):
                    try: scan_args["top_n"] = int(rest[i + 1])
                    except ValueError: pass
                    i += 2; continue
                if low in ("--backtest", "--bt"):
                    scan_args["backtest"] = True; i += 1; continue
                if low in ("--refresh", "-r"):
                    scan_args["refresh"] = True; i += 1; continue
                if low in ("--start",) and i + 1 < len(rest):
                    scan_args["start"] = rest[i + 1]; i += 2; continue
                if low in ("--end",) and i + 1 < len(rest):
                    scan_args["end"] = rest[i + 1]; i += 2; continue
                if low in ("--capital",) and i + 1 < len(rest):
                    try: scan_args["capital"] = float(rest[i + 1])
                    except ValueError: pass
                    i += 2; continue
                if low in ("--rule",) and i + 1 < len(rest):
                    scan_args["rule"] = rest[i + 1]; i += 2; continue
                if low in ("--allrules", "--all-rules"):
                    scan_args["allrules"] = True; i += 1; continue
                if low in ("--limit",) and i + 1 < len(rest):
                    try: scan_args["limit"] = int(rest[i + 1])
                    except ValueError: pass
                    i += 2; continue
                theme_parts.append(p); i += 1
            scan_args["theme"] = " ".join(theme_parts).strip()
            return ("benzong_scan", scan_args)

        meta = ""
        manual = False
        refresh = False
        check = False
        for p in parts[1:]:
            if p in ("--manual", "-m", "manual"):
                manual = True
            elif p in ("--refresh", "-r", "refresh"):
                refresh = True
            elif p in ("--check", "--体检", "check"):
                check = True
            elif not meta:
                meta = p
        return ("benzong", {"meta": meta, "manual": manual, "refresh": refresh, "check": check})

    print(f"  [!] 无法识别: {text}  输入 h 查看用法")
    return None


# ── 命令执行 ──────────────────────────────────────────────
def _ask_score(prompt: str, default: float = 0.0) -> float:
    """交互式询问 0-100 分数，回车用默认值。"""
    try:
        ans = input(f"  {prompt}（0-100，回车={default}）: ").strip()
        if not ans:
            return default
        v = float(ans)
        return max(0.0, min(100.0, v))
    except (ValueError, EOFError, KeyboardInterrupt):
        return default


def run_benzong_scoring(meta: str = "", manual: bool = False, refresh: bool = False):
    """v0.8.6.2：笨总「超景气价值投机」6 维评分。

    默认（v0.8.6.2）：自动 AI 评分 — 输入代码直接出 6 维结果
      bz 600519          → 自动 AI 评分（每维度调 1 次 AI，约 30-60s）
      bz 600519 --refresh → 强制刷新（跳过缓存）
      bz --manual         → 旧交互式手动打分（v0.8.6.1 兜底）
    """
    # manual 模式走旧交互式
    if manual or not meta:
        return _run_benzong_manual(meta)

    # 自动模式
    return _run_benzong_auto(meta, force_refresh=refresh)


def _run_benzong_auto(code: str, force_refresh: bool = False):
    """v0.8.6.2 自动 AI 评分（默认路径）。"""
    from src.core.benzong import auto_score

    print()
    print("=" * 64)
    print(f"  📊 笨总 AI 自动评分 — {code}")
    print("=" * 64)
    print()
    print("  正在评估 6 个维度（每维度调 AI 1 次 + 数据拉取，预计 30-60s）...")
    print("  缓存：同股同日同维度只算一次，自动存盘 ~/.muyun/benzong_cache/，无需手动导出")
    print("  ✓ 命中缓存秒回；--refresh 强制重算")
    print()

    try:
        result = auto_score(code, force_refresh=force_refresh)
    except KeyboardInterrupt:
        print("\n  [!] 已取消")
        return
    except Exception as e:
        print(f"\n  [red]✗ 评分失败: {type(e).__name__}: {e}[/red]")
        print(f"  [dim]可尝试 bz --manual 走交互式兜底[/dim]")
        return

    bs = result.score
    meta = result.dimensions_meta
    precondition_failed = (bs.industry_prosperity == 0)

    # 每维度输出
    dim_labels = [
        ("industry_prosperity", "1️⃣ 行业景气度"),
        ("business_purity",     "2️⃣ 业务纯度  "),
        ("valuation_position",  "3️⃣ 历史估值位置"),
        ("industry_leader",     "4️⃣ 细分行业龙头"),
        ("market_recognition",  "5️⃣ 市场辨识度"),
        ("risk_deduction",      "6️⃣ 个股风险值"),
    ]
    for key, label in dim_labels:
        m = meta.get(key, {})
        score = m.get("score", 0)
        conf = m.get("confidence", 0)
        cached = " (缓存)" if key in result.cache_hits else ""
        if conf < 0.5:
            warn_marker = "⚠"
            conf_hint = " ⚠该维降级，分仅供参考（不影响其他维）"
        else:
            warn_marker = "✓"
            conf_hint = ""
        print(f"  {warn_marker} {label} [conf {conf:.2f}]{cached} = {score:>5.1f} 分{conf_hint}")
        sources = m.get("sources", [])
        if sources:
            print(f"      来源：{' / '.join(sources[:3])}")
        reasoning = m.get("reasoning", "")
        if reasoning:
            print(f"      AI: {reasoning[:100]}{'...' if len(reasoning) > 100 else ''}")
        for w in m.get("warnings", []):
            print(f"      [yellow]⚠ {w}[/yellow]")
        print()

    # 总分
    print("─" * 64)
    grade = bs.effective_grade()
    raw_grade = bs.grade()
    grade_label = {
        "A": "🏆 A 级 — 超优质",
        "B": "✅ B 级 — 优秀（可买入）",
        "C": "🟡 C 级 — 可观察",
        "D": "🔻 D 级 — 勉强观望",
        "F": "❌ F 级 — 放弃",
    }[grade]
    norm = bs.normalized_score()
    downgrade_note = f"（原始 {raw_grade} 级，被行业景气度闸门降级）" if grade != raw_grade else ""
    if precondition_failed:
        print(f"  💯 评分：{norm:.0f}/100 → {grade_label}")
        print(f"  [bold red]⚠ 大前提失效（行业景气度=0）：此评分/等级不具参考意义，不可作为买入依据！[/bold red]")
        print(f"  [dim]笨总教学：没有高景气行业判断，其他维度再高也无意义（中免反面案例）[/dim]")
    else:
        print(f"  💯 评分：{norm:.0f}/100 → {grade_label} {downgrade_note}")
        if downgrade_note:
            print(f"  [dim]行业景气度 {bs.industry_prosperity:.0f} 偏低（笨总大前提存疑），实际等级已下调[/dim]")
    print(f"  🎯 总体置信度：{result.overall_confidence:.2f}", end="")
    if result.overall_confidence < 0.5:
        print("  [yellow]⚠ 偏低（有维度降级，总分仅供参考）[/yellow]")
    else:
        print()
    if result.cache_hits:
        print(f"  💾 缓存命中：{len(result.cache_hits)}/6 维度（同股同日复用，--refresh 重算）")
    print("─" * 64)

    # 警告（大前提失效时不重复打，已在上方红字提示）
    other_warnings = [w for w in result.warnings if "大前提失效" not in w] if precondition_failed else result.warnings
    if other_warnings:
        print()
        for w in other_warnings:
            print(f"  [yellow]{w}[/yellow]")
        print()

    # 大前提警告（非失效时不显示，失效时上方已红字提示，此处保留兜底）
    if not precondition_failed:
        pre = bs.precondition_warning()
        if pre:
            print(f"  [bold red]{pre}[/bold red]")
            print()

    print(f"  📚 评分依据：笨总教学 + xlsx 打分表（v0.8.6.1 试金石 4 案例已验证）")
    print(f"  💡 提示：bz {code} --refresh 强制刷新；bz --manual 走交互式兜底；bz --check 体检数据源")
    print()


def run_benzong_scan(args: dict):
    """v0.8.6.3 笨总选股初筛（ISS-041 方向 A）。

    scan 技术面初筛 → 笨总 6 维 AI 批量评分 → TopN 排名 → 可选批量回测。

    用法：
      bz scan                  默认 healthy_pullback 规则初筛 → 笨总评分 Top10
      bz scan AI,半导体        指定主题词缩小候选范围
      bz scan --top 5          Top5
      bz scan --allrules       四规则全跑(各Top5合并去重)→笨总评分，不挑市况全覆盖
      bz scan --limit 15       限制初筛候选数（控制 AI 耗时，默认15）
      bz scan --backtest --start 2024-01-01 --end 2024-12-31  对 TopN 批量回测
    """
    from src.scanner.scanner_engine import ScannerEngine
    from src.core.benzong.batch_scorer import auto_score_batch

    theme = args.get("theme", "")
    top_n = args.get("top_n", 10)
    do_backtest = args.get("backtest", False)
    limit = args.get("limit", 15)
    rule_name = args.get("rule", "healthy_pullback")
    force_refresh = args.get("refresh", False)
    allrules = args.get("allrules", False)  # 方案C: 四规则全跑合并

    print()
    print("=" * 64)
    print("  🔍 笨总选股初筛 — scan → 6维AI评分 → TopN")
    print("=" * 64)
    rule_display = "四规则全跑(各Top5合并)" if allrules else rule_name
    print(f"  规则: {rule_display} | 主题词: {theme or '(全市场)'} | 候选上限: {limit}")
    print(f"  TopN: {top_n} | 回测: {'是' if do_backtest else '否'}")
    print()

    # ── Step 1: 候选股获取 ──
    # 有主题词 → 法C 精准定位（AI直接报股+Baostock验证，绕过板块匹配天花板）
    # 无主题词 → quick_scan 技术面初筛
    from src.cli.main import load_config
    config = load_config()
    scanner_cfg = config.get("scanner", {})
    skills_dir = config.get("skills", {}).get("dir", "./src/skills")
    enabled_skills = config.get("skills", {}).get("enabled", [])
    signal_weights = config.get("decision", {}).get("signal_weights", {})
    skill_types = config.get("skills", {}).get("types", {})
    entry_exit_config = config.get("entry_exit", None)

    # 排除已持仓
    exclude_codes = set()
    if scanner_cfg.get("auto_exclude_holdings", True):
        try:
            from src.data.portfolio import PortfolioManager
            pm = PortfolioManager()
            exclude_codes = {pos.stock_code for pos in pm.list_positions()}
        except Exception:
            pass

    located_stocks = None  # 法C结果（有主题词时填充）
    if theme:
        # ── 双通道选股（去龙头偏向）──
        # 通道A 法C：AI 报股，覆盖细分材料（THS 无对应板块的，如 PBO树脂）
        # 通道B 全市场客观筛选：quick_scan(market_query=主题词) 从 THS 行业/概念成分股
        #        捞全市场成员（含中小盘非龙头），客观数据不靠 AI 记忆
        # 两通道合并去重 → 笨总评分排序
        from src.core.benzong.theme_locator import locate_theme_stocks
        terms = [t.strip() for t in theme.split(",") if t.strip()]
        print(f"  [Step 1/3] 双通道选股：法C精准定位 + 全市场行业筛选...")

        # 通道A：法C
        located = locate_theme_stocks(terms, config=config)
        a_stocks = [s for s in located.get("stocks", [])
                    if s["code"] not in exclude_codes]
        if located.get("invalid"):
            print(f"  丢弃 {len(located['invalid'])} 只（AI可能给错代码）："
                  + ", ".join(f"{i['code']}({i['name']})" for i in located["invalid"][:5]))

        # 通道B：全市场客观筛选（按主题词匹配 THS 行业/概念成分股）
        b_stocks = []
        try:
            b_engine = ScannerEngine(
                rules_path=scanner_cfg.get("rules_path", "./src/scanner/scan_rules.yaml"),
                skills_dir=skills_dir, enabled_skills=enabled_skills,
                signal_weights=signal_weights, skill_types=skill_types,
                ai_config=config.get("ai", None),
                cache_ttl=scanner_cfg.get("cache_ttl", 300),
                entry_exit_config=entry_exit_config,
            )
            b_candidates, b_info = b_engine.quick_scan(
                rule_name="theme_members",  # 宽口径规则，主要靠主题词过滤而非技术形态
                market_query=theme,
                exclude_codes=exclude_codes,
            )
            if not b_info.get("error"):
                a_codes = {s["code"] for s in a_stocks}
                for c in b_candidates:
                    if c.stock_code not in a_codes and c.stock_code not in exclude_codes:
                        b_stocks.append({"code": c.stock_code,
                                         "name": getattr(c, "stock_name", c.stock_code),
                                         "term": theme, "why": "全市场行业筛选"})
        except Exception as e:
            print(f"  [dim]通道B 全市场筛选跳过: {type(e).__name__}: {str(e)[:60]}[/dim]")

        # 合并：法C 优先（细分纯正），全市场补充非龙头
        merged = a_stocks + b_stocks
        located_stocks = merged
        if not located_stocks:
            print(f"  ⚠ 主题「{theme}」两通道均未定位到A股公司")
            print(f"  可能：AI不熟悉该细分领域 + 主题词未匹配到行业板块。可尝试换更通用的主题词")
            return
        codes = [s["code"] for s in located_stocks[:limit]]
        print(f"  ✓ 法C {len(a_stocks)} 只 + 全市场 {len(b_stocks)} 只 → 合并 {len(merged)} 只，取前 {len(codes)} 只评分")
        for s in located_stocks[:limit]:
            print(f"    {s['code']} {s['name'][:10]} [{s['term']}] {s['why'][:40]}")
        print()
    else:
        # ── 技术面初筛 ──
        engine = ScannerEngine(
            rules_path=scanner_cfg.get("rules_path", "./src/scanner/scan_rules.yaml"),
            skills_dir=skills_dir,
            enabled_skills=enabled_skills,
            signal_weights=signal_weights,
            skill_types=skill_types,
            ai_config=config.get("ai", None),
            cache_ttl=scanner_cfg.get("cache_ttl", 300),
            entry_exit_config=entry_exit_config,
        )

        if allrules:
            # 方案C: 四规则全跑，各取Top5合并去重
            print("  [Step 1/3] 四规则全跑初筛（各Top5合并）...")
            ALL_RULES = ["healthy_pullback", "steady_advance", "shrink_pullback", "value_pick"]
            merged_codes = []
            seen = set(exclude_codes)
            rule_hits = {}  # 记录每只股被几条规则选中（多规则共振=强标的）
            for r in ALL_RULES:
                try:
                    cands, info = engine.quick_scan(rule_name=r, market_query=None, exclude_codes=exclude_codes)
                except Exception as e:
                    print(f"  [dim]{r} 规则失败: {type(e).__name__}: {str(e)[:50]}[/dim]")
                    continue
                if not cands:
                    print(f"  [dim]{r}: 无候选[/dim]")
                    continue
                added = 0
                for c in cands:
                    if c.stock_code not in seen and added < 5:
                        seen.add(c.stock_code)
                        merged_codes.append(c.stock_code)
                        rule_hits[c.stock_code] = [r]
                        added += 1
                    elif c.stock_code in rule_hits:
                        rule_hits[c.stock_code].append(r)  # 已被其他规则选，记共振
                print(f"  ✓ {r}: 取 {added} 只")
            # 四规则平等，全部合并去重后送笨总评分统一排序，不按规则顺序截断
            # （否则排在后的 value_pick 会被机械砍掉，没机会评分）
            codes = merged_codes
            if not codes:
                print(f"  [red]✗ 四规则均无候选(全市场行情源可能失效)[/red]")
                print(f"  [yellow]→ 改用主题词模式: bz scan <主题词>(如 bz scan AI,半导体)[/yellow]")
                print(f"  [yellow]→ 或单股分析: l <代码> / bz <代码>(走Baostock)[/yellow]")
                return
            resonate = {c: rs for c, rs in rule_hits.items() if len(rs) > 1}
            print(f"  ✓ 四规则合并去重 {len(codes)} 只（全部送笨总评分，含 {len(resonate)} 只多规则共振）")
            print()
        else:
            print("  [Step 1/3] 技术面初筛中...")
            try:
                candidates, scan_info = engine.quick_scan(
                    rule_name=rule_name,
                    market_query=None,
                    exclude_codes=exclude_codes,
                )
            except Exception as e:
                print(f"  [red]✗ 初筛失败: {type(e).__name__}: {e}[/red]")
                return

            if "error" in scan_info:
                err = scan_info['error']
                if "全市场" in err or "行情数据" in err:
                    print(f"  [red]✗ 全市场行情源失效(新浪/东财当前不可用)[/red]")
                    print(f"  [yellow]→ 改用主题词模式: bz scan <主题词>(如 bz scan AI,半导体)[/yellow]")
                    print(f"  [yellow]→ 或单股分析: l <代码> / bz <代码>(走Baostock,不依赖全市场快照)[/yellow]")
                    print(f"  [dim]全市场源失效是外部数据源问题,非代码bug,恢复后自动可用[/dim]")
                else:
                    print(f"  [red]✗ 扫描失败: {err}[/red]")
                return
            if not candidates:
                print(f"  [yellow]⚠ 初筛无候选股（可能非交易时段或条件过严）[/yellow]")
                return

            codes = [c.stock_code for c in candidates[:limit]]
            print(f"  ✓ 初筛 {len(candidates)} 只 → 取前 {len(codes)} 只评分")
            print()

    # ── Step 2: 笨总批量 AI 评分 ──
    print(f"  [Step 2/3] 笨总 6 维 AI 评分中（{len(codes)}只 × 5维 ≈ {len(codes)*5}次AI，请耐心）...")
    def _progress(i, total, code, status):
        print(f"    ({i}/{total}) {code} → {status}")

    batch = auto_score_batch(codes, top_n=top_n, force_refresh=force_refresh,
                             config=config, progress_cb=_progress)
    print()

    # ── Step 3: 展示笨总 TopN 排名 ──
    print("─" * 64)
    print(f"  📊 笨总 Top{len(batch['top_n'])} 排名（评分 {batch['scored']}只 / 失败 {batch['failed']}只）")
    print("─" * 64)
    try:
        from rich.table import Table
        from rich.console import Console
        rc = Console()
        tbl = Table(show_header=True, header_style="bold cyan", show_lines=False)
        tbl.add_column("#", width=3)
        tbl.add_column("代码", width=8)
        tbl.add_column("名称", width=10)
        tbl.add_column("评分", justify="right")
        tbl.add_column("等级", justify="center")
        tbl.add_column("置信度", justify="right")
        tbl.add_column("行业景气/纯度/估值/龙头/辨识/风险")
        for idx, item in enumerate(batch["top_n"], 1):
            ds = item["dim_scores"]
            ip = ds.get("industry_prosperity", 0)
            # 景气度低于阈值标红：笨总大前提，景气≤30=边际下行/=0=失效
            dim_parts = []
            for d in ["industry_prosperity", "business_purity", "valuation_position",
                      "industry_leader", "market_recognition", "risk_deduction"]:
                v = ds.get(d, 0)
                if d == "industry_prosperity" and v <= 30:
                    dim_parts.append(f"[red]{v:.0f}[/red]")
                else:
                    dim_parts.append(f"{v:.0f}")
            dims_str = "/".join(dim_parts)
            eff = item.get("effective_grade", item["grade"])
            raw = item["grade"]
            grade_color = {"A": "green", "B": "green", "C": "yellow", "D": "red", "F": "red"}.get(eff, "white")
            # 实际等级与原始等级不同 → 标注被景气度闸门降级
            grade_disp = f"[{grade_color}]{eff}[/{grade_color}]"
            if eff != raw:
                grade_disp += f"[dim](原{raw})[/dim]"
            veto = " 🚨否决" if item.get("invalidate") else ""
            norm = item.get("normalized_score", item["total_score"])
            tbl.add_row(str(idx), item["code"], item["name"][:8],
                        f"{norm:.0f}",
                        grade_disp + veto,
                        f"{item['confidence']:.2f}", dims_str)
        rc.print(tbl)
        print(f"  [dim]评分=归一化(0-100) | 等级含行业景气度闸门(景气≤30最高C/=0判F) | 景气分标红=笨总大前提存疑[/dim]")
    except Exception:
        # rich 不可用时降级纯文本
        for idx, item in enumerate(batch["top_n"], 1):
            eff = item.get("effective_grade", item["grade"])
            norm = item.get("normalized_score", item["total_score"])
            raw_tag = f"(原{item['grade']})" if eff != item["grade"] else ""
            print(f"  {idx}. {item['code']} {item['name'][:8]} | {norm:.0f} {eff}{raw_tag} | conf {item['confidence']:.2f}")
    print()

    if batch["failures"]:
        print(f"  [dim]失败: {', '.join(f['code'] for f in batch['failures'])}[/dim]")
        print()

    # ── Step 4 (可选): 批量回测 TopN ──
    if not do_backtest:
        print(f"  💡 提示：加 --backtest --start YYYY-MM-DD --end YYYY-MM-DD 对 Top{len(batch['top_n'])} 批量回测")
        print()
        return

    from datetime import datetime, timedelta
    start = args.get("start") or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    end = args.get("end") or datetime.now().strftime("%Y-%m-%d")
    capital = args.get("capital", 100000.0)

    print("=" * 64)
    print(f"  📈 批量回测 Top{len(batch['top_n'])} — {start} ~ {end} 资金¥{capital:,.0f}")
    print("=" * 64)

    from src.core.backtest_engine import BacktestEngine
    from src.cli.main import load_pyramid_config, normalize_stock_code
    pyramid_config = load_pyramid_config(config)

    bt_rows = []
    for idx, item in enumerate(batch["top_n"], 1):
        code = normalize_stock_code(item["code"])
        print(f"  ({idx}/{len(batch['top_n'])}) 回测 {code} {item['name'][:8]}...")
        try:
            eng = BacktestEngine(
                stock_code=code, start_date=start, end_date=end,
                initial_capital=capital, execution_mode="framework_strict",
                layer_mode="decision_strategy_execution",
                skills_dir=skills_dir, signal_weights=signal_weights,
                skill_types=skill_types, entry_exit_config=entry_exit_config,
                pyramid_config=pyramid_config,
            )
            res = eng.run()
            bt_rows.append({
                "code": code, "name": item["name"][:8],
                "bz_score": item["total_score"], "bz_grade": item["grade"],
                "return": res.total_return_pct, "bench": res.benchmark_return_pct,
                "trades": getattr(res, "total_trades", 0) or 0,
            })
        except Exception as e:
            print(f"    [red]✗ 回测失败: {type(e).__name__}: {e}[/red]")
            bt_rows.append({"code": code, "name": item["name"][:8],
                            "bz_score": item["total_score"], "bz_grade": item["grade"],
                            "return": None, "bench": None, "trades": 0})

    # 回测对比表
    print()
    print("─" * 64)
    print(f"  📊 笨总排名 vs 回测收益对比")
    print("─" * 64)
    try:
        from rich.table import Table
        from rich.console import Console
        rc = Console()
        tbl = Table(show_header=True, header_style="bold cyan")
        tbl.add_column("#", width=3)
        tbl.add_column("代码", width=8)
        tbl.add_column("名称", width=10)
        tbl.add_column("笨总分", justify="right")
        tbl.add_column("等级", justify="center")
        tbl.add_column("收益%", justify="right")
        tbl.add_column("基准%", justify="right")
        tbl.add_column("超额%", justify="right")
        tbl.add_column("交易次", justify="right")
        for idx, r in enumerate(bt_rows, 1):
            if r["return"] is None:
                tbl.add_row(str(idx), r["code"], r["name"], f"{r['bz_score']:.1f}",
                            r["bz_grade"], "[red]失败[/red]", "-", "-", "-")
                continue
            excess = r["return"] - (r["bench"] or 0)
            ret_color = "green" if r["return"] > 0 else "red"
            exc_color = "green" if excess > 0 else "red"
            tbl.add_row(str(idx), r["code"], r["name"], f"{r['bz_score']:.1f}",
                        r["bz_grade"], f"[{ret_color}]{r['return']:+.2f}[/{ret_color}]",
                        f"{r['bench']:+.2f}", f"[{exc_color}]{excess:+.2f}[/{exc_color}]",
                        str(r["trades"]))
        rc.print(tbl)
    except Exception:
        for idx, r in enumerate(bt_rows, 1):
            ret = f"{r['return']:+.2f}%" if r["return"] is not None else "失败"
            print(f"  {idx}. {r['code']} {r['name']} | 笨总{r['bz_score']:.1f}{r['bz_grade']} | 收益{ret}")
    print()
    print(f"  💡 笨总高分股回测未必跑赢——笨总偏定性选股，回测是技术面择时，对比表诚实展示")
    print()


def _run_benzong_manual(meta: str = ""):
    """v0.8.6.1 交互式手动打分（兜底，保留）。"""
    from src.core.benzong import score_one

    print()
    print("=" * 60)
    print("  📊 笨总「超景气价值投机」6 维打分")
    print("  来源：B 站 up 主「笨笨的韭菜」教学体系")
    if meta:
        print(f"  标的: {meta}")
    print("=" * 60)
    print()
    print("⚠ 大前提：必须先确认本股所在行业是高景气 / 现象级拐点已出现")
    print("  否则打分模型失效（教学 2 反面案例：中免 0 景气度 = 模型作废）")
    print()
    print("请逐项打分（每项 0-100，回车跳过给 0 分）：")
    print()

    ip = _ask_score("1️⃣ 行业景气度（权重 20%）", 0)
    if ip == 0:
        print()
        print("⚠ 行业景气度=0 → 大前提失效，本次打分无意义。")
        print("  建议先回去识别现象级拐点事件，再来打分。")
        print()
        return

    bp = _ask_score("2️⃣ 业务纯度（主营占比，权重 40% 最重要）", 100)
    vp = _ask_score("3️⃣ 历史估值位置（近 3 年最低价 200% 内=满分，权重 25%）", 50)
    il = _ask_score("4️⃣ 细分行业龙头（全球/国内/A股龙头，权重 15%）", 80)
    mr = _ask_score("5️⃣ 市场辨识度（提产品第一反应是它，权重 20%）", 80)
    rd = _ask_score("6️⃣ 个股风险值（扣分项，0=最好。定增/减持/官司各 +20）", 0)

    print()
    print("7️⃣ 全市场流动性（影响系数）：")
    try:
        liq_str = input("   单日成交额（万亿，如 1.2，回车=1.0）: ").strip()
        liq = float(liq_str) if liq_str else 1.0
    except (ValueError, EOFError):
        liq = 1.0

    s = score_one(
        industry_prosperity=ip,
        business_purity=bp,
        valuation_position=vp,
        industry_leader=il,
        market_recognition=mr,
        risk_deduction=rd,
        market_turnover_trillion=liq,
        stock_code=meta,
        stock_name=meta,
    )

    # 输出结果
    print()
    print("─" * 60)
    print(f"  📋 打分明细（流动性系数 ×{s.liquidity_coeff}）")
    print("─" * 60)
    contribs = s.contributions
    print(f"  行业景气度  ×0.20 = {contribs['industry_prosperity']:>+6.2f}")
    print(f"  业务纯度    ×0.40 = {contribs['business_purity']:>+6.2f}")
    print(f"  历史估值    ×0.25 = {contribs['valuation_position']:>+6.2f}")
    print(f"  细分龙头    ×0.15 = {contribs['industry_leader']:>+6.2f}")
    print(f"  市场辨识度  ×0.20 = {contribs['market_recognition']:>+6.2f}")
    print(f"  个股风险值  ×-0.20= {contribs['risk_deduction']:>+6.2f}（扣分）")
    print(f"  原始累加         = {s.raw_sum:>+6.2f}")
    print()

    grade = s.effective_grade()
    raw_grade = s.grade()
    grade_label = {
        "A": "🏆 A 级 — 超优质",
        "B": "✅ B 级 — 优秀（可买入）",
        "C": "🟡 C 级 — 可观察",
        "D": "🔻 D 级 — 勉强观望",
        "F": "❌ F 级 — 放弃",
    }[grade]
    downgrade_note = f"（原始 {raw_grade}，景气度闸门降级）" if grade != raw_grade else ""
    print(f"  💯 评分：{s.normalized_score():.0f}/100 → {grade_label} {downgrade_note}")
    print("─" * 60)

    warn = s.precondition_warning()
    if warn:
        print()
        print(f"  {warn}")

    print()
    print("📚 评分依据：教学 2 + 笨总选股打分表.xlsx")
    print("   案例对照：HND（83.2 B 级买入）/ THS（84.6 B 级买入）/ 中免（0 景气度作废）")
    print()


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

    elif mode == "pos_plan":
        manage_positions("plan", stock_code=args.get("stock_code", ""),
                         update=args.get("update", False))

    elif mode == "chat":
        from src.chat.agent import run_chat_repl
        config = load_config()
        run_chat_repl(config)

    elif mode == "benzong":
        # v0.8.6.2: bz <code> 默认自动 AI 评分；--manual 走旧交互式
        # v0.8.6.3: bz --check 数据源连通性体检（ISS-043）
        if args.get("check"):
            from src.data.source_check import check_all_sources, format_report
            print(format_report(check_all_sources()))
            return
        run_benzong_scoring(
            meta=args.get("meta", ""),
            manual=args.get("manual", False),
            refresh=args.get("refresh", False),
        )

    elif mode == "benzong_scan":
        # v0.8.6.3: bz scan 选股初筛→笨总批量评分→可选回测（ISS-041 方向 A）
        run_benzong_scan(args)


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
