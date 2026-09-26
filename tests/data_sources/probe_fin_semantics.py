"""字段语义核验探针（plan/fusion iteration2 R1 验收6，external opt-in，真实网络）

目标（DATA_TRUST §2/§3：ISS-114 以原始资料核实，不自动 ×100）：
1. 正常公司（贵州茅台 600519）与 ISS-114 涉事样本（万科A 000002）的多季度财务
   原始响应**完整留样**（字段名/接口参数/查询时点/源版本/报告期），归档写一次幂等
2. 相邻期量级跳变筛查（>10x/符号翻转 → SUSPECT 隔离标记，不换算不纠偏）
3. ISS-114 交叉核定：用东财资产负债表**绝对值**（akshare，同报告期同合并口径）
   按定义重算 负债总额/资产总额 ——与 baostock liabilityToAsset 对比，判定哪一侧
   是原始财报口径（交叉佐证，非多数投票——绝对值按定义计算 > 比例字段直读）
4. 字段语义登记表（FIELD_DEFINITIONS）逐字段打印 verification 状态，供回写 DATA_COVERAGE

产物：tests/artifacts/probe_fin_semantics.log + tests/artifacts/probe_fin_semantics_raw/
跑法（真实网络，opt-in）：
    PYTHONUTF8=1 PYTHONPATH=. python tests/data_sources/probe_fin_semantics.py
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)

from src.data import financial_data as fd  # noqa: E402
from src.data.akshare_client import _ensure_baostock_login  # noqa: E402
from src.data.research_store import ResearchStore  # noqa: E402
from src.data.research_snapshot import screen_semantic_anomalies  # noqa: E402

ART = Path(__file__).resolve().parents[2] / "tests" / "artifacts"
RAW_DIR = ART / "probe_fin_semantics_raw"
STORE = ResearchStore(RAW_DIR)

STOCKS = [("000002", "万科A", 2021, 2024), ("600519", "贵州茅台", 2023, 2024)]


def fetch_history(stock_code, year_from, year_to):
    """多季度采集（balance 接口为主——ISS-114 涉事接口），原始逐字段留样。"""
    out = []
    for year in range(year_from, year_to + 1):
        for quarter in (1, 2, 3, 4):
            recs = fd.capture_quarterly_evidence(stock_code, year, quarter, store=STORE)
            out.extend(r for r in recs if r.metric_or_claim == "liabilityToAsset"
                       or r.metric_or_claim in ("netProfit", "totalShare"))
    return out


def crosscheck_em_balance(stock_code, periods):
    """东财资产负债表绝对值 → 按定义重算资产负债率（同报告期）。"""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 不可用"}
    try:
        symbol = ("SH" if stock_code.startswith(("6", "9")) else "SZ") + stock_code
        df = ak.stock_balance_sheet_by_report_em(symbol=symbol)
    except Exception as e:  # noqa: BLE001 —— 探针如实登记失败，不造假
        return {"error": f"东财资产负债表获取失败: {e}"}
    if df is None or df.empty:
        return {"error": "东财资产负债表空返回"}
    df["REPORT_DATE"] = df["REPORT_DATE"].astype(str).str[:10]
    out = {}
    for period in periods:
        row = df[df["REPORT_DATE"] == period]
        if row.empty:
            out[period] = {"error": "无该报告期"}
            continue
        r = row.iloc[0]
        liab, asset = r.get("TOTAL_LIABILITIES"), r.get("TOTAL_ASSETS")
        if liab is None or asset is None or asset in (0, "0"):
            out[period] = {"error": "绝对值字段缺失"}
            continue
        out[period] = {"liab": float(liab), "asset": float(asset),
                       "ratio_by_definition": float(liab) / float(asset)}
    return out


def main():
    log = []
    def say(msg=""):
        print(msg)
        log.append(msg)

    say("=" * 78)
    say("【R1 验收6】字段语义核验探针（原始留样 + 量级筛查 + ISS-114 交叉核定）")
    say(f"运行时点: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    say(f"原始留样目录: {RAW_DIR}（写一次幂等）")
    if not _ensure_baostock_login():
        say("!! baostock 登录失败——本轮探查未完成，登记网络失败（不产 fixture 假称实测）")
        ART.mkdir(parents=True, exist_ok=True)
        (ART / "probe_fin_semantics.log").write_text("\n".join(log), encoding="utf-8")
        return 1

    for code, name, y0, y1 in STOCKS:
        say("-" * 78)
        say(f"【{code} {name}】{y0}Q1-{y1}Q4 季频采集+留样")
        records = fetch_history(code, y0, y1)
        say(f"  采集证据数: {len(records)}（留样 {len(list((RAW_DIR / 'raw').glob('*.json')))} 份累计）")
        lta = sorted((r for r in records if r.metric_or_claim == "liabilityToAsset"),
                     key=lambda r: (r.period_end or ""))
        say("  liabilityToAsset（资产负债率，期末存量比）逐期：")
        for r in lta:
            say(f"    {r.period_end}  pub={str(r.published_at)[:10]}  value={r.value}"
                f"  semantic={r.semantic_status}")
        screened = screen_semantic_anomalies(list(records))
        suspects = [r for r in screened if r.semantic_status == "SUSPECT"]
        say(f"  量级筛查（>10x/符号翻转，RATIO/STOCK/PER_SHARE_TTM）：命中 {len(suspects)} 条")
        for r in suspects:
            say(f"    SUSPECT: {r.metric_or_claim} {r.period_end} value={r.value}")
            say(f"             note={r.semantic_note[:80]}")
        if code == "000002":
            periods = [r.period_end for r in lta if r.period_end]
            say("  ISS-114 交叉核定（东财资产负债表绝对值按定义重算——同报告期同合并口径）：")
            cc = crosscheck_em_balance(code, periods)
            for p, v in cc.items():
                raw = next((r.raw_value for r in lta if r.period_end == p), None)
                # 打印**供应商原值**（raw_value）——capture 管线在留样后已应用版本化映射，
                # r.value 是 canonical；交叉核定必须对原值否则自我印证（审查 P2）
                if isinstance(v, dict) and "ratio_by_definition" in v:
                    match = ("≈baostock 原值（该期供应商为原始口径）"
                             if abs(v["ratio_by_definition"] - (raw or 0)) < 0.01 else
                             "≠baostock 原值（该期供应商口径可疑——SUSPECT/映射成立方向）")
                    say(f"    {p}: 定义重算={v['ratio_by_definition']:.4f} "
                        f"(负债{v['liab']:.0f}/资产{v['asset']:.0f}) vs baostock原值={raw}"
                        f" (canonical={next((r.value for r in lta if r.period_end == p), None)}) —— {match}")
                else:
                    say(f"    {p}: {v}")
            say("  纪律：以上为交叉佐证——真值认定仍以发行人原始财报为准，不自动换算")

    say("-" * 78)
    say("【字段语义登记表】verification_status（逐项待原始财报复核——本探针提供佐证材料）：")
    by_kind = {}
    for metric, d in fd.FIELD_DEFINITIONS.items():
        by_kind.setdefault((d.value_kind, d.period_basis), []).append(metric)
    for (vk, pb), metrics in sorted(by_kind.items()):
        say(f"  {vk}/{pb}: {len(metrics)} 字段（{'、'.join(metrics)}）")
    say(f"  全部 {len(fd.FIELD_DEFINITIONS)} 字段 verification_status="
        "name_semantics_pending_original（按字段名语义+茅台实测锚定登记；"
        "本探针的绝对值交叉核定为 liabilityToAsset 提供佐证）")
    say("=" * 78)
    ART.mkdir(parents=True, exist_ok=True)
    (ART / "probe_fin_semantics.log").write_text("\n".join(log), encoding="utf-8")
    say(f"日志已写 {ART / 'probe_fin_semantics.log'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
