"""E0 正确性基线 external runner（plan/fusion EXPERIMENTS.md E0，VALIDATION §2 manifest）

回答的问题（E_SPEC）：同一历史快照、旧策略参数下，仅终态/事实持仓/执行正确性修正
改变了哪些收益成分——「旧收益含哪些错误或执行差异」。

双臂设计（同快照同参数，仅执行正确性不同）：
- 臂 A（legacy）：t_plus_1_lot_mode=False——T+1 单标量 buy_date 整仓判定（现行为）
- 臂 B（corrected）：t_plus_1_lot_mode=True——T+1 按批次份额（G11：当日新买不可卖，
  旧份额当日可卖；末日强平只清当日以前批次）

样本：复用 test_jumpA_backtest_5year.CASES（牛股/见顶/平庸三组，人工标注 mode）。
诚实 caveat（报告必须带）：
1. 人工标注 mode 对 mode 层 bug 免疫（ISS-065 记忆）——本实验不测 mode 判定
2. 回测 ai_enabled=False——F2 的 AI 方向感知修正在回测路径无区分力（AI 修正是
   live 语义）；E0 的可测量差异集中在执行记账层
3. 印花税历史切换（A14）等此前已修——本批不动
4. 预期差异可能很小——全 0 也是合法结论（旧收益基本不含执行错误成分）

跑法（external opt-in，走真实 baostock 网络，不进 pytest）：
    PYTHONUTF8=1 PYTHONPATH=. python tests/backtest/e0_correctness_baseline.py --smoke
    PYTHONUTF8=1 PYTHONPATH=. python tests/backtest/e0_correctness_baseline.py --full
    PYTHONUTF8=1 PYTHONPATH=. python tests/backtest/e0_correctness_baseline.py --report   # 只从已有结果出报告

结果增量写 tests/artifacts/e0_baseline/results.jsonl（断点续跑：同 (code,year,arm) 跳过）；
manifest 落 tests/artifacts/e0_baseline/manifest_<trial>.json；
报告写 plan/fusion/E0_BASELINE_REPORT.md。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
# 金融 API 不走代理（同 test_jumpA_backtest_5year 口径）
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)
os.environ["TQDM_DISABLE"] = "1"

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime

ART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "e0_baseline")
RESULTS_PATH = os.path.join(ART_DIR, "results.jsonl")
REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                           "plan", "fusion", "E0_BASELINE_REPORT.md")


def _settings_sha() -> str:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                     "configs", "settings.yaml")
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def run_arm(code: str, year: str, force_mode, lot_mode: bool, timeout_s: int):
    """跑单案例单臂。返回 (BacktestResult, t1_lot_blocked_count) 或 None=超时。"""
    from src.cli.main import load_config, normalize_stock_code
    from src.core.backtest_engine import BacktestEngine

    def _run():
        cfg = load_config()
        qizong_codes = {code} if force_mode == "qizong" else None
        jianzong_codes = {code} if force_mode == "jianzong" else None
        eng = BacktestEngine(
            stock_code=normalize_stock_code(code),
            start_date=f"{year}-01-01", end_date=f"{year}-12-31",
            initial_capital=200000.0,
            execution_mode="framework_strict",
            layer_mode="decision_strategy_execution",
            skills_dir=cfg.get("skills", {}).get("dir", "./src/skills"),
            signal_weights=cfg.get("decision", {}).get("signal_weights", None),
            skill_types=cfg.get("skills", {}).get("types", None),
            entry_exit_config=cfg.get("entry_exit", None),
            enable_trade_plan=True,
            qizong_codes=qizong_codes,      # 人工标注 mode（同 5 年回测口径）
            jianzong_codes=jianzong_codes,
            benzong_auto_mode=False,        # 关闭自动评分定 mode（人工标注）
            t_plus_1_lot_mode=lot_mode,     # ← E0 唯一变量：批次份额 T+1
        )
        res = eng.run()
        return res, eng._t1_lot_blocked_count

    # 防冻结包装器口径（记忆 LRN：with 块退出 join 会卡死线程 → shutdown(wait=False)）
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(_run)
        return fut.result(timeout=timeout_s)
    except FuturesTimeout:
        return None
    finally:
        ex.shutdown(wait=False)


def _load_done() -> set:
    """已完成键 = 有真实指标的行；超时/报错行不算 done（重跑同命令即重试）。"""
    done = set()
    if os.path.exists(RESULTS_PATH):
        with open(RESULTS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get("timeout") or r.get("error"):
                        continue  # 失败试验保留在账本但不阻塞续跑
                    done.add((r["code"], r["year"], r["arm"]))
                except (json.JSONDecodeError, KeyError):
                    continue
    return done


def _append_result(row: dict) -> None:
    os.makedirs(ART_DIR, exist_ok=True)
    with open(RESULTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _res_metrics(res, blocked_t1) -> dict:
    return {
        "final_value": round(res.final_value, 2),
        "total_return_pct": round(res.total_return_pct, 4),
        "benchmark_return_pct": round(res.benchmark_return_pct, 4),
        "max_drawdown_pct": round(res.max_drawdown_pct, 4),
        "total_trades": res.total_trades,
        "buy_count": res.buy_count,
        "sell_count": res.sell_count,
        "blocked_limit_up": res.blocked_by_limit_up,
        "blocked_limit_down": res.blocked_by_limit_down,
        "blocked_no_open_price": res.blocked_by_no_open_price,
        "t1_lot_blocked": blocked_t1,
    }


def run_all(cases, timeout_s: int, dump_trades: bool = False) -> None:
    done = _load_done()
    for code, name, year, group, mode_label in cases:
        force_mode = mode_label if mode_label in ("qizong", "jianzong") else None
        for arm, lot in (("A_legacy", False), ("B_lot", True)):
            if (code, year, arm) in done and not dump_trades:
                # dump-trades 模式强制重跑（交易对象只在内存里，导出需真实 run）
                print(f"  ↷ 已有结果，跳过 {year} {code} {arm}", flush=True)
                continue
            print(f"▶ {year} {code} {name} [{group}] mode={mode_label} 臂={arm}", flush=True)
            out = None
            err = None
            try:
                out = run_arm(code, year, force_mode, lot, timeout_s)
            except Exception as e:  # 非超时异常也记账继续，不中止整批
                err = f"{type(e).__name__}: {e}"
                print(f"    ✗ 异常 {err}", flush=True)
            row = {"code": code, "name": name, "year": year, "group": group,
                   "mode": mode_label, "arm": arm,
                   "ran_at": datetime.now().isoformat(timespec="seconds")}
            if err is not None:
                row["error"] = err
            elif out is None:
                row["timeout"] = True
                print(f"    ⏱ 超时(>{timeout_s}s)", flush=True)
            else:
                res, blocked = out
                row.update(_res_metrics(res, blocked))
                print(f"    收益 {row['total_return_pct']:.2f}%  回撤 {row['max_drawdown_pct']:.2f}%"
                      f"  交易 {row['total_trades']}  T+1批拦截 {row['t1_lot_blocked']}", flush=True)
                if dump_trades and res is not None:
                    _dump_trades(code, year, arm, res)
            if not dump_trades:
                # dump 模式为 E6 供给交易明细——结果行已记录过，重复追加只会让账本膨胀
                _append_result(row)


def _dump_trades(code: str, year: str, arm: str, res) -> None:
    """逐笔交易导出（E6 组合实验的「同一单股动作集合」输入；TradeRecord JSON）。"""
    tdir = os.path.join(ART_DIR, "trades")
    os.makedirs(tdir, exist_ok=True)
    path = os.path.join(tdir, f"{code}_{year}_{arm}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([t.model_dump(mode="json") for t in res.trades], f, ensure_ascii=False, indent=1)


def write_manifest(cases) -> str:
    from src.core.experiment import ExperimentManifest, InfoSetTag
    trial = datetime.now().strftime("%Y%m%d_%H%M%S")
    sample = [f"{c}|{y}|{g}|mode={m}" for c, _, y, g, m in cases]
    m = ExperimentManifest.build(
        experiment_id="E0",
        config_hash=_settings_sha(),
        as_of=datetime.now().strftime("%Y-%m-%d"),
        sample_set=sample,
        data_versions={"kline": "baostock query_history_k_data（kline_cache 当日缓存）",
                       "mode": "human_annotated（tests/backtest/test_jumpA_backtest_5year.CASES）",
                       "source_tag": InfoSetTag.RULE_PROXY.value},
        trading_calendar="baostock 交易日（DataFeeder 内建）",
        fee_rules="佣金 0.025%（min 5元）+ 印花税 0.05%（2023-08-28 前 0.1%，A14）+ 过户费 0.001% + 滑点 0.1%",
        ai_model="",  # 回测禁 AI（backtest_engine ai_enabled=False）
    )
    os.makedirs(ART_DIR, exist_ok=True)
    path = os.path.join(ART_DIR, f"manifest_{trial}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**m.model_dump(), "fingerprint": m.fingerprint()},
                  f, ensure_ascii=False, indent=2)
    print(f"manifest → {path} (fp={m.fingerprint()})", flush=True)
    return trial


def build_report() -> None:
    """从 results.jsonl 汇总出 plan/fusion/E0_BASELINE_REPORT.md。"""
    rows = []
    if os.path.exists(RESULTS_PATH):
        with open(RESULTS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    by_key = {}
    for r in rows:
        by_key.setdefault((r["code"], r["year"]), {})[r["arm"]] = r

    lines = [
        "# E0 正确性基线报告（plan/fusion EXPERIMENTS.md E0）",
        "",
        f"生成：{datetime.now().isoformat(timespec='seconds')}；样本口径 = 人工标注 mode 案例集"
        "（test_jumpA_backtest_5year.CASES，framework_strict，20万初始资金）",
        "",
        "**诚实 caveat**：",
        "1. 人工标注 mode 对 mode 层 bug 免疫（ISS-065）——本实验不测 mode 判定；",
        "2. 回测 `ai_enabled=False`——F2 的 AI 方向感知修正是 live 语义，回测路径无区分力；",
        "3. 本批唯一变量 = T+1 批次份额口径（G11）：臂 B 当日新买份额不可卖、末日强平只清当日以前批次；",
        "4. 差异近 0 也是合法结论（旧收益基本不含执行错误成分），不为制造差异改参数。",
        "",
        "## 逐案对照",
        "",
        "| 年份 | 代码 | 名称 | 组别 | mode | A_legacy 收益% | B_lot 收益% | Δpp | A 回撤% | B 回撤% | A 交易 | B 交易 | B 的T+1批拦截 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    deltas = []
    for (code, year), arms in sorted(by_key.items()):
        a = arms.get("A_legacy")
        b = arms.get("B_lot")
        if not a or not b:
            continue
        if a.get("timeout") or b.get("timeout") or a.get("error") or b.get("error"):
            continue
        d = round(b["total_return_pct"] - a["total_return_pct"], 4)
        deltas.append(d)
        lines.append(
            f"| {year} | {code} | {a['name']} | {a['group']} | {a['mode']} "
            f"| {a['total_return_pct']:.2f} | {b['total_return_pct']:.2f} | {d:+.2f} "
            f"| {a['max_drawdown_pct']:.2f} | {b['max_drawdown_pct']:.2f} "
            f"| {a['total_trades']} | {b['total_trades']} | {b['t1_lot_blocked']} |")
    n = len(deltas)
    lines += ["", "## 汇总", ""]
    if n:
        import statistics
        changed = sum(1 for d in deltas if abs(d) >= 0.01)
        max_abs = max(abs(d) for d in deltas)
        mean_abs = abs(statistics.mean(deltas))
        lines += [
            f"- 有效配对 {n} 例；收益差异 ≥0.01pp 的 {changed} 例",
            f"- Δpp 均值 {statistics.mean(deltas):+.4f}，中位数 {statistics.median(deltas):+.4f}，"
            f"最大绝对值 {max_abs:.4f}",
            f"- T+1 批次拦截合计 {sum(arms.get('B_lot', {}).get('t1_lot_blocked', 0) for arms in by_key.values())} 次",
        ]
        # 结论判定沿用项目噪声阈（调参红线 LRN-20260619-001：单股<2pp、整体<1pp 视为噪声）
        if max_abs < 2.0 and mean_abs < 1.0:
            concl = ("执行口径差异全部在项目噪声阈内（单股<2pp、整体<1pp）——旧回测基线"
                     "可视为与修正后基线等价；后续实验（E3/E4/E6）沿用既有基线，臂 B 仅作敏感性参照")
        else:
            concl = "存在超出噪声阈的执行口径差异——后续实验（E3/E4/E6）应以臂 B 为修正后基线"
        lines += [f"- **E0 结论（初步）**：{concl}", ""]
    else:
        lines += ["- 暂无完整配对结果（先跑 --smoke / --full）", ""]
    lines += [
        "## 信息集与登记",
        "",
        "- source_tag = rule_bz_proxy；manifest 见 tests/artifacts/e0_baseline/manifest_*.json",
        "- 试验编号与冻结时间以 manifest 为准；失败/超时案例保留在 results.jsonl 不删除（VALIDATION §3）",
        "- 下一步：E3（持有纪律）/E4（技术择时）以臂 B 为修正后基线做配对对照",
        "",
    ]
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"报告 → {REPORT_PATH}", flush=True)
    print("\n".join(lines[-14:]))


def main():
    ap = argparse.ArgumentParser(description="E0 正确性基线双臂回测")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--smoke", action="store_true", help="冒烟：2 例代表案例")
    g.add_argument("--full", action="store_true", help="全量：21 案例")
    g.add_argument("--report", action="store_true", help="只从已有结果出报告")
    g.add_argument("--dump-trades", action="store_true", help="重跑全量双臂并导出逐笔交易（E6 输入）")
    ap.add_argument("--timeout", type=int, default=1200, help="单臂超时秒数")
    args = ap.parse_args()

    from test_jumpA_backtest_5year import CASES

    if args.report:
        build_report()
        return

    if args.smoke:
        # 代表案例：气宗牛股（宁德2020）+ 见顶组（茅台2021）
        cases = [("300750", "宁德时代", "2020", "bull", "qizong"),
                 ("600519", "贵州茅台", "2021", "top", "none")]
    else:
        cases = list(CASES)

    print("=" * 80)
    print(f"  E0 正确性基线（{'冒烟' if args.smoke else '全量'} {len(cases)} 例 × 双臂）"
          + ("+ 逐笔导出" if args.dump_trades else ""))
    print("  唯一变量：t_plus_1_lot_mode（T+1 批次份额）；其余参数与五年回测口径一致")
    print("=" * 80, flush=True)
    write_manifest(cases)
    run_all(cases, args.timeout, dump_trades=args.dump_trades)
    build_report()


if __name__ == "__main__":
    main()
