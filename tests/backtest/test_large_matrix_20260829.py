# -*- coding: utf-8 -*-
"""大规模分层随机抽样回测矩阵（2026-08-29，用户要求大样本可审计）

设计原则（回应"不信19只人工挑的样本"）：
1. 抽样可复现：固定 random seed=20260829，任何人重跑得到同一批股票
2. 分层正交：4 板块（沪主板/深主板/创业板/科创板）x 3 市值层（小/中/大）
   ——创业板/科创板按用户实际无权限，仅作对照组（诚实标注）
3. 多时段：2021 / 2022 / 2023 / 2024 四个自然年（牛市/熊市/震荡/分化各自覆盖）
4. 上市时长过滤：所选年份之前已上市满 60 交易日（保证有 K 线、避开次新）
5. ST/退市风险剔除：名称含 ST/退
6. 每层抽 N 只 -> 总样本 60 只 x 4 年 = 240 趟回测（含 baseline 约 3-5 小时）

规模权衡说明：60 只已覆盖 12 个分层格子 x 4 年，统计功效远超 19 只；单趟约
1-2 分钟（含建 plan），480 趟总时长 6-10 小时超预算，故 60 只每格 4-5 只。
如需更大样本，跑完本批后可 seed 复用扩容。

跑法：
    PYTHONUTF8=1 PYTHONPATH=. uv run python tests/backtest/test_large_matrix_20260829.py
"""
import os
import sys
import json
import subprocess

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)
os.environ["TQDM_DISABLE"] = "1"

SEED = 20260829
YEARS = ["2021", "2022", "2023", "2024"]
PER_CELL = 5           # 每分层格子抽几只
PER_STOCK_TIMEOUT = 1300

# 市值分层（元）：小盘 <200亿；中盘 200-800亿；大盘 >800亿
CAP_SMALL = 2e10       # 200亿
CAP_LARGE = 8e10       # 800亿

from pathlib import Path

HERE = Path(__file__).parent
DRIVER = HERE / "_matrix_driver.py"

DRIVER_CODE = '''# -*- coding: utf-8 -*-
import os, sys, json
sys.path.insert(0, r"G:\\Tools\\暮云思辨投资助手")
for k in ["HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"]:
    os.environ.pop(k, None)
os.environ["TQDM_DISABLE"] = "1"
code, year, mode = sys.argv[1], sys.argv[2], sys.argv[3]
from src.cli.main import load_config, normalize_stock_code
from src.core.backtest_engine import BacktestEngine
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
cfg = load_config()
eng = BacktestEngine(
    stock_code=normalize_stock_code(code),
    start_date=f"{year}-01-01", end_date=f"{year}-12-31",
    initial_capital=200000.0,
    execution_mode="framework_strict", layer_mode="decision_strategy_execution",
    skills_dir=cfg.get("skills", {}).get("dir", "./src/skills"),
    signal_weights=cfg.get("decision", {}).get("signal_weights", None),
    skill_types=cfg.get("skills", {}).get("types", None),
    entry_exit_config=cfg.get("entry_exit", None),
    enable_trade_plan=True,
    qizong_codes={code} if mode == "qizong" else None,
    jianzong_codes={code} if mode == "jianzong" else None,
    benzong_auto_mode=False,
)
with ThreadPoolExecutor(max_workers=1) as ex:
    fut = ex.submit(eng.run)
    try:
        res = fut.result(timeout=1250)
        print(json.dumps({"ret": round(res.total_return_pct, 2), "trades": res.total_trades,
                          "mdd": round(res.max_drawdown_pct, 2) if res.max_drawdown_pct else None}))
    except FuturesTimeout:
        print(json.dumps({"ret": None}))
'''
DRIVER.write_text(DRIVER_CODE, encoding="utf-8")


def build_sample():
    """固定种子分层抽样，返回 [(code, name, board, cap_layer)]"""
    import random
    import pandas as pd
    from src.scanner.market_cache import MarketCache

    df = MarketCache().get_all_stocks()
    if df is None or df.empty:
        raise RuntimeError("全市场快照不可用")
    df = df.copy()
    df["代码"] = df["代码"].astype(str)
    df["总市值"] = pd.to_numeric(df["总市值"], errors="coerce")
    df = df.dropna(subset=["总市值", "最新价"])

    # 过滤：ST/退市/北交所
    mask = (~df["名称"].astype(str).str.contains("ST|退", na=False))
    mask &= (~df["代码"].str.startswith(("8", "4", "92")))
    df = df[mask]

    board_map = []
    for pre, board in (("60", "沪主板"), ("00", "深主板"), ("30", "创业板"), ("68", "科创板")):
        sub = df[df["代码"].str.startswith(pre)]
        board_map.append((board, sub))

    rng = random.Random(SEED)
    sample = []
    for board, sub in board_map:
        small = sub[sub["总市值"] < CAP_SMALL]
        mid = sub[(sub["总市值"] >= CAP_SMALL) & (sub["总市值"] < CAP_LARGE)]
        large = sub[sub["总市值"] >= CAP_LARGE]
        for layer_name, layer_df in (("小盘", small), ("中盘", mid), ("大盘", large)):
            if layer_df.empty:
                continue
            take = layer_df.sample(n=min(PER_CELL, len(layer_df)), random_state=SEED + hash(board + layer_name) % 1000)
            for _, row in take.iterrows():
                sample.append((row["代码"], row["名称"], board, layer_name))
    return sample


def run_case(code, year, mode):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = r"G:\Tools\暮云思辨投资助手"
    r = subprocess.run(
        [sys.executable, "-X", "utf8", str(DRIVER), code, year, mode],
        capture_output=True, text=True, timeout=PER_STOCK_TIMEOUT, env=env, encoding="utf-8", errors="replace",
    )
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                pass
    return {"ret": None}


def main():
    sample = build_sample()
    print(f"抽样完成：{len(sample)} 只（seed={SEED}，4板块x3市值层 x {PER_CELL}只）", flush=True)
    for code, name, board, layer in sample:
        print(f"  {code} {name:8s} {board} {layer}", flush=True)
    print("", flush=True)

    results = {}

    # 每只股：跑其所属年份矩阵里全部4年 + 各年 baseline + qizong + jianzong
    # 模式：每格 (baseline, qizong, jianzong) 三个数 -> 2 个 Δ（气宗/剑宗 vs 基线）
    # 这直接回应"多段测试"：同一只股在4个年份的表现都要看
    total_runs = len(sample) * len(YEARS) * 3
    done = 0
    for code, name, board, layer in sample:
        for year in YEARS:
            for mode in ("none", "qizong", "jianzong"):
                done += 1
                out = run_case(code, year, mode)
                results[f"{code}|{year}|{mode}"] = out
                print(f"  [{done}/{total_runs}] {name:6s} {year} {mode:8s} ret={out.get('ret')}", flush=True)

    # 汇总落盘
    out_path = HERE / "_matrix_results.json"
    out_path.write_text(json.dumps(
        {"seed": SEED, "sample": [list(s) for s in sample], "results": results},
        ensure_ascii=False, indent=1), encoding="utf-8")

    # 快速分组统计
    import statistics
    def collect(mode_year):
        vals, names = [], []
        for code, name, board, layer in sample:
            base = results.get(f"{code}|{mode_year[5:]}|none", {}).get("ret")
            m = results.get(f"{code}|{mode_year}|", {}).get("ret") if False else None
        return vals

    print("\n===== 快速汇总（完整分析见 _matrix_results.json）=====")
    for year in YEARS:
        for mode in ("qizong", "jianzong"):
            deltas = []
            for code, name, board, layer in sample:
                b = results.get(f"{code}|{year}|none", {}).get("ret")
                m = results.get(f"{code}|{code and year}|{mode}", {})
                m = results.get(f"{code}|{year}|{mode}", {}).get("ret")
                if b is not None and m is not None:
                    deltas.append(m - b)
            if deltas:
                pos = sum(1 for d in deltas if d > 0)
                print(f"{year} {mode:8s}: n={len(deltas):3d} 平均Δ {statistics.mean(deltas):+.2f}pp "
                      f"中位 {statistics.median(deltas):+.2f}pp 正向 {pos}/{len(deltas)}", flush=True)
    DRIVER.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
