"""E6 组合实验 external runner（plan/fusion EXPERIMENTS.md E6，VALIDATION §3）

回答的问题（E_SPEC）：同一单股动作集合下，「逐股建议对照」vs「统一资金与集中度约束」
——是否减少不可实现仓位和集中风险。

v1 口径（诚实登记，报告必须带）：
- 动作集 = E0 臂 A（legacy）的 21 案例逐笔交易（tests/artifacts/e0_baseline/trades/
  *_A_legacy.json，--dump-trades 导出）——同一动作集合固定
- 语义映射：BUY position_ratio_after=r → 「该股目标仓位 r（账户相对）」；SELL
  CLOSE_ALL（含回测窗口末日强平）→ 清仓该股；REDUCE → 降到 r。同日多股同时加仓的
  超额认购是真实现象（G09：十股均建议增仓现金只够两股）——正是组合预算存在的理由
- 资本：两臂同 NAV=420万（=21×20万，与 E0 各自账户总资本一致）
- 臂 N（逐股建议）：共享现金池按 (日期,代码) 到达序足额成交，现金不足即拒——无预算协调
- 臂 U（统一预算）：solve_budget（per_stock_max=0.10 + 现金约束；排列无关、拒绝原因明确）
- 盯市：baostock 前复权收盘（adjustflag=2，与 DataFeeder A05 同基）；执行价用 E0 交易
  自带价格（两臂一致）
- v1 已知缺口：PortfolioReplay 的 T+1 为整仓锁（F8 审查 P1-B 登记，批次份额模型已在
  backtest_engine 落地、回放模型待跟进）；费率用回放默认（佣金 0.03% + 卖出印花税
  0.1%），与回测费率略异；无行业暴露约束（行业映射未接线）； sell 受阻计数如实报告

跑法（external，opt-in；K线走 baostock）：
    PYTHONUTF8=1 PYTHONPATH=. python tests/backtest/e6_portfolio_budget.py
报告 → plan/fusion/E6_REPORT.md；明细 → tests/artifacts/e6_baseline/（gitignored 本地）
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(k, None)
os.environ["TQDM_DISABLE"] = "1"

import json
from datetime import datetime

ART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts", "e6_baseline")
E0_TRADES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "artifacts",
                             "e0_baseline", "trades")
REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                           "plan", "fusion", "E6_REPORT.md")

def _risk_profile() -> tuple[float, str]:
    """用户风险档（settings.yaml fusion.risk_profile，2026-09-26 用户拍板三数）。

    返回 (per_stock_max, 来源说明)；配置缺失/读取失败回退保守默认 0.10 并标注。"""
    try:
        from src.cli.main import load_config
        rp = (load_config().get("fusion") or {}).get("risk_profile") or {}
        v = rp.get("per_stock_max")
        if v is not None:
            return float(v), "settings.yaml fusion.risk_profile（用户拍板）"
    except Exception as e:
        print(f"  ⚠ 风险档读取失败，回退默认 0.10: {e}", flush=True)
    return 0.10, "回退默认（配置未配置）"


PER_STOCK_CAP, _RISK_SOURCE = _risk_profile()
INITIAL_NAV = 21 * 200000.0
WINDOW_END_FORCE = "[回测结束强制清仓]"


def load_action_stream() -> dict:
    """E0 臂 A 逐笔交易 → {code: [trade dict]}（同一单股动作集合）。

    同股多年窗案例（宁德 2020/2023、神华 2022/2023）交易**合并**（按日期排序）：
    各年窗以末日强平收口、时间不重叠，合并后动作流完整（不能按文件覆盖——会静默丢年窗）。
    """
    stream: dict = {}
    for fn in sorted(os.listdir(E0_TRADES_DIR)):
        if not fn.endswith("_A_legacy.json"):
            continue
        code = fn.split("_")[0]
        with open(os.path.join(E0_TRADES_DIR, fn), "r", encoding="utf-8") as f:
            stream.setdefault(code, []).extend(json.load(f))
    for code in stream:
        stream[code].sort(key=lambda t: t["date"])
    return stream


def load_drawdown_estimates() -> dict:
    """各股压力损失率估计 = 其自身回测期最大回撤（下限 0.10；results.jsonl 臂 A 行）。"""
    out: dict = {}
    path = os.path.join(os.path.dirname(E0_TRADES_DIR), "results.jsonl")
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("arm") != "A_legacy" or r.get("timeout") or r.get("error"):
                continue
            dd = (r.get("max_drawdown_pct") or 0.0) / 100.0
            code = r.get("code")
            out[code] = max(out.get(code, 0.0), dd)
    return {c: max(v, 0.10) for c, v in out.items()}


def get_close_series(bs_code: str, start: str, end: str, timeout: int = 30) -> dict:
    """前复权收盘序列（adjustflag=2 与 DataFeeder A05 同基）。"""
    import baostock as bs
    from src.data.akshare_client import _call_with_timeout
    from concurrent.futures import TimeoutError as _FT

    def _read():
        rs = bs.query_history_k_data_plus(bs_code, "date,close",
                                          start_date=start, end_date=end,
                                          frequency="d", adjustflag="2")
        if rs.error_code != "0":
            # 显式告警（监督员通宵批 P1：静默空序列会让盯市失真进报告）
            print(f"  ⚠ {bs_code} 收盘序列接口错误 {rs.error_code} {rs.error_msg}——按最近成交价盯市",
                  flush=True)
            return {}
        out = {}
        while rs.next():
            d, c = rs.get_row_data()
            if c:
                out[d] = float(c)
        return out

    try:
        return _call_with_timeout(_read, timeout=timeout)
    except _FT:
        print(f"  ⚠ {bs_code} 收盘序列超时——该股事件日按最近成交价盯市", flush=True)
        return {}


class _ArmState:
    """单臂组合状态（共享现金 + 持仓；两臂同一事件流）。"""

    def __init__(self):
        from src.core.experiment import PortfolioReplay
        self.replay = PortfolioReplay(initial_cash=INITIAL_NAV)
        self.buy_requested_w = 0.0
        self.buy_filled_w = 0.0
        self.rejected_solve: list[tuple[str, str, str]] = []   # 预算求解拒绝/搁置（原因明确）
        self.rejected_exec: list[tuple[str, str, str]] = []    # 执行层拒绝（现金不足一手/整手缺口）
        self.t1_blocked = 0
        self.max_weight = 0.0
        self.max_same_day_demand_w = 0.0

    def weight(self, code: str, nav: float) -> float:
        pos = self.replay.positions.get(code)
        if pos is None:
            return 0.0
        return pos.shares * pos.last_price / nav if nav > 0 else 0.0


def run_arm(mode: str, stream: dict, closes: dict, dd_estimates: dict) -> _ArmState:
    """mode: naive（逐股建议无协调）/ unified（solve_budget 统一预算）。"""
    from src.core.portfolio_policy import AddProposal, BudgetConstraints, HoldingWeight, solve_budget

    st = _ArmState()
    all_dates = sorted({t["date"] for trades in stream.values() for t in trades})
    for d in all_dates:
        nav = st.replay.nav({})  # 最近成交价口径的过渡 NAV（事件执行前）
        # ── 卖出先于买入（exits first；两臂同口径）──
        for code, trades in sorted(stream.items()):
            for t in trades:
                if t["date"] != d or t["action"] != "SELL":
                    continue
                pos = st.replay.positions.get(code)
                if pos is None:
                    continue
                pa = t.get("position_action")
                is_close = pa == "CLOSE_ALL" or t.get("reason") == WINDOW_END_FORCE
                try:
                    if is_close:
                        st.replay.sell(code, d, t["price"])
                    else:  # REDUCE：降到 position_ratio_after
                        target_mv = (t.get("position_ratio_after") or 0.0) * nav
                        cur_mv = pos.shares * t["price"]
                        sell_shares = int(max(0.0, (cur_mv - target_mv)) / t["price"] / 100) * 100
                        if sell_shares > 0:
                            st.replay.sell(code, d, t["price"], shares=min(sell_shares, pos.shares))
                except ValueError:
                    st.t1_blocked += 1  # T+1 整仓锁（v1 已知缺口）受阻，如实计数
        # ── 买入建议 ──
        proposals = []
        for code, trades in sorted(stream.items()):
            for t in trades:
                if t["date"] != d or t["action"] != "BUY":
                    continue
                target_w = t.get("position_ratio_after") or 0.0
                cur_w = st.weight(code, nav)
                demand_w = target_w - cur_w
                if demand_w <= 1e-6:
                    continue
                proposals.append((code, target_w, demand_w))
        st.max_same_day_demand_w = max(st.max_same_day_demand_w, sum(p[2] for p in proposals))
        if mode == "naive":
            for code, target_w, demand_w in proposals:
                st.buy_requested_w += demand_w
                cash_w = st.replay.cash / nav if nav > 0 else 0.0
                fill_w = min(demand_w, cash_w * 0.95)
                if fill_w * nav < 100 * 1.0:
                    st.rejected_exec.append((d, code, "现金不足一手（逐股建议无预算协调）"))
                    continue
                try:
                    st.replay.buy(code, d, _price_of(stream, code, d), fill_w, total_nav=nav)
                    st.buy_filled_w += fill_w
                except ValueError as e:
                    st.rejected_exec.append((d, code, f"执行拒绝: {e}"))
        else:
            holdings = [HoldingWeight(stock_code=c, weight=st.weight(c, nav))
                        for c in st.replay.positions]
            props = [AddProposal(stock_code=c, target_weight=tw,
                                 pressure_loss_rate=dd_estimates.get(c))
                     for c, tw, _ in proposals]
            sol = solve_budget(props, holdings,
                               BudgetConstraints(per_stock_max=PER_STOCK_CAP,
                                                 cash_nav=st.replay.cash / nav if nav > 0 else None),
                               sell_eligible_nav=0.0)
            adds_by_code = {a.stock_code: a for a in sol.adds}
            for code, target_w, demand_w in proposals:
                st.buy_requested_w += demand_w
                line = adds_by_code.get(code)
                if line is None or not line.feasible:
                    reason = next((r.reason for r in sol.rejected if r.stock_code == code),
                                  "被预算求解搁置")
                    st.rejected_solve.append((d, code, reason))
                    continue
                try:
                    st.replay.buy(code, d, _price_of(stream, code, d), line.add_weight, total_nav=nav)
                    st.buy_filled_w += line.add_weight
                except ValueError as e:
                    # 监督员通宵批 P1：求解批准≠执行可行——solve_budget 不知整手约束，
                    # 批准额低于一手时执行层拒绝（与求解拒绝分计，不混入预算叙事）
                    st.rejected_exec.append((d, code, f"执行拒绝: {e}"))
        # ── 盯市与集中度（当日收盘可得则用收盘，否则最近成交价）──
        nav_mark = st.replay.nav({c: closes.get(c, {}).get(d) for c in st.replay.positions})
        if nav_mark > 0:
            for code, pos in st.replay.positions.items():
                px = closes.get(code, {}).get(d) or pos.last_price
                w = pos.shares * px / nav_mark
                st.max_weight = max(st.max_weight, w)
    return st


def _price_of(stream: dict, code: str, d: str) -> float:
    for t in stream[code]:
        if t["date"] == d and t["action"] == "BUY":
            return t["price"]
    raise KeyError(f"{code} {d} 无买入价")


def hhi(weights: dict) -> float:
    tot = sum(weights.values())
    if tot <= 0:
        return 0.0
    return sum((w / tot) ** 2 for w in weights.values())


def final_weights(st: _ArmState, closes: dict, last_date: str) -> dict:
    out = {}
    for code, pos in st.replay.positions.items():
        px = closes.get(code, {}).get(last_date) or pos.last_price
        out[code] = pos.shares * px
    return out


def main():
    from src.core.experiment import ExperimentManifest, InfoSetTag

    stream = load_action_stream()
    # 零交易案例（策略当年未开仓）不产生动作，剔除并如实计数
    empty = [c for c, s in stream.items() if not s]
    for c in empty:
        del stream[c]
    codes = sorted(stream)
    print(f"动作集：{len(codes)} 案例臂 A 逐笔交易（零交易剔除 {len(empty)}：{'、'.join(empty) or '无'}）",
          flush=True)

    # 收盘序列（各案例自身回测年窗；缓存友好）
    from src.data.financial_data import _to_baostock_code
    from src.data.akshare_client import _ensure_baostock_login
    _ensure_baostock_login()
    closes: dict = {}
    for code, trades in stream.items():
        ds = [t["date"] for t in trades]
        start, end = min(ds), max(ds)
        closes[code] = get_close_series(_to_baostock_code(code), start, end)
    print(f"收盘序列就绪：{sum(1 for v in closes.values() if v)}/{len(codes)}", flush=True)
    dd_estimates = load_drawdown_estimates()
    print(f"压力损失率估计（各股自身回测期最大回撤，下限 0.10）：{len(dd_estimates)} 只就绪",
          flush=True)

    sample = sorted(f"{c}|{min(t['date'] for t in s)}~{max(t['date'] for t in s)}"
                    for c, s in stream.items())
    m = ExperimentManifest.build(
        experiment_id="E6", config_hash=f"per_stock_max={PER_STOCK_CAP};nav={INITIAL_NAV:.0f}",
        as_of=datetime.now().strftime("%Y-%m-%d"), sample_set=sample,
        data_versions={"actions": "E0 arm A trades (--dump-trades)",
                       "close": "baostock 前复权收盘（adjustflag=2）",
                       "source_tag": InfoSetTag.RULE_PROXY.value},
        fee_rules="PortfolioReplay 默认（佣金 0.03% + 卖出印花税 0.1%）",
        ai_model="")
    os.makedirs(ART_DIR, exist_ok=True)
    with open(os.path.join(ART_DIR, f"manifest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"),
              "w", encoding="utf-8") as f:
        json.dump({**m.model_dump(), "fingerprint": m.fingerprint()}, f, ensure_ascii=False, indent=2)

    st_n = run_arm("naive", stream, closes, dd_estimates)
    st_u = run_arm("unified", stream, closes, dd_estimates)
    last_date = max(t["date"] for s in stream.values() for t in s)

    def _summary(st: _ArmState, label: str) -> dict:
        fw = final_weights(st, closes, last_date)
        nav_end = st.replay.cash + sum(fw.values())
        ws = {c: v / nav_end for c, v in fw.items() if v > 0}
        return {
            "label": label,
            "final_nav": round(nav_end, 2),
            "return_pct": round((nav_end / INITIAL_NAV - 1) * 100, 4),
            "buy_requested_w": round(st.buy_requested_w, 4),
            "buy_filled_w": round(st.buy_filled_w, 4),
            "solve_rejected": len(st.rejected_solve),
            "exec_rejected": len(st.rejected_exec),
            "t1_blocked": st.t1_blocked,
            "max_weight": round(st.max_weight, 4),
            "end_hhi": round(hhi(ws), 4),
            "end_positions": len(ws),
            "max_same_day_demand_w": round(st.max_same_day_demand_w, 4),
            "_rejected": st.rejected_solve + st.rejected_exec,
        }

    s_n, s_u = _summary(st_n, "naive"), _summary(st_u, "unified")
    lines = [
        "# E6 组合实验报告（plan/fusion EXPERIMENTS.md E6）",
        "",
        f"生成：{datetime.now().isoformat(timespec='seconds')}｜NAV=420万（两臂同）｜"
        f"动作集=E0 臂 A 逐笔交易（{len(stream) + len(empty)} 案例，同股多年窗合并为 "
        f"{len(stream)} 只标的，零交易剔除 {len(empty)} 例）｜统一臂 per_stock_max="
        f"{PER_STOCK_CAP:.0%}（{_RISK_SOURCE}；约束买入时点，不约束价格漂移后的权重变化）",
        "",
        "**v1 口径 caveat**：回放 T+1 为整仓锁（F8 P1-B 登记）；回放费率与回测略异；无行业"
        "暴露约束（行业映射未接线）；同日多股按账户相对仓位直译 → 超额认购是真实现象（G09）；"
        "压力损失率以各股自身回测期最大回撤登记（组合损失预算约束未启用——该估计仅用于越过"
        "「未知不给精确额度」门，不参与额度计算）；E0 的窗口末日强平在组合臂中先于当日买入执行"
        "（exits-first），与回测内部顺序略有重排，两臂同口径。",
        "",
        "| 臂 | 期末净值 | 收益% | 买入请求权重 | 成交权重 | 求解拒绝 | 执行拒绝 | T+1受阻 | 最大单股权重 | 期末HHI | 期末持仓数 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        f"| N 逐股建议 | {s_n['final_nav']:.0f} | {s_n['return_pct']:+.2f} | {s_n['buy_requested_w']:.1%} "
        f"| {s_n['buy_filled_w']:.1%} | {s_n['solve_rejected']} | {s_n['exec_rejected']} | {s_n['t1_blocked']} "
        f"| {s_n['max_weight']:.1%} | {s_n['end_hhi']:.3f} | {s_n['end_positions']} |",
        f"| U 统一预算 | {s_u['final_nav']:.0f} | {s_u['return_pct']:+.2f} | {s_u['buy_requested_w']:.1%} "
        f"| {s_u['buy_filled_w']:.1%} | {s_u['solve_rejected']} | {s_u['exec_rejected']} | {s_u['t1_blocked']} "
        f"| {s_u['max_weight']:.1%} | {s_u['end_hhi']:.3f} | {s_u['end_positions']} |",
        "",
        f"- 同日最大合计买入需求（臂内路径值）：N {s_n['max_same_day_demand_w']:.1%} / "
        f"U {s_u['max_same_day_demand_w']:.1%} of NAV（超过 100% 即当日不可全部实现）",
        f"- 集中风险：最大单股权重 naive {s_n['max_weight']:.1%} vs unified {s_u['max_weight']:.1%}"
        f"（约束 {PER_STOCK_CAP:.0%} 只管买入时点）；期末 HHI {s_n['end_hhi']:.3f} vs {s_u['end_hhi']:.3f}",
        f"- 拒绝明细（各取前 10；求解拒绝=预算/集中度约束，执行拒绝=整手/现金不可行）：",
    ]
    for label, s in (("N", s_n), ("U", s_u)):
        lines.append(f"  - 臂 {label}: " + ("; ".join(f"{d} {c}（{r}）" for d, c, r in s["_rejected"][:10]) or "无"))
    # 结论只写数据支撑的断言（监督员通宵批 P1：不写「减少不可实现仓位」——统一臂
    # 求解拒绝更多且执行层整手缺口混在执行拒绝里，该指标 v1 未定义）
    if s_u["max_weight"] < s_n["max_weight"]:
        conc = (f"统一预算把最大单股权重从 {s_n['max_weight']:.1%} 压到 {s_u['max_weight']:.1%}"
                f"（买入时点约束生效）；代价是求解拒绝更多（{s_u['solve_rejected']} vs "
                f"naive 路径现金拒绝 {s_n['exec_rejected']}），且执行层不知整手约束——"
                f"部分被批准额度因不足一手被拒（{s_u['exec_rejected']} 笔，登记为口径缺口）")
    else:
        conc = "本样本下统一预算未显示集中度收益——登记观察"
    lines += ["", f"- **E6 结论（v1 初步）**：{conc}",
              "- ⚠️ 两臂收益差异**不构成策略优劣证据**——naive 臂的收益来自「先到先得」路径"
              "与同窗高集中持仓，E6 主指标是实现可行性与集中度（VALIDATION §1：不把执行口径"
              "差异算成策略优劣）",
              "- source_tag=rule_bz_proxy；manifest 见 tests/artifacts/e6_baseline/",
              "- v1 为组合预算语义的首个真实执行；口径升级（行业约束/批次 T+1 回放/费率对齐）随后续批"]
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines[8:16]), flush=True)
    print(f"报告 → {REPORT_PATH}", flush=True)


if __name__ == "__main__":
    main()
