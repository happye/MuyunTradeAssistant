"""跳法A 阶段4：五年回测验证（2020-2024，手动指定 mode）

验证目标：跳法A 的气宗持有纪律 + 高位止盈3维度，在历史五年是否真能
"拿住牛股 + 逃顶见顶股"，净效果是否为正。

方法（路 A，诚实可控）：
- 不用 rule_scorer 自动定 mode（issue_041 验证其三维失效+时点错位，不可信）
- 人工标注每只股当年该是气宗(牛股该拿)还是非气宗(见顶/平庸该走)
- 每只跑两遍：baseline(无mode) vs 气宗(qizong)，对比 Δ
- 五年分年份看稳定性（2020新能源/2021白酒见顶/2022煤炭/2023AI/2024高股息）

案例选择（基于公开历史，代表不同行情）：
- 牛股组（该气宗拿住）：当年主升浪的龙头
- 见顶组（该逃顶不死扛）：当年见大顶后转跌的
- 对照组（平庸，测假阳性）：当年横盘/阴跌的

注：
1. 高位止盈3维度在回测里大部分不触发（turnover/announcements 历史不可得），
   本脚本主要验证"气宗持有纪律"，逃顶信号需 live 路径另验
2. 气宗 max_hold=180 天，可能跨年；回测按完整年度跑
3. 人工 mode 标注基于公开历史，非 AI 评分，有主观性——但比 rule_scorer 可信

跑法：
    PYTHONUTF8=1 PYTHONPATH=. uv run python tests/test_jumpA_backtest_5year.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
# 金融 API 不走代理
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)
os.environ["TQDM_DISABLE"] = "1"

from src.cli.main import load_config, normalize_stock_code
from src.core.backtest_engine import BacktestEngine
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

# 单只股回测的总超时（秒）。某只股卡死（如比亚迪2020边界case）时跳过继续下一只，
# 不让一只股拖垮全盘。正常一整年回测约 5-15 分钟，给 20 分钟裕量。
PER_STOCK_TIMEOUT = 1200

# 五年案例：(code, name, 年份, 组别, 人工mode标注)
# 组别：bull=牛股该气宗拿住 / top=见顶该逃顶 / flat=平庸对照
# mode标注：qizong=该气宗 / none=该非气宗(走原策略)
# 选股依据公开历史行情，非 AI 评分
CASES = [
    # 2020：新能源主升浪
    ("300750", "宁德时代", "2020", "bull", "qizong"),
    ("002594", "比亚迪",   "2020", "bull", "qizong"),
    # 2021：白酒/消费见顶
    ("600519", "贵州茅台", "2021", "top",  "none"),   # 2021初见顶后回落
    ("000858", "五粮液",   "2021", "top",  "none"),
    # 2022：煤炭高股息逆势
    ("601088", "中国神华", "2022", "bull", "qizong"),
    ("601225", "陕西煤业", "2022", "bull", "qizong"),
    # 2023：AI 算力主题
    ("002230", "科大讯飞", "2023", "bull", "qizong"),
    ("688256", "寒武纪",   "2023", "bull", "qizong"),
    # 2024：高股息 + 出海
    ("601857", "中国石油", "2024", "bull", "qizong"),
    ("600188", "兖矿能源", "2024", "bull", "qizong"),
    # 对照组（横盘/阴跌，测气宗是否会误拿死扛）
    ("601888", "中国中免", "2022", "flat", "none"),   # 持续阴跌
    ("000661", "长春高新", "2022", "flat", "none"),   # 集采阴跌

    # === 批次2：笨总标准选股，大中小盘各3，2023-2025（网络搜索+Baostock核实）===
    # 大盘（>1000亿）
    ("300308", "中际旭创", "2024", "bull_large", "jianzong"),  # AI光模块一波流
    ("300750", "宁德时代", "2023", "bull_large", "qizong"),    # 锂电长牛(同年不同段)
    ("601088", "中国神华", "2023", "bull_large", "qizong"),    # 红利慢牛
    # 中盘（200-1000亿）
    ("300274", "阳光电源", "2023", "bull_mid", "jianzong"),    # 光储一波流
    ("601138", "工业富联", "2024", "bull_mid", "jianzong"),    # AI服务器一波流
    ("600276", "恒瑞医药", "2024", "bull_mid", "qizong"),      # 创新药长牛
    # 小盘（<200亿，把握度较低，Baostock核实代码真实）
    ("603662", "柯力传感", "2024", "bull_small", "jianzong"),  # 机器人主题一波流
    ("301105", "鸿铭股份", "2023", "bull_small", "qizong"),    # 包装设备小盘慢牛
    ("301550", "斯菱智驱", "2024", "bull_small", "jianzong"),  # 汽车轴承一波流（实名斯菱智驱）
]

CAPITAL = 200000.0

# 环境变量 BATCH2_ONLY=1 时只跑批次2(大中小盘9支),跳过原12支
_BATCH2_ONLY = os.environ.get("BATCH2_ONLY") == "1"
_ACTIVE_CASES = CASES[12:] if _BATCH2_ONLY else CASES


def _year_range(year: str):
    return f"{year}-01-01", f"{year}-12-31"


def run_one(code, year, force_mode=None):
    """跑单股回测，带总超时保护。超时返回 None。

    force_mode: "qizong"/"jianzong"/None。None=baseline(无mode)。
    """
    def _run():
        cfg = load_config()
        start, end = _year_range(year)
        qizong_codes = {code} if force_mode == "qizong" else None
        jianzong_codes = {code} if force_mode == "jianzong" else None
        eng = BacktestEngine(
            stock_code=normalize_stock_code(code),
            start_date=start, end_date=end, initial_capital=CAPITAL,
            execution_mode="framework_strict",
            layer_mode="decision_strategy_execution",
            skills_dir=cfg.get("skills", {}).get("dir", "./src/skills"),
            signal_weights=cfg.get("decision", {}).get("signal_weights", None),
            skill_types=cfg.get("skills", {}).get("types", None),
            entry_exit_config=cfg.get("entry_exit", None),
            enable_trade_plan=True,
            qizong_codes=qizong_codes,    # 手动指定气宗股
            jianzong_codes=jianzong_codes,  # 手动指定剑宗股
            benzong_auto_mode=False,    # 关闭自动评分定mode（用人工标注）
        )
        res = eng.run()
        return res, getattr(eng, "_plan_stats", {})
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_run)
        try:
            return fut.result(timeout=PER_STOCK_TIMEOUT)
        except FuturesTimeout:
            return None, None  # 超时，调用方处理


def main():
    print("=" * 90)
    print("  跳法A 阶段4：五年回测验证（2020-2024，手动指定 mode）" + ("【批次2:大中小盘9支】" if _BATCH2_ONLY else ""))
    print("=" * 90)
    print("  baseline: 无 mode（PlanGuard 仅压 weak_sell）")
    print("  气宗:     压 trend_exit+take_profit_trim，max_hold=180")
    print("  剑宗:     max_hold=30，紧止损(1×ATR)，破线即走")
    print("  none:     人工判定不该定mode，两模式都走 baseline")
    print()

    rows = []
    for code, name, year, group, mode_label in _ACTIVE_CASES:
        print(f"▶ {year} {code} {name} [{group}] 标注mode={mode_label}", flush=True)
        try:
            # 有 mode 标注的股(qizong/jianzong)：baseline vs 强制mode对比；none股：只跑baseline
            base, base_stats = run_one(code, year, force_mode=None)
            if base is None:
                print(f"    ⏱ baseline 超时(>{PER_STOCK_TIMEOUT}s)，跳过该股", flush=True)
                rows.append({"code": code, "name": name, "year": year, "group": group,
                             "mode": mode_label, "base_ret": None, "qz_ret": None,
                             "delta": None, "bench": None, "base_trades": 0, "qz_trades": 0,
                             "qz_suppressed": 0, "timeout": True})
                continue
            if mode_label in ("qizong", "jianzong"):
                qz, qz_stats = run_one(code, year, force_mode=mode_label)
                if qz is None:
                    print(f"    ⏱ {mode_label}超时，用 baseline 结果", flush=True)
                    qz, qz_stats = base, base_stats
            else:
                qz, qz_stats = base, base_stats  # none股，两模式同
            delta = qz.total_return_pct - base.total_return_pct
            rows.append({
                "code": code, "name": name, "year": year, "group": group,
                "mode": mode_label,
                "base_ret": base.total_return_pct, "qz_ret": qz.total_return_pct,
                "delta": delta,
                "bench": base.benchmark_return_pct,
                "base_trades": getattr(base, "total_trades", 0) or 0,
                "qz_trades": getattr(qz, "total_trades", 0) or 0,
                "qz_suppressed": qz_stats.get("weak_sells_suppressed_by_guard", 0) if qz_stats else 0,
                "timeout": False,
            })
            print(f"    baseline {base.total_return_pct:+.2f}% → {mode_label} {qz.total_return_pct:+.2f}% "
                  f"Δ {delta:+.2f}pp (压制{qz_stats.get('weak_sells_suppressed_by_guard',0) if qz_stats else 0}次)", flush=True)
        except Exception as e:
            print(f"    ✗ 失败: {type(e).__name__}: {e}", flush=True)
            rows.append({"code": code, "name": name, "year": year, "group": group,
                         "mode": mode_label, "base_ret": None, "qz_ret": None,
                         "delta": None, "bench": None, "base_trades": 0, "qz_trades": 0,
                         "qz_suppressed": 0, "timeout": False})

    # 汇总
    print()
    print("=" * 90)
    print(f"  {'年份':<6}{'代码':<8}{'名称':<10}{'组':<12}{'mode':<8}{'基准%':>8}{'base%':>9}{'mode%':>9}{'Δpp':>8}{'压制':>5}")
    print("-" * 90)
    bull_large_deltas, bull_mid_deltas, bull_small_deltas = [], [], []
    top_deltas, flat_deltas = [], []
    for r in rows:
        if r["base_ret"] is None:
            tag = "⏱超时" if r.get("timeout") else "✗失败"
            print(f"  {r['year']:<6}{r['code']:<8}{r['name'][:8]:<10}{r['group']:<12}{r['mode']:<8}  {tag}")
            continue
        print(f"  {r['year']:<6}{r['code']:<8}{r['name'][:8]:<10}{r['group']:<12}{r['mode']:<8}"
              f"{r['bench'] or 0:>8.1f}{r['base_ret']:>9.2f}{r['qz_ret']:>9.2f}"
              f"{r['delta']:>+8.2f}{r['qz_suppressed']:>5}")
        g = r["group"]
        if g in ("bull", "bull_large"):
            bull_large_deltas.append(r["delta"])
        elif g == "bull_mid":
            bull_mid_deltas.append(r["delta"])
        elif g == "bull_small":
            bull_small_deltas.append(r["delta"])
        elif g == "top":
            top_deltas.append(r["delta"])
        else:
            flat_deltas.append(r["delta"])

    print("-" * 90)
    print()
    print("  ── 分组统计 ──")
    if bull_large_deltas:
        avg = sum(bull_large_deltas) / len(bull_large_deltas)
        pos = sum(1 for d in bull_large_deltas if d > 0)
        print(f"  牛股-大盘组 {len(bull_large_deltas)}只: 平均Δ {avg:+.2f}pp, 正向{pos}/{len(bull_large_deltas)}")
    if bull_mid_deltas:
        avg = sum(bull_mid_deltas) / len(bull_mid_deltas)
        pos = sum(1 for d in bull_mid_deltas if d > 0)
        print(f"  牛股-中盘组 {len(bull_mid_deltas)}只: 平均Δ {avg:+.2f}pp, 正向{pos}/{len(bull_mid_deltas)}")
    if bull_small_deltas:
        avg = sum(bull_small_deltas) / len(bull_small_deltas)
        pos = sum(1 for d in bull_small_deltas if d > 0)
        print(f"  牛股-小盘组 {len(bull_small_deltas)}只: 平均Δ {avg:+.2f}pp, 正向{pos}/{len(bull_small_deltas)}")
    if top_deltas:
        avg = sum(top_deltas) / len(top_deltas)
        print(f"  见顶组(该逃顶) {len(top_deltas)}只: 平均Δ {avg:+.2f}pp")
        print(f"    → 预期：接近0或略负（气宗不该死扛见顶股，靠致命止损/时间止损兜底）")
    if flat_deltas:
        avg = sum(flat_deltas) / len(flat_deltas)
        print(f"  对照组(平庸) {len(flat_deltas)}只: 平均Δ {avg:+.2f}pp")
        print(f"    → 预期：接近0（气宗不该误拿平庸股死扛）")
    print()
    print("  ── 判读 ──")
    print("    · 牛股组显著正 + 见顶/对照组不恶化 → 跳法A 气宗方向对，值得继续")
    print("    · 牛股组无改善 → 气宗压不住（止盈/止损仍在卖），光持有不够")
    print("    · 牛股组正但见顶组大幅恶化 → 净效果存疑，需更精准区分回调vs见顶")
    print("    · 注：高位止盈3维度在回测里大部分不触发（历史换手/公告不可得），")
    print("      本脚本主要验证气宗持有纪律，逃顶信号需 live 路径另验")
    print("=" * 90)


if __name__ == "__main__":
    main()
