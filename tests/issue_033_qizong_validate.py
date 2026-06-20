"""ISS-033+笨总 气宗持有回测验证（v0.8.6.3）

验证问题：笨总能选牛股但分不清回调vs见顶。气宗模式（压 trend_exit 拿住）能否
降低牛市错过率？净效果如何（牛股改善 vs 见顶股恶化）？

方法：5 只笨总高分股（4 只 ISS-027 卖飞牛股 + 中免对照），每只跑两遍 2024 全年：
  baseline: 原策略（PlanGuard 仅压 weak_sell）
  qizong:   气宗模式（PlanGuard 压 weak_sell + trend_exit，max_hold=180）
对比收益%/超额%/交易次数。

注：笨总评分是当前快照(2026-06)非回测时点(2024)，但龙头/纯度稳定，作粗略代理。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)
os.environ["TQDM_DISABLE"] = "1"

from src.cli.main import load_config, load_pyramid_config, normalize_stock_code
from src.core.backtest_engine import BacktestEngine

STOCKS = [
    ("300750", "宁德时代", "卖飞牛股"),
    ("300059", "东方财富", "卖飞牛股"),
    ("601398", "工商银行", "卖飞牛股"),
    ("002475", "立讯精密", "卖飞牛股"),
    ("601888", "中国中免", "对照(转熊)"),
]
START, END, CAPITAL = "2024-01-01", "2024-12-31", 200000.0


def run_one(code, qizong_codes=None):
    cfg = load_config()
    eng = BacktestEngine(
        stock_code=normalize_stock_code(code),
        start_date=START, end_date=END, initial_capital=CAPITAL,
        execution_mode="framework_strict",
        layer_mode="decision_strategy_execution",
        skills_dir=cfg.get("skills", {}).get("dir", "./src/skills"),
        signal_weights=cfg.get("decision", {}).get("signal_weights", None),
        skill_types=cfg.get("skills", {}).get("types", None),
        entry_exit_config=cfg.get("entry_exit", None),
        pyramid_config=load_pyramid_config(cfg),
        enable_trade_plan=True,
        qizong_codes=qizong_codes,
    )
    res = eng.run()
    return res


def main():
    print("=" * 78)
    print("  ISS-033+笨总 气宗持有回测验证（2024全年，5只笨总高分股）")
    print("=" * 78)
    print(f"  baseline: PlanGuard 仅压 weak_sell（原策略）")
    print(f"  qizong:   气宗模式 压 weak_sell+trend_exit, max_hold=180")
    print()

    rows = []
    for code, name, group in STOCKS:
        print(f"▶ {code} {name} [{group}]")
        try:
            base = run_one(code, qizong_codes=None)
            qz = run_one(code, qizong_codes={code})
            rows.append({
                "code": code, "name": name, "group": group,
                "base_ret": base.total_return_pct, "qz_ret": qz.total_return_pct,
                "bench": base.benchmark_return_pct,
                "base_trades": getattr(base, "total_trades", 0) or 0,
                "qz_trades": getattr(qz, "total_trades", 0) or 0,
            })
            print(f"    baseline {base.total_return_pct:+.2f}% ({getattr(base,'total_trades',0)}笔) → "
                  f"气宗 {qz.total_return_pct:+.2f}% ({getattr(qz,'total_trades',0)}笔) "
                  f"Δ {qz.total_return_pct - base.total_return_pct:+.2f}pp")
        except Exception as e:
            print(f"    ✗ 失败: {type(e).__name__}: {e}")
            rows.append({"code": code, "name": name, "group": group,
                         "base_ret": None, "qz_ret": None, "bench": None,
                         "base_trades": 0, "qz_trades": 0})

    print()
    print("=" * 78)
    print(f"  {'代码':<8}{'名称':<10}{'组':<14}{'基准%':>8}{'baseline%':>11}{'气宗%':>9}{'Δpp':>8}{'笔base/气宗':>12}")
    print("-" * 78)
    miss_deltas, contrast_deltas = [], []
    for r in rows:
        if r["base_ret"] is None:
            print(f"  {r['code']:<8}{r['name'][:8]:<10}{r['group']:<14}  失败")
            continue
        delta = r["qz_ret"] - r["base_ret"]
        print(f"  {r['code']:<8}{r['name'][:8]:<10}{r['group']:<14}{r['bench'] or 0:>8.2f}"
              f"{r['base_ret']:>11.2f}{r['qz_ret']:>9.2f}{delta:>+8.2f}"
              f"{str(r['base_trades'])+'/'+str(r['qz_trades']):>12}")
        if "卖飞" in r["group"]:
            miss_deltas.append(delta)
        else:
            contrast_deltas.append(delta)

    print("-" * 78)
    if miss_deltas:
        avg_miss = sum(miss_deltas) / len(miss_deltas)
        print(f"  卖飞牛股组平均 Δ: {avg_miss:+.2f}pp （正=气宗改善, 负=恶化）")
    if contrast_deltas:
        avg_ctr = sum(contrast_deltas) / len(contrast_deltas)
        print(f"  对照(转熊)组平均 Δ: {avg_ctr:+.2f}pp （负=气宗让见顶股恶化, 预期代价）")
    print()
    print("  判读：")
    print("    · 卖飞组显著正 + 对照组恶化可控 → 气宗方向对，值得 pivot 笨总当主驾")
    print("    · 卖飞组无改善 → 气宗也压不住(止盈/止损仍在卖)，光持有不够")
    print("    · 卖飞组正但对照组更负 → 净效果存疑，需更精准区分回调vs见顶")
    print("=" * 78)


if __name__ == "__main__":
    main()
