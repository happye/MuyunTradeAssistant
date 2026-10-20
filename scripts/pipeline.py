#!/usr/bin/env python3
"""暮云 每日串联流水线（本地原型，只读：不改 portfolio.yaml、不记建议、不下单）

阶段：
  1 大盘  ：沪深300 趋势 + 恐慌指数（fear）
  2 持仓  ：逐只 规则引擎+策略层+AI情绪（orchestrator.analyze，带持仓上下文）
            + 交易计划止损/持有期核对 + 单股额度观察
  3 选股  ：全市场规则/主题初筛（排除持仓）→ 前 N 只深度分析
  4 验证  ：候选（有建仓动作/BUY）与可选持仓的回测（framework_strict，T+1/涨跌停/费用滑点由引擎负责）
  5 评分  ：(可选 --bz) 笨总 6 维 AI 评分（候选 + 持仓）
  6 仓位  ：展示研究目标；未接入账户数量/可用现金核验，不提供下单数量
  7 报告  ：Markdown + JSON；(可选) DeepSeek 中文晨报；存 AI笔记/pipeline/ 供 chat read_file 读取

用法：
  MUYUN_DATA_SHIM=tencent .venv/bin/python scripts/pipeline.py --nav 69500 --top 5 [--theme 煤炭] [--bz] [--no-ai] [--backtest-holdings]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
for _k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    os.environ.pop(_k, None)
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("MUYUN_FAST_FAIL", "1")        # 慢/不可达的可选数据源首次失败后熔断
os.environ.setdefault("MUYUN_DATA_CALL_TIMEOUT", "12")

import src  # noqa: E402,F401  (触发可选数据垫片)
from src.cli.main import load_config  # noqa: E402

ACTION_CN = {"OPEN": "试探建仓", "ADD": "加仓", "REDUCE": "减仓", "CLOSE_ALL": "清仓",
             "HOLD_POSITION": "维持", "STAY_OUT": "观望"}
QUANTITY_PENDING = "股数/可用现金待核，不提供下单数量"


def _ev(x):
    return getattr(x, "value", x)


class Stage:
    def __init__(self, log):
        self.log = log

    def run(self, name, fn, *a, **kw):
        t = time.time()
        try:
            out = fn(*a, **kw)
            self.log.append({"stage": name, "ok": True, "sec": round(time.time() - t, 1)})
            print(f"  ✓ {name}（{time.time() - t:.1f}s）", flush=True)
            return out
        except Exception as e:  # 单阶段失败不拖垮流水线
            self.log.append({"stage": name, "ok": False, "sec": round(time.time() - t, 1),
                             "error": f"{type(e).__name__}: {e}"})
            print(f"  ✗ {name}: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc(limit=3)
            return None


def stage_market():
    from src.data.akshare_client import AKShareClient
    idx = AKShareClient._get_index_trend() or {}
    try:
        from src.core.fear_index import get_fear_report
        r = get_fear_report("")
        fear = {k: r.get(k) for k in ("as_of", "score", "tier", "missing")}
    except Exception as e:
        fear = {"error": str(e)}
    return {"index": {k: idx.get(k) for k in ("trend", "close", "ma20", "ma60", "ma250", "change_pct")},
            "fear": fear}


def analyze_one(orch, pm, code, *, ai=True):
    """单股分析（只读：不写观察量/建议/证据）。"""
    from src.data.akshare_client import AKShareClient
    sd_ = AKShareClient.calculate_indicators(code)
    if sd_ is None:
        raise RuntimeError(f"{code} 行情/指标获取失败")
    facts = pm.request_account_facts()
    ctx = facts.context_for(code, price=sd_.price,
                            price_as_of=(str(sd_.quote_as_of or "") or None))
    state = pm.to_strategy_state(code, price=sd_.price, price_as_of=sd_.quote_as_of,
                                 nav=facts.nav, nav_as_of=facts.nav_day, account_context=ctx)
    pos = facts.projection_for(code)
    has_pos = ctx.position_state in ("HELD", "UNKNOWN")
    dr, sd, ee, air = orch.analyze(
        sd_, current_position_ratio=state.current_position_ratio, strategy_state=state,
        ai_enabled=ai, has_position=has_pos,
        entry_price=pos.entry_price if pos else None,
        high_since_entry=pos.high_since_entry if pos else None,
        trade_plan=ctx.trade_plan)
    atr = getattr(sd_, "atr", None) or getattr(sd_, "atr_14", None)
    return {
        "code": code, "name": sd_.stock_name, "price": sd_.price, "change_pct": sd_.change_pct,
        "ma20": sd_.ma20, "ma60": sd_.ma60, "atr": atr, "rsi_6": sd_.rsi_6,
        "decision": _ev(dr.decision), "score": round(dr.score, 3), "market_state": _ev(dr.state),
        "strategy_decision": _ev(sd.decision), "position_action": _ev(sd.position_action),
        "action_semantic": sd.action_semantic, "sell_path": sd.sell_path,
        "target_ratio": sd.position_ratio, "reasons": (dr.reason or [])[:6],
        "warnings": (dr.warnings or [])[:4],
        "ai": ({"sentiment": air.sentiment, "confidence": air.confidence, "risk": air.risk_level,
                "summary": air.summary} if air is not None else None),
        "has_position": has_pos,
    }


def stage_holdings(orch, pm, nav, risk_profile, ai):
    out = []
    cap = (risk_profile or {}).get("per_stock_max", 0.2)
    for pos in pm.list_positions():
        code = pos.stock_code
        try:
            a = analyze_one(orch, pm, code, ai=ai)
        except Exception as e:
            out.append({"code": code, "name": pos.stock_name, "error": str(e)})
            continue
        price = a["price"]
        ratio = None if getattr(pos, "ratio_stale", False) else pos.current_ratio
        a.update({
            "entry_price": pos.entry_price, "ratio": ratio,
            "pnl_pct": round((price / pos.entry_price - 1) * 100, 2) if pos.entry_price else None,
            "est_shares": None, "suggest_shares_delta": None,
            "quantity_status": "UNKNOWN", "quantity_note": QUANTITY_PENDING,
            "over_cap": ratio > cap if ratio is not None else None,
        })
        flags = []
        tp = pos.trade_plan
        if tp is not None:
            stop = getattr(tp, "current_stop", None)
            a["plan_stop"] = stop
            a["plan_targets"] = getattr(tp, "when_sell_targets", None)
            if stop and price <= stop:
                flags.append(f"已跌破计划止损 {stop}")
            opened = getattr(tp, "opened_at", None)
            mh = getattr(tp, "max_hold_days", None)
            if opened and mh:
                held = (datetime.now() - datetime.strptime(str(opened)[:10], "%Y-%m-%d")).days
                if held > mh:
                    flags.append(f"持有{held}天>计划上限{mh}天")
        else:
            flags.append("无交易计划")
        if a["pnl_pct"] is not None and a["pnl_pct"] <= -20:
            flags.append(f"浮亏{a['pnl_pct']}%")
        if a["over_cap"]:
            flags.append(f"超单股上限{cap:.0%}")
        a["flags"] = flags
        out.append(a)
    return out


def stage_candidates(orch, pm, scanner, rule, theme, top, ai):
    held = {p.stock_code for p in pm.list_positions()}
    cands, info = scanner.quick_scan(rule_name=rule, market_query=theme, exclude_codes=held)
    rows = []
    for c in cands[:top]:
        try:
            a = analyze_one(orch, pm, c.stock_code, ai=ai)
            a["industry"] = c.industry
            rows.append(a)
        except Exception as e:
            rows.append({"code": c.stock_code, "name": c.stock_name, "error": str(e)})
    info = info or {}
    meta = {"rule": rule, "theme": theme, "scanned": len(cands),
            "theme_filter_failed": bool(getattr(scanner, "last_theme_filter_failed", False))}
    return rows, meta


def stage_backtest(cfg, codes, start, end, capital):
    from src.core.backtest_engine import BacktestEngine
    from src.cli.main import normalize_stock_code
    res = {}
    for code in codes:
        try:
            eng = BacktestEngine(
                stock_code=normalize_stock_code(code), start_date=start, end_date=end,
                initial_capital=capital, execution_mode="framework_strict",
                layer_mode="decision_strategy_execution",
                skills_dir=cfg.get("skills", {}).get("dir", "./src/skills"),
                signal_weights=cfg.get("decision", {}).get("signal_weights"),
                skill_types=cfg.get("skills", {}).get("types"),
                entry_exit_config=cfg.get("entry_exit"))
            r = eng.run()
            res[code] = {"total_return_pct": round(r.total_return_pct, 2),
                         "benchmark_pct": round(r.benchmark_return_pct, 2),
                         "excess_pct": round(r.total_return_pct - r.benchmark_return_pct, 2),
                         "max_dd_pct": round(r.max_drawdown_pct, 2), "trades": r.total_trades,
                         "win_rate": round(r.win_rate, 1), "sharpe": round(r.sharpe_ratio, 2)}
        except Exception as e:
            res[code] = {"error": f"{type(e).__name__}: {e}"}
    return res


def stage_bz(codes_names):
    from src.core.benzong.auto_scorer import auto_score
    out = {}
    for code, name in codes_names:
        try:
            r = auto_score(code, name)
            s = r.score
            grade = s.effective_grade() if hasattr(s, "effective_grade") else s.grade()
            degraded = [k for k, m in (r.dimensions_meta or {}).items()
                        if isinstance(m, dict) and m.get("confidence", 1) == 0]
            out[code] = {"total": s.total_score, "grade": grade,
                         "confidence": r.overall_confidence, "degraded_dims": degraded,
                         "invalidate": r.invalidate}
        except Exception as e:
            out[code] = {"error": f"{type(e).__name__}: {e}"}
    return out


def stage_sizing(cands, holdings, nav, risk_profile):
    """Research targets only: ratios and a NAV estimate do not establish cash/shares."""
    cap = (risk_profile or {}).get("per_stock_max", 0.2)
    plans = []
    for c in cands:
        if c.get("error") or c.get("position_action") not in ("OPEN", "ADD"):
            continue
        plans.append({"code": c["code"], "name": c["name"],
                      "target_ratio": c.get("target_ratio"),
                      "shares": None, "cost": None, "stop_2atr": None,
                      "risk_at_stop": None, "risk_pct_nav": None,
                      "status": "UNKNOWN", "note": QUANTITY_PENDING})
    # list_positions may be incomplete; even an empty list cannot establish cash.
    # This prototype has no independent quantity/cash allocator wired in.
    return {"exposure": None, "cash_ratio": None, "per_stock_max": cap,
            "status": "UNKNOWN", "note": QUANTITY_PENDING, "plans": plans}


def ai_summary(cfg, payload):
    from openai import OpenAI
    from src.core.ai_model import thinking_disabled_body
    p = cfg.get("ai", {}).get("provider", "deepseek")
    pc = cfg.get("ai", {}).get(p, {})
    key = pc.get("api_key") or os.environ.get(f"{p.upper()}_API_KEY")
    if not key:
        return None
    model = pc.get("model", "deepseek-flash")
    cli = OpenAI(api_key=key, base_url=pc.get("base_url"), timeout=90)
    msg = (f"今天是 {datetime.now():%Y-%m-%d}（恐慌指数基准日可能是上一交易日）。你是A股交易纪律助手。基于下面的结构化流水线结果（规则引擎+策略层输出，非你自己的判断），"
           "用中文写一份 300 字以内的晨报：①大盘与情绪一句话 ②持仓中最需要处理的 1-3 只及原因（引用止损/计划/信号）"
           "③候选是否值得关注（必须引用回测超额与评分，数据缺失要明说）④今日纪律提醒。"
           "不得编造数据；建议≠成交。\n\n" + json.dumps(payload, ensure_ascii=False, default=str)[:24000])
    r = cli.chat.completions.create(model=model, messages=[{"role": "user", "content": msg}],
                                    max_tokens=900, temperature=0.3, **thinking_disabled_body(model))
    return (r.choices[0].message.content or "").strip()


def fmt_md(R):
    def pct(value, digits=1):
        return "UNKNOWN" if value is None else f"{value:.{digits}%}"

    L = [f"# 暮云每日流水线报告 {R['generated_at']}", "",
         "> 只读原型输出：建议≠成交，未修改持仓、未记录建议。数据源：" + R["data_source"], ""]
    m = R.get("market") or {}
    idx, fear = m.get("index", {}), m.get("fear", {})
    L += ["## 1. 大盘", f"- 沪深300 收盘 {idx.get('close')}，趋势 **{idx.get('trend')}**，"
          f"MA20={idx.get('ma20')} MA60={idx.get('ma60')}",
          f"- 恐慌指数 {fear.get('score')}（{fear.get('tier')}，基准日 {fear.get('as_of')}，缺失成分 {fear.get('missing')}）", ""]
    s = R.get("sizing") or {}
    L += ["## 2. 持仓体检", f"总仓位 {pct(s.get('exposure'))}，可用现金 UNKNOWN，"
          f"单股上限 {pct(s.get('per_stock_max'), 0)}，NAV（用户估计/默认，非账户事实）{R['nav']:,.0f} 元",
          QUANTITY_PENDING, "",
          "| 代码 | 名称 | 现价 | 成本 | 浮盈% | 仓位 | 股数核验 | 决策 | 建议动作 | 卖出路径 | AI情绪 | 风险标记 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for h in R.get("holdings") or []:
        if h.get("error"):
            L.append(f"| {h['code']} | {h.get('name')} | 错误: {h['error']} |||||||||| |")
            continue
        ai = h.get("ai") or {}
        act = ACTION_CN.get(h["position_action"], h["position_action"])
        L.append(f"| {h['code']} | {h['name']} | {h['price']} | {h['entry_price']} | {h['pnl_pct']} | "
                 f"{pct(h.get('ratio'))} | UNKNOWN | {h['decision']} | {act} | {h.get('sell_path') or '-'} | "
                 f"{ai.get('sentiment', '-')} | {'；'.join(h.get('flags') or []) or '-'} |")
    cm = R.get("cand_meta") or {}
    L += ["", "## 3. 选股候选", f"规则 `{cm.get('rule')}` 主题 `{cm.get('theme')}`，"
          f"初筛 {cm.get('scanned')} 只，深度分析前 {len(R.get('candidates') or [])} 只"
          + ("（⚠ 主题过滤失败，按全市场）" if cm.get('theme_filter_failed') else "")
          + "。排序：建仓动作 > BUY > 笨总分+0.2×回测超额 > 规则评分；第一行=今日最佳候选，WATCH=只观察不买。", "",
          "| 代码 | 名称 | 现价 | 决策 | 动作 | 评分 | 回测收益% | 基准% | 超额% | 最大回撤% | 笨总 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    bt, bz = R.get("backtest") or {}, R.get("bz") or {}
    def _rank(c):
        z = (bz.get(c.get("code"), {}) or {}).get("total") or 0
        b = (bt.get(c.get("code"), {}) or {}).get("excess_pct") or 0
        return (c.get("position_action") in ("OPEN", "ADD"), c.get("decision") == "BUY", z + b * 0.2, c.get("score") or 0)
    for c in sorted([c for c in R.get("candidates") or [] if not c.get("error")], key=_rank, reverse=True) + \
            [c for c in R.get("candidates") or [] if c.get("error")]:
        if c.get("error"):
            L.append(f"| {c['code']} | {c.get('name')} | 错误 {c['error']} |||||||||")
            continue
        b, z = bt.get(c["code"], {}), bz.get(c["code"], {})
        zt = f"{z.get('total')}/{z.get('grade')}" if z.get("total") is not None else "-"
        L.append(f"| {c['code']} | {c['name']} | {c['price']} | {c['decision']} | "
                 f"{ACTION_CN.get(c['position_action'], c['position_action'])} | {c['score']} | "
                 f"{b.get('total_return_pct', '-')} | {b.get('benchmark_pct', '-')} | {b.get('excess_pct', '-')} | "
                 f"{b.get('max_dd_pct', '-')} | {zt} |")
    L += ["", "## 4. 仓位研究目标（数量待核）", "- " + QUANTITY_PENDING]
    if s.get("plans"):
        for p in s["plans"]:
            L.append(f"- {p['code']} {p['name']}：研究目标 {pct(p.get('target_ratio'))}；{p['note']}")
    else:
        L.append("- 无满足建仓动作的候选（无操作是合法结果）")
    hcodes = {h['code'] for h in R.get('holdings') or []}
    hb = {k: v for k, v in bt.items() if k in hcodes}
    if hb:
        L += ["", "## 5. 持仓策略回测（检验本策略在该股历史上是否有效）", "",
              "| 代码 | 策略收益% | 买入持有% | 超额% | 回撤% | 交易数 | 笨总 |", "|---|---|---|---|---|---|---|"]
        for k, b in hb.items():
            z = bz.get(k, {})
            zt = f"{z.get('total')}/{z.get('grade')}" if z.get("total") is not None else "-"
            L.append(f"| {k} | {b.get('total_return_pct')} | {b.get('benchmark_pct')} | {b.get('excess_pct')} | "
                     f"{b.get('max_dd_pct')} | {b.get('trades')} | {zt} |" if not b.get("error") else f"| {k} | {b['error']} ||||||")
    if R.get("ai_summary"):
        L += ["", "## 6. AI 晨报（DeepSeek，基于以上结构化结果）", "", R["ai_summary"]]
    L += ["", "## 附：阶段耗时", ""] + [f"- {x['stage']}: {'OK' if x['ok'] else 'FAIL ' + x.get('error', '')} {x['sec']}s"
                                   for x in R["stages"]]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nav", type=float, default=100000, help="NAV 用户估计值（默认10万元），仅作研究/回测输入，不证明账户现金或授权下单数量")
    ap.add_argument("--rule", default="healthy_pullback")
    ap.add_argument("--theme", default=None)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--bz", action="store_true", help="笨总 AI 评分（较慢、计费；当日缓存）")
    ap.add_argument("--bz-scope", choices=["cands", "holdings", "all"], default="all")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--bt-years", type=float, default=2.0)
    ap.add_argument("--backtest-holdings", action="store_true")
    ap.add_argument("--bt-top-cands", type=int, default=3, help="另外回测笨总分最高的前 N 只候选（即使是 WATCH）")
    ap.add_argument("--out", default=str(ROOT / "AI笔记" / "pipeline"))
    ap.add_argument("--tag", default="", help="输出文件名标签，如 premarket/postmarket")
    a = ap.parse_args()

    cfg = load_config()
    from src.chat import tools as T
    from src.data.portfolio import PortfolioManager
    T.init_engines(cfg)
    orch, scanner = T._orchestrator, T._scanner_engine
    pm = PortfolioManager()
    rp = (cfg.get("fusion") or {}).get("risk_profile") or {}
    log = []
    st = Stage(log)
    ai = not a.no_ai
    print("暮云每日流水线（只读）", flush=True)
    R = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "nav": a.nav,
         "nav_source": "USER_ESTIMATE_OR_DEFAULT", "account_nav": None,
         "data_source": ("腾讯/新浪垫片(MUYUN_DATA_SHIM)" if os.environ.get("MUYUN_DATA_SHIM") else "Baostock/AKShare 原生"),
         "stages": log}
    R["market"] = st.run("1 大盘与情绪", stage_market)
    R["holdings"] = st.run("2 持仓体检", stage_holdings, orch, pm, a.nav, rp, ai) or []
    cands, meta = st.run("3 选股初筛+深度分析", stage_candidates, orch, pm, scanner, a.rule, a.theme, a.top, ai) or ([], {})
    R["candidates"], R["cand_meta"] = cands, meta
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=int(365 * a.bt_years))).strftime("%Y-%m-%d")
    bt_codes = [c["code"] for c in cands if not c.get("error")
                and (c.get("position_action") in ("OPEN", "ADD") or c.get("decision") == "BUY")]
    if a.backtest_holdings:
        bt_codes += [h["code"] for h in R["holdings"] if not h.get("error")]
    R["backtest"] = st.run("4 回测验证", stage_backtest, cfg, list(dict.fromkeys(bt_codes)), start, end,
                           max(a.nav, 100000)) or {}
    if a.bz:
        names = []
        if a.bz_scope in ("cands", "all"):
            names += [(c["code"], c.get("name", "")) for c in cands if not c.get("error")][:a.top]
        if a.bz_scope in ("holdings", "all"):
            names += [(h["code"], h.get("name", "")) for h in R["holdings"] if not h.get("error")]
        R["bz"] = st.run("5 笨总评分", stage_bz, names) or {}
    if a.bt_top_cands and R.get("bz"):
        ranked = sorted([c for c in cands if not c.get("error") and c["code"] not in R["backtest"]],
                        key=lambda c: (R["bz"].get(c["code"], {}) or {}).get("total") or 0, reverse=True)
        extra = st.run("4b 候选补充回测", stage_backtest, cfg, [c["code"] for c in ranked[:a.bt_top_cands]],
                       start, end, max(a.nav, 100000)) or {}
        R["backtest"].update(extra)
    R["sizing"] = st.run("6 仓位换算", stage_sizing, cands, R["holdings"], a.nav, rp) or {}
    if ai:
        slim = {k: R.get(k) for k in ("market", "holdings", "candidates", "backtest", "bz", "sizing")}
        R["ai_summary"] = st.run("7 AI 晨报", ai_summary, cfg, slim)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M") + (f"_{a.tag}" if a.tag else "")
    (out / f"pipeline_{stamp}.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    md = fmt_md(R)
    (out / f"pipeline_{stamp}.md").write_text(md, encoding="utf-8")
    (out / "latest.md").write_text(md, encoding="utf-8")
    if a.tag:
        (out / f"latest_{a.tag}.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"已保存: {out}/pipeline_{stamp}.md（chat 中可说：读取 AI笔记/pipeline/latest.md 并回答…）")


if __name__ == "__main__":
    main()
