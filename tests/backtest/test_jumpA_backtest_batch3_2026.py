"""跳法A 批次3：2026年热门板块回测（2026-01-01 ~ 2026-08-21，手动指定 mode）

背景（用户需求 2026-08-23）：批次1/2 覆盖 2020-2024，缺"最近半年热门板块、高波动、
上下半年切换频繁"的案例。本批全部为 2026 年热门主线个股，且与批次1/2 的 21 案例
零重复。区间特意包含 8/19 暴跌（创业板 -6.26%），检验策略在真实闪崩中的表现。

选股依据（数据画像见 git log 对应 commit；baostock 2026-01-01~08-21 真实日线）：
- 兆易创新  存储半导体   H1 +245.8% → H2 -49.8%，MDD -59.4%（7月底见顶暴跌之最）
- 澜起科技  内存接口     H1 +142.6% → H2 -35.3%
- 沪电股份  PCB算力      H1 +101.2% → H2 -20.7%，趋势主升
- 北方华创  半导体设备   H1 +86.4% → H2 -19.2%
- 新易盛    CPO光模块    高波动巨震 MDD -52.8%，对标批次2中际旭创的 jianzong 标注
- 药明康德  CXO创新药    H1 +31.7%/H2 +30.8% 双升，MDD 仅 -16.3%——低波动稳步上行气宗理想型
- 海光信息  国产算力芯片 H1 +63.8% → H2 -33.5% 且 MDD 落在 8/21（至今未止跌）——见顶组
- 五洲新春  机器人轴承   全年阴跌 YTD -30.3%——对照组测假阳性

mode 标注规则同批次1/2（人工按公开行情，主观性已知）：
qizong=当年主升浪该拿住 / jianzong=高波动一波流快进快出 / none=该走原策略(见顶/对照)

跑法：
    PYTHONUTF8=1 PYTHONPATH=. uv run python tests/backtest/test_jumpA_backtest_batch3_2026.py
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

PER_STOCK_TIMEOUT = 1200
CAPITAL = 200000.0
START, END = "2026-01-01", "2026-08-21"

# (code, name, 组别, mode标注)
CASES = [
    ("603986", "兆易创新", "bull-crash", "qizong"),
    ("688008", "澜起科技", "bull-crash", "qizong"),
    ("002463", "沪电股份", "bull-trend", "qizong"),
    ("002371", "北方华创", "bull-trend", "qizong"),
    ("300502", "新易盛", "high-vol", "jianzong"),
    ("603259", "药明康德", "steady-bull", "qizong"),
    ("688041", "海光信息", "topping", "none"),
    ("603667", "五洲新春", "flat-down", "none"),
]


def _range():
    return START, END


def run_one(code, force_mode=None):
    """跑单股回测，带总超时保护。超时返回 None。"""

    def _run():
        cfg = load_config()
        start, end = _range()
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
            qizong_codes=qizong_codes,
            jianzong_codes=jianzong_codes,
            benzong_auto_mode=False,    # 关闭自动评分定mode（用人工标注，同批次1/2）
        )
        return eng.run()

    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_run)
        try:
            return fut.result(timeout=PER_STOCK_TIMEOUT)
        except FuturesTimeout:
            print(f"    [超时跳过] {code} {force_mode}")
            return None


def main():
    print("=" * 90)
    print(f"  跳法A 批次3：2026热门板块回测（{START} ~ {END}，手动指定 mode）")
    print("=" * 90)
    results = []
    for code, name, group, mode in CASES:
        print(f"▶ {code} {name} [{group}] 标注mode={mode}")
        base_res = run_one(code, None)
        if base_res is None:
            continue
        if mode in ("qizong", "jianzong"):
            mode_res = run_one(code, mode)
            mode_label, mode_pct = mode, (mode_res.total_return_pct if mode_res else float("nan"))
        else:  # none: 两遍都走 baseline，Δ 应为 0
            mode_res, mode_label, mode_pct = base_res, "none", base_res.total_return_pct
        if mode_res is None:
            continue
        delta = mode_pct - base_res.total_return_pct
        results.append((code, name, group, mode_label,
                        base_res.total_return_pct, mode_pct, delta))
        print(f"    baseline {base_res.total_return_pct:+.2f}% → {mode_label} {mode_pct:+.2f}% Δ {delta:+.2f}pp")

    print("=" * 90)
    print(f"{'代码':<8}{'名称':<10}{'组别':<12}{'mode':<10}{'baseline%':>10}{'mode%':>10}{'Δpp':>9}")
    for code, name, group, m, b, mp, d in results:
        print(f"{code:<8}{name:<10}{group:<12}{m:<10}{b:>10.2f}{mp:>10.2f}{d:>+9.2f}")
    print("-" * 90)
    for label, keys in (("暴涨见顶组(bull-crash)", {"bull-crash"}),
                        ("趋势主升组(bull-trend)", {"bull-trend"}),
                        ("稳态长牛(steady-bull)", {"steady-bull"}),
                        ("高波动(jianzong)", {"high-vol"}),
                        ("见顶/对照组(none)", {"topping", "flat-down"})):
        ds = [d for _, _, g, _, _, _, d in results if g in keys]
        if ds:
            pos = sum(1 for x in ds if x > 0)
            print(f"  {label}: {len(ds)}只 平均Δ {sum(ds)/len(ds):+.2f}pp 正向{pos}/{len(ds)}")
    print("=" * 90)


if __name__ == "__main__":
    main()
