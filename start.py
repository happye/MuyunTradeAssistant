#!/usr/bin/env python3
"""暮云思辨投资助手 - 交互式启动脚本
双击即可运行，自动进入交互循环模式。
命令行带参数时直接代理给 src.cli.main（支持所有 argparse 参数）。
"""

import sys
import os
import copy
import math
import subprocess
import time

# 确保工作目录为脚本所在目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 清理代理环境变量 + NO_PROXY=*：金融数据API(新浪/东财/同花顺)直连更稳，
# 走代理会被clash拦截返回456/HTML。NO_PROXY=* 强制 requests/urllib3 对所有域名直连，
# 即使开着 clash 系统代理也能跑。必须在import数据模块前设。
# baostock 走自研socket不读NO_PROXY，无所谓；akshare/efinance 底层都是requests会读。
for _k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"   # 所有域名强制直连(绕过clash系统代理)
os.environ["no_proxy"] = "*"

from src.data.source_check import fix_curl_ssl_paths

fix_curl_ssl_paths()

# Windows 下设置 UTF-8（通过环境变量，不替换sys.stdout避免与Rich冲突）
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.system("chcp 65001 >nul 2>&1")

VERSION = "v0.8.42"  # 当次行业适用性与现金条件化观察；验收状态见README。

# ISS-078：REPL 内新增的降级告警走标准 logging（WARNING+ 无 handler 时经 lastResort
# 输出 stderr，plain_errors 过滤器若已挂根 handler 会同步做人话翻译与会话汇总）
import logging
logger = logging.getLogger("start")

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


def _display_width(s: str) -> int:
    """终端显示宽度：全角字符（中文、中文标点）按 2 列，框线/符号（─│★●，
    ambiguous 类）按 1 列——与中文 Windows UTF-8 终端实际渲染一致。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
               for ch in s)


def show_help():
    """命令菜单（v0.8.17 重排）：行宽由 _display_width 程序化对齐，杜绝参差。"""
    W = 70   # 内容区显示宽度

    def _row(text: str = "") -> str:
        pad = W - _display_width(text)
        return "│ " + text + " " * max(pad, 0) + " │"

    def _cmd(cmd: str, desc: str) -> str:
        """命令行：命令列左对齐 26 列，说明列跟随。"""
        gap = 26 - _display_width(cmd)
        return _row("  " + cmd + " " * max(gap, 1) + desc)

    L = []
    L.append("┌" + "─" * (W + 2) + "┐")
    L.append(_row("暮云思辨投资助手 · 命令菜单"))
    L.append(_row("典型流程: today 看待办 → scan / bz scan 选股 → l all 深分析 → pos add 建仓"))
    L.append(_row("          → diff 看变化 → scan review 复盘检验"))
    L.append(_row())
    L.append(_row("● 每日入口"))
    L.append(_cmd("today", "今日工作台（待办/持仓/等待——建议≠成交）"))
    L.append(_cmd("shadow", "影子对照报告（legacy vs fusion 决策表差异观察）"))
    L.append(_cmd("plan2 <代码> mid|long <意图>", "立中期/长期投资计划（影子出真判断的前提）"))
    L.append(_cmd("research <代码>", "研究工作台：--cash-materials 清单.json 导入材料；--cash-verify 回执ID 核对数字，加 --source-check 核官方字段，再加 --industry-check 查询SSE行业适用性并比较现金；--capture 采财务；--claims/--checkpoint 绑证据"))
    L.append(_row())
    L.append(_row("● 分析"))
    L.append(_cmd("l <代码>", "实时深分析（证据卡+本地只读研究状态）"))
    L.append(_cmd("l <代码>,<代码2>", "多代码批量（逗号/空格分隔均可）"))
    L.append(_cmd("l 600519,000001 --budget 600", "可选时限；到点不开新只，当前只分析完"))
    L.append(_cmd("l all [-f]", "批量深分析最近扫描（-f 强制重析当天已析）"))
    L.append(_cmd("l all [-f] --budget 600", "可选，确认后计时；当前只分析完返回"))
    L.append(_cmd("la", "一键分析所有持仓（简明卡）"))
    L.append(_cmd("la --budget 600", "可选，默认不启用；到点不开新只"))
    L.append(_cmd("<6位代码>", "直接输代码，等同 l <代码>"))
    L.append(_cmd("#N", "取最近扫描第 N 只（l/bz/pos 通用）"))
    L.append(_cmd("diff", "列出可对比的股票（要两次分析记录）"))
    L.append(_cmd("diff <代码>", "同股最近两次分析对比（变化点）"))
    L.append(_row())
    L.append(_row("● 扫描与复盘"))
    L.append(_cmd("scan [s] [--budget 秒]", "一键分析所有持仓（非全市场扫描）"))
    L.append(_cmd("scan market [规则|主题]", "全市场扫描（主题词如 AI,半导体）"))
    L.append(_cmd("scan market deep", "扫描 + 自动深度分析"))
    L.append(_cmd("scan market deep --budget 600", "自动深析可选；到期不开新只，当前只跑完"))
    L.append(_cmd("scan review [天数]", "复盘近N天扫描涨跌+按方法分组（默认7）"))
    L.append(_cmd("scan review import", "一次性导入旧扫描报告（幂等可重跑）"))
    L.append(_cmd("rules", "列出全部扫描规则"))
    L.append(_cmd("industries [词] / concepts [词]", "行业 / 概念板块"))
    L.append(_row())
    L.append(_row("● 笨总评分（超景气价值投机）"))
    L.append(_cmd("bz <代码>[,代码2]", "AI 6维评分排名（当日缓存不重复计费）"))
    L.append(_cmd("bz <代码> --refresh", "强制刷新单只评分"))
    L.append(_cmd("bz scan [主题] [--top N]", "主题选股初筛 + 批量评分排名"))
    L.append(_cmd("bz scan <规则> [主题]", "规则初筛 + 评分（规则名可模糊匹配）"))
    L.append(_cmd("bz scan --allrules", "五规则全跑合并去重（各 Top5）"))
    L.append(_cmd("bz scan --budget 600 --limit 15", "可选；默认不启用，到期不开新阶段"))
    L.append(_cmd("bz scan ... --backtest [起 止]", "评分后批量回测验证"))
    L.append(_cmd("ba", "批量评分最近扫描结果（缓存优先）"))
    L.append(_cmd("ba --budget 60", "可选，默认不启用；到点不开新只"))
    L.append(_cmd("bz 600519,000001 --budget 60", "可选；到点不开新只，当前只跑完返回"))
    L.append(_cmd("bz --check", "数据源连通性体检"))
    L.append(_cmd("bz --manual", "旧交互式手动打分（兜底）"))
    L.append(_row())
    L.append(_row("● 观察池"))
    L.append(_cmd("watch", "在池股 入池价→现价 + 走势（即复盘）"))
    L.append(_cmd("watch add <代码|#N> [名] [价]", "手动入池（缺省拉实时价）"))
    L.append(_cmd("watch rm <代码>", "移出（出池后分析 WATCH 会重新入池）"))
    L.append(_row("  分析出 WATCH 的票自动入池；scan review 用 ✓ 标注在池股"))
    L.append(_row())
    L.append(_row("● 持仓"))
    L.append(_cmd("pos [list]", "持仓列表"))
    L.append(_cmd("pos add <代码> [名] [价] [仓位]", "建仓（带价格才生成计划草稿）"))
    L.append(_cmd("pos confirm <代码> [--qty 股数 --price 价]", "确认实际成交（带 --qty 入事件账本）"))
    L.append(_cmd("pos opening <现金> / openinglot <码 量 成本 [日]>", "期初导入（现金锚定/持仓批次）"))
    L.append(_cmd("pos rm <代码>", "删除持仓记录"))
    L.append(_cmd("pos plan <代码>|all [--update]", "查看 / 生成 / 更新交易计划"))
    L.append(_cmd("pos overweight <代码> [依据]", "超配策略（教学十，只出手一次）"))
    L.append(_row())
    L.append(_row("● 回测"))
    L.append(_cmd("b <代码> [起 止 资金]", "单只回测（默认近1年，10万）"))
    L.append(_cmd("bb <代码,代码> [起 止]", "批量回测验证"))
    L.append(_row())
    L.append(_row("● 市场情报"))
    L.append(_cmd("expect [天数]", "预期事件日历 + 透支面板（默认30天）"))
    L.append(_cmd("events", "事件驱动预警（事后）"))
    L.append(_cmd("fear", "市场恐慌指数 0-100（越高越恐慌）"))
    L.append(_cmd("fear history", "多周期回顾 + 走势图（--no-chart 跳绘图）"))
    L.append(_cmd("fear backfill [天数]", "涨停池历史回填（断点续传，首次建议跑）"))
    L.append(_cmd("chains", "产业链图谱列表 / 管理"))
    L.append(_row())
    L.append(_row("● 工具与开关"))
    L.append(_cmd("doctor", "运行诊断（环境/依赖/配置/缓存）"))
    L.append(_cmd("tasks", "批量任务：run 身份/已完成/降级/失败/未记录结果"))
    L.append(_cmd("noai / debug", "切换 AI 开关 / AI 调试模式"))
    L.append(_cmd("chat", "AI 对话（自然语言跑任意命令+改持仓）"))
    L.append(_row("  h | help | ?  显示本菜单        q | quit  退出"))
    L.append(_row())
    L.append(_row("● 扫描规则速查（scan market / bz scan <规则> 通用）"))
    L.append(_row("  healthy_pullback 健康回调★   steady_advance  温和上涨"))
    L.append(_row("  shrink_pullback 缩量回调      value_pick      低估值"))
    L.append(_row("  theme_members   主题成分      oversold_watch  超跌错杀"))
    L.append(_row("  （来源：B站「笨笨的韭菜」教学体系；rules 可看全部参数）"))
    L.append(_row())
    L.append(_row("● 示例"))
    L.append(_row("  scan market AI,半导体    主题扫描 → l all → tasks 看进度"))
    L.append(_row("  bz scan 氮化镓 --top 5   主题选股评分"))
    L.append(_row("  pos add #1 融捷股份 35.20 0.20   扫描第1只直接建仓"))
    L.append(_row("  b 000001 2025-01-01 2026-01-01 200000   自定义区间回测"))
    L.append(_row())
    L.append(_row("● 提示"))
    L.append(_row("  数据源超时会停止等待并降级；底层请求可能仍在运行，避免连续重试"))
    L.append(_row("  已放弃的嵌套取数同样跳过快照/K线缓存写入"))
    L.append(_row("  主题代码查询与读取共用15秒等待，登录同会话"))
    L.append(_row("  备用行情查询与读取共用30秒等待，登录同会话"))
    L.append(_row("  Baostock 共享会话忙或超时：立即降级，不另起会话"))
    L.append("└" + "─" * (W + 2) + "┘")
    print("\n".join(L))
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


def _resolve_index_arg(s: str):
    """解析 #N 引用：取最近 scan 第 N 只（1-based）。返回 (code, name) 或 None。

    供 l #1 / bz #3 / pos add #3 序号快捷用。无 scan 或越界时打印提示。
    """
    if not s.startswith("#") or not s[1:].isdigit():
        return None
    from src.cli.session_state import resolve_index, last_scan_count
    n = int(s[1:])
    r = resolve_index(n)
    if r is None:
        total = last_scan_count()
        if total == 0:
            print("  [!] 还没有扫描结果。先跑：bz scan [主题] 或 scan market")
        else:
            print(f"  [!] 序号 {n} 超出范围（最近扫描共 {total} 只）")
        return None
    item, warning = r
    if warning:
        print(f"  ⚠ {warning}")
    return item.get("code", ""), item.get("name", "")


def _iter_code_pieces_raw(token):
    """单个 token 按中英文逗号/顿号/分号切出的全部非空片段（含 - 开头的片段）。"""
    for piece in str(token).replace("，", ",").replace("、", ",").replace("；", ";").replace(";", ",").split(","):
        piece = piece.strip()
        if piece:
            yield piece


def _iter_code_pieces(tokens: list):
    """把 token 按中英文逗号/顿号/分号切成片段（跳过空片段与 - 开头的 flag）。"""
    for tok in tokens:
        for piece in _iter_code_pieces_raw(tok):
            if not piece.startswith("-"):
                yield piece


def _extract_codes(tokens: list) -> tuple:
    """从 token 列表提取股票代码（v0.8.9.0 ISS-087 多代码支持）。

    分隔符：token 内部支持 英文逗号/中文逗号/顿号/分号（中英文），token 之间
    天然按空格切分——三种分隔符可混用。每个片段支持：
    - 6位代码或带后缀（600519 / 000001.SZ，_is_stock_code 判定）
    - #N 序号引用（复用 _resolve_index_arg，取最近扫描第 N 只）
    - 以 - 开头的 token 视为 flag 跳过（如 -f/--force/--refresh）

    Returns:
        (codes, invalid)：codes 去重保序；invalid 为既非代码也非引用的片段
        （供调用方告警，消除此前"多给代码静默丢弃"的可用性陷阱）。
    """
    codes: list = []
    invalid: list = []
    # 中英文逗号/顿号/分号统一当分隔符（空格已由外层 split 处理）
    for piece in _iter_code_pieces(tokens):
        if piece.startswith("#"):
            ref = _resolve_index_arg(piece)
            if ref and ref[0]:
                if ref[0] not in codes:
                    codes.append(ref[0])
            else:
                invalid.append(piece)
        elif _is_stock_code(piece):
            if piece not in codes:
                codes.append(piece)
        else:
            invalid.append(piece)
    return codes, invalid


def _distinct_code_pieces(tokens: list) -> int:
    """纯语法计数（T05-L）：不同的「代码形状」片段数，不解析 #N（不读扫描状态）。

    只计 6 位代码/带后缀代码与 #数字 引用；无法识别的片段不算代码（否则
    `l #1 foo --budget 60` 会被当成多只而先读扫描状态）。预算只用于多代码批，
    单个代码形状片段（含单个 #N）必须在 resolve_index 之前拒绝。
    """
    return len({p for p in _iter_code_pieces(tokens)
                if _is_stock_code(p) or (p.startswith("#") and p[1:].isdigit())})


def _parse_budget_seconds(raw):
    """解析启动预算秒数（T05-J）：只接受有限正数；返回 (秒数, 错误说明)。

    nan/inf/0/负数/非数字一律显式拒绝——预算没有隐藏默认值。
    """
    text = str(raw).strip()
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None, f"--budget 值无效: {text[:32]!r}（需有限正数秒）"
    if not math.isfinite(value):
        return None, f"--budget 值无效: {text[:32]!r}（需有限正数秒，nan/inf 不接受）"
    if value <= 0:
        return None, f"--budget 值无效: {text[:32]!r}（需大于 0 秒）"
    return value, None


_BUDGET_HELP = ("预算用法: --budget <秒> 或 --budget=<秒>（有限正数；支持 ba/bzall、"
                "bz scan、bz 多代码、l 多代码/la/l all 批量深析，scan market deep "
                "自动深度分析，以及 scan 持仓扫描；到期只阻止开始下一步，已开始的调用会跑完，"
                "准备阶段可能超出，非强制中断/无费用上限）")

# T05-L：不支持入口的统一说明（单只 l/bz、scan 等）；T05-N 起 bz scan 已支持；
# T05-O 起 scan market deep 自动深度分析也支持（普通交互 scan market 仍不支持）；
# T05-P 起顶层 scan 持仓扫描也支持
_BUDGET_SCOPE = ("预算仅用于 ba/bzall、bz scan、bz 多代码、l 多代码/la/l all 批量深析，"
                 "scan market deep 自动深度分析，以及 scan 持仓扫描")

# T05-O：scan market deep 预算的拒绝/校验文案（不暴露内部 helper/文件名）
_SCAN_MARKET_BUDGET_SCOPE = "预算仅用于 scan market deep 自动深度分析"
_SCAN_MARKET_BUDGET_INVALID = ("  [!] 预算参数无效（需有限正数秒）；本次未开始扫描"
                               "（不加载配置/账户、不扫描、不落盘）")

# T05-J / rev2 J7：普通无预算流程的主动可发现性提示（在确认/缓存预览之前打印）。
# 只说“可选、默认不启用、到点不开新只/当前只跑完”，不新增确认、不改 kwargs。
_BUDGET_HINT = ("  可选时限：末尾可加 --budget 60（默认不启用）；"
                "到点不开始下一只，当前一只跑完再返回")


# ── T05-N：bz scan 可选启动预算与候选上限（limit/top 正整数，默认 15/10）──
_SCAN_BUDGET_HINT = ("  可选启动预算：末尾可加 --budget 600 --limit 15（默认不启用；"
                     "从本次扫描入口计时，到期不再开始新阶段/评分/回测，已开始的调用会跑完；"
                     "非强制中断、非费用上限）")

_SCAN_BUDGET_INVALID = ("  [!] 预算参数无效（需有限正数秒）；本次未开始扫描"
                        "（不加载配置/账户、不定位/扫描、不评分/回测、不落盘）")


def _scan_budget_notice(seconds: float) -> str:
    """bz scan 预算语义：从入口计时（准备也计入），到期只阻止开始下一阶段。"""
    return (f"  启动预算：{seconds:g} 秒（bz scan；从本次扫描入口计时，"
            "配置/账户/主题定位/扫描准备都计入；到期不再开始新阶段/评分/回测，"
            "已开始的调用会跑完；准备阶段可能超出预算——非强制中断、非费用上限）")


def _scan_positive_int(raw, *, from_text: bool):
    """limit/top 正整数校验：解析层从文本转 int，程序化入口只接受 int（排除 bool）。"""
    if from_text:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return None, f"{raw!r} 不是整数"
    else:
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None, f"{raw!r} 不是整数（程序化入口只接受 int）"
        value = raw
    if value <= 0:
        return None, f"{value} 需大于 0"
    return value, None


def _scan_limit_codes(codes, limit: int) -> list:
    """候选去重后按原顺序取前 limit 个（limit 是送评分名单上限，不是发现调用上限）。"""
    return _dedupe_live_codes(list(codes))[:limit]


def _scan_round_robin(buckets, cap: int) -> list:
    """在规则贡献桶上轮询选取最多 cap 只，避免先跑的规则占满截断。"""
    out = []
    depth = max((len(b) for b in buckets), default=0)
    for depth_i in range(depth):
        for bucket in buckets:
            if len(out) >= cap:
                return out
            if depth_i < len(bucket):
                out.append(bucket[depth_i])
    return out


def _scan_prep_stop(stage: str) -> None:
    """准备阶段到期：不开始后续业务，不落盘（已完成的调用保留）。"""
    print(f"  ⏱ 启动预算已到期：{stage}，本次不再开始后续阶段")
    print("  未开始候选定位/扫描/评分，不写 last_scan/正式扫描报告、不开启回测"
          "（已完成的准备调用保留；非强制中断）")


def _scan_candidates_incomplete(reason: str) -> None:
    """候选阶段到期且名单未完整形成：显式不完整，本批不评分、不落盘。"""
    print(f"  ⏱ 启动预算已到期：{reason}，当前候选不完整")
    print("  本批不开始评分；部分名单不落盘（不写 last_scan/正式扫描报告）、不开启回测")


def _scan_candidates_complete_no_scoring(n: int) -> None:
    """候选已完整但截止已到：不开始评分阶段，不把未评分名单当完整结果落盘。"""
    print(f"  ⏱ 启动预算已到期：候选已完整形成（{n} 只），但本批不开始评分")
    print("  本次不写 last_scan/正式扫描报告、不开启回测（不把未评分名单当完整结果）")



def _split_budget_option(tokens: list):
    """分离启动预算选项（T05-J）：返回 (budget_seconds|None, 其余 token, 错误说明|None)。

    只识别 `--budget VALUE` 与 `--budget=VALUE`；缺值/重复/非数字/非有限/<=0 都返回
    明确错误，供调用方在业务动作（确认/扫描读取/网络/AI/账本）之前拒绝。未出现
    `--budget` 时 budget=None 且 rest 与 tokens 逐项一致——无预算路径行为不变。
    """
    budget = None
    rest = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = str(tok).lower()
        if low == "--budget":
            if i + 1 >= len(tokens):
                return None, rest, "--budget 缺少秒数值"
            raw = tokens[i + 1]
            i += 2
        elif low.startswith("--budget="):
            raw = str(tok).split("=", 1)[1]
            i += 1
        else:
            rest.append(tok)
            i += 1
            continue
        if budget is not None:
            return None, rest, "--budget 只能给一次（重复无效）"
        seconds, err = _parse_budget_seconds(raw)
        if err:
            return None, rest, err
        budget = seconds
    return budget, rest, None


def _budget_notice_text(seconds: float, scope: str) -> str:
    """预算语义的用户可见说明（确认前打印；计时从确认后/批次入口才开始）。"""
    return (f"  启动预算：{seconds:g} 秒（{scope}；确认后开始计时，确认前缓存预览不计入；"
            "到期不再开始新股票，已开始的股票会跑完，配置/取数等准备阶段可能超出预算"
            "——非强制中断、非费用上限）")


def _scan_budget_tokens(tokens: list):
    """扫描预算 option token（T05-J fix2）：返回 (是否出现合法预算选项, 未知预算形状)。

    只核以 `-` 开头的 option token：`--budget` / `--budget=…` 算合法形状；其余
    `--budget*`（如 --budgetx、--budgets、--budget-any）算未知形状，必须在 parse
    所有早返回之前显式拒绝，不能变成主题/位置参数或被静默忽略。普通文本里出现
    budget 字样（非 option token）不受影响。
    """
    has_budget = False
    unknown = []
    for tok in tokens:
        low = str(tok).lower()
        if not low.startswith("-"):
            # T05-L：用逗号/顿号/分号粘在代码后的预算选项（如 600519,000001,--budget=60）
            # 会在代码提取时被当成 flag 静默丢掉——必须显式拒绝，不能悄悄变成无预算批
            if any(p.startswith(("--budget", "-budget")) for p in _iter_code_pieces_raw(low)):
                unknown.append(tok)
            continue
        if low == "--budget" or low.startswith("--budget="):
            has_budget = True
        elif low.startswith("--budget") or low.startswith("-budget"):
            unknown.append(tok)
    return has_budget, unknown


def parse_input(user_input: str):
    """解析用户输入，返回 (mode, args_dict) 或 None"""
    # 兼容处理：在 GBK/Windows 命令行下，如果输入通过管道输入且含有 UTF-8 BOM，
    # 极易被误解码为 "锘" / "縝" 拼接词。此处在字节/字符层面予以智能过滤。
    try:
        raw_bytes = user_input.encode("gbk")
        if raw_bytes.startswith(b"\xef\xbb\xbf"):
            raw_bytes = raw_bytes[3:]
        elif raw_bytes.startswith(b"\xff\xfe"):
            raw_bytes = raw_bytes[2:]
        text = raw_bytes.decode("gbk").strip()
    except Exception:
        text = user_input.strip()
        for char in ["\ufeff", "锘", "縝", "锘?"]:
            text = text.replace(char, "")
        text = text.strip()

    if not text:
        return None

    parts = text.split()
    cmd = parts[0].lower()

    # ── T05-J fix2：预算 option token 统一前置门（所有早返回之前）──
    # 未知预算形状（--budgetx/--budgets/--budget-any…）不得变成主题/位置参数或被
    # 忽略；合法 --budget 只属于 ba/bzall、bz 多代码共用批与（T05-L）l 多代码/la/
    # l all 批量深析，其余命令上的显式预算同样拒绝（不是静默忽略）。单只 l/裸代码
    # 由各自分支在解析 #N 之前拒绝。只核以 - 开头的 option token，普通文本不受影响。
    _has_budget_opt, _unknown_budget_opts = _scan_budget_tokens(parts[1:])
    if _unknown_budget_opts:
        print(f"  [!] 未知预算选项: {'、'.join(str(t) for t in _unknown_budget_opts[:3])}"
              "（只支持 --budget <秒> 或 --budget=<秒>）")
        print(f"        {_BUDGET_HELP}")
        return None
    if (_has_budget_opt
            and cmd not in ("ba", "bzall", "bz", "benzong", "笨总", "l", "live", "la", "lall",
                            "scan", "s")
            and not _is_stock_code(parts[0])):
        print(f"  [!] 该命令不支持 --budget（{_BUDGET_SCOPE}）")
        print(f"        {_BUDGET_HELP}")
        return None

    # ── 基础命令 ──
    # v0.8.9.5（彻查批 P3）：原此处有一段与下方完全重复的 q/help/la/ba 解析块
    # （复制粘贴残留，第二份永不生效），已删除
    if cmd in ("q", "quit", "exit"):
        return ("quit", {})
    if cmd in ("h", "help", "?"):
        return ("help", {})

    # ── 实时分析：l/live + 代码，或直接输入6位代码 ──
    if cmd in ("la", "lall"):
        # T05-L：la 支持可选启动预算；无预算时返回旧形状（不新增键）
        _budget, _rest, _budget_err = _split_budget_option(parts[1:])
        if _budget_err:
            print(f"  [!] {_budget_err}")
            print(f"        {_BUDGET_HELP}")
            return None
        if _budget is not None:
            return ("live_all", {"budget": _budget})
        return ("live_all", {})

    if cmd in ("ba", "bzall"):
        # T05-J：可选启动预算（到期不再开始下一只股票）；默认无预算、无隐藏默认值
        budget, rest, budget_err = _split_budget_option(parts[1:])
        if budget_err:
            print(f"  [!] {budget_err}")
            print(f"        {_BUDGET_HELP}")
            return None
        if rest:
            print(f"  [!] ba/bzall 不接受其他参数: {' '.join(str(t) for t in rest)[:60]}")
            print(f"        {_BUDGET_HELP}")
            return None
        args = {}
        if budget is not None:
            args["budget"] = budget
        return ("benzong_all", args)

    if cmd in ("l", "live"):
        # T05-L：l 多代码 / l all 支持可选启动预算；预算值先从 token 里分离（绝不变成
        # 证券代码），语法坏值与单只（含单个 #N）在代码提取/扫描读取之前拒绝
        budget, rest, budget_err = _split_budget_option(parts[1:])
        if budget_err:
            print(f"  [!] {budget_err}")
            print(f"        {_BUDGET_HELP}")
            return None
        if not rest:
            print("  [!] 用法: l <代码>[,代码2,...] | l #N | l all[-f]（批量深分析最近扫描）")
            print("      多代码用 英文逗号/中文逗号/空格 分隔，如: l 600519,000001 或 l 600519 000001")
            return None
        # v0.8.7.9: l all 批量深分析最近一次扫描结果（bz scan / scan market），补齐 ba（批评分）/la（批持仓）空白
        # v0.8.7.9: 当天已深分析的默认跳过，-f/--force 强制全部重分析
        if rest[0].lower() == "all":
            force = any(str(p).lower() in ("-f", "--force") for p in rest[1:])
            scan_args = {"force": force}
            if budget is not None:
                scan_args["budget"] = budget
            return ("live_scan_all", scan_args)
        if budget is not None and _distinct_code_pieces(rest) < 2:
            print(f"  [!] 单只深析不支持 --budget（{_BUDGET_SCOPE}）")
            return None
        codes, invalid = _extract_codes(rest)
        if invalid:
            print(f"  [!] 无法识别的参数已跳过: {'、'.join(invalid)}（代码用6位数字；批量用逗号/空格分隔）")
        if not codes:
            print("  [!] 用法: l <6位股票代码>  例如: l 600519（多代码: l 600519,000001）")
            return None
        if len(codes) == 1:
            if budget is not None:
                print(f"  [!] 单只深析不支持 --budget（{_BUDGET_SCOPE}）")
                return None
            return ("live", {"stock_code": codes[0]})
        # v0.8.9.0 ISS-087: 多代码一次性批量深分析（compact 卡片 + 单只失败跳过）
        multi_args = {"codes": codes}
        if budget is not None:
            multi_args["budget"] = budget
        return ("live_multi", multi_args)

    if _is_stock_code(parts[0]):
        # T05-L：裸多代码（live_multi 语义）支持可选预算；单只裸代码在解析前拒绝
        budget, rest_tail, budget_err = _split_budget_option(parts[1:])
        if budget_err:
            print(f"  [!] {budget_err}")
            print(f"        {_BUDGET_HELP}")
            return None
        tokens = [parts[0]] + rest_tail
        if budget is not None and _distinct_code_pieces(tokens) < 2:
            print(f"  [!] 单只/裸代码深析不支持 --budget（{_BUDGET_SCOPE}）")
            return None
        # ISS-087：裸代码输入也支持多只（"600519 000001"），单只行为不变
        codes, invalid = _extract_codes(tokens)
        if invalid:
            print(f"  [!] 无法识别的参数已跳过: {'、'.join(invalid)}")
        if budget is not None and len(codes) < 2:
            print(f"  [!] 单只/裸代码深析不支持 --budget（{_BUDGET_SCOPE}）")
            return None
        if budget is not None:
            return ("live_multi", {"codes": codes, "budget": budget})
        if len(codes) >= 2:
            return ("live_multi", {"codes": codes})
        return ("live", {"stock_code": codes[0] if codes else parts[0]})

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
            # T05-O：预算只属于既有 `scan market deep` 自动深度分析；普通交互
            # scan market 带预算在业务/规则解析前拒绝，不新增 --deep 别名。
            budget, rest, budget_err = _split_budget_option(parts[2:])
            if budget_err:
                print(f"  [!] {budget_err}")
                print(f"        {_BUDGET_HELP}")
                return None
            args = {"rule_name": "healthy_pullback", "market_query": None, "deep": False}
            # scan market deep [主题]
            if rest and rest[0].lower() == "deep":
                args["deep"] = True
                rest = rest[1:]
            elif budget is not None:
                print(f"  [!] 该命令不支持 --budget（{_SCAN_MARKET_BUDGET_SCOPE}；"
                      "普通交互 scan market 不带 deep）；本次未执行")
                print("        用法: scan market deep [规则/主题] --budget 600")
                return None
            if rest:
                # 第一个参数当rule_name（cli/main resolve_rule_name模糊匹配；匹配失败则当主题词）
                args["rule_name"] = rest[0]
                if len(rest) > 1:
                    args["market_query"] = " ".join(rest[1:]).strip()
            if budget is not None:
                args["budget"] = budget
            return ("scan_market", args)
        # scan review [天数]：复盘近N天扫描历史的涨跌（验证选股，v0.8.11）
        if len(parts) >= 2 and parts[1].lower() in ("review", "复盘"):
            if _has_budget_opt:
                print(f"  [!] scan review 不支持 --budget（{_BUDGET_SCOPE}）；本次未执行")
                return None
            rest = parts[2:]
            # scan review import：导入 v0.8.7~v0.8.10 的旧扫描报告（一次性）
            if rest and rest[0].lower() in ("import", "导入"):
                return ("scan_review_import", {})
            days = 7
            if rest:
                try:
                    days = max(1, min(90, int(rest[0])))
                except ValueError:
                    print(f"  [!] '{rest[0]}' 不是有效天数，已按默认 7 天处理（用法: scan review 14）")
            return ("scan_review", {"days": days})
        # T05-P：顶层 scan 是一键持仓分析，支持可选 --budget；剥离预算后仍有其他 token
        # （主题/代码等）在业务前拒绝，不再静默忽略主题词去跑全部持仓 AI 分析。
        budget, rest, budget_err = _split_budget_option(parts[1:])
        if budget_err:
            print(f"  [!] {budget_err}")
            print(f"        {_BUDGET_HELP}")
            return None
        if rest:
            print(f"  [!] scan 是一键持仓分析，不接受主题/额外参数: "
                  f"{' '.join(str(t) for t in rest)[:60]}；本次未执行")
            _sub = str(rest[0]).lower()
            if budget is not None and _sub in ("market", "m"):
                # 预算写在子命令前：指向真正支持预算的写法，不让用户二次被拒
                print("        预算请写在子命令之后：scan market deep [规则|主题] --budget 600"
                      "（普通 scan market 不支持预算）")
            elif budget is not None and _sub in ("review", "复盘"):
                print("        scan review 复盘不支持 --budget；持仓分析可选: scan --budget 600")
            else:
                print("        全市场主题扫描请用: scan market <规则|主题>；持仓分析可选: scan --budget 600")
            return None
        if budget is not None:
            return ("scan", {"budget": budget})
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
    if cmd == "expect":
        days = 30
        if len(parts) >= 2:
            try:
                days = int(parts[1])
            except ValueError:
                # v0.8.7.5 审计修复 A33：非法参数此前静默吞掉回落 30 天，用户不知道
                print(f"  [!] '{parts[1]}' 不是有效天数，已按默认 30 天处理（用法: expect 60）")
        return ("expect", {"days": days})

    # ── 运行诊断（v0.8.16，plan/ M6）：只读零 AI 环境体检 ──
    if cmd in ("doctor", "诊断"):
        return ("doctor", {})

    # ── 分析对比（v0.8.17，plan/ C1）：同股最近两次深分析比较 ──
    if cmd in ("diff", "对比"):
        rest = [p for p in parts[1:] if p.strip()]
        use_ai = "--ai" in rest
        rest = [p for p in rest if p != "--ai"]
        if not rest or not rest[0].strip():
            return ("diff_list", {})  # 无参数 → 列出可对比股票（用户走查 2026-09-26 需求）
        return ("diff_evidence", {"code": rest[0], "ai": use_ai})

    # ── 批量任务账本（v0.8.17，plan/ C2）──
    if cmd in ("tasks", "任务"):
        return ("batch_tasks", {})

    # ── 今日工作台（v0.8.19，plan/fusion F7）：先持仓风险，再等条件 ──
    if cmd in ("today", "今日"):
        return ("today", {})

    # ── 影子对照报告（plan/fusion 影子阶段前置）：legacy vs fusion 决策表差异观察 ──
    if cmd in ("shadow", "影子"):
        return ("shadow", {})

    # ── 单股研究工作台（plan/fusion R8）：中长期比较/系统草稿/待验证节点 ──
    if cmd in ("research", "研究"):
        if "--cash-materials" in parts:
            import shlex
            try:
                tokens = shlex.split(text, posix=False)
                tokens = [token[1:-1] if len(token) >= 2 and token[0] in ("'", '"') and token[-1] == token[0] else token for token in tokens]
                return ("research", {"rest": tokens[1:]})
            except ValueError:
                # Preserve a failing intake branch; never fall through into research.
                return ("research", {"rest": ["--cash-materials"]})
        return ("research", {"rest": parts[1:]})


    # ── 计划V2（plan/fusion F5/F7）：中期/长期投资计划——影子出真判断的前提 ──
    if cmd in ("plan2", "计划2"):
        return ("plan2", {"rest": parts[1:]})

    # ── 市场恐慌指数（v0.8.10）──
    if cmd == "fear":
        rest = parts[1:]
        args = {"action": "overview", "scope": "", "refresh": False, "chart": True}
        if rest and rest[0].lower() == "history":
            args["action"] = "history"
            if "--no-chart" in rest:
                args["chart"] = False
        elif rest and rest[0].lower() == "backfill":
            args["action"] = "backfill"
            if len(rest) >= 2:
                try:
                    args["days"] = max(5, min(500, int(rest[1])))
                except ValueError:
                    print(f"  [!] '{rest[1]}' 不是有效天数（用法: fear backfill 250）")
                    return None
        else:
            # 非旗标参数当 scope（板块/概念/代码，当前版本返回未上线提示）
            args["scope"] = " ".join(t for t in rest if not t.startswith("-"))
        if "--refresh" in rest or "-r" in rest:
            args["refresh"] = True
        return ("fear", args)

    # ── 观察池（v0.8.12）──
    if cmd in ("watch", "观察池"):
        args = {"action": "list", "code": "", "name": "", "price": None}
        rest = parts[1:]
        if rest and rest[0].lower() in ("add", "加入"):
            args["action"] = "add"
            if len(rest) >= 2:
                arg = rest[1]
                if arg.startswith("#"):
                    resolved = _resolve_index_arg(arg)
                    if not resolved:
                        return None
                    args["code"], args["name"] = resolved
                else:
                    args["code"] = arg
            if len(rest) >= 3:
                try:
                    args["price"] = float(rest[2])
                except ValueError:
                    args["name"] = rest[2]
            if len(rest) >= 4:
                try:
                    args["price"] = float(rest[3])
                except ValueError:
                    pass
            return ("watch_pool", args)
        if rest and rest[0].lower() in ("rm", "remove", "del", "移出"):
            args["action"] = "rm"
            if len(rest) >= 2:
                args["code"] = rest[1]
            return ("watch_pool", args)
        return ("watch_pool", args)

    # ── 持仓管理 ──
    if cmd == "pos":
        if len(parts) < 2:
            return ("pos_list", {})
        sub = parts[1].lower()
        if sub in ("list", "ls"):
            return ("pos_list", {})
        elif sub in ("add", "a"):
            if len(parts) < 3:
                print("  [!] 用法: pos add <代码|#N> [名称] [价格] [仓位]")
                return None
            if parts[2].startswith("#"):
                # #N：code+name 取自最近扫描，后面是 price [ratio]
                ref = _resolve_index_arg(parts[2])
                if ref is None:
                    return None
                args = {"stock_code": ref[0], "name": ref[1]}
                if len(parts) >= 4:
                    try:
                        args["price"] = float(parts[3])
                    except ValueError:
                        print(f"  [!] 价格无效: '{parts[3]}'（需数字），pos add 取消")
                        return None
                if len(parts) >= 5:
                    try:
                        args["ratio"] = float(parts[4])
                    except ValueError:
                        print(f"  [!] 仓位无效: '{parts[4]}'（需数字 0-1），pos add 取消")
                        return None
            else:
                args = {"stock_code": parts[2]}
                if len(parts) >= 4:
                    args["name"] = parts[3]
                if len(parts) >= 5:
                    try:
                        args["price"] = float(parts[4])
                    except ValueError:
                        print(f"  [!] 价格无效: '{parts[4]}'（需数字），pos add 取消")
                        return None
                if len(parts) >= 6:
                    try:
                        args["ratio"] = float(parts[5])
                    except ValueError:
                        print(f"  [!] 仓位无效: '{parts[5]}'（需数字 0-1），pos add 取消")
                        return None
            return ("pos_add", args)
        elif sub in ("remove", "rm", "r", "del", "d"):
            if len(parts) < 3:
                print("  [!] 用法: pos rm <代码>")
                return None
            return ("pos_remove", {"stock_code": parts[2]})
        elif sub in ("confirm", "cf"):
            # F1（plan/fusion）：确认实际成交——分析建议不是成交事实，这里落账
            # J2：带 --qty 数量级成交走账户事件账本事务（唯一事实源）；仅仓位比例走旧投影
            if len(parts) < 3:
                print("  [!] 用法: pos confirm <代码> [实际仓位变化] [成交价] [--qty 股数 --fee 费用 --date 日期]")
                print("      pos confirm 601318                        按建议全额确认（比例投影）")
                print("      pos confirm 601318 0.1                    部分成交（仓位变化 10%）")
                print("      pos confirm 601318 0.1 48.5               部分成交并记成交价")
                print("      pos confirm 601318 --qty 500 --price 48.5 数量级成交（入事件账本——数量/现金事实源）")
                return None
            args = {"stock_code": parts[2], "ratio": None, "price": 0.0,
                    "qty": None, "fee": 0.0, "trade_date": "", "note": ""}
            positional = []
            _FLAGS_WITH_VALUE = {"--qty", "--fee", "--date", "--note", "--price"}
            i = 3
            while i < len(parts):
                tok = parts[i]
                if tok.startswith("--"):
                    if tok not in _FLAGS_WITH_VALUE:
                        print(f"  [!] 未知参数: {tok}（支持 --qty/--fee/--date/--note/--price）")
                        return None
                    if i + 1 >= len(parts):
                        print(f"  [!] {tok} 缺值")
                        return None
                    val = parts[i + 1]
                    i += 2  # 值随 flag 一起消费（不再当位置参数）
                    if tok == "--date":
                        args["trade_date"] = val
                    elif tok == "--note":
                        args["note"] = val
                    else:
                        try:
                            valf = float(val)
                        except ValueError:
                            print(f"  [!] {tok} 值无效: '{val}'（需数字）")
                            return None
                        if not math.isfinite(valf):
                            print(f"  [!] {tok} 值无效: '{val}'（需有限数字）——确认取消")
                            return None
                        if tok == "--fee" and valf < 0:
                            print(f"  [!] --fee 不能为负数（手续费填 0 或正数）: '{val}'——确认取消")
                            return None
                        if tok == "--qty" and (valf <= 0 or valf != int(valf)):
                            print(f"  [!] --qty 须为正整数（股数）: '{val}'——确认取消")
                            return None
                        args[tok[2:]] = valf
                else:
                    positional.append(tok)
                    i += 1
            if len(positional) >= 1:
                try:
                    args["ratio"] = float(positional[0])
                except ValueError:
                    print(f"  [!] 仓位变化无效: '{positional[0]}'（需数字 0-1），pos confirm 取消")
                    return None
                if not math.isfinite(args["ratio"]):
                    print(f"  [!] 仓位变化无效: '{positional[0]}'（需有限数字 0-1），pos confirm 取消")
                    return None
            if len(positional) >= 2:
                try:
                    args["price"] = float(positional[1])
                except ValueError:
                    print(f"  [!] 成交价无效: '{positional[1]}'（需数字），pos confirm 取消")
                    return None
                if not math.isfinite(args["price"]):
                    print(f"  [!] 成交价无效: '{positional[1]}'（需有限数字），pos confirm 取消")
                    return None
            return ("pos_confirm", args)
        elif sub in ("opening", "openinglot"):
            # J2 期初导入：用户确认的数量/成本/现金时点 → 账户事件账本（OPENING 事件）
            if sub == "opening":
                if len(parts) < 3:
                    print("  [!] 用法: pos opening <期初现金>（元）")
                    print("      pos opening 50000        锚定期初现金")
                    return None
                try:
                    cash = float(parts[2])
                except ValueError:
                    print(f"  [!] 期初现金无效: '{parts[2]}'（需数字）")
                    return None
                if not math.isfinite(cash):
                    print(f"  [!] 期初现金无效: '{parts[2]}'（需有限数字）——期初导入取消")
                    return None
                return ("pos_opening", {"cash": cash})
            # openinglot
            if len(parts) < 5:
                print("  [!] 用法: pos openinglot <代码> <数量> <成本价> [买入日期]")
                print("      pos openinglot 600519 500 1500.0 2026-09-01")
                return None
            lot_args = {"security_id": parts[2]}
            try:
                lot_args["quantity"] = int(parts[3])
                lot_args["cost_price"] = float(parts[4])
            except ValueError:
                print("  [!] 数量/成本价无效（数量须正整数）")
                return None
            if not math.isfinite(lot_args["cost_price"]):
                print(f"  [!] 成本价无效: '{parts[4]}'（需有限数字）——期初导入取消")
                return None
            try:
                float(lot_args["quantity"])
            except OverflowError:
                print(f"  [!] 数量过大: '{parts[3][:20]}…'（需合理正整数）——期初导入取消")
                return None
            if len(parts) >= 6:
                lot_args["acquired_at"] = parts[5]
            return ("pos_openinglot", lot_args)
        elif sub in ("verify", "vz"):
            # 用户走查缺口（ISS-111）：旧记录肉眼核对后的确认动作
            if len(parts) < 3:
                print("  [!] 用法: pos verify <代码>（核对数量/成本/日期无误后消除提醒）")
                return None
            return ("pos_verify", {"stock_code": parts[2]})
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
            # v0.8.7.5 审计修复：帮助文本曾宣传 --u（实际只认 --update/-u），补齐防用户照旧写法静默失效
            update = "--update" in parts[3:] or "-u" in parts[3:] or "--u" in parts[3:]
            return ("pos_plan", {"stock_code": target, "update": update})
        elif sub in ("overweight", "ow"):
            # 报告3.1 笨总教学十超配策略：pos overweight <代码> [依据...]
            if len(parts) < 3:
                print("  [!] 用法: pos overweight <代码> [超配依据]")
                print("      pos overweight 600519 AI算力需求质变")
                print("      铁律: 涨幅3-10倍禁入 / 只出手一次 / 1-2月到期 / 非添油战术")
                return None
            code = parts[2]
            basis = " ".join(parts[3:]) if len(parts) > 3 else ""
            return ("pos_overweight", {"stock_code": code, "name": basis})
        else:
            print(f"  [!] 未知: pos {sub}  用法: pos / pos add / pos rm / pos plan / pos overweight")
            return None

    # ── 产业链图谱管理（ISS-061 v5 动态维护）──
    if cmd == "chains":
        from src.data.industry_data import list_graphs, delete_auto_graph
        if len(parts) >= 3 and parts[1].lower() in ("rm", "remove", "del"):
            print("  " + delete_auto_graph(parts[2]))
            return None
        graphs = list_graphs()
        if not graphs:
            print("  [!] 无图谱（手写 configs/industry_chains.yaml + 自举 industry_chains_auto.yaml）")
            return None
        print(f"\n  产业链图谱（{len(graphs)}条，手写=人工维护 / 自举=AI沉淀可rm）：\n")
        for g in sorted(graphs, key=lambda x: (x["source"], x["name"])):
            src = "手写" if g["source"] == "manual" else f"自举@{g['created_at'] or '?'}"
            alias = ",".join(g["aliases"][:4])
            print(f"    {g['name']:<8s} [{src}] {g['company_count']}家公司"
                  + (f"  别名:{alias}" if alias else ""))
        print("\n  用法: chains rm <链名>（删自举图谱，重新分析会再沉淀）\n")
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
        # T05-J：启动预算只属于 ba/bzall 与 bz 多代码共用批；在这里先分离并校验，
        # 坏预算/不支持入口（scan/check/manual/单只）在代码提取与扫描读取之前拒绝。
        budget, bz_tokens, budget_err = _split_budget_option(parts[1:])
        if budget_err:
            print(f"  [!] {budget_err}")
            print(f"        {_BUDGET_HELP}")
            return None
        scan_intent = bool(bz_tokens) and bz_tokens[0].lower() in ("scan", "筛选", "选股")
        if not scan_intent:
            _lows = [str(p).lower() for p in bz_tokens]
            if budget is not None and any(p in ("--check", "--体检", "check") for p in _lows):
                print(f"  [!] bz --check 不支持 --budget（{_BUDGET_SCOPE}）")
                return None
            if budget is not None and any(p in ("--manual", "-m", "manual") for p in _lows):
                print(f"  [!] bz --manual 不支持 --budget（{_BUDGET_SCOPE}）")
                return None
        # T05-N：bz scan 起支持预算；移除预算 token 后继续旧解析（无预算时 bz_tokens
        # 与 parts[1:] 逐项等价，旧返回形状不变）。预算值不会混入主题词。
        parts = [parts[0]] + bz_tokens
        if len(parts) >= 2 and parts[1].lower() in ("scan", "筛选", "选股"):
            scan_args = {"theme": "", "top_n": 10, "backtest": False,
                         "start": "", "end": "", "capital": 100000.0,
                         "rule": "healthy_pullback", "limit": 15, "refresh": False}
            if budget is not None:
                scan_args["budget"] = budget
            rest = parts[2:]
            i = 0
            theme_parts = []
            while i < len(rest):
                p = rest[i]
                low = p.lower()
                if low == "--top":
                    if i + 1 >= len(rest):
                        print("  [!] --top 缺少数值（需正整数，默认10）")
                        return None
                    _val, _err = _scan_positive_int(rest[i + 1], from_text=True)
                    if _err:
                        print(f"  [!] --top 值无效: {_err}（需正整数，默认10）")
                        return None
                    scan_args["top_n"] = _val
                    i += 2; continue
                if low.startswith("--top="):
                    print(f"  [!] 不支持等号写法: {p}（请用 --top 10）")
                    return None
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
                    scan_args["rule"] = rest[i + 1]
                    scan_args["rule_explicit"] = True  # 显式指定（主题模式下也生效）
                    i += 2; continue
                if low in ("--allrules", "--all-rules"):
                    scan_args["allrules"] = True; i += 1; continue
                if low == "--limit":
                    if i + 1 >= len(rest):
                        print("  [!] --limit 缺少数值（需正整数，默认15）")
                        return None
                    _val, _err = _scan_positive_int(rest[i + 1], from_text=True)
                    if _err:
                        print(f"  [!] --limit 值无效: {_err}（需正整数，默认15）")
                        return None
                    scan_args["limit"] = _val
                    i += 2; continue
                if low.startswith("--limit="):
                    print(f"  [!] 不支持等号写法: {p}（请用 --limit 15）")
                    return None
                theme_parts.append(p); i += 1
            theme_str = " ".join(theme_parts).strip()
            # v0.8.7.4: 对齐 scan market 语义 -- 未用 --rule 时，第一个裸参数先当规则候选
            # （run_benzong_scan 里 resolve_rule_name 模糊匹配，未命中则该词归位主题词）
            if theme_parts and not scan_args.get("rule_explicit"):
                scan_args["rule"] = theme_parts[0]
                scan_args["rule_from_positional"] = True
                theme_str = " ".join(theme_parts[1:]).strip()
            scan_args["theme"] = theme_str
            return ("benzong_scan", scan_args)

        meta = ""
        manual = False
        refresh = False
        check = False
        non_flag_tokens = []
        for p in parts[1:]:
            # v0.8.8.6：flag 匹配大小写不敏感——此前 `bz <code> -R` 不匹配也无提示，
            # 静默按缓存跑且不触发 chat confirm 门
            pl = p.lower()
            if pl in ("--manual", "-m", "manual"):
                manual = True
            elif pl in ("--refresh", "-r", "refresh"):
                refresh = True
            elif pl in ("--check", "--体检", "check"):
                check = True
            elif pl.startswith("-"):
                # T05-J：预算可用入口不再静默吞掉未知选项（如 --foo/-f）
                print(f"  [!] 未知选项: {p}（bz 支持 --refresh/-r/--manual/-m/--check；"
                      "多代码批量另支持 --budget）")
                return None
            else:
                non_flag_tokens.append(p)
        # ISS-087 多代码：提取代码（英文逗号/中文逗号/空格混分/#N 引用），非法 token 告警
        codes, invalid = _extract_codes(non_flag_tokens)
        if manual and len(codes) > 1:
            print("  [!] --manual 交互式只支持单只，仅处理第一只（多代码批量请去掉 --manual 走自动评分）")
            codes = codes[:1]
        if invalid and codes:
            print(f"  [!] 无法识别的参数已跳过: {'、'.join(invalid)}")
        if len(codes) >= 2:
            # v0.8.9.0 ISS-087: 多代码批量评分——复用 ba 同款批量管道
            # （AI client 建一次/整批共享成交额/串行防登录态竞争/单只失败不阻塞/缓存优先）
            multi_args = {"codes": codes, "refresh": refresh}
            if budget is not None:
                multi_args["budget"] = budget
            return ("benzong_multi", multi_args)
        if budget is not None:
            print(f"  [!] 单只 bz 不支持 --budget（{_BUDGET_SCOPE}；"
                  "多代码请写成 bz 600519,000001 --budget 60）")
            return None
        if len(codes) == 1:
            meta = codes[0]
        elif invalid:
            # 无有效代码：单 token 保持旧行为（主题词等原样传给评分器按现有报错处理）；
            # 多 token 则明确告警后仍取第一个（消除静默丢弃）
            if len(invalid) > 1:
                print(f"  [!] 以下参数不是有效代码，已跳过: {'、'.join(invalid[1:])}")
            meta = invalid[0]
        return ("benzong", {"meta": meta, "manual": manual, "refresh": refresh, "check": check})

    print(f"  [!] 无法识别: {text}  输入 h 查看用法")
    _suggest_command(cmd, parts)
    return None


# ── 未知命令「猜你想输」（2026-09-05 丝滑批：只提示不代跑） ─────
_COMMAND_HINTS = {
    "l": "l <代码>[,代码2...] 多代码深析 | la 一键析全部持仓 | l all 批量析最近扫描",
    "live": "l <代码>[,代码2...] 深析（l 即 live；中英文逗号/空格分隔多只）",
    "la": "la 一键分析全部持仓（含本地只读研究状态）",
    "ba": "ba 批量笨总评分最近扫描结果",
    "bz": "bz <代码>[,代码2...] 评分(支持多代码) | bz scan <主题> 主题选股",
    "b": "b <代码> 回测 | bb 批量回测",
    "bb": "bb 批量回测",
    "scan": "scan 一键分析全部持仓（--budget 可选） | scan market [规则|主题] 全市场扫描",
    "s": "scan market [主题] 全市场扫描（s 即 scan）",
    "pos": "pos 持仓总览 | pos add/rm <代码> 建仓/清仓 | pos plan 交易计划",
    "expect": "expect 未来事件日历+预期透支度",
    "fear": "fear 恐慌指数总览 | fear history 多周期回顾+走势图 | fear backfill 回填历史",
    "doctor": "doctor 运行诊断：解释器/依赖/配置/状态缓存只读体检（零 AI 零网络）",
    "diff": "diff 列出可对比股票 | diff <代码> 同股最近两次深分析对比",
    "tasks": "tasks 批量任务账本：每次 run 的已完成/降级/失败/未记录结果；旧记录只读，记录≠缓存资格",
    "today": "today 今日工作台：待确认建议/继续持有/等待条件——建议≠成交",
    "shadow": "shadow 影子对照报告：legacy 终态 vs fusion_mid/long 决策表差异（分原因）",
    "plan2": "plan2 列出计划 | plan2 <代码> mid|long <意图> [--facts a,b] [--until 日期] | plan2 accept/rm <代码>",
    "research": "research <代码> --cash-materials 位置清单.json [--json]（归档材料） | research <代码> --cash-verify 回执ID [--source-check [--industry-check]] [--json]（默认离线核对；source-check核官方字段，另加industry-check合计PDF一次/SSE一次形成当前条件化观察；历史资格仍待核；两种材料操作单独使用，不生成研究草稿）；普通研究：[--capture] [--claims 主张.json --checkpoint 描述 --confirm-risk [--ref 证据ID] [--review-at 带时区时间 --refute-if 推翻事实 --on-expiry REVIEW_REQUIRED]]",
    "events": "events 事后事件复盘",
    "chat": "chat 进入 AI 对话模式",
    "help": "h 或 ? 查看全部命令用法",
    "quit": "q 退出",
}
_COMMAND_TOKENS = list(_COMMAND_HINTS.keys())


def _suggest_command(cmd: str, parts: list) -> None:
    """未知命令时按编辑距离给「猜你想输」建议；像代码就顺带提示格式。"""
    import difflib
    # 参数像 6 位代码但位数不对（4-5 位数字，如手滑少打一位）才提示代码格式；
    # 2-3 位数字更可能是一般参数（如 expect 30），不往代码上引导
    if len(parts) > 1 and parts[1].isdigit() and 4 <= len(parts[1]) <= 5:
        print(f"  💡 股票代码须为 6 位数字（当前「{parts[1]}」{len(parts[1])} 位），例: l 600519")
    matches = difflib.get_close_matches(cmd, _COMMAND_TOKENS, n=2, cutoff=0.6)
    if matches:
        hints = "；".join(_COMMAND_HINTS[m] for m in matches)
        print(f"  💡 你是想输入「{matches[0]}」吗？  {hints}")


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
    # ISS-091：评分缓存 90 天自动清理（跨日键本就失效，这里防文件无限累积）
    try:
        from datetime import datetime as _dt, timedelta as _td
        from src.core.benzong import cache as _bz_cache
        _removed = _bz_cache.clear(before_date=(_dt.now() - _td(days=90)).strftime("%Y-%m-%d"))
        if _removed:
            print(f"  🧹 已清理 {_removed} 个 90 天前的笨总评分缓存文件")
    except Exception:
        pass

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
        print(f"\n  ✗ 评分失败: {type(e).__name__}: {e}")
        print(f"  可尝试 bz --manual 走交互式兜底")
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
            print(f"      来源：{' / '.join(sources)}")
        reasoning = m.get("reasoning", "")
        if reasoning:
            print(f"      AI: {reasoning}")
        for w in m.get("warnings", []):
            print(f"      ⚠ {w}")
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
        print(f"  ⚠ 大前提失效（行业景气度=0）：此评分/等级不具参考意义，不可作为买入依据！")
        print(f"  笨总教学：没有高景气行业判断，其他维度再高也无意义（中免反面案例）")
    else:
        print(f"  💯 评分：{norm:.0f}/100 → {grade_label} {downgrade_note}")
        if downgrade_note:
            print(f"  行业景气度 {bs.industry_prosperity:.0f} 偏低（笨总大前提存疑），实际等级已下调")
    print(f"  🎯 总体置信度：{result.overall_confidence:.2f}", end="")
    if result.overall_confidence < 0.5:
        print("  ⚠ 偏低（有维度降级，总分仅供参考）")
    else:
        print()
    # 报告1.1 笨总实操流动性状态（教学二/五，非10万亿极端顶）
    try:
        from src.core.exit_signals.macro import assess_liquidity_state, assess_market_breadth
        _liq = assess_liquidity_state(result.market_turnover)
        if _liq:
            print(f"  流动性[{_liq[0]}]：{_liq[1]}")
        _br = assess_market_breadth()
        if _br:
            print(f"  市场宽度[{_br[0]}]：{_br[1]}")
    except Exception:
        pass
    if result.cache_hits:
        print(f"  💾 缓存命中：{len(result.cache_hits)}/6 维度（同股同日复用，--refresh 重算）")
    print("─" * 64)

    # 警告（大前提失效时不重复打，已在上方红字提示）
    other_warnings = [w for w in result.warnings if "大前提失效" not in w] if precondition_failed else result.warnings
    if other_warnings:
        print()
        for w in other_warnings:
            print(f"  {w}")
        print()

    # 大前提警告（非失效时不显示，失效时上方已红字提示，此处保留兜底）
    if not precondition_failed:
        pre = bs.precondition_warning()
        if pre:
            print(f"  {pre}")
            print()

    print(f"  📚 评分依据：笨总教学 + xlsx 打分表（v0.8.6.1 试金石 4 案例已验证）")
    print(f"  💡 提示：bz {code} --refresh 强制刷新；bz --manual 走交互式兜底；bz --check 体检数据源")
    report_path = _persist_benzong_report(code, result, bs)
    if report_path:
        print(f"  📝 完整报告已保存：{report_path}")
    print()


def _persist_benzong_report(code: str, result, bs) -> str:
    """v0.8.6.7：把笨总 6 维评分完整报告持久化为 Markdown，方便回头阅读。

    文件名：{YYYY-MM-DD_HH-MM}_{code}_{name}.md（时间+股票代码+股票名字）
    路径：项目根/分析报告/bz/（已 gitignore，不污染仓库）
    返回写入路径；失败返回空串（不抛异常，不影响主流程）。
    """
    from datetime import datetime
    from pathlib import Path
    import re

    name = (getattr(bs, "stock_name", None) or "").strip() or code
    safe_name = re.sub(r'[<>:"/\\|?*\s]+', "_", name).strip("_.") or code
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d_%H-%M")
    full_ts = now.strftime("%Y-%m-%d %H:%M:%S")

    proj_root = Path(__file__).resolve().parent
    report_dir = proj_root / "分析报告" / "bz"
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ""
    report_path = report_dir / f"{ts}_{code}_{safe_name}.md"

    meta = result.dimensions_meta
    dim_labels = [
        ("industry_prosperity", "1️⃣ 行业景气度",   0.20,  "0.20"),
        ("business_purity",     "2️⃣ 业务纯度",     0.40,  "0.40"),
        ("valuation_position",  "3️⃣ 历史估值位置", 0.25,  "0.25"),
        ("industry_leader",     "4️⃣ 细分行业龙头", 0.15,  "0.15"),
        ("market_recognition",  "5️⃣ 市场辨识度",   0.20,  "0.20"),
        ("risk_deduction",      "6️⃣ 个股风险值",   -0.20, "-0.20"),
    ]

    grade = bs.effective_grade()
    raw_grade = bs.grade()
    grade_label = {
        "A": "🏆 A 级 — 超优质",
        "B": "✅ B 级 — 优秀（可买入）",
        "C": "🟡 C 级 — 可观察",
        "D": "🔻 D 级 — 勉强观望",
        "F": "❌ F 级 — 放弃",
    }[grade]
    downgrade_note = f"（原始 {raw_grade} 级，被行业景气度闸门降级）" if grade != raw_grade else ""
    precondition_failed = (bs.industry_prosperity == 0)

    L = []
    L.append(f"# 笨总 6 维评分报告 — {code} {name}")
    L.append("")
    L.append(f"> 评分时间：{full_ts}")
    L.append(f"> 股票代码：{code}  |  股票名称：{name}")
    L.append(f"> 数据缓存：{len(result.cache_hits)}/6 维度命中（同股同日复用，--refresh 重算）")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 一、总评")
    L.append("")
    norm = bs.normalized_score()
    L.append(f"- 归一化评分：**{norm:.0f}/100**")
    L.append(f"- 实际等级：**{grade}** — {grade_label} {downgrade_note}".rstrip())
    L.append(f"- 原始等级：{raw_grade}")
    conf_hint = " ⚠偏低（有维度降级，总分仅供参考）" if result.overall_confidence < 0.5 else ""
    L.append(f"- 总体置信度：{result.overall_confidence:.2f}{conf_hint}")
    L.append(f"- 大前提失效（行业景气度=0）：{'是 ⚠ 评分不具参考意义，不可作买入依据' if precondition_failed else '否'}")
    L.append(f"- 一票否决：{'是 🚨 强烈建议放弃' if result.invalidate else '否'}")
    L.append("")

    pre = bs.precondition_warning()
    if pre:
        L.append(f"> ⚠ {pre}")
        L.append("")

    L.append("## 二、6 维明细")
    L.append("")
    for key, label, _w, w_str in dim_labels:
        m = meta.get(key, {})
        score = m.get("score", 0)
        conf = m.get("confidence", 0)
        cached = "是" if key in result.cache_hits else "否"
        degraded = " ⚠该维降级，分仅供参考（不影响其他维）" if conf < 0.5 else ""
        L.append(f"### {label}（权重 {w_str}）")
        L.append("")
        L.append(f"- 得分：{score:.1f} / 100")
        L.append(f"- 置信度：{conf:.2f}{degraded}")
        L.append(f"- 命中缓存：{cached}")
        sources = m.get("sources", [])
        if sources:
            L.append("- 数据来源：")
            for s in sources:
                L.append(f"  - {s}")
        else:
            L.append("- 数据来源：（无）")
        reasoning = m.get("reasoning", "")
        L.append("- AI 推理：")
        if reasoning:
            L.append("")
            for rl in reasoning.splitlines():
                L.append(f"  {rl}")
        else:
            L.append("  （无）")
        warns = m.get("warnings", [])
        if warns:
            L.append("- 维度警告：")
            for w in warns:
                L.append(f"  - ⚠ {w}")
        L.append("")

    L.append(f"## 三、贡献分解（流动性系数 ×{bs.liquidity_coeff:.1f}）")
    L.append("")
    c = bs.contributions
    L.append(f"- 行业景气度  ×0.20 = {c['industry_prosperity']:>+6.2f}")
    L.append(f"- 业务纯度    ×0.40 = {c['business_purity']:>+6.2f}")
    L.append(f"- 历史估值    ×0.25 = {c['valuation_position']:>+6.2f}")
    L.append(f"- 细分龙头    ×0.15 = {c['industry_leader']:>+6.2f}")
    L.append(f"- 市场辨识度  ×0.20 = {c['market_recognition']:>+6.2f}")
    L.append(f"- 个股风险值  ×-0.20= {c['risk_deduction']:>+6.2f}（扣分）")
    L.append(f"- 原始累加         = {bs.raw_sum:>+6.2f}")
    L.append(f"- × 流动性系数 {bs.liquidity_coeff:.1f} = **{bs.total_score:.2f}**")
    L.append("")

    L.append("## 四、警告汇总")
    L.append("")
    if result.warnings:
        for w in result.warnings:
            L.append(f"- {w}")
    else:
        L.append("无")
    L.append("")

    L.append("---")
    L.append("")
    L.append("评分依据：笨总教学 + xlsx 打分表（v0.8.6.1 试金石 4 案例已验证）")
    L.append("生成工具：暮云思辨投资助手")

    try:
        report_path.write_text("\n".join(L), encoding="utf-8")
        return str(report_path)
    except OSError:
        return ""


def run_benzong_scan(args: dict):
    """v0.8.6.3 笨总选股初筛（ISS-041 方向 A）。

    scan 技术面初筛 → 笨总 6 维评分（5 AI + 1 规则） → TopN 排名 → 可选批量回测。

    用法：
            bz scan                  默认 healthy_pullback 规则初筛 → 笨总评分 Top10
            bz scan AI,半导体        指定主题词缩小候选范围
            bz scan --top 5          Top5
            bz scan --limit 15       限制送入评分的去重候选数（默认15）
            bz scan --budget 600     可选启动预算（秒；同一monotonic截止贯穿准备/评分/回测）
      bz scan --backtest --start 2024-01-01 --end 2024-12-31  对 TopN 批量回测

    T05-N：坏预算/坏 limit/top 先于任何业务拒绝（解析层与程序化入口都校验）；预算从
    执行入口起算，到期只阻止开始下一阶段（配置/账户/规则/定位/扫描/评分/回测），
    已开始的调用会跑完；候选不完整或评分部分不覆盖上次有效扫描、不开启 TopN 回测。
    """
    # ── T05-N：程序化入口校验（坏值零业务；先于配置/账户/定位/扫描/评分/回测）──
    if not isinstance(args, dict):
        print("  [!] bz scan 参数无效（需字典）；本次未开始扫描")
        return
    _budget_raw = args.get("budget")
    _budget_seconds = None
    _entry_at = None
    if _budget_raw is not None:
        # T05-N fix2：入口起算必须早于验证 import（import 耗时也计入同一预算），
        # 验证成功后用该入口时点算截止，不再从验证之后重起钟。
        _entry_at = _monotonic()
        from src.core.benzong.batch_scorer import budget_descriptor
        _budget_desc = budget_descriptor(_budget_raw)
        if _budget_desc is None:
            print(_SCAN_BUDGET_INVALID)
            return
        _budget_seconds = _budget_desc["budget_seconds"]
    _limit, _err = _scan_positive_int(args.get("limit", 15), from_text=False)
    if _err:
        print(f"  [!] 候选上限 limit 无效: {_err}（需正整数，默认15）；本次未开始扫描"
              "（不加载配置/账户、不定位/扫描、不评分/回测、不落盘）")
        return
    _top_n, _err = _scan_positive_int(args.get("top_n", 10), from_text=False)
    if _err:
        print(f"  [!] TopN 无效: {_err}（需正整数，默认10）；本次未开始扫描"
              "（不加载配置/账户、不定位/扫描、不评分/回测、不落盘）")
        return

    # T05-N：同一 monotonic 绝对截止从执行入口起算（配置/账户/主题/扫描准备都计入）。
    # 无预算路径不取 clock、不加签名/逻辑分支。
    deadline = (None if _budget_seconds is None
                else _entry_at + _budget_seconds)

    if deadline is None:
        print(_SCAN_BUDGET_HINT)
    else:
        print(_scan_budget_notice(_budget_seconds))
    print()

    def _expired() -> bool:
        return _live_deadline_expired(deadline)

    if _expired():
        _scan_prep_stop("入口校验后（尚未解析规则）")
        return

    from src.scanner.scanner_engine import ScannerEngine
    from src.core.benzong.batch_scorer import auto_score_batch

    # T05-N fix2：模块 import 也可能耗尽预算——首次构造扫描引擎前再核同一截止
    # （该构造会准备 MarketCache/规则 YAML；此处只做截止门，不宣称已证费用）。
    if _expired():
        _scan_prep_stop("模块导入后（尚未构造扫描引擎）")
        return

    theme = args.get("theme", "")
    top_n = _top_n
    do_backtest = args.get("backtest", False)
    limit = _limit
    rule_name = args.get("rule", "healthy_pullback")
    rule_explicit = bool(args.get("rule_explicit"))
    rule_from_positional = bool(args.get("rule_from_positional"))
    force_refresh = args.get("refresh", False)
    allrules = args.get("allrules", False)  # 方案C: 五规则全跑合并

    # v0.8.7.4: 规则名模糊解析。位置参数候选未命中规则 -> 该词归位主题词；
    # 显式 --rule 拼错 -> 提示后按宽口径继续（不中断）。rule_effective 决定主题模式下
    # 通道B 是否用该规则做技术面过滤（默认 theme_members 宽口径，ISS-047 防漏股）。
    rule_effective = False
    try:
        _r_eng = ScannerEngine()  # 默认 rules_path（scanner_cfg 此时未定义）
        # T05-N fix1：构造本身可能耗尽预算——真正调用 resolve_rule_name 前再核同一截止
        if _expired():
            _scan_prep_stop("规则引擎构造后（尚未解析规则）")
            return
        _resolved = _r_eng.resolve_rule_name(rule_name)
    except Exception:
        _resolved = None
    if _resolved:
        rule_name = _resolved
        rule_effective = rule_explicit or rule_from_positional
    elif rule_from_positional:
        theme = f"{rule_name} {theme}".strip()
        rule_name = "healthy_pullback"
    elif rule_explicit:
        print(f"  [!] 未识别规则 '{rule_name}'，按宽口径继续（可用规则跑 rules 查看）")
        rule_name = "healthy_pullback"

    if _expired():
        _scan_prep_stop("规则解析后（尚未加载配置/账户）")
        return

    # v0.8.7.4补：allrules 权威化 -- 五规则全跑按全市场跑，主题词/规则参数忽略并明示
    # （否则 bz scan --allrules 科技 会显示'五规则全跑'实际走主题双通道，显示与行为打架）
    # v0.8.7.5 审计修复 A29：条件补 rule_from_positional——
    # `bz scan --allrules shrink_pullback` 此前裸参数规则被静默丢弃无任何提示
    if allrules:
        if theme or rule_explicit or rule_from_positional:
            print("  [!] 五规则全跑模式按全市场跑，主题词/规则参数已忽略")
        theme = ""
        rule_effective = False

    print()
    print("=" * 64)
    print("  🔍 笨总选股初筛 — scan → 6维评分(5AI+1规则) → TopN")
    print("=" * 64)
    if allrules:
        rule_display = "五规则全跑(各Top5合并)"
    elif theme and not rule_effective:
        rule_display = "theme_members(宽口径)"
    else:
        rule_display = rule_name
    print(f"  规则: {rule_display} | 主题词: {theme or '(全市场)'} | 候选上限: {limit}")
    print(f"  TopN: {top_n} | 回测: {'是' if do_backtest else '否'}")
    if allrules:
        print(f"  候选上限说明：用户请求 {limit} | allrules 结构上限 25"
              "（5 规则 × 每规则最多 5 个新代码）")
    print()

    # ── Step 1: 候选股获取 ──
    # 有主题词 → 法C 精准定位（AI直接报股+Baostock验证，绕过板块匹配天花板）
    # 无主题词 → quick_scan 技术面初筛
    from src.cli.main import load_config
    if _expired():
        _scan_prep_stop("加载配置前")
        return
    config = load_config()
    scanner_cfg = config.get("scanner", {})
    skills_dir = config.get("skills", {}).get("dir", "./src/skills")
    enabled_skills = config.get("skills", {}).get("enabled", [])
    signal_weights = config.get("decision", {}).get("signal_weights", {})
    skill_types = config.get("skills", {}).get("types", {})
    entry_exit_config = config.get("entry_exit", None)

    if _expired():
        _scan_prep_stop("配置加载后（尚未准备账户/定位/扫描）")
        return

    # 排除已持仓
    exclude_codes = set()
    if scanner_cfg.get("auto_exclude_holdings", True):
        try:
            from src.data.portfolio import PortfolioManager
            pm = PortfolioManager()
            # O0/Y2 同类接线：排除集经同一请求快照形成（账本确定有仓 ∪ 投影代码）
            # ——账本独有持仓同样不进扫描推荐
            _entries, _ = pm.request_account_facts().holding_entries()
            exclude_codes = {e.stock_code for e in _entries}
        except Exception as e:
            # ISS-078：排除持仓失败必须留痕（此前静默 fail-open，已持仓股混入候选无感知）
            logger.warning(f"持仓读取失败，本次扫描无法排除已持仓股: {e}")

    if _expired():
        _scan_prep_stop("账户准备后（尚未定位/扫描）")
        return

    located_stocks = None  # 法C结果（有主题词时填充）
    if theme:
        # ── 双通道选股（去龙头偏向）──
        # 通道A 法C：AI 报股，覆盖细分材料（THS 无对应板块的，如 PBO树脂）
        # 通道B 全市场客观筛选：quick_scan(market_query=主题词) 从 THS 行业/概念成分股
        #        捞全市场成员（含中小盘非龙头），客观数据不靠 AI 记忆
        # 两通道合并去重 → 笨总评分排序
        from src.core.benzong.theme_locator import locate_theme_stocks
        terms = [t.strip() for t in theme.split(",") if t.strip()]
        if _expired():
            _scan_prep_stop("主题定位开始前")
            return
        print(f"  [Step 1/3] 双通道选股：法C精准定位 + 全市场行业筛选...")
        # T05-N fix1：标题输出也可能耗尽预算——真正调用 locate_theme_stocks 前再核截止
        if _expired():
            _scan_prep_stop("双通道选股标题输出后（法C定位未开始）")
            return

        # 通道A：法C（v0.8.7.9 起同主题词当日走文件缓存，--refresh 强刷）
        located = locate_theme_stocks(terms, config=config, use_cache=not force_refresh)
        if located.get("cached"):
            print("  ♻ 法C结果命中今日缓存（同主题词，加 --refresh 强刷）")
        a_stocks = [s for s in located.get("stocks", [])
                    if s["code"] not in exclude_codes]
        if located.get("invalid"):
            print(f"  丢弃 {len(located['invalid'])} 只（AI可能给错代码）："
                  + ", ".join(f"{i['code']}({i['name']})" for i in located["invalid"][:5]))

        # T05-N：通道A 完成即核同一截止；通道B 未跑时候选不完整，本批不评分、不落盘
        if _expired():
            _scan_candidates_incomplete("主题通道A（法C）已完成，但通道B全市场筛选未开始")
            return

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
            if _expired():
                _scan_candidates_incomplete("主题通道B引擎已构造，但全市场筛选未开始")
                return
            b_rule = rule_name if rule_effective else "theme_members"
            # 宽口径规则主要靠主题词过滤而非技术形态（ISS-047 防漏股默认）；
            # 用户显式指定规则时改用该规则做技术面过滤（规则+主题组合，v0.8.7.4）
            b_candidates, b_info = b_engine.quick_scan(
                rule_name=b_rule,
                market_query=theme,
                exclude_codes=exclude_codes,
            )
            _cs = (b_info or {}).get("cache_status", {}).get("stocks", {})
            if _cs.get("cached") and not _cs.get("expired", True) and _cs.get("age_seconds", 0) > 5:
                print(f"  ♻ 全市场快照缓存命中（{_cs['age_seconds']}秒前拉取，未重爬行情源）")
            if not b_info.get("error"):
                a_codes = {s["code"] for s in a_stocks}
                for c in b_candidates:
                    if c.stock_code not in a_codes and c.stock_code not in exclude_codes:
                        b_stocks.append({"code": c.stock_code,
                                         "name": getattr(c, "stock_name", c.stock_code),
                                         "term": theme, "why": "全市场行业筛选"})
        except Exception as e:
            print(f"  通道B 全市场筛选跳过: {type(e).__name__}: {str(e)[:60]}")

        # 合并：法C 优先（细分纯正），全市场补充非龙头
        merged = a_stocks + b_stocks
        located_stocks = merged
        if not located_stocks:
            print(f"  ⚠ 主题「{theme}」两通道均未定位到A股公司")
            print(f"  可能：AI不熟悉该细分领域 + 主题词未匹配到行业板块。可尝试换更通用的主题词")
            return
        # T05-N：limit 是送入评分的去重候选上限（去重后按原顺序取前 limit 只）
        codes = _scan_limit_codes([s["code"] for s in located_stocks], limit)
        if _expired():
            _scan_candidates_complete_no_scoring(len(codes))
            return
        _by_code = {}
        for s in located_stocks:
            _by_code.setdefault(s["code"], s)
        print(f"  ✓ 法C {len(a_stocks)} 只 + 全市场 {len(b_stocks)} 只 → "
              f"合并 {len(merged)} 只，取前 {len(codes)} 只评分")
        for _c in codes:
            s = _by_code[_c]
            print(f"    {s['code']} {s['name'][:10]} [{s['term']}] {s['why'][:40]}")
        print()
    else:
        # ── 技术面初筛 ──
        if _expired():
            _scan_prep_stop("技术面初筛开始前")
            return
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
        if _expired():
            _scan_prep_stop("技术面初筛引擎构造后（尚未调用扫描）")
            return

        if allrules:
            # 方案C: 五条市场规则全跑，各取Top5合并去重
            # 注：theme_members 是主题专用规则（无主题词时=全市场流动性Top80），不参加 --allrules 全跑
            print("  [Step 1/3] 五规则全跑初筛（各Top5合并）...")
            ALL_RULES = ["healthy_pullback", "steady_advance", "shrink_pullback", "value_pick", "oversold_watch"]
            rule_buckets = []   # [(规则, [该规则贡献的新代码...])]：结构上限 5×5=25
            rule_hits = {}      # 每只股被哪些规则选中（多规则共振=强标的）
            seen = set(exclude_codes)
            for _ri, r in enumerate(ALL_RULES):
                if _expired():
                    _scan_candidates_incomplete(
                        f"五规则初筛第 {_ri + 1}/5 条规则（{r}）开始前")
                    return
                try:
                    cands, info = engine.quick_scan(rule_name=r, market_query=None, exclude_codes=exclude_codes)
                except Exception as e:
                    print(f"  {r} 规则失败: {type(e).__name__}: {str(e)[:50]}")
                    if _expired() and _ri < len(ALL_RULES) - 1:
                        _scan_candidates_incomplete(
                            f"第 {_ri + 1}/5 条规则（{r}）失败后（后续规则未扫描）")
                        return
                    continue
                if _expired() and _ri < len(ALL_RULES) - 1:
                    _scan_candidates_incomplete(
                        f"第 {_ri + 1}/5 条规则（{r}）扫描结束后（后续规则未扫描）")
                    return
                if not cands:
                    print(f"  {r}: 无候选")
                    continue
                bucket = []
                added = 0
                for c in cands:
                    if c.stock_code not in seen and added < 5:
                        seen.add(c.stock_code)
                        bucket.append(c.stock_code)
                        rule_hits[c.stock_code] = [r]
                        added += 1
                    elif c.stock_code in rule_hits:
                        rule_hits[c.stock_code].append(r)  # 已被其他规则选，记共振
                rule_buckets.append((r, bucket))
                print(f"  ✓ {r}: 取 {added} 只")
            # T05-N：仍五规则、每规则最多贡献 5 个新代码（结构最多 25）；在原规则贡献桶上
            # 轮询选取最多 min(limit,25)，避免先跑的规则占满截断；不额外扩大扫描/补桶。
            pool = [code for _r, bucket in rule_buckets for code in bucket]
            if not pool:
                print(f"  ✗ 五规则均无候选(全市场行情源可能失效)")
                print(f"  → 改用主题词模式: bz scan <主题词>(如 bz scan AI,半导体)")
                print(f"  → 或单股分析: l <代码> / bz <代码>(走Baostock)")
                return
            cap = min(limit, 25)
            codes = _scan_round_robin([bucket for _r, bucket in rule_buckets], cap)
            if _expired():
                _scan_candidates_complete_no_scoring(len(codes))
                return
            _selected_hits = {c: rule_hits.get(c, []) for c in codes}
            _resonate_n = sum(1 for hs in _selected_hits.values() if len(hs) > 1)
            print(f"  用户请求候选上限 limit={limit} | 五规则结构上限 25"
                  "（5 规则 × 每规则最多 5 个新代码）"
                  f" | 实际选取 {len(codes)} 只送评分")
            if limit > 25:
                print(f"  请求 {limit} 超过结构上限：按 25 截断（不是静默忽略；"
                      "不额外扩大扫描/补桶）")
            print(f"  ✓ 五规则合并去重 {len(pool)} 只（结构上限 25）→ 轮询选取 "
                  f"{len(codes)} 只送评分（含 {_resonate_n} 只多规则共振）")
            print("  已选证券完整规则命中（来自候选池已有信息）:")
            for _c in codes:
                _hits = _selected_hits[_c]
                print(f"    {_c}  {'、'.join(_hits)}"
                      + ("（共振）" if len(_hits) > 1 else ""))
            print()
        else:
            print("  [Step 1/3] 技术面初筛中...")
            # T05-N fix1：标题输出也可能耗尽预算——真正调用 quick_scan 前再核截止
            if _expired():
                _scan_prep_stop("技术面初筛标题输出后（初筛未开始）")
                return
            try:
                candidates, scan_info = engine.quick_scan(
                    rule_name=rule_name,
                    market_query=None,
                    exclude_codes=exclude_codes,
                )
            except Exception as e:
                print(f"  ✗ 初筛失败: {type(e).__name__}: {e}")
                return

            if "error" in scan_info:
                err = scan_info['error']
                if "全市场" in err or "行情数据" in err:
                    print(f"  ✗ 全市场行情源失效(新浪/东财当前不可用)")
                    print(f"  → 改用主题词模式: bz scan <主题词>(如 bz scan AI,半导体)")
                    print(f"  → 或单股分析: l <代码> / bz <代码>(走Baostock,不依赖全市场快照)")
                    print(f"  全市场源失效是外部数据源问题,非代码bug,恢复后自动可用")
                else:
                    print(f"  ✗ 扫描失败: {err}")
                return
            _cs = (scan_info or {}).get("cache_status", {}).get("stocks", {})
            if _cs.get("cached") and not _cs.get("expired", True) and _cs.get("age_seconds", 0) > 5:
                print(f"  ♻ 全市场快照缓存命中（{_cs['age_seconds']}秒前拉取，未重爬行情源）")
            if not candidates:
                print(f"  ⚠ 初筛无候选股（可能非交易时段或条件过严）")
                return

            # T05-N：limit 是送入评分的去重候选上限（不是发现接口/调用次数上限）
            codes = _scan_limit_codes([c.stock_code for c in candidates], limit)
            if _expired():
                _scan_candidates_complete_no_scoring(len(codes))
                return
            print(f"  ✓ 初筛 {len(candidates)} 只 → 取前 {len(codes)} 只评分")
            print()

    # ── Step 2: 笨总批量评分（6 维 = 5 AI + 1 规则）──
    if _expired():
        _scan_candidates_complete_no_scoring(len(codes))
        return
    print(f"  [Step 2/3] 笨总 6 维评分中（5 维 AI + 1 维规则；{len(codes)} 只候选；"
          "缓存命中/降级/主题定位/sector-meta/重试都会改变实际请求数，"
          "它不是实耗次数或费用上限）...")
    # T05-N fix1：评分标题输出也可能耗尽预算——真正调用评分器前再核同一截止
    if _expired():
        _scan_prep_stop("评分标题输出后（评分未开始）")
        return
    def _progress(i, total, code, status):
        print(f"    ({i}/{total}) {code} → {status}")

    # T05-N：同一绝对截止点（monotonic 秒）原样交给既有 auto_score_batch(deadline=...)；
    # 无预算路径保持旧调用（不新增 kwargs，旧 mock/调用方兼容）。
    if deadline is None:
        batch = auto_score_batch(codes, top_n=top_n, force_refresh=force_refresh,
                                 config=config, progress_cb=_progress)
    else:
        batch = auto_score_batch(codes, top_n=top_n, force_refresh=force_refresh,
                                 config=config, progress_cb=_progress, deadline=deadline)
    print()

    _not_started = list(batch.get("not_started") or [])
    _partial = bool(_not_started) or bool(batch.get("stop_reason"))

    # ── Step 3: 展示笨总 TopN 排名 ──
    print("─" * 64)
    if _partial:
        print(f"  📊 笨总 Top{len(batch['top_n'])} 排名（部分结果：评分 {batch['scored']}只 / "
              f"预算未开始 {len(_not_started)}只 / 失败 {batch['failed']}只）")
        print("  ⏱ 启动预算到期：本批评分未完整——以下为部分结果，不覆盖上次有效 "
              "last_scan/正式扫描报告，不开启 TopN 回测")
    else:
        print(f"  📊 笨总 Top{len(batch['top_n'])} 排名（评分 {batch['scored']}只 / "
              f"失败 {batch['failed']}只）")
    print("─" * 64)
    if _partial and not batch["top_n"]:
        print("  （本批无已完成的评分结果；未开始项不计失败、不显示收益0）")
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
        print(f"  评分=归一化(0-100) | 等级含行业景气度闸门(景气≤30最高C/=0判F) | 景气分标红=笨总大前提存疑")
    except Exception:
        # rich 不可用时降级纯文本
        for idx, item in enumerate(batch["top_n"], 1):
            eff = item.get("effective_grade", item["grade"])
            norm = item.get("normalized_score", item["total_score"])
            raw_tag = f"(原{item['grade']})" if eff != item["grade"] else ""
            print(f"  {idx}. {item['code']} {item['name'][:8]} | {norm:.0f} {eff}{raw_tag} | conf {item['confidence']:.2f}")
    print()

    if batch["failures"]:
        print(f"  失败: {', '.join(f['code'] for f in batch['failures'])}")
        print()

    # T05-N：只有“确认完整评分 + 有效 TopN”才沿原路径保存；部分/空名单不覆盖上次扫描。
    _has_top = bool(batch["top_n"])
    if _partial:
        print("  ⏱ 部分结果仅本次展示：不覆盖上次有效 last_scan/正式扫描报告，"
              "不开启 TopN 回测")
    elif not _has_top:
        print("  ⚠ 本批无有效 TopN：不覆盖上次有效扫描、不写正式扫描报告、不开启回测")

    _scan_snapshot_ok = False
    if not _partial and _has_top:
        # v0.8.7 体验重构 Step1：scan 结果落盘 + 存状态供 #N 快捷接住
        # T05-N fix1：快照/历史/报告是三个独立事实，必须分别读取并展示实际结果；
        # helper 失败返回 ScanSaveResult(False,...)/"" 而不是抛异常——不能当保存已证。
        _items = []
        for _it in batch["top_n"]:
            _items.append({
                "code": _it["code"], "name": _it["name"],
                "score": _it.get("normalized_score", _it["total_score"]),
                "grade": _it.get("effective_grade", _it["grade"]),
                "confidence": _it["confidence"],
                "dims": _it.get("dim_scores", {}),
                "invalidate": _it.get("invalidate", False),
            })
        _src = f"bz scan {theme}".strip()
        _saved = None
        _save_error = None
        try:
            from src.cli.session_state import save_last_scan, persist_scan_report
            _saved = save_last_scan(_items, _src)
        except Exception as _e:
            _save_error = _e
        _snapshot_ok = bool(getattr(_saved, "snapshot", False))
        _history_ok = bool(getattr(_saved, "history", False))
        _saved_ts = getattr(_saved, "timestamp", "") or ""
        if _save_error is None:
            try:
                _rp = persist_scan_report(_items, _src, timestamp=(_saved_ts or None))
            except Exception:
                _rp = ""
            _report_ok = bool(_rp)
        else:
            _rp = ""
            _report_ok = False
        if _save_error is not None:
            print("  scan 落盘异常（保存结果不可用）；快照/历史/报告均未确认："
                  f"{type(_save_error).__name__}: {_save_error}")
            print("  ⚠ 本批新名单未确认写入 last_scan：请不要按 l all/#N/pos add #N "
                  "操作本批（避免误用上一次旧名单）")
        else:
            print(f"  📝 扫描保存：快照{'成功' if _snapshot_ok else '失败'}"
                  f" | 历史{'成功' if _history_ok else '失败'}"
                  f" | {'报告已生成：' + _rp if _report_ok else '报告未生成'}")
            _scan_snapshot_ok = _snapshot_ok
            if _snapshot_ok:
                print(f"  💡 后续：l all 批量深分析这{len(batch['top_n'])}只(简明卡)")
                print(f"     或单只：l #1 深分析 / bz #1 重评分 / pos add #1 建仓")
            else:
                print("  ⚠ 本批新名单未写入 last_scan：请不要按 l all/#N/pos add #N "
                      "操作本批（避免误用上一次旧名单）")
            if not _history_ok:
                print("  ⚠ 复盘历史未追加：scan review 看不到本批（不影响本次排名/回测）")
        print()

    if _partial or not _has_top:
        if do_backtest:
            print("  💡 本批 TopN 回测未开始；需要完整结果请重跑新的独立扫描")
        else:
            print("  💡 提示：需要完整结果请重跑新的独立扫描（本次结果未保存）")
        print()
        return

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
    # T05-N：完成评分后、回测开始前到期 —— 保留完整评分扫描；不启动构造/run，
    # 下面的逐项登记会显式标“未开始”（不计失败/收益0），最后再给计数。
    # T05-N fix1：是否“已保存”只能按实际快照结果说，不能无条件声称。
    if _expired():
        print(f"  ⏱ 启动预算已到期：回测未开始（0/{len(batch['top_n'])} 只）")
        if _scan_snapshot_ok:
            print("  已完整评分且新名单快照已保存；未开始项不计失败、不显示收益0")
        else:
            print("  评分已在内存完成，但本批新名单未成功保存"
                  "（不要按 #N/l all 操作旧名单）；未开始项不计失败、不显示收益0")
        print()

    from src.core.backtest_engine import BacktestEngine
    from src.cli.main import normalize_stock_code

    bt_rows = []
    bt_total = len(batch["top_n"])
    bt_not_started = 0
    for idx, item in enumerate(batch["top_n"], 1):
        code = normalize_stock_code(item["code"])
        # T05-N：构造引擎前核同一截止；到期不再开始后续回测调用
        if _expired():
            for _idx in range(idx, bt_total + 1):
                _item = batch["top_n"][_idx - 1]
                _code = normalize_stock_code(_item["code"])
                print(f"  ({_idx}/{bt_total}) {_code} {_item['name'][:8]}："
                      "未开始回测（启动预算到期）")
                bt_rows.append({"code": _code, "name": _item["name"][:8],
                                "bz_score": _item["total_score"],
                                "bz_grade": _item["grade"],
                                "return": None, "bench": None, "trades": 0,
                                "status": "not_started"})
                bt_not_started += 1
            break
        print(f"  ({idx}/{bt_total}) 回测 {code} {item['name'][:8]}...")
        # 慢标题输出后、构造前再核一次：到期不启动构造
        if _expired():
            print("    ⏱ 标题输出后启动预算到期：本只未开始回测"
                  "（不计失败、不显示收益0）")
            bt_rows.append({"code": code, "name": item["name"][:8],
                            "bz_score": item["total_score"],
                            "bz_grade": item["grade"],
                            "return": None, "bench": None, "trades": 0,
                            "status": "not_started"})
            bt_not_started += 1
            continue
        try:
            eng = BacktestEngine(
                stock_code=code, start_date=start, end_date=end,
                initial_capital=capital, execution_mode="framework_strict",
                layer_mode="decision_strategy_execution",
                skills_dir=skills_dir, signal_weights=signal_weights,
                skill_types=skill_types, entry_exit_config=entry_exit_config,
            )
        except Exception as e:
            print(f"    ✗ 回测失败: {type(e).__name__}: {e}")
            bt_rows.append({"code": code, "name": item["name"][:8],
                            "bz_score": item["total_score"], "bz_grade": item["grade"],
                            "return": None, "bench": None, "trades": 0,
                            "status": "failed"})
            continue
        # 构造可能较慢：run() 前再核同一截止，到期不启动
        if _expired():
            print("    ⏱ 引擎已构造但启动预算到期：未调用 run()"
                  "（不计失败、不显示收益0）")
            bt_rows.append({"code": code, "name": item["name"][:8],
                            "bz_score": item["total_score"],
                            "bz_grade": item["grade"],
                            "return": None, "bench": None, "trades": 0,
                            "status": "not_started"})
            bt_not_started += 1
            continue
        try:
            res = eng.run()
            bt_rows.append({
                "code": code, "name": item["name"][:8],
                "bz_score": item["total_score"], "bz_grade": item["grade"],
                "return": res.total_return_pct, "bench": res.benchmark_return_pct,
                "trades": getattr(res, "total_trades", 0) or 0,
                "status": "ok",
            })
        except Exception as e:
            print(f"    ✗ 回测失败: {type(e).__name__}: {e}")
            bt_rows.append({"code": code, "name": item["name"][:8],
                            "bz_score": item["total_score"], "bz_grade": item["grade"],
                            "return": None, "bench": None, "trades": 0,
                            "status": "failed"})

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
            if r.get("status") == "not_started":
                tbl.add_row(str(idx), r["code"], r["name"], f"{r['bz_score']:.1f}",
                            r["bz_grade"], "[yellow]未开始[/yellow]", "-", "-", "-")
                continue
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
            if r.get("status") == "not_started":
                print(f"  {idx}. {r['code']} {r['name']} | 笨总{r['bz_score']:.1f}"
                      f"{r['bz_grade']} | 未开始（预算到期；不计失败/收益0）")
                continue
            ret = f"{r['return']:+.2f}%" if r["return"] is not None else "失败"
            print(f"  {idx}. {r['code']} {r['name']} | 笨总{r['bz_score']:.1f}{r['bz_grade']} | 收益{ret}")
    # T05-N fix1：成功/失败/未开始分别计数——构造或 run 异常不得计入“完成/成功”。
    bt_ok = sum(1 for r in bt_rows if r.get("status") == "ok")
    bt_failed = sum(1 for r in bt_rows if r.get("status") == "failed")
    print(f"  回测结果：成功 {bt_ok}/{bt_total} 只；失败 {bt_failed} 只；"
          f"未开始 {bt_not_started} 只（未开始=启动预算到期，不计失败、不显示收益0）")
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

def _dispatch_pos_cli(mode: str, args: dict, manage_positions) -> bool:
    """T04-E：pos_* 命令分发表（从 run_cli 抽出的连贯一块）。

    命中并执行后返回 True；非 pos_* mode 返回 False（供 run_cli 继续 elif）。
    行为与抽出前逐分支等价——黄金测试 test_t04e_dispatch_golden 锁死。
    """
    if mode == "pos_list":
        manage_positions("list")
        return True
    if mode == "pos_add":
        manage_positions(
            "add",
            stock_code=args.get("stock_code", ""),
            name=args.get("name", ""),
            price=args.get("price", 0.0),
            ratio=args.get("ratio", 0.20),
        )
        return True
    if mode == "pos_remove":
        manage_positions("remove", stock_code=args.get("stock_code", ""))
        return True
    if mode == "pos_confirm":
        # F1：确认实际成交（账户事实唯一入口；含 J2：--qty 数量成交入账户事件账本）
        manage_positions(
            "confirm",
            stock_code=args.get("stock_code", ""),
            ratio=args.get("ratio"),
            price=args.get("price", 0.0),
            qty=args.get("qty"),
            fee=args.get("fee", 0.0),
            trade_date=args.get("trade_date", ""),
            note=args.get("note", ""),
        )
        return True
    if mode == "pos_verify":
        # ISS-111：可记录核对确认（市值校准，不产生费用）
        manage_positions("verify", stock_code=args.get("stock_code", ""))
        return True
    if mode == "pos_opening":
        # J2 期初导入：现金锚定
        manage_positions("opening", cash=args.get("cash"))
        return True
    if mode == "pos_openinglot":
        # J2 期初导入：持仓批次
        manage_positions(
            "openinglot",
            security_id=args.get("security_id", ""),
            qty=args.get("quantity"),
            cost_price=args.get("cost_price", 0.0),
            acquired_at=args.get("acquired_at", ""),
        )
        return True
    if mode == "pos_plan":
        manage_positions(
            "plan",
            stock_code=args.get("stock_code", ""),
            update=args.get("update", False),
        )
        return True
    if mode == "pos_overweight":
        manage_positions(
            "overweight",
            stock_code=args.get("stock_code", ""),
            name=args.get("name", ""),
        )
        return True
    return False



def _dispatch_benzong_cli(mode: str, args: dict) -> bool:
    """T04-F：benzong_* 连贯分发助手（从 run_cli 抽出行为等价一块）。

    命中并执行后返回 True；非 benzong_* mode 返回 False，由 run_cli 继续 elif。
    行为锁定靠 test_t04e_dispatch_golden（含 T04-F0 扩展）。
    """
    if mode == "benzong":
        # v0.8.6.2: bz <code> 默认自动 AI 评分；--manual 走旧交互式
        # v0.8.6.3: bz --check 数据源连通性体检（ISS-043）
        if args.get("check"):
            from src.data.source_check import check_all_sources, format_report
            print(format_report(check_all_sources()))
            return True
        run_benzong_scoring(
            meta=args.get("meta", ""),
            manual=args.get("manual", False),
            refresh=args.get("refresh", False),
        )
        return True

    if mode == "benzong_all":
        from src.cli import session_state
        # T05-J：坏预算必须在读取扫描结果/缓存预览之前拒绝（正常 parse 已校验，
        # 这里防绕过 parse 的程序化调用）
        budget = args.get("budget")
        budget_seconds = None
        if budget is not None:
            from src.core.benzong.batch_scorer import budget_descriptor
            _budget_desc = budget_descriptor(budget)
            if _budget_desc is None:
                print("  [!] 预算参数无效（需有限正数秒）；本次未开始评分"
                      "（不读取扫描结果、不调用 AI、不写账本）")
                return True
            budget_seconds = _budget_desc["budget_seconds"]
        from src.core.benzong import cache as bz_cache
        from datetime import datetime as _dt
        last = session_state.get_last_scan()
        if not last or not last.get("items"):
            print("\n  [!] 还没有扫描结果。先跑：bz scan <主题> 或 scan market")
            return True
        items = last["items"]
        src_label = last.get("source", "?")
        when = (last.get("timestamp") or "")[:16].replace("T", " ")
        try:
            _age_min = max(0, round((_dt.now() - _dt.fromisoformat(last.get("timestamp") or "")).total_seconds() / 60))
        except Exception:
            _age_min = None
        if _age_min is not None and _age_min > 30:
            print(f"  [!] 注意：该扫描已是 {_age_min} 分钟前的结果，行情可能已变化")
        today = _dt.now().strftime("%Y-%m-%d")
        dims = ["industry_prosperity", "business_purity", "valuation_position",
                "industry_leader", "market_recognition", "risk_deduction"]
        print(f"\n  最近扫描：{src_label}（{when}），共 {len(items)} 只")
        if budget_seconds is not None:
            print(_budget_notice_text(budget_seconds, "ba 批量笨总评分"))
        else:
            # J7/final_gate E：正常无预算也主动告知可选时限，且必须在实际缓存预览
            # （bz_cache.get）之前输出（无新确认、不改 kwargs）
            print(_BUDGET_HINT)
        uncached = sum(
            1 for it in items
            if isinstance(it, dict) and it.get("code")
            and any(bz_cache.get(it["code"], today, d) is None for d in dims)
        )
        if uncached:
            est_min = max(1, round(uncached * 0.5))
            try:
                ans = input(f"  其中 {uncached} 只今天未评分，将调用 AI（约 {est_min} 分钟）。继续?(y/N) ")
            except (EOFError, KeyboardInterrupt):
                print("  已取消")
                return True
            if ans.strip().lower() not in ("y", "yes"):
                print("  已取消")
                return True
        else:
            print("  全部命中今日缓存，直接出排名表（不再产生维度评分费用）\n")
        from src.cli.main import benzong_batch_analyze
        if budget_seconds is not None:
            benzong_batch_analyze(items, src_label=src_label, budget=budget_seconds)
        else:
            benzong_batch_analyze(items, src_label=src_label)
        return True

    if mode == "benzong_multi":
        # v0.8.9.0 ISS-087: bz 多代码批量评分——复用 ba 同款管道
        # （auto_score_batch：AI client 建一次、整批共享全市场成交额、串行防
        # baostock 登录态竞争、单只失败不阻塞、同日缓存优先不重算）
        from src.cli.main import benzong_batch_analyze
        # T05-J：坏预算先拒绝（不读缓存、不确认、不调用 AI/账本）
        budget = args.get("budget")
        budget_seconds = None
        if budget is not None:
            from src.core.benzong.batch_scorer import budget_descriptor
            _budget_desc = budget_descriptor(budget)
            if _budget_desc is None:
                print("  [!] 预算参数无效（需有限正数秒）；本次未开始评分"
                      "（不读取缓存、不调用 AI、不写账本）")
                return True
            budget_seconds = _budget_desc["budget_seconds"]
        from src.core.benzong import cache as bz_cache
        from datetime import datetime as _dt
        codes = [c for c in args.get("codes", []) if c]
        if not codes:
            print("  [!] 没有可评分的代码")
            return True
        today = _dt.now().strftime("%Y-%m-%d")
        dims = ["industry_prosperity", "business_purity", "valuation_position",
                "industry_leader", "market_recognition", "risk_deduction"]
        print(f"\n  笨总批量评分 {len(codes)} 只：{'、'.join(codes)}"
              + ("（--refresh 强制重算）" if args.get("refresh") else ""))
        if budget_seconds is not None:
            print(_budget_notice_text(budget_seconds, "bz 多代码批量笨总评分"))
        else:
            # J7/final_gate E：正常无预算也主动告知可选时限，且必须在实际缓存预览
            # （bz_cache.get）之前输出（无新确认、不改 kwargs）
            print(_BUDGET_HINT)
        uncached = [
            c for c in codes
            if any(bz_cache.get(c, today, d) is None for d in dims)
        ]
        if args.get("refresh"):
            est_min = max(1, round(len(codes) * 0.5))
            print(f"  每只 6 维 AI（约 {est_min} 分钟/只），共约 {est_min * len(codes)} 分钟")
        elif uncached:
            est_min = max(1, round(len(uncached) * 0.5))
            try:
                ans = input(
                    f"  其中 {len(uncached)} 只今天未评分，将调用 AI（约 {est_min} 分钟）。继续?(y/N) ")
            except (EOFError, KeyboardInterrupt):
                print("  已取消")
                return True
            if ans.strip().lower() not in ("y", "yes"):
                print("  已取消")
                return True
        else:
            print("  全部命中今日缓存，直接出排名表（不再产生维度评分费用）")
        items = [{"code": c} for c in codes]
        if budget_seconds is not None:
            benzong_batch_analyze(items, force_refresh=args.get("refresh", False),
                                  budget=budget_seconds)
        else:
            benzong_batch_analyze(items, force_refresh=args.get("refresh", False))
        return True

    if mode == "benzong_scan":
        # v0.8.6.3: bz scan 选股初筛→笨总批量评分→可选回测（ISS-041 方向 A）
        run_benzong_scan(args)
        return True

    return False



def _batch_ledger_begin(ss, kind: str, key: str, codes: list, budget=None, snapshot=None):
    """C2/T05-H：登记一次独立批量运行并显示 run 身份；返回令牌（None=记录不可用）。

    每次调用都会新建 run_id/roster——不继承旧 run 的完成/失败，也不冒充缓存命中。
    T05-L：budget 为已校验的 live 预算描述；无预算时不传该参数（旧调用方/旧 mock 兼容）。
    T05-M：snapshot 为该 kind 合法的配置身份描述（live 配置指纹/ba 执行参数快照），
    只传实际有的参数，保持旧签名在无预算无描述时逐项不变。
    """
    if budget is None and snapshot is None:
        run_id = ss.batch_task_start(kind, key, codes)
    elif budget is None:
        run_id = ss.batch_task_start(kind, key, codes, snapshot=snapshot)
    elif snapshot is None:
        run_id = ss.batch_task_start(kind, key, codes, budget=budget)
    else:
        run_id = ss.batch_task_start(kind, key, codes, snapshot=snapshot, budget=budget)
    if run_id:
        state = (f"配置已冻结；指纹={snapshot['sha256']}" if snapshot
                 else "配置未冻结")
        print(f"  任务记录：run {run_id}（{state}；本次独立 run，不继承旧记录）")
    else:
        print("  [!] 任务记录保存失败：本次进度不会被记录（不影响分析）")
    return run_id


def _batch_task_mark(ss, run_id, kind: str, key: str, code: str, ok: bool, note: str = "",
                     degraded: bool = False) -> bool:
    """C2/T05-H：逐项记账（必须携带本次 run 令牌）；返回记录是否可信。

    缺令牌 / 旧 run 晚到 / 清单外 / 写盘失败都由 session_state fail-closed 返回 False，
    入口据此提示“进度可能不完整”，不把可见性记录当成业务结果。
    T05-L：degraded=True 只用于“仅行情”降级；非降级调用保持旧签名。
    """
    try:
        if degraded:
            return bool(ss.batch_task_mark(kind, key, code, ok, note, run_id=run_id,
                                           degraded=True))
        return bool(ss.batch_task_mark(kind, key, code, ok, note, run_id=run_id))
    except Exception:
        return False


def _batch_task_not_started(ss, run_id, kind: str, key: str, code: str, reason: str) -> bool:
    """T05-L：登记调度器确认“从未开始”的代码（预算到期）；返回记录是否可信。"""
    if not run_id:
        return False
    try:
        return bool(ss.batch_task_not_started(kind, key, code, run_id=run_id, reason=reason))
    except Exception:
        return False


def _batch_ledger_finish(run_id, rec_ok: bool, ok_n: int, failed_n: int, total: int,
                         degraded_n: int = 0, not_started_n=None) -> None:
    """C2/T05-H：跑完显示已完成/降级/失败/未记录结果计数；记录不可信时明确提示。

    T05-L：仅行情计“降级”；有预算时单列“预算未开始”。结果未知的股票不记账，
    落在“未记录结果”（不冒充成功/失败/未开始）。
    """
    ns = not_started_n or 0
    pending = max(0, total - ok_n - degraded_n - failed_n - ns)
    print(f"  任务记录：run {run_id}（已完成 {ok_n} / 降级 {degraded_n} / 失败 {failed_n} / "
          + (f"预算未开始 {ns} / " if not_started_n is not None else "")
          + f"未记录结果 {pending}）")
    if not rec_ok:
        print("  [!] 任务记录写入失败：本次进度可能不完整（分析继续）")


# ── T05-L：live 深分析批（l 多代码 / la / l all）的真实结果与可选启动预算 ──

def _monotonic() -> float:
    """live 批启动预算的单调钟入口（同一批只取一次截止点；测试可替换为假钟）。"""
    return time.monotonic()


def _live_deadline_expired(deadline) -> bool:
    return deadline is not None and _monotonic() >= deadline


_LIVE_BUDGET_HINT = ("  可选时限：末尾可加 --budget 600（秒，默认不启用）；"
                     "到点不开始下一只，当前一只分析完再返回")

_LIVE_QUOTE_ONLY_NOTE = "仅行情：技术分析未完成（可重试）"


def _live_budget_notice(seconds: float, scope: str, after_confirm: bool) -> str:
    """live 预算语义的用户可见说明（l all 确认后才计时；la/l 多代码从入口计时）。"""
    when = ("确认后开始计时" if after_confirm
            else "从现在开始计时，读取持仓/预取行情等准备也计入")
    return (f"  启动预算：{seconds:g} 秒（{scope}；{when}；到期不再开始新股票，"
            "已开始的股票会分析完，准备阶段可能超出预算——非强制中断、非费用上限）")


def _live_budget_desc(args):
    """取 live 预算描述：(描述|None, 是否可继续)。统一门已先拒坏值，这里防直接调用绕过。"""
    budget = args.get("budget") if isinstance(args, dict) else None
    if budget is None:
        return None, True
    from src.cli.session_state import live_budget_descriptor
    desc = live_budget_descriptor(budget)
    if desc is None:
        print(_LIVE_BUDGET_INVALID)
        return None, False
    return desc, True


def _dedupe_live_codes(codes) -> list:
    """保序去重（执行名单 = 账本 roster = 计数口径）；空值忽略。"""
    out, seen = [], set()
    for c in codes or []:
        if not c:
            continue
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


# ── T05-M：live 批配置一次冻结与非秘密配置指纹 ─────────────────────

_LIVE_CONFIG_FAILED = ("  配置加载失败：本次未开始分析"
                       "（未登记任务、未调用分析/AI；请检查配置后重试）")
_LIVE_CONFIG_NO_FP = ("  配置范围说明：本次配置无法安全派生非秘密指纹，"
                      "配置身份按未冻结记录（分析仍按本批冻结副本继续）")


def _freeze_live_config(load_config, ai_overrides):
    """在预算起点之后、首只股票之前只加载一次配置并深拷贝冻结（T05-M）。

    返回 (冻结配置, True) 或 (None, False)。失败（读取/解析/非 dict/复制/覆盖）时
    调用方必须在首只之前终止本次批：不登记 run、不调用分析与 AI。日志只记录异常
    类型，绝不拼接异常正文——配置解析异常可能带出密钥内容。
    """
    try:
        loaded = load_config()
    except Exception as e:
        logger.debug("live 批配置加载失败（本次未开始分析，原因类型 %s）",
                     type(e).__name__)
        print(_LIVE_CONFIG_FAILED)
        return None, False
    if not isinstance(loaded, dict):
        logger.debug("live 批配置根不是字典（本次未开始分析，类型 %s）",
                     type(loaded).__name__)
        print(_LIVE_CONFIG_FAILED)
        return None, False
    try:
        frozen = copy.deepcopy(loaded)
    except Exception as e:
        logger.debug("live 批配置深拷贝失败（本次未开始分析，原因类型 %s）",
                     type(e).__name__)
        print(_LIVE_CONFIG_FAILED)
        return None, False
    if ai_overrides:
        # noai 等 REPL 覆盖必须进冻结副本：批内所有股票共用同一份有效配置
        try:
            from src.cli.main import apply_ai_overrides
            frozen = apply_ai_overrides(frozen, ai_overrides)
        except Exception as e:
            logger.debug("live 批配置覆盖失败（本次未开始分析，原因类型 %s）",
                         type(e).__name__)
            print(_LIVE_CONFIG_FAILED)
            return None, False
    return frozen, True


def _live_config_descriptor(frozen):
    """从冻结配置派生 live 配置指纹描述；不可安全派生时返回 None（不冒称已冻结）。"""
    from src.cli.session_state import derive_live_config_descriptor
    try:
        return derive_live_config_descriptor(frozen)
    except Exception as e:
        logger.debug("live 批配置指纹派生异常（按未冻结记录，原因类型 %s）",
                     type(e).__name__)
        return None


def _report_live_config(frozen):
    """启动行：配置已冻结 + 规范指纹 + 范围说明；返回账本用的指纹描述或 None。

    指纹与 tasks 展示同源（session_state 同一 schema/scope/64 位小写 hex 门）。
    """
    desc = _live_config_descriptor(frozen)
    if desc is None:
        print(_LIVE_CONFIG_NO_FP)
        return None
    print(f"  配置: live 批配置已冻结；指纹={desc['sha256']}"
          "（本批只加载一次，不逐股重读）")
    print("  配置范围: 仅白名单非秘密分析参数；技能YAML内容/数据/提示词未核定"
          "（不授予缓存/信号资格）")
    return desc


def _run_live_batch_loop(ss, analyze_live, ai_overrides, *, kind: str, key: str,
                         entries: list, account_facts, run_id, deadline,
                         deep_source=None, rec_ok: bool = True, config=None) -> dict:
    """T05-L：live 批逐股循环（l 多代码 / la / l all 共用）。

    entries: [(code, 标题)]，已保序去重，与账本 roster 同源。每只：核截止 → 打印
    标题 → 临近调用前再核截止 → 登记已开始 → 调 analyze_live。
    - LiveOutcome.COMPLETE：记完成；deep_source 非 None（l 多代码 / l all）才写当日已析
    - LiveOutcome.QUOTE_ONLY：记降级（可重试），不写当日已析
    - 其它返回值（None/bool/字符串/…）：结果未知——不计成功/失败/未开始、不写账本
    - SystemExit/异常：记失败（单只失败不拖垮批次）
    预算到期只阻止开始后续股票，已开始的分析允许跑完；未开始项只来自本调度器确认。
    T05-M：config 为本批冻结的有效配置，逐只传给 analyze_live（由它再做逐股深拷贝）；
    None 表示本批没有冻结配置（冻结前预算已到期，一只也不会开始）。
    """
    from src.cli.main import LiveOutcome
    res = {"complete": [], "quote_only": [], "unknown": [], "failed": [],
           "not_started": [], "stopped": False, "rec_ok": rec_ok}
    started = set()
    codes = [c for c, _ in entries]
    total = len(entries)

    def _stop_rest(rest):
        res["stopped"] = True
        for c in rest:
            if c in started or c in res["not_started"]:
                continue
            res["not_started"].append(c)
            if not _batch_task_not_started(ss, run_id, kind, key, c,
                                           ss.LIVE_BUDGET_STOP_REASON):
                res["rec_ok"] = False

    for i, (code, header) in enumerate(entries, 1):
        if _live_deadline_expired(deadline):
            _stop_rest(codes[i - 1:])
            break
        print(f"\n{'='*60}")
        print(f"  [{i}/{total}] {header}")
        print(f"{'='*60}")
        if _live_deadline_expired(deadline):
            print("  预算到期：本只未开始（未调用分析）")
            _stop_rest(codes[i - 1:])
            break
        started.add(code)
        try:
            outcome = analyze_live(code, ai_overrides=ai_overrides, ai_debug=_ai_debug,
                                   compact=True, account_facts=account_facts,
                                   config=config)
        except SystemExit:
            print(f"  [!] {code} 数据获取失败（已跳过，不影响其余）")
            res["rec_ok"] = _batch_task_mark(ss, run_id, kind, key, code, False,
                                             "数据获取失败") and res["rec_ok"]
            res["failed"].append(code)
            continue
        except Exception as e:
            print(f"  [!] {code} 分析失败: {type(e).__name__}: {e}")
            res["rec_ok"] = _batch_task_mark(ss, run_id, kind, key, code, False,
                                             f"{type(e).__name__}: {e}") and res["rec_ok"]
            res["failed"].append(code)
            continue
        if outcome is LiveOutcome.COMPLETE:
            res["complete"].append(code)
            res["rec_ok"] = _batch_task_mark(ss, run_id, kind, key, code, True) and res["rec_ok"]
            if deep_source is not None:
                # 完整分析才记当日已析（失败/仅行情/未知下次自动重试）；逐只落盘。
                # 记录异常一律按“未保存”处理，绝不打断后续股票
                try:
                    _saved = bool(ss.mark_deep_analyzed([code], source=deep_source))
                except Exception:
                    _saved = False
                if not _saved:
                    print(f"  ⚠ {code} 当日已析记录未保存（记录文件异常或写入失败）："
                          "下次 l all 可能重复分析")
        elif outcome is LiveOutcome.QUOTE_ONLY:
            res["quote_only"].append(code)
            print(f"  ⚠ {code} 仅取得行情，技术分析未完成：记为降级（可重试），不计成功"
                  + ("，不记当日已析" if deep_source is not None else ""))
            res["rec_ok"] = _batch_task_mark(ss, run_id, kind, key, code, True,
                                             _LIVE_QUOTE_ONLY_NOTE,
                                             degraded=True) and res["rec_ok"]
        else:
            res["unknown"].append(code)
            print(f"  ⚠ {code} 未返回完成状态（结果未知）：不计成功/失败"
                  + ("，不记当日已析" if deep_source is not None else ""))
    return res


def _print_live_batch_summary(res: dict, *, kind: str, total: int, run_id,
                              budget_desc) -> None:
    """T05-L：live 批收尾——完成/仅行情/未知/预算未开始/失败分开计，预算语义如实说明。"""
    complete, quote_only = res["complete"], res["quote_only"]
    unknown, failed, not_started = res["unknown"], res["failed"], res["not_started"]
    line = f"\n  批量完成：{len(complete)}/{total} 成功"
    if quote_only:
        line += f"；仅行情(降级，可重试) {len(quote_only)}: {', '.join(quote_only)}"
    if unknown:
        line += f"；结果未知 {len(unknown)}: {', '.join(unknown)}"
    if budget_desc is not None and not_started:
        line += f"；预算未开始 {len(not_started)}"
    if failed:
        line += f"；失败: {', '.join(failed)}"
    print(line)
    if budget_desc is not None:
        if res["stopped"]:
            print("  预算到期：不再开始后续股票（已开始的股票已分析完；准备阶段可能超出预算，"
                  "非强制中断）")
            follow = ("下次 l all 会继续这些股票" if kind == "l all"
                      else "需要时重跑会开新的独立 run")
            print(f"  预算未开始 {len(not_started)} 只：{', '.join(not_started)}"
                  f"（未调用分析，不计失败；{follow}）")
        print("  预算说明：到期只阻止开始下一只股票；已开始的股票与准备阶段继续完成，"
              "实际耗时可能超出预算（非强制中断、无费用上限）")
    if run_id:
        _batch_ledger_finish(run_id, res["rec_ok"], len(complete), len(failed), total,
                             degraded_n=len(quote_only),
                             not_started_n=(len(not_started) if budget_desc is not None
                                            else None))


def _dispatch_live_cli(mode: str, args: dict, analyze_live, ai_overrides: dict,
                       load_config=None) -> bool:
    """T04-G：live_* 连贯分发助手（从 run_cli 抽出行为等价一块）。

    命中并执行后返回 True；非 live_* mode 返回 False，由 run_cli 继续 elif。
    承接：live / live_multi / live_all / live_scan_all。
    行为锁定：test_t04e_dispatch_golden（含 T04-G0 扩展）。
    T05-L：三个批量入口只把 LiveOutcome.COMPLETE 记成功；可选 --budget 共用同一
    单调截止点（到期不开始下一只，已开始的分析跑完）。
    T05-M：三个批量入口在预算起点之后、首只股票之前只加载一次配置并深拷贝冻结，
    并用同一冻结配置派生 live 配置指纹（启动行 + 账本 + tasks 同源）。load_config
    缺省时按 main.load_config 处理（保持直接调用者的旧签名可用）。
    """
    global _ai_debug

    _load = load_config
    if _load is None:
        from src.cli.main import load_config as _load_config

        _load = _load_config

    if mode == "live":
        # T05-L：单只深析不支持预算——绕过 run_cli 统一门的直接调用也显式拒绝，不静默忽略
        if isinstance(args, dict) and args.get("budget") is not None:
            print(f"  [!] 单只深析不支持 --budget（{_BUDGET_SCOPE}）")
            return True
        # T05-M：单只保持既有行为（不传 config，analyze_live 照旧读盘）
        analyze_live(args["stock_code"], ai_overrides=ai_overrides, ai_debug=_ai_debug)
        return True

    if mode == "live_multi":
        # v0.8.9.0 ISS-087: 多代码一次性批量深分析（l 600519,000001 / l 600519 000001）。
        # 模式对齐 l all：compact 卡片 + 单只失败跳过不拖垮批次 + 完整分析记当日已析
        # （后续 l all 自动跳过，缓存化省钱）。显式点名的代码全部重析，不做当日跳过。
        from src.cli import session_state as _ss
        budget_desc, ok = _live_budget_desc(args)
        if not ok:
            return True
        codes = _dedupe_live_codes(args.get("codes", []))
        if not codes:
            print("  [!] 没有可分析的代码")
            return True
        print(f"\n  批量深分析 {len(codes)} 只（每只约0.5~1分钟，含AI调用）：{'、'.join(codes)}")
        # T05-L：手动多代码在预取/账户快照之前起算（同一截止点贯穿全批）
        deadline = None
        if budget_desc is not None:
            deadline = _monotonic() + budget_desc["budget_seconds"]
            print(_live_budget_notice(budget_desc["budget_seconds"], "l 多代码",
                                      after_confirm=False))
        else:
            print(_LIVE_BUDGET_HINT)
        # T05-M：预算起点之后、首只之前只加载一次配置并深拷贝冻结（含 noai 覆盖）。
        # 冻结前预算已到期 → 不加载配置，按 T05-L 由调度器登记未开始。
        _frozen, _config_desc = None, None
        if not _live_deadline_expired(deadline):
            _frozen, _frozen_ok = _freeze_live_config(_load, ai_overrides)
            if not _frozen_ok:
                return True
            _config_desc = _report_live_config(_frozen)
        # ISS-088：批量预取行情（新浪批量/东财全市场 1 次请求），逐只分析时
        # get_realtime_quote 直接命中预取映射——N 只行情从 N 次请求压成 1 次
        if not _live_deadline_expired(deadline):
            try:
                from src.data.akshare_client import AKShareClient
                got = AKShareClient.prefetch_realtime_quotes(codes)
                print(f"  行情预取：{got}/{len(codes)} 只命中批量源（逐只分析零重复请求）")
            except Exception as _e:
                print(f"  [!] 行情预取失败（不影响分析，逐只回退）: {type(_e).__name__}")
        # C2/T05-H：批量任务账本（tasks 命令可查进度与失败项）——键含代码集摘要，
        # 防不同代码集同数量碰撞；每次启动新建独立 run，令牌绑定全部逐项 mark
        _tk = "手动多代码 " + ",".join(codes[:4]) + ("…" if len(codes) > 4 else "")
        run_id = _batch_ledger_begin(_ss, "l", _tk, codes, budget=budget_desc,
                                     snapshot=_config_desc)
        # N0/B2：批量请求共享同一账户事实（同版本；下一命令重新读）
        _acct_facts = None
        if not _live_deadline_expired(deadline):
            from src.data.portfolio import PortfolioManager as _PM_multi
            _acct_facts = _PM_multi().request_account_facts(estimate_nav=True)
        res = _run_live_batch_loop(
            _ss, analyze_live, ai_overrides, kind="l", key=_tk,
            entries=[(c, c) for c in codes], account_facts=_acct_facts,
            run_id=run_id, deadline=deadline, deep_source="手动多代码",
            rec_ok=bool(run_id), config=_frozen)
        _print_live_batch_summary(res, kind="l", total=len(codes), run_id=run_id,
                                  budget_desc=budget_desc)
        return True

    if mode == "live_all":
        from src.cli import session_state as _ss_la
        budget_desc, ok = _live_budget_desc(args)
        if not ok:
            return True
        # T05-L：la 在读取账户快照之前起算；快照同时形成 roster 与全批上下文
        deadline = None
        if budget_desc is not None:
            deadline = _monotonic() + budget_desc["budget_seconds"]
            print(_live_budget_notice(budget_desc["budget_seconds"], "la",
                                      after_confirm=False))
            if _live_deadline_expired(deadline):
                print("  预算到期：尚未读取持仓清单，本次未开始分析"
                      "（持仓名单未知，未登记任务；需要时重跑）")
                return True
        # T05-M：预算起点之后、读取持仓/首只之前只加载一次配置并深拷贝冻结。
        # 冻结失败 → 首只之前终止：不读清单、不登记 run、不调用分析/AI。
        _frozen, _frozen_ok = _freeze_live_config(_load, ai_overrides)
        if not _frozen_ok:
            return True
        _config_desc = _report_live_config(_frozen)
        from src.data.portfolio import PortfolioManager
        pm = PortfolioManager()
        # O0/Y2：批量清单先经同一请求快照形成（账本确定有仓 ∪ 兼容投影代码，
        # 归一去重）——账本独有持仓不再在读快照前被"当前无持仓"吞掉；
        # 同批 1 次快照，下一命令新建实例重新读
        _acct_facts = pm.request_account_facts(estimate_nav=True)
        _entries, _incomplete = _acct_facts.holding_entries()
        if not _entries:
            if _incomplete:
                print("\n  [!] 持仓清单读取异常（账本/持仓库）——清单不完整，待对账（不当作空仓）")
            else:
                print("\n  当前无持仓记录")
            return True
        _seen, _uniq = set(), []
        for e in _entries:
            _c = str(e.stock_code)
            if not e.stock_code or _c in _seen:
                continue
            _seen.add(_c)
            _uniq.append(e)
        print(f"\n  一键分析所有持仓（{len(_uniq)}只）—— 简明模式，每只一张人话摘要卡（详情单独跑 l <代码>）\n")
        if _incomplete:
            print("  [!] ⚠ 账本/持仓库读取异常——持仓清单可能不完整，待对账（不当作空仓）\n")
        if budget_desc is None:
            print(_LIVE_BUDGET_HINT)
        _la_codes = [str(e.stock_code) for e in _uniq]
        _tk_la = "持仓 " + ",".join(_la_codes[:4]) + ("…" if len(_la_codes) > 4 else "")
        run_id = _batch_ledger_begin(_ss_la, "la", _tk_la, _la_codes, budget=budget_desc,
                                     snapshot=_config_desc)
        _la_entries = []
        for e in _uniq:
            _src_tag = "  [账本持仓·投影缺失——成本/比例未知]" if e.from_ledger else ""
            _la_entries.append((str(e.stock_code),
                                f"{e.stock_name or e.stock_code} ({e.stock_code}){_src_tag}"))
        res = _run_live_batch_loop(
            _ss_la, analyze_live, ai_overrides, kind="la", key=_tk_la,
            entries=_la_entries, account_facts=_acct_facts, run_id=run_id,
            deadline=deadline, deep_source=None, rec_ok=bool(run_id),
            config=_frozen)
        _print_live_batch_summary(res, kind="la", total=len(_la_entries), run_id=run_id,
                                  budget_desc=budget_desc)
        return True

    if mode == "live_scan_all":
        # v0.8.7.9: l all —— 对最近一次扫描结果（bz scan / scan market）批量深度分析
        # 模式对齐 ba（读 session_state + 过期提示 + y/N 确认）与 la（compact 卡 + 单只失败跳过）
        # 去重：当天已**完整**深分析的默认跳过（T05-L 起只认带完成资格标记的记录），
        # -f/--force 强制重析
        from src.cli import session_state
        from datetime import datetime as _dt
        budget_desc, ok = _live_budget_desc(args)
        if not ok:
            return True
        force = bool(args.get("force"))
        last = session_state.get_last_scan()
        if not last or not last.get("items"):
            print("\n  [!] 还没有扫描结果。先跑：bz scan <主题> 或 scan market [规则]")
            return True
        items, _seen_codes = [], set()
        for it in last["items"]:
            if isinstance(it, dict) and it.get("code") and str(it["code"]) not in _seen_codes:
                _seen_codes.add(str(it["code"]))
                items.append(it)
        if not items:
            print("\n  [!] 最近扫描结果里没有可用代码")
            return True
        records = {} if force else session_state.get_deep_analyzed()
        done = {c: r for c, r in records.items() if session_state.deep_record_complete(r)}
        new_items = [it for it in items if it["code"] not in done]
        skipped = [it for it in items if it["code"] in done]
        # T05-L：今天的旧版记录（无完成资格标记，修复前可能把仅行情算作已析）不再直接跳过
        unverified = [it for it in new_items if it["code"] in records]
        when = (last.get("timestamp") or "")[:16].replace("T", " ")
        try:
            _age_min = max(0, round((_dt.now() - _dt.fromisoformat(last.get("timestamp") or "")).total_seconds() / 60))
        except Exception:
            _age_min = None
        print(f"\n  最近扫描：{last.get('source', '?')}（{when}），共 {len(items)} 只")
        if _age_min is not None and _age_min > 30:
            print(f"  [!] 注意：该扫描已是 {_age_min} 分钟前的结果，行情可能已变化")
        if skipped:
            _sk = "、".join(f"{it.get('name') or it['code']}({done[it['code']].get('time', '?')}析)"
                           for it in skipped[:6])
            _more = f" 等{len(skipped)}只" if len(skipped) > 6 else ""
            print(f"  ⏭ 今天已深分析 {len(skipped)} 只，默认跳过：{_sk}{_more}")
        if unverified:
            _uv = "、".join(f"{it.get('name') or it['code']}" for it in unverified[:6])
            _uv_more = f" 等{len(unverified)}只" if len(unverified) > 6 else ""
            print(f"  ⚠ 今天另有 {len(unverified)} 只是旧版已析记录（无完成资格标记，修复前可能把"
                  f"仅行情算作已析）：本次将重新深分析（含AI调用费用）：{_uv}{_uv_more}")
        if not new_items:
            print("  ✓ 这批今天全都分析过了（不重复花AI费用）。强制重分析：l all -f")
            return True
        print(f"  将深分析 {len(new_items)} 只，每只一张简明卡（同 la），详情再单独 l <代码>")
        if budget_desc is not None:
            print(_live_budget_notice(budget_desc["budget_seconds"], "l all",
                                      after_confirm=True))
        else:
            print(_LIVE_BUDGET_HINT)
        try:
            ans = input(f"  批量深度分析 {len(new_items)} 只（每只约0.5~1分钟，含AI调用）。继续?(y/N) ")
        except (EOFError, KeyboardInterrupt):
            print("\n  已取消")
            return True
        if ans.strip().lower() not in ("y", "yes"):
            print("  已取消")
            return True
        # T05-L：l all 在用户确认之后、执行准备（账本/账户快照）之前起算
        deadline = (_monotonic() + budget_desc["budget_seconds"]
                    if budget_desc is not None else None)
        # T05-M：确认/起算之后、首只之前只加载一次配置并深拷贝冻结。冻结前预算已
        # 到期 → 不加载配置，按 T05-L 由调度器登记未开始（roster 已知）。
        _frozen, _config_desc = None, None
        if not _live_deadline_expired(deadline):
            _frozen, _frozen_ok = _freeze_live_config(_load, ai_overrides)
            if not _frozen_ok:
                return True
            _config_desc = _report_live_config(_frozen)
        src_label = last.get("source", "?")
        _codes = [str(it["code"]) for it in new_items]
        # C2/T05-H：批量任务账本——每次运行独立 run，令牌绑定全部逐项 mark
        run_id = _batch_ledger_begin(session_state, "l all", src_label, _codes,
                                     budget=budget_desc, snapshot=_config_desc)
        # N0/B2：批量请求共享同一账户事实（同版本；下一命令重新读）
        _acct_facts = None
        if not _live_deadline_expired(deadline):
            from src.data.portfolio import PortfolioManager as _PM_all
            _acct_facts = _PM_all().request_account_facts(estimate_nav=True)
        _scan_entries = [(str(it["code"]), f"{it.get('name', '') or it['code']} ({it['code']})")
                         for it in new_items]
        res = _run_live_batch_loop(
            session_state, analyze_live, ai_overrides, kind="l all", key=src_label,
            entries=_scan_entries, account_facts=_acct_facts, run_id=run_id,
            deadline=deadline, deep_source=src_label, rec_ok=bool(run_id),
            config=_frozen)
        _print_live_batch_summary(res, kind="l all", total=len(_scan_entries),
                                  run_id=run_id, budget_desc=budget_desc)
        return True

    return False

def _dispatch_scan_cli(
    mode: str,
    args: dict,
    *,
    scan_market,
    scan_events,
    scan_review,
    scan_review_import,
    load_config,
    console,
) -> bool:
    """T04-H：scan_* 相关分发助手（从 run_cli 抽出，行为等价一挪）。

    本助手执行后返回 True；非 scan 相关 mode 返回 False，交 run_cli 继续 elif。
    承接：scan_market / rules / industries / concepts / events /
    scan_review / scan_review_import。
    行为锁定见 test_t04e_dispatch_golden（含 T04-H0 扩展）。
    """
    global _ai_debug, _no_ai

    if mode == "scan_market":
        _scan_market_kwargs = {
            "rule_name": args.get("rule_name", "healthy_pullback"),
            "market_query": args.get("market_query"),
            "ai_debug": _ai_debug,
            "deep": args.get("deep", False),
            "ai_enabled": not _no_ai,
        }
        if args.get("budget") is not None:
            _scan_market_kwargs["budget_seconds"] = args["budget"]
        scan_market(**_scan_market_kwargs)
        return True

    if mode == "rules":
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
        return True

    if mode == "industries":
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
        return True

    if mode == "concepts":
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
        return True

    if mode == "events":
        scan_events(ai_debug=_ai_debug)
        return True

    if mode == "scan_review":
        # v0.8.11：扫描复盘——近N天扫描历史的涨跌验证（零AI调用，仅行情数据）
        scan_review(days=args.get("days", 7))
        return True

    if mode == "scan_review_import":
        # v0.8.11：旧扫描报告（分析报告/scan/*.md）一次性导入历史
        scan_review_import()
        return True

    return False



def _dispatch_observe_cli(
    mode: str,
    args: dict,
    *,
    show_expect,
    show_fear,
    watch_pool,
) -> bool:
    """T04-I：expect/fear/watch_pool 观察类分发助手（从 run_cli 抽出，行为等价一挪）。

    本助手执行后返回 True；非观察类 mode 返回 False，交 run_cli 继续 elif。
    承接：expect / fear / watch_pool。
    行为锁定见 test_t04e_dispatch_golden（含 T04-I0 扩展）。
    """
    if mode == "expect":
        show_expect(days=args.get("days", 30))
        return True

    if mode == "fear":
        show_fear(args)
        return True

    if mode == "watch_pool":
        # v0.8.12：观察池——WATCH 自动入池，watch 复盘引擎看入池以来表现
        watch_pool(args)
        return True

    return False



def _dispatch_ops_cli(
    mode: str,
    args: dict,
    *,
    doctor,
    diff_evidence_cmd,
    diff_list_cmd,
    batch_tasks_cmd,
    today_command,
) -> bool:
    """T04-J：doctor/diff_*/batch_tasks/today 运维诊断类分发助手（从 run_cli 抽出，行为等价一挪）。

    本助手执行后返回 True；非运维诊断类 mode 返回 False，交 run_cli 继续 elif。
    承接：doctor / diff_evidence / diff_list / batch_tasks / today。
    行为锁定见 test_t04e_dispatch_golden（含 T04-J0 扩展）。
    """
    if mode == "doctor":
        # v0.8.16：运行诊断——只读零 AI 环境体检（plan/ M6）
        doctor()
        return True

    if mode == "diff_evidence":
        # v0.8.17：分析对比——同股最近两次深分析的关键证据变化（plan/ C1）
        diff_evidence_cmd(args.get("code", ""), use_ai=bool(args.get("ai")))
        return True

    if mode == "diff_list":
        # 用户走查 2026-09-26：diff 无参数 → 可对比股票清单
        diff_list_cmd()
        return True

    if mode == "batch_tasks":
        # v0.8.17：批量任务账本——进度与失败项可见（plan/ C2）
        batch_tasks_cmd()
        return True

    if mode == "today":
        # v0.8.19：今日工作台——先持仓风险，再等条件（plan/fusion F7）
        today_command()
        return True

    return False



def _dispatch_fusion_cli(
    mode: str,
    args: dict,
    *,
    shadow_command,
    plan2_command,
    research_command,
) -> bool:
    """T04-K：shadow/plan2/research fusion 类分发助手（从 run_cli 抽出，行为等价一挪）。

    本助手执行后返回 True；非 fusion 类 mode 返回 False，交 run_cli 继续 elif。
    承接：shadow / plan2 / research。
    行为锁定见 test_t04e_dispatch_golden（含 T04-K0 扩展）。
    """
    if mode == "shadow":
        # plan/fusion 影子阶段前置：legacy vs fusion_mid/long 决策表差异观察报告
        shadow_command()
        return True

    if mode == "plan2":
        # plan/fusion F5/F7：中期/长期投资计划（草稿→确认激活）
        plan2_command(args.get("rest") or [])
        return True

    if mode == "research":
        # plan/fusion R8：单股研究工作台——中长期比较/系统草稿/待验证节点
        research_command(args.get("rest") or [])
        return True

    return False



def _dispatch_backtest_cli(
    mode: str,
    args: dict,
    *,
    run_backtest,
    run_batch_validation,
) -> bool:
    """T04-L：backtest/batch_backtest 回测类分发助手，从 run_cli 抽出行为等价一块。

    命中并执行后返回 True；非回测类 mode 返回 False，交 run_cli 继续 elif。
    衔接：backtest / batch_backtest。
    行为锁定：test_t04e_dispatch_golden（含 T04-L0 扩展）。
    """
    if mode == "backtest":
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
        return True

    if mode == "batch_backtest":
        from datetime import datetime, timedelta
        start_date = args.get("start_date") or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        end_date = args.get("end_date") or datetime.now().strftime("%Y-%m-%d")
        run_batch_validation(
            stock_codes=args["codes"],
            start_date=start_date,
            end_date=end_date,
        )
        return True

    return False


_SUPPORTED_BUDGET_MODES = ("benzong_all", "benzong_multi", "benzong_scan")
# T05-L：live 深分析批（l 多代码 / la / l all）的预算走 live 合同（单只 live 仍不支持）
_LIVE_BUDGET_MODES = ("live_multi", "live_all", "live_scan_all")

_LIVE_BUDGET_INVALID = ("  [!] 预算参数无效（需有限正数秒）；本次未开始分析"
                        "（不读取扫描结果/持仓、不调用 AI、不写账本）")

# T05-P：顶层 scan 持仓扫描的坏预算拒绝文案（与 analyze_portfolio 程序化入口同语义）
_SCAN_PORTFOLIO_BUDGET_INVALID = ("  [!] 预算参数无效（需有限正数秒）；本次未开始持仓扫描"
                                  "（不读取账户/持仓、不取数、不调用 AI）")


def _reject_budget_request(mode: str, args) -> bool:
    """T05-J fix2：run_cli 最前统一预算门（在确认/扫描/缓存/网络/AI/账本之前）。

    - 不支持的 mode 只要显式 budget 非 None（含正数/坏值）一律拒绝，不执行业务
    - 支持 mode 的坏预算（非有限正数秒）同样先拒绝（ba/bz 与 live 各按自己的合同）
    - 无 budget / budget=None：完全保持旧行为
    返回 True 表示已拒绝（run_cli 必须直接 return）。
    """
    if not isinstance(args, dict):
        return False
    budget = args.get("budget")
    if budget is None:
        return False
    if mode in _LIVE_BUDGET_MODES:
        from src.cli.session_state import live_budget_descriptor
        if live_budget_descriptor(budget) is None:
            print(_LIVE_BUDGET_INVALID)
            return True
        return False
    if mode == "scan":
        # T05-P：顶层 scan 持仓扫描预算（同门校验有限正数秒；合法放行，坏值业务前拒绝）。
        from src.core.benzong.batch_scorer import budget_descriptor
        if budget_descriptor(budget) is None:
            print(_SCAN_PORTFOLIO_BUDGET_INVALID)
            return True
        return False
    if mode == "scan_market":
        # T05-O：scan market deep 自动深度分析预算（同门校验有限正数秒；非 deep 拒绝）。
        from src.core.benzong.batch_scorer import budget_descriptor
        if budget_descriptor(budget) is None:
            print(_SCAN_MARKET_BUDGET_INVALID)
            return True
        if not (isinstance(args, dict) and args.get("deep") is True):
            print(f"  [!] 该命令不支持 --budget（{_SCAN_MARKET_BUDGET_SCOPE}）；本次未执行")
            return True
        return False
    if mode not in _SUPPORTED_BUDGET_MODES:
        print(f"  [!] 该命令不支持 --budget（{_BUDGET_SCOPE}）；本次未执行")
        return True
    from src.core.benzong.batch_scorer import budget_descriptor
    if budget_descriptor(budget) is None:
        if mode == "benzong_scan":
            print(_SCAN_BUDGET_INVALID)
        else:
            print("  [!] 预算参数无效（需有限正数秒）；本次未开始评分"
                  "（不读取扫描结果/缓存、不调用 AI、不写账本）")
        return True
    return False


def run_cli(mode: str, args: dict):
    """调用 CLI 主程序执行分析"""
    global _ai_debug, _no_ai

    # T05-J fix2：预算统一门必须在任何业务动作/import 之前（防程序化绕过 parse）
    if _reject_budget_request(mode, args):
        return

    from src.cli.main import (
        analyze_live, run_backtest, run_batch_validation,
        manage_positions, load_config, analyze_portfolio,
        console, scan_market, scan_events, show_expect, show_fear,
        scan_review, scan_review_import, watch_pool, doctor, diff_evidence_cmd,
        diff_list_cmd, batch_tasks_cmd, today_command, shadow_command, plan2_command,
        research_command,
    )

    ai_overrides = {}
    if _no_ai:
        ai_overrides["disable_ai"] = True

    if _dispatch_live_cli(mode, args, analyze_live, ai_overrides,
                          load_config=load_config):
        pass

    elif mode == "scan":
        if args.get("budget") is not None:
            analyze_portfolio(ai_overrides=ai_overrides, ai_debug=_ai_debug,
                              budget_seconds=args["budget"])
        else:
            analyze_portfolio(ai_overrides=ai_overrides, ai_debug=_ai_debug)

    elif _dispatch_scan_cli(
        mode,
        args,
        scan_market=scan_market,
        scan_events=scan_events,
        scan_review=scan_review,
        scan_review_import=scan_review_import,
        load_config=load_config,
        console=console,
    ):
        pass

    elif _dispatch_observe_cli(
        mode,
        args,
        show_expect=show_expect,
        show_fear=show_fear,
        watch_pool=watch_pool,
    ):
        pass

    elif _dispatch_ops_cli(
        mode,
        args,
        doctor=doctor,
        diff_evidence_cmd=diff_evidence_cmd,
        diff_list_cmd=diff_list_cmd,
        batch_tasks_cmd=batch_tasks_cmd,
        today_command=today_command,
    ):
        pass

    elif _dispatch_fusion_cli(
        mode,
        args,
        shadow_command=shadow_command,
        plan2_command=plan2_command,
        research_command=research_command,
    ):
        pass

    elif mode == "noai":
        _no_ai = not _no_ai
        status = "已禁用（纯技术面）" if _no_ai else "已启用"
        print(f"  AI调节层: {status}")

    elif mode == "debug":
        _ai_debug = not _ai_debug
        status = "开启" if _ai_debug else "关闭"
        print(f"  AI调试模式: {status}")

    elif _dispatch_backtest_cli(
        mode,
        args,
        run_backtest=run_backtest,
        run_batch_validation=run_batch_validation,
    ):
        pass

    elif _dispatch_pos_cli(mode, args, manage_positions):
        pass

    elif mode == "chat":
        # 子进程化（ISS-060遗留）：chat退出即OS整体回收（含torch等导入开销~440MB，
        # 进程内无法卸载模块），主进程零残留、永不import chat/RAG/torch。
        # 仅交互终端走子进程：管道模式下父进程input()预读缓冲会导致子进程丢失stdin行
        if sys.stdin.isatty():
            completed = subprocess.run([find_python(), "-m", "src.chat"], cwd=os.getcwd())
            if completed.returncode != 0:
                print(f"\n  [!] chat 子进程异常退出（code={completed.returncode}）")
        else:
            try:
                from src.chat.agent import run_chat_repl
                from src.cli.main import load_config
            except ImportError as e:
                print(f"\n  [!] Chat 依赖缺失: {e}")
                print("  [!] 请先安装依赖（事实源 requirements.txt）: pip install -r requirements.txt")
                return
            run_chat_repl(load_config())

    elif _dispatch_benzong_cli(mode, args):
        pass

    else:
        # 防御兜底：mode 名是 chat run_command 桥 + confirm 硬门的协议，
        # 正常 parse_input 不会产生未知 mode；出现说明桥接层被改坏，宁告警勿静默
        print(f"  [!] 未知执行模式: {mode}（内部协议名，请检查 parse_input/run_cli 派发）")


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
        except SystemExit:
            # v0.8.7.5 审计修复 A12：CLI 内部（如 l <代码> 数据获取失败 sys.exit(1)）会抛
            # SystemExit，继承 BaseException，except Exception 抓不住 → 一次网络抖动把用户
            # 踢出 REPL。REPL 内吞掉退出码回到提示符；独立 CLI 进程的退出码行为不受影响。
            print("\n  [命令异常退出] 数据源可能暂时不可用，可稍后重试（不影响 REPL，输入 h 查看用法）")
        except Exception as e:
            print(f"\n  [错误] {e}")
            print("  输入 h 查看用法")

        # v0.8.7.3 方案C：命令结束后输出人话汇总（收集的是本条命令期间产生的降级告警）
        try:
            from src.cli.plain_errors import drain_new, render_summary
            _summary = render_summary(drain_new())
            if _summary:
                print(_summary)
        except Exception:
            pass  # 汇总渲染失败不影响主流程

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
