"""行业数据层 (v1, ISS-061) - chat 行业分析的结构化数据供给。

三层职责：
1. 产业链图谱（configs/industry_chains.yaml）：环节-公司-关键变量静态知识 + 持仓交叉定位
2. 动态数据（akshare 实测接口）：商品价格/现货基差/仓单/需求月度数据
3. 组装：build_industry_report 拼接"行业数据包"给 chat analyze_industry 工具

设计约束：
- 每个网络调用 daemon 线程 + 25s 超时（不挂起 chat）
- TTL 缓存 1 小时（日线/月度数据，重复调用秒回）
- 新鲜度守卫：期货日线最后交易日距今 >15 天视为休眠合约（如动力煤 ZC0），
  标注[数据过期]而非当新数据用（2026-08-15 实测发现 ZC0 停在 2022-12）
- 单项失败降级标注[数据缺失]，不整体失败（ISS-059 失败语义）
- 已实测接口坑：futures_spot_price 参数是 vars_list 不是 symbol；
  PMI/PPI 表倒序（head 是最新）；乘用车产量表 2026 列未来月份为 NaN
"""
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

GRAPH_PATH = "./configs/industry_chains.yaml"
_CACHE_TTL = 3600          # 日线/月度数据 1 小时缓存
_STALE_DAYS = 15           # 期货日线最后交易日距今超过此天数 = 休眠/过期
_MISSING = "[数据缺失]"
_cache: dict = {}          # key -> (ts, text)
_graph_cache: dict | None = None

# 品种单位（期货报价单位不同品种不同）
_VAR_UNITS = {"LC": "元/吨", "PS": "元/吨", "SI": "元/吨", "AU": "元/克"}


def _fetch_with_timeout(fn, timeout: int = 25):
    """daemon 线程 + 超时包裹（复用 chat tools 的防挂起模式）。返回 (ok, result/err_str)。"""
    box = [None, None]

    def _f():
        try:
            box[0] = fn()
        except Exception as e:
            box[1] = f"{type(e).__name__}: {str(e)[:80]}"

    t = threading.Thread(target=_f, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return False, "超时"
    if box[1]:
        return False, box[1]
    return True, box[0]


def _cached(key: str, fn):
    """TTL 缓存包装：fn() 直接返回文本，抛异常视作失败。
    成功缓存 1 小时；失败/缺失结果缓存 5 分钟（避免反复打挂掉的接口）。"""
    now = time.time()
    hit = _cache.get(key)
    if hit:
        ts, text = hit
        ttl = _CACHE_TTL if not text.startswith((_MISSING, "[数据过期")) else 300
        if now - ts < ttl:
            return text
    try:
        text = fn()
    except Exception as e:
        text = f"{_MISSING}（{type(e).__name__}: {str(e)[:60]}）"
    _cache[key] = (now, text)
    return text


# ── 图谱加载与匹配 ─────────────────────────────────────────

def load_chains() -> dict:
    """加载产业链图谱（进程内缓存）。失败返回空 dict（工具层降级）。"""
    global _graph_cache
    if _graph_cache is not None:
        return _graph_cache
    try:
        with open(GRAPH_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _graph_cache = data.get("chains", {}) or {}
    except Exception as e:
        logger.warning(f"产业链图谱加载失败: {e}")
        _graph_cache = {}
    return _graph_cache


def match_chain(query: str):
    """按别名/链名匹配图谱。返回 (链名, 链配置) 或 None。

    匹配规则：query 包含别名，或别名包含 query（双向，长度>=2 防单字误匹配；
    单字别名如"锂"仅当 query 与别名完全相等时命中）。
    """
    q = (query or "").strip()
    if not q:
        return None
    chains = load_chains()
    best = None  # 最长匹配优先（"碳酸锂"优先于"锂"）
    for name, cfg in chains.items():
        for alias in [name] + list(cfg.get("aliases", [])):
            a = (alias or "").strip()
            if not a:
                continue
            if q == a or (len(a) >= 2 and len(q) >= 2 and (a in q or q in a)):
                if best is None or len(a) > len(best[1]):
                    best = (name, a)
    if best is None:
        return None
    return best[0], chains[best[0]]


def _company_codes(chain_cfg: dict) -> dict:
    """提取图谱内全部 6 位代码 -> (环节路径, 公司标注)。供持仓交叉定位。"""
    code_map = {}
    for sec_name, sections in (chain_cfg.get("sections") or {}).items():
        for sec in sections or []:
            stage = f"{sec_name}·{sec.get('环节', '')}"
            for comp in sec.get("代表公司", []):
                m = re.match(r"(\d{6})\s+([^(（]+)", comp)
                if m:
                    code_map[m.group(1)] = (stage, m.group(2).strip())
    return code_map


# ── 纯格式化函数（可单测，不碰网络）───────────────────────

def format_daily(df, var: str) -> str:
    """期货日线 -> 价格趋势摘要（最新价/90日区间分位/新鲜度守卫）。df: date/open/high/low/close"""
    if df is None or len(df) == 0:
        return f"{_MISSING}（日线为空）"
    try:
        last = df.iloc[-1]
        last_date = str(last["date"])
        unit = _VAR_UNITS.get(var, "")
        # 新鲜度守卫：休眠合约（如 ZC0 动力煤停在 2022-12）不许当新数据
        try:
            d = datetime.strptime(last_date[:10], "%Y-%m-%d")
            stale_days = (datetime.now() - d).days
        except ValueError:
            stale_days = -1
        stale_mark = ""
        if stale_days > _STALE_DAYS or stale_days < 0:
            stale_mark = f" ⚠️[数据过期：最后交易日 {last_date}，合约疑似休眠，不可作当前价格依据]"
            # 过期数据只报事实不报分析
            return (f"最新收盘 {last['close']:g} {unit}（{last_date}）{stale_mark}")
        close = float(last["close"])
        w90 = df.tail(90)["close"].astype(float)
        lo90, hi90 = w90.min(), w90.max()
        pct90 = (close - lo90) / (hi90 - lo90) * 100 if hi90 > lo90 else 50.0
        y250 = df.tail(250)["close"].astype(float)
        lo1y, hi1y = y250.min(), y250.max()
        pct1y = (close - lo1y) / (hi1y - lo1y) * 100 if hi1y > lo1y else 50.0
        chg90 = (close / float(df.tail(91).iloc[0]["close"]) - 1) * 100 if len(df) >= 91 else float("nan")
        return (
            f"最新收盘 {close:g} {unit}（{last_date}）；"
            f"近90日区间 {lo90:g}~{hi90:g} {unit}，现价处 {pct90:.0f}% 分位；"
            f"近一年区间 {lo1y:g}~{hi1y:g}，处 {pct1y:.0f}% 分位；近90日涨跌 {chg90:+.1f}%"
            f"（分位低=价格近底、分位高=近顶，非买卖信号）"
        )
    except Exception as e:
        return f"{_MISSING}（日线解析失败: {type(e).__name__}）"


def format_spot(df, vars_needed: list) -> str:
    """现货/基差表 -> 每品种一行（现货价/主力价/基差及升贴水含义）。"""
    if df is None or len(df) == 0:
        return f"{_MISSING}（现货数据为空）"
    lines = []
    try:
        for _, row in df.iterrows():
            var = str(row.get("symbol", ""))
            unit = _VAR_UNITS.get(var.upper(), "")
            spot = row.get("spot_price")
            dom = row.get("dominant_contract_price")
            basis = row.get("dom_basis")
            date = str(row.get("date", ""))
            try:
                s, dm, b = float(spot), float(dom), float(basis)
                direction = "现货升水（近端偏紧）" if b > 0 else "现货贴水（近端过剩/仓单压力）"
                lines.append(
                    f"{var} 现货 {s:g} {unit}，主力合约 {dm:g}（{date[:8]}），基差 {b:+g} {direction}"
                )
            except (TypeError, ValueError):
                continue
        return "\n".join(lines) if lines else f"{_MISSING}（现货数据解析为空）"
    except Exception as e:
        return f"{_MISSING}（现货解析失败: {type(e).__name__}）"


def format_inventory(df) -> str:
    """仓单表 -> 最新库存 + 30日趋势（累库/去库方向）。"""
    if df is None or len(df) == 0:
        return f"{_MISSING}（仓单为空）"
    try:
        last = df.iloc[-1]
        cur, date = float(last["库存"]), str(last["日期"])
        w30 = df.tail(30)["库存"].astype(float)
        base30 = float(w30.iloc[0])
        chg = (cur / base30 - 1) * 100 if base30 else float("nan")
        direction = "持续累库（供给过剩或需求疲弱信号）" if chg > 2 else (
            "持续去库（供需收紧信号）" if chg < -2 else "基本持平")
        return f"最新仓单 {cur:g}（{date}），近30日 {chg:+.1f}% {direction}（仓单=交割库库存代理，非全社会库存）"
    except Exception as e:
        return f"{_MISSING}（仓单解析失败: {type(e).__name__}）"


def format_demand(dfs: dict) -> str:
    """需求数据组 -> 多行摘要。dfs 键: nev/car_sales/man_rank/electricity（可为 None）。"""
    lines = []
    nev = dfs.get("nev")
    if nev is not None and len(nev):
        try:
            r = nev.iloc[-1]
            lines.append(f"新能源车渗透率（乘联会）: {r['月份']} NEV {r['NEV']:g}% / 燃油车 {r['ICE']:g}%")
        except Exception:
            pass
    cs = dfs.get("car_sales")
    if cs is not None and len(cs):
        try:
            # 2026 列未来月份为 NaN，取最后一个非空行
            valid = cs[cs["2026年"].notna()]
            if len(valid):
                r = valid.iloc[-1]
                yoy = (float(r["2026年"]) / float(r["2025年"]) - 1) * 100 if r["2025年"] else float("nan")
                lines.append(
                    f"狭义乘用车产量（乘联会）: {r['月份']} {r['2026年']:.1f} 万辆，同比 {yoy:+.1f}%")
        except Exception:
            pass
    mr = dfs.get("man_rank")
    if mr is not None and len(mr):
        try:
            # 列名随月份变（如 '2026年7月'），取后两列稳定拿 厂商/最新月
            cols = list(mr.columns)
            latest_col = cols[-1]
            prev_col = cols[-2]
            top = mr.head(3)
            parts = []
            for _, r in top.iterrows():
                v = r[latest_col]
                if v == v:  # 非 NaN
                    parts.append(f"{r['厂商']} {float(v):.1f}万辆")
            lines.append(f"厂商批发榜Top3（{latest_col}）: " + "、".join(parts) + f"（对照列 {prev_col}）")
        except Exception:
            pass
    ele = dfs.get("electricity")
    if ele is not None and len(ele):
        try:
            r = ele.iloc[-1]
            lines.append(
                f"全社会用电量（能源局）: {r['统计时间']} 同比 {r['全社会用电量同比']:+.1f}%")
        except Exception:
            pass
    if not lines:
        return f"{_MISSING}（该行业无免费需求端接口，用新闻/政策代理）"
    return "\n".join(lines)


def format_macro() -> str:
    """宏观景气底色：PMI + PPI（表倒序，head 是最新）。带缓存。"""
    def _pull():
        import akshare as ak
        pmi = ak.macro_china_pmi().head(1).iloc[0]
        ppi = ak.macro_china_ppi().head(1).iloc[0]
        mfg = float(pmi["制造业-指数"])
        nonmfg = float(pmi["非制造业-指数"])
        ppi_yoy = float(ppi["当月同比增长"])
        zone = "扩张区间" if mfg >= 50 else "收缩区间"
        return (
            f"制造业PMI {mfg:.1f}（{zone}）、非制造业 {nonmfg:.1f}（{pmi['月份']}）；"
            f"PPI 同比 {ppi_yoy:+.1f}%（{ppi['月份']}，上游价格{'回升' if ppi_yoy > 0 else '回落'}）"
        )

    def _wrapped():
        ok, result = _fetch_with_timeout(_pull)
        if not ok:
            raise RuntimeError(str(result))
        return result

    return _cached("macro", _wrapped)


def _spot_dates(n: int = 4):
    """现货接口日期候选：今天起往前 n 天（周末/节假日当天返回空表，需回退重试）。"""
    d = datetime.now()
    return [(d - timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


# ── 数据组装（网络层，带缓存）──────────────────────────────

def get_commodity_section(chain_cfg: dict) -> str:
    """商品价格组：日线 + 现货基差 + 仓单。未绑定的链返回缺口说明。"""
    com = chain_cfg.get("commodity")
    if not com:
        return f"{_MISSING}（该行业无商品期货价格绑定，价格维度用新闻代理）"
    name = com.get("name", "")

    def _pull():
        import akshare as ak
        parts = []
        daily_sym = com.get("daily_symbol")
        if daily_sym:
            ok, df = _fetch_with_timeout(lambda: ak.futures_zh_daily_sina(symbol=daily_sym))
            parts.append(f"- 期货{daily_sym}: " + (format_daily(df, daily_sym[:2]) if ok else f"{_MISSING}（{df}）"))
        spot_vars = com.get("spot_vars") or []
        if spot_vars:
            # 当天非交易日返回空表 -> 依次回退最近4天，仍空则降级
            spot_text = f"{_MISSING}（现货数据为空）"
            for date_str in _spot_dates():
                ok, df = _fetch_with_timeout(
                    lambda d=date_str: ak.futures_spot_price(d, vars_list=spot_vars))
                if ok and df is not None and len(df):
                    spot_text = format_spot(df, spot_vars)
                    break
                if not ok:
                    spot_text = f"{_MISSING}（{df}）"
                    break
            parts.append("- 现货/基差: " + spot_text)
        inv_sym = com.get("inventory_symbol")
        if inv_sym:
            ok, df = _fetch_with_timeout(lambda: ak.futures_inventory_em(symbol=inv_sym))
            parts.append(f"- {inv_sym}仓单(库存代理): " + (format_inventory(df) if ok else f"{_MISSING}（{df}）"))
        return "\n".join(parts)

    return _cached(f"commodity:{name}", _pull)


def get_demand_section(chain_cfg: dict) -> str:
    """需求组：按链绑定的数据源拉取。"""
    wanted = chain_cfg.get("demand_data") or []

    def _pull():
        import akshare as ak
        dfs = {}
        if "nev_penetration" in wanted:
            ok, dfs["nev"] = _fetch_with_timeout(
                lambda: ak.car_market_fuel_cpca(symbol="新能源"))
            if not ok:
                dfs["nev"] = None
        if "car_sales" in wanted:
            ok, dfs["car_sales"] = _fetch_with_timeout(
                lambda: ak.car_market_total_cpca(symbol="狭义乘用车", indicator="产量"))
            if not ok:
                dfs["car_sales"] = None
            ok, dfs["man_rank"] = _fetch_with_timeout(
                lambda: ak.car_market_man_rank_cpca(symbol="狭义乘用车-单月", indicator="批发"))
            if not ok:
                dfs["man_rank"] = None
        if "electricity" in wanted:
            ok, dfs["electricity"] = _fetch_with_timeout(
                lambda: ak.macro_china_society_electricity())
            if not ok:
                dfs["electricity"] = None
        text = format_demand(dfs)
        # 动力电池装机/储能装机：akshare 无接口，固定诚实标注（锂电链）
        if "nev_penetration" in wanted:
            text += "\n[数据缺口] 动力电池装机量/储能装机量无免费接口，用销量渗透率+排产新闻代理"
        return text

    return _cached(f"demand:{chain_cfg.get('aliases', [''])[0]}", _pull)


def format_chain_graph(name: str, chain_cfg: dict, positions: list | None = None) -> str:
    """图谱文本化：环节结构 + 周期锚点 + 分析要点 + 持仓交叉定位。"""
    lines = [f"【{name}产业链结构】 {chain_cfg.get('description', '')}", ""]
    for sec_name, sections in (chain_cfg.get("sections") or {}).items():
        lines.append(f"{sec_name}:")
        for sec in sections or []:
            feature = f"（{sec['特点']}）" if sec.get("特点") else ""
            lines.append(f"  - {sec.get('环节', '')}{feature}")
            for comp in sec.get("代表公司", []):
                lines.append(f"      {comp}")
    anchors = chain_cfg.get("cycle_anchors") or []
    if anchors:
        lines.append("")
        lines.append("周期位置锚点: " + "；".join(anchors))
    notes = chain_cfg.get("analysis_notes") or []
    if notes:
        lines.append("分析要点: " + "；".join(notes))
    # 持仓交叉定位
    if positions:
        code_map = _company_codes(chain_cfg)
        hits = []
        for p in positions:
            code = str(p.get("stock_code", "")).split(".")[0]
            if code in code_map:
                stage, comp_name = code_map[code]
                ratio = p.get("current_ratio")
                ratio_s = f" 仓位{ratio*100:.0f}%" if isinstance(ratio, (int, float)) else ""
                hits.append(f"{code} {comp_name}{ratio_s} -> 位于{stage}")
        if hits:
            lines.append("")
            lines.append("【用户持仓在链条中的位置】")
            lines.extend(f"  - {h}" for h in hits)
    return "\n".join(lines)


def build_industry_report(query: str, positions: list | None = None) -> str:
    """组装完整行业数据包（chat analyze_industry 工具入口）。

    顺序：图谱(含持仓定位) -> 商品价格 -> 需求 -> 供给说明 -> 宏观底色。
    图谱未命中返回以 [工具失败] 开头的提示（ISS-059 语义）。
    """
    matched = match_chain(query)
    if not matched:
        chains = load_chains()
        supported = "、".join(
            f"{n}({','.join(list(c.get('aliases', []))[:4])})" for n, c in chains.items())
        return f"[工具失败] 未识别行业'{query}'。当前图谱支持: {supported}"
    name, cfg = matched
    sections = [
        format_chain_graph(name, cfg, positions),
        "",
        f"【商品价格】\n{get_commodity_section(cfg)}",
        "",
        f"【需求端】\n{get_demand_section(cfg)}",
        "",
        f"【供给端】\n{cfg.get('supply_note', '')}",
        "",
        f"【宏观底色】\n{format_macro()}",
    ]
    return "\n".join(sections)


def clear_cache():
    """清空 TTL 缓存（测试用）。"""
    _cache.clear()
