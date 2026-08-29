"""小盘组止损参数对照实验（2026-08-29，修复后口径基线上）

背景：v0.8.7.5 修复后重跑，小盘组 3 只平均 Δ-16.71pp（柯力 -29.85 / 斯菱 -23.97），
用户主仓是小盘股（几十元价位，无科创/创业板权限），此问题必须深挖。

假设（H1-H3，一次实验全部检验）：
- H1 止损太紧：小盘高波动股 1×ATR 止损在正常波动中被打掉（假止损），错过回补
       -> 放宽到 1.5×/2.0× 看 Δ 是否修复
- H2 持有期太短：30 天强制到期出清，把一波流行情腰斩
       -> 延长到 45/60 天看 Δ
- H3 组合最优：宽止损 + 长持有

方法：柯力传感 + 斯菱智驱（两只恶化最狠的 jianzong 小盘）× 网格 6 档参数。
每格 = baseline(none) 与 参数档 各跑一遍，Δ = 参数档收益 - baseline 收益。
环境变量注入（MUYUN_JIANZONG_ATR_MULT / MUYUN_JIANZONG_HOLD_DAYS），
generator 默认值不变，实盘零影响。

跑法（约 20-40 分钟）：
    PYTHONUTF8=1 PYTHONPATH=. uv run python tests/backtest/test_smallcap_stop_compare.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)
os.environ["TQDM_DISABLE"] = "1"

CASES = [
    ("603662", "柯力传感", "2024"),   # Δ-29.85 恶化最狠
    ("301550", "斯菱智驱", "2024"),   # Δ-23.97
]

# 参数网格：tag -> (ATR倍数, 持有天数)
GRID = [
    ("基准(1.0x,30d)", 1.0, 30),     # 当前线上值，作对照锚
    ("宽止损A(1.5x,30d)", 1.5, 30),
    ("宽止损B(2.0x,30d)", 2.0, 30),
    ("长持有A(1.0x,45d)", 1.0, 45),
    ("长持有B(1.0x,60d)", 1.0, 60),
    ("组合(1.5x,45d)", 1.5, 45),
]

PER_STOCK_TIMEOUT = 1200
CAPITAL = 200000.0


def run_once(code, year, atr_mult, hold_days):
    """跑一遍回测。环境变量注入参数；结束清除。"""
    os.environ["MUYUN_JIANZONG_ATR_MULT"] = str(atr_mult)
    os.environ["MUYUN_JIANZONG_HOLD_DAYS"] = str(hold_days)
    try:
        from src.cli.main import load_config, load_pyramid_config, normalize_stock_code
        from src.core.backtest_engine import BacktestEngine
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
        cfg = load_config()
        eng = BacktestEngine(
            stock_code=normalize_stock_code(code),
            start_date=f"{year}-01-01", end_date=f"{year}-12-31",
            initial_capital=CAPITAL,
            execution_mode="framework_strict",
            layer_mode="decision_strategy_execution",
            skills_dir=cfg.get("skills", {}).get("dir", "./src/skills"),
            signal_weights=cfg.get("decision", {}).get("signal_weights", None),
            skill_types=cfg.get("skills", {}).get("types", None),
            entry_exit_config=cfg.get("entry_exit", None),
            pyramid_config=load_pyramid_config(cfg),
            enable_trade_plan=True,
            jianzong_codes={code},
            benzong_auto_mode=False,
        )
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(eng.run)
            try:
                return fut.result(timeout=PER_STOCK_TIMEOUT)
            except FuturesTimeout:
                return None
    finally:
        os.environ.pop("MUYUN_JIANZONG_ATR_MULT", None)
        os.environ.pop("MUYUN_JIANZONG_HOLD_DAYS", None)


def main():
    print("=" * 88)
    print("  小盘组止损参数对照实验（修复后口径，2026-08-29）")
    print("=" * 88)

    # Step 1: 各股 baseline（无 mode）一遍
    baselines = {}
    for code, name, year in CASES:
        print(f"▶ baseline {code} {name} {year}")
        from src.cli.main import load_config, load_pyramid_config, normalize_stock_code
        from src.core.backtest_engine import BacktestEngine
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
        cfg = load_config()
        eng = BacktestEngine(
            stock_code=normalize_stock_code(code),
            start_date=f"{year}-01-01", end_date=f"{year}-12-31",
            initial_capital=CAPITAL,
            execution_mode="framework_strict",
            layer_mode="decision_strategy_execution",
            skills_dir=cfg.get("skills", {}).get("dir", "./src/skills"),
            signal_weights=cfg.get("decision", {}).get("signal_weights", None),
            skill_types=cfg.get("skills", {}).get("types", None),
            entry_exit_config=cfg.get("entry_exit", None),
            pyramid_config=load_pyramid_config(cfg),
            enable_trade_plan=True,
            benzong_auto_mode=False,
        )
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(eng.run)
            try:
                baselines[code] = fut.result(timeout=PER_STOCK_TIMEOUT).total_return_pct
            except FuturesTimeout:
                baselines[code] = None
        print(f"    baseline {baselines[code]}")

    # Step 2: 网格
    rows = []
    for tag, atr, hold in GRID:
        for code, name, year in CASES:
            res = run_once(code, year, atr, hold)
            if res is None:
                rows.append((tag, code, name, None, None))
                print(f"  {tag:18} {name} [超时跳过]")
                continue
            delta = res.total_return_pct - baselines[code]
            rows.append((tag, code, name, res.total_return_pct, delta))
            trades = getattr(res, "total_trades", "?")
            print(f"  {tag:18} {name} 收益 {res.total_return_pct:+.2f}%  Δ{delta:+.2f}pp  交易{trades}")

    print("=" * 88)
    print(f"{'参数档':<20}{'柯力Δ':>10}{'斯菱Δ':>10}{'两股平均Δ':>12}")
    for tag, *_ in GRID:
        ds = [d for t, c, n, r, d in rows if t == tag and d is not None]
        if len(ds) == len(CASES):
            print(f"{tag:<20}{ds[0]:>+10.2f}{ds[1]:>+10.2f}{sum(ds)/len(ds):>+12.2f}")
    print("=" * 88)
    print("判读：平均Δ相对基准档的改善幅度 = 假设成立程度")
    print("  H1 止损太紧成立 -> 1.5x/2.0x 档 Δ 显著改善")
    print("  H2 持有太短成立 -> 45d/60d 档 Δ 显著改善")
    print("  若全部不改善 -> 问题不在参数，在 jianzong 的卖出纪律本身（需看交易明细）")


if __name__ == "__main__":
    main()
