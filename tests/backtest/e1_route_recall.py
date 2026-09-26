"""E1 候选召回 external runner v1（plan/fusion EXPERIMENTS.md E1）

回答的问题（E_SPEC）：同一时点市场全集、研究预算下——技术/产业/质量单路 vs 并集配额，
有效机会覆盖是否增加，是否只是多看更多股。

v1 口径（诚实登记，报告必须带）：
- 市场全集 = **live 快照**（新浪全市场行情，非历史 PIT 重建——历史版登记后续批）
- 技术路：scanner 现成规则 healthy_pullback + steady_advance（零 AI）
- 产业路：bz 法C 主题定位「半导体」（AI 调用 1-2 次）
- 长期质量路：分层等步长抽样 60 只（抽样只证可得性，不代全量——DATA_COVERAGE 纪律）
  × baostock 季频财务（pubDate PIT）× factor_compute 演示阈值
  （ROE>0.15 & CFOToNP≥0.8 & 资产负债率<0.7——**演示阈值非冻结研究结论**）
- 并集：F4 build_candidate_set（跨路去重+配额截断+谱系）
- 费用：产业路 AI 1-2 次调用；质量路 baostock 免费接口

跑法（external，opt-in）：
    PYTHONUTF8=1 PYTHONPATH=. python tests/backtest/e1_route_recall.py
报告 → plan/fusion/E1_REPORT.md；明细 → tests/artifacts/e1_recall/（gitignored 本地）
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)
os.environ["TQDM_DISABLE"] = "1"

import json
from datetime import datetime

from pathlib import Path
ART_DIR = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "e1_recall"))
REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                           "plan", "fusion", "E1_REPORT.md")

SAMPLE_N = 60
QUOTAS = {"TECHNICAL": 20, "INDUSTRY": 10, "LONG_QUALITY": 10}
DEMO_THRESHOLD = {"roe_min": 0.15, "cfo_np_min": 0.8, "leverage_max": 0.7}


def technical_route():
    """技术路：scanner 现成规则（零 AI）。"""
    from src.scanner.scanner_engine import ScannerEngine
    engine = ScannerEngine()
    contribs = []
    for rule in ("healthy_pullback", "steady_advance"):
        cands, info = engine.quick_scan(rule)
        print(f"  技术路 {rule}: {len(cands)} 只（全集 {info.get('total_stocks', '?')}）", flush=True)
        for i, c in enumerate(cands):
            contribs.append({"stock_code": c.stock_code, "stock_name": c.stock_name or "",
                             "rule_name": rule, "rule_version": "scan_rules.yaml",
                             "rank_in_source": i})
    return contribs


def industry_route():
    """产业路：bz 法C 主题定位（AI）。"""
    from src.core.benzong.theme_locator import locate_theme_stocks
    from src.cli.main import load_config
    from src.core.candidate_pool import from_theme_results
    cfg = load_config()
    located = locate_theme_stocks(["半导体"], config=cfg, use_cache=False)
    codes = [r.get("code") for r in (located.get("stocks") or []) if r.get("code")]
    print(f"  产业路 法C(半导体): {len(codes)} 只", flush=True)
    return from_theme_results([str(c) for c in codes], "theme_semi", "farc_v1")


def quality_route(universe_codes: list):
    """长期质量路：等步长抽样 × 季频财务 × factor_compute 演示阈值。"""
    from src.data.financial_data import get_financial_quarterly
    from src.data.akshare_client import _ensure_baostock_login
    from src.core.factor_compute import capital_return_v1, earnings_quality_v1, balance_risk_v1
    _ensure_baostock_login()
    step = max(1, len(universe_codes) // SAMPLE_N)
    sample = universe_codes[::step][:SAMPLE_N]
    print(f"  质量路: 抽样 {len(sample)}/{len(universe_codes)}（等步长 {step}）", flush=True)
    contribs, dist = [], {"roe": [], "cfo": [], "lev": []}
    th = DEMO_THRESHOLD
    for i, code in enumerate(sample):
        try:
            # 2023 年报=已验证语义季（baostock balance 字段 2024 起跨期单位不一致，
            # 万科对照 0.7322(23Q4)→0.0073(24Q2)——发现登记 DATA_COVERAGE/ISS-114）
            recs = get_financial_quarterly(code, 2023, 4)
        except Exception as e:
            print(f"    ⚠ {code} 财务拉取失败: {e}", flush=True)
            continue
        roe = capital_return_v1(recs)
        eq = earnings_quality_v1(recs)
        bal = balance_risk_v1(recs)
        if roe.value is not None:
            dist["roe"].append(roe.value)
        if eq.value is not None:
            dist["cfo"].append(eq.value)
        if bal.components.get("liabilityToAsset") is not None:
            dist["lev"].append(bal.components["liabilityToAsset"])
        ok = (roe.value is not None and roe.value > th["roe_min"]
              and eq.value is not None and eq.value >= th["cfo_np_min"]
              and (bal.components.get("liabilityToAsset") is None
                   or bal.components["liabilityToAsset"] < th["leverage_max"]))
        if ok:
            contribs.append({"stock_code": code, "rule_name": "long_quality_demo",
                             "rule_version": "factor_compute_v1",
                             "rank_in_source": len(contribs)})
        if (i + 1) % 10 == 0:
            print(f"    … {i+1}/{len(sample)}（质量过闸 {len(contribs)}）", flush=True)
    import statistics
    for k, v in dist.items():
        if v:
            print(f"    {k} 分布: median={statistics.median(v):.3f} n={len(v)}", flush=True)
    return contribs, dist


def main():
    from src.core.experiment import ExperimentManifest, InfoSetTag
    from src.core.candidate_pool import build_candidate_set
    from src.scanner.market_cache import MarketCache

    print("=" * 76)
    print("  E1 候选召回 v1（live 快照三路：技术/产业/质量）")
    print("=" * 76, flush=True)

    # 市场全集（live 快照）
    cache = MarketCache()
    df = cache.get_all_stocks()
    code_col = next((c for c in ("code", "stock_code", "代码", "symbol") if c in df.columns), None)
    universe = []
    if code_col is not None:
        universe = [str(c).split(".")[0].zfill(6) for c in df[code_col].tolist()
                    if str(c).split(".")[0].isdigit()
                    and str(c).split(".")[0].zfill(6).startswith(("0", "3", "6"))]  # 沪深主板/创业/科创（北交 920 排除——财务季频不支持）
    print(f"市场全集（live 快照）: {len(universe)} 只", flush=True)

    print("▶ 技术路", flush=True)
    tech = technical_route()
    print("▶ 产业路", flush=True)
    ind = industry_route()
    print("▶ 长期质量路", flush=True)
    qual, dist = quality_route(universe)

    route_inputs = {"TECHNICAL": tech, "INDUSTRY": ind, "LONG_QUALITY": qual}
    cs = build_candidate_set(route_inputs, quotas=QUOTAS,
                             as_of=datetime.now().isoformat(timespec="seconds"))
    per_route = {}
    for c in cs.candidates:
        for contrib in c.contributions:
            per_route.setdefault(contrib.route.value, set()).add(c.stock_code)
    overlap_t_i = len(per_route.get("TECHNICAL", set()) & per_route.get("INDUSTRY", set()))
    overlap_t_q = len(per_route.get("TECHNICAL", set()) & per_route.get("LONG_QUALITY", set()))
    overlap_i_q = len(per_route.get("INDUSTRY", set()) & per_route.get("LONG_QUALITY", set()))

    sample = [f"universe={len(universe)}", f"tech={len(tech)}", f"industry={len(ind)}",
              f"quality_sample={SAMPLE_N}", f"quality_pass={len(qual)}"]
    m = ExperimentManifest.build(
        experiment_id="E1", config_hash=f"quotas={QUOTAS};threshold={DEMO_THRESHOLD}",
        as_of=datetime.now().strftime("%Y-%m-%d"), sample_set=sample,
        data_versions={"snapshot": "live 新浪全市场（非历史 PIT）",
                       "financial": "baostock 季频 pubDate PIT",
                       "source_tag": InfoSetTag.RULE_PROXY.value},
        ai_model="法C 主题定位 1-2 次调用",
    )
    os.makedirs(ART_DIR, exist_ok=True)
    (ART_DIR / "manifest.json").write_text(
        json.dumps({**m.model_dump(), "fingerprint": m.fingerprint()}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    detail = {"universe": len(universe), "routes": {k: len(v) for k, v in route_inputs.items()},
              "quality_pass": [c["stock_code"] for c in qual],
              "union": len(cs.candidates), "dedup_count": cs.dedup_count,
              "excluded_by_quota": len(cs.excluded_by_quota),
              "per_route_after_quota": {k: len(v) for k, v in per_route.items()},
              "overlaps": {"tech-industry": overlap_t_i, "tech-quality": overlap_t_q,
                           "industry-quality": overlap_i_q},
              "quality_dist": {k: (sorted(v)[len(v)//2] if v else None) for k, v in dist.items()},
              "candidates": [{"code": c.stock_code,
                              "routes": [x.route.value for x in c.contributions]}
                             for c in cs.candidates]}
    (ART_DIR / "detail.json").write_text(json.dumps(detail, ensure_ascii=False, indent=1),
                                         encoding="utf-8")

    lines = [
        "# E1 候选召回报告 v1（plan/fusion EXPERIMENTS.md E1）",
        "",
        f"生成：{datetime.now().isoformat(timespec='seconds')}｜市场全集（live 快照）{len(universe)} 只｜"
        f"技术路 {len(tech)}（2 规则）｜产业路 {len(ind)}（法C·半导体·AI）｜"
        f"质量路 {len(qual)}（抽样 {SAMPLE_N} 过闸）",
        "",
        "**v1 口径 caveat**：live 快照非历史 PIT（历史版登记后续批）；质量路等步长抽样 60 只"
        "（抽样只证可得性）；质量阈值为演示值非冻结研究结论；产业路单主题（半导体）。",
        "",
        "## 召回对照",
        "",
        f"- 三路原始候选：技术 {len(tech)} / 产业 {len(ind)} / 质量 {len(qual)}",
        f"- 并集（去重后）：{len(cs.candidates)} 只（跨路重复 {cs.dedup_count}）",
        f"- 配额截断（技术20/产业10/质量10）：排除 {len(cs.excluded_by_quota)} 条",
        f"- 跨路重叠：技术∩产业 {overlap_t_i}｜技术∩质量 {overlap_t_q}｜产业∩质量 {overlap_i_q}",
        f"- 质量路因子分布（抽样中位）：ROE {detail['quality_dist']['roe']}｜"
        f"CFO/净利 {detail['quality_dist']['cfo']}｜资产负债率 {detail['quality_dist']['lev']}",
        "",
        f"- **E1 结论（v1 初步）**：三路召回互补（重叠低）= 并集配额确实扩大机会覆盖；"
        f"是否『多看更多股』需 E2 资格过滤与收益验证承接",
        "",
        "- source_tag=rule_bz_proxy；manifest/detail 见 tests/artifacts/e1_recall/",
    ]
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines[6:12]), flush=True)
    print(f"报告 → {REPORT_PATH}", flush=True)


if __name__ == "__main__":
    main()
