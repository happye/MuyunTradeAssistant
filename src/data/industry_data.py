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

_CACHE_TTL = 3600          # 日线/月度数据 1 小时缓存
_STALE_DAYS = 15           # 期货日线最后交易日距今超过此天数 = 休眠/过期
_MISSING = "[数据缺失]"
_cache: dict = {}          # key -> (ts, text)

# 品种单位（期货报价单位不同品种不同）
_VAR_UNITS = {"LC": "元/吨", "PS": "元/吨", "SI": "元/吨", "AU": "元/克", "AG": "元/千克"}

# 全行业商品映射：行业关键词 -> [(期货品种代码, 展示名), ...]
# 现货锚来自 spot_price_table_qh 82品种表；日线 = 品种代码+"0"（新浪连续合约）。
# 未实测过的品种靠降级兜底（现货空/日线失败 -> [数据缺失]），不影响整体。
COMMODITY_MAP: dict = {
    "生猪养殖链": {
        "keywords": ["生猪", "猪", "养殖", "猪肉", "牧原", "猪周期"],
        "vars": [("LH", "生猪"), ("C", "玉米"), ("M", "豆粕")],
    },
    "钢铁链": {
        "keywords": ["钢铁", "钢", "螺纹", "铁矿石", "建材"],
        "vars": [("RB", "螺纹钢"), ("I", "铁矿石"), ("J", "焦炭"), ("JM", "焦煤")],
    },
    "有色链": {
        "keywords": ["有色", "铜", "铝", "锌", "镍", "锡", "电解铝", "铜矿"],
        "vars": [("CU", "铜"), ("AL", "铝"), ("ZN", "锌"), ("NI", "镍"), ("SN", "锡")],
    },
    "贵金属链": {
        "keywords": ["白银", "贵金属"],
        "vars": [("AG", "白银"), ("AU", "黄金")],
    },
    "煤炭链": {
        "keywords": ["焦煤", "焦炭", "动力煤"],
        "vars": [("JM", "焦煤"), ("J", "焦炭")],
    },
    "石化链": {
        "keywords": ["石化", "原油", "沥青", "燃油", "石油"],
        "vars": [("SC", "原油"), ("FU", "燃料油"), ("BU", "沥青")],
    },
    "化工链": {
        "keywords": ["化工", "聚酯", "塑料", "PVC", "甲醇", "PTA", "涤纶"],
        "vars": [("TA", "PTA"), ("MA", "甲醇"), ("PP", "聚丙烯"), ("V", "PVC")],
    },
    "玻璃纯碱链": {
        "keywords": ["玻璃", "纯碱", "光伏玻璃"],
        "vars": [("FG", "玻璃"), ("SA", "纯碱")],
    },
    "造纸链": {
        "keywords": ["造纸", "纸浆", "文化纸"],
        "vars": [("SP", "纸浆")],
    },
    "橡胶轮胎链": {
        "keywords": ["橡胶", "轮胎"],
        "vars": [("RU", "橡胶")],
    },
    "种植链": {
        "keywords": ["种植", "白糖", "棉花", "苹果", "花生", "农业", "种业"],
        "vars": [("SR", "白糖"), ("CF", "棉花"), ("AP", "苹果"), ("PK", "花生")],
    },
    "油脂饲料链": {
        "keywords": ["饲料", "豆粕", "油脂", "豆油", "压榨"],
        "vars": [("M", "豆粕"), ("Y", "豆油"), ("P", "棕榈油")],
    },
    "化肥链": {
        "keywords": ["化肥", "尿素", "磷肥"],
        "vars": [("UR", "尿素")],
    },
}

# 提问里常见的非行业词后缀（板块匹配前剔除，提高命中率）
_QUERY_STRIP_WORDS = ["板块", "行业", "概念", "股票", "怎么样", "怎么看", "看法", "分析",
                     "前景", "周期", "走势", "行情", "还有", "我的", "持仓"]


def _clean_query(query: str) -> str:
    q = (query or "").strip()
    for w in _QUERY_STRIP_WORDS:
        q = q.replace(w, "")
    return q.strip()


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

GRAPH_PATH = "./configs/industry_chains.yaml"
# 自举图谱：AI首次完整分析非图谱行业后，通过 save_chain_graph 工具沉淀的结构。
# 与手写图谱同 schema；手写优先（同名冲突时），来源标记为 auto（输出时注明未经人工复核）。
AUTO_GRAPH_PATH = "./configs/industry_chains_auto.yaml"
_graph_cache: dict | None = None
_graph_sources: dict = {}   # 链名 -> "manual" / "auto"


def load_chains() -> dict:
    """加载产业链图谱（手写+自举合并，进程内缓存）。失败返回空 dict（工具层降级）。"""
    global _graph_cache, _graph_sources
    if _graph_cache is not None:
        return _graph_cache
    chains, sources = {}, {}
    for path, tag in ((GRAPH_PATH, "manual"), (AUTO_GRAPH_PATH, "auto")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for name, cfg in (data.get("chains", {}) or {}).items():
                if isinstance(cfg, dict) and name not in chains:  # 手写优先
                    chains[name] = cfg
                    sources[name] = tag
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"产业链图谱加载失败({path}): {e}")
    _graph_cache, _graph_sources = chains, sources
    return _graph_cache


def save_auto_chain(name: str, graph: dict) -> str:
    """把AI梳理的产业链结构沉淀到自举图谱文件（save_chain_graph 工具入口）。

    校验 schema（必须含 sections）后写入 AUTO_GRAPH_PATH 并刷新缓存；
    同名覆盖。graph 由调用方（chat 工具层）解析 YAML 得到。
    """
    import copy
    name = (name or "").strip()
    if not name or len(name) > 20:
        raise ValueError(f"链名无效: {name!r}")
    if not isinstance(graph, dict) or not graph.get("sections"):
        raise ValueError("图谱必须包含 sections（上中下游环节结构）")
    sections = graph["sections"]
    if not isinstance(sections, dict) or not any(sections.values()):
        raise ValueError("sections 必须是非空 dict（如 {上游: [...], 下游: [...]}）")
    # 大小保护：防模型输出超长结构撑爆文件
    graph = copy.deepcopy(graph)
    graph["aliases"] = [str(a)[:12] for a in (graph.get("aliases") or [])][:10]
    graph["source_note"] = "AI自举生成（基于当次板块成分股+主营构成数据），未经人工复核"

    data = {"chains": {}}
    try:
        with open(AUTO_GRAPH_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {"chains": {}}
        if not isinstance(data.get("chains"), dict):
            data = {"chains": {}}
    except FileNotFoundError:
        pass
    data["chains"][name] = graph
    with open(AUTO_GRAPH_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=100)
    # 刷新缓存
    global _graph_cache, _graph_sources
    _graph_cache, _graph_sources = None, {}
    load_chains()
    return f"已沉淀「{name}」产业链图谱（自举，下次分析直接复用）"


def graph_source(name: str) -> str:
    """图谱来源：manual（手写）/ auto（AI自举）/ ""（不在图谱）。"""
    load_chains()
    return _graph_sources.get(name, "")


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


def match_commodity(query: str):
    """行业词 -> 商品链映射。返回 (链名, vars列表) 或 None。"""
    q = _clean_query(query)
    if not q:
        return None
    best = None
    for chain_name, cfg in COMMODITY_MAP.items():
        for kw in cfg["keywords"]:
            if kw == q or (len(kw) >= 2 and len(q) >= 2 and (kw in q or q in kw)):
                if best is None or len(kw) > best[1]:
                    best = (chain_name, len(kw))
    if best is None:
        return None
    return best[0], COMMODITY_MAP[best[0]]["vars"]


def get_generic_commodity_section(query: str) -> str:
    """通用商品价格锚（图谱外行业）：现货基差 + 日线趋势，命中映射才拉。"""
    matched = match_commodity(query)
    if not matched:
        return f"[数据缺口] 该行业未映射到商品期货（无价格锚），价格维度用新闻/财报代理"
    chain_name, vars_list = matched
    var_codes = [v for v, _ in vars_list]
    unit_hint = "；".join(f"{name}={code}" for code, name in vars_list)

    def _pull():
        import akshare as ak
        parts = [f"{chain_name}价格锚（品种: {unit_hint}）:"]
        # 现货/基差：日期回退最近4天
        spot_lines = None
        for date_str in _spot_dates():
            ok, df = _fetch_with_timeout(
                lambda d=date_str: ak.futures_spot_price(d, vars_list=var_codes))
            if ok and df is not None and len(df):
                spot_lines = format_spot(df, var_codes)
                break
            if not ok:
                spot_lines = f"{_MISSING}（{df}）"
                break
        parts.append("- 现货/基差: " + (spot_lines or f"{_MISSING}（现货数据为空）"))
        # 日线（每个品种一条，单品种失败降级）
        for var, name in vars_list:
            ok, df = _fetch_with_timeout(
                lambda s=f"{var}0": ak.futures_zh_daily_sina(symbol=s))
            parts.append(f"- {name}: " + (format_daily(df, var) if ok else f"{_MISSING}（{df}）"))
        return "\n".join(parts)

    return _cached(f"generic_commodity:{chain_name}", _pull)


def fetch_main_business(stock_code: str) -> str:
    """个股主营构成（东财，必须带交易所前缀）。失败抛异常，调用方兜底。

    供 chat get_main_business 工具与全行业引擎的成分股抽样共用。
    """
    code = (stock_code or "").strip().split(".")[0]
    if not (code.isdigit() and len(code) == 6):
        raise ValueError(f"股票代码格式错误: {stock_code}（需6位数字）")
    if code.startswith("6"):
        symbol = f"SH{code}"
    elif code.startswith(("0", "3")):
        symbol = f"SZ{code}"
    else:
        raise ValueError(f"暂不支持该板块代码: {code}（仅沪深A股）")

    import akshare as ak
    df = ak.stock_zygc_em(symbol=symbol)
    if df is None or len(df) == 0:
        raise ValueError(f"未获取到 {code} 的主营构成数据")
    # 列（2026-08 实测）: 股票代码/报告日期/分类类型/主营构成/主营收入/收入比例/主营成本/成本比例/主营利润/利润比例/毛利率
    try:
        cat_col = next(c for c in df.columns if "分类" in str(c))
        prod = df[df[cat_col].astype(str).str.contains("产品", na=False)]
    except StopIteration:
        prod = df
    if len(prod) == 0:
        prod = df
    period = ""
    try:
        date_col = next(c for c in df.columns if "日期" in str(c) or "报告" in str(c))
        latest = prod[date_col].astype(str).max()
        prod = prod[prod[date_col].astype(str) == latest]
        period = f"（报告期 {latest}）"
    except StopIteration:
        pass
    name_col = next((c for c in prod.columns if "构成" in str(c)), None)
    ratio_col = next((c for c in prod.columns if "收入比例" in str(c)), None)
    profit_col = next((c for c in prod.columns if "利润比例" in str(c)), None)
    lines = [f"{code} 主营构成（按产品）{period}:"]
    for _, r in prod.head(6).iterrows():
        name = str(r[name_col]) if name_col is not None else ""
        seg = name
        if ratio_col is not None:
            try:
                seg += f": 收入占比 {float(r[ratio_col])*100:.1f}%"
            except (TypeError, ValueError):
                pass
        if profit_col is not None:
            try:
                pv = float(r[profit_col]) * 100
                seg += f"，利润占比 {pv:.1f}%"
            except (TypeError, ValueError):
                pass
        lines.append(f"  - {seg}")
    return "\n".join(lines)


def _lcs_len(a: str, b: str) -> int:
    """最长公共子串长度（板块名模糊匹配用，串都很短）。"""
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            best = max(best, k)
    return best


def _resolve_board(query: str, scanner_engine):
    """行业词 -> THS行业/概念板块成分股。返回 (板块名, 板块类型, 代码列表) 或 None。

    匹配分级：精确相等 > 互相包含 > 最长公共子串>=2（如"生猪养殖"->"养殖业"，
    共享"养殖"）。多个候选时取更短板块名（更精确）。
    """
    if scanner_engine is None:
        return None
    q = _clean_query(query)
    if len(q) < 2:
        return None
    try:
        for board_type, list_fn, cons_attr in (
            ("行业", scanner_engine.get_industry_list, "get_stocks_by_industry"),
            ("概念", scanner_engine.get_concept_list, "get_stocks_by_concept"),
        ):
            boards = list_fn() or []
            best = None       # 板块名
            best_rank = 0     # 0=不匹配 1=LCS 2=包含 3=精确
            for b in boards:
                name = b.get("name", "")
                if not name or len(name) < 2:
                    continue
                if q == name:
                    rank = 3
                elif q in name or name in q:
                    rank = 2
                else:
                    lcs = _lcs_len(q, name)
                    rank = 1 if lcs >= 2 else 0
                if rank == 0:
                    continue
                # 高等级优先；同等级取更短板块名（更精确）
                if best is None or rank > best_rank or (
                        rank == best_rank and len(name) < len(best)):
                    best, best_rank = name, rank
            if best:
                fetcher = getattr(scanner_engine.market_cache, cons_attr, None)
                if fetcher is None:
                    continue
                ok, codes = _fetch_with_timeout(lambda b=best: fetcher(b), timeout=18)
                if ok and codes:
                    return best, board_type, list(codes)
        return None
    except Exception as e:
        logger.warning(f"板块解析失败: {e}")
        return None


def _board_section(query: str, positions: list | None, scanner_engine):
    """板块解析节（通用引擎与自举图谱路径共用）。

    返回 (节文本, [(code, name, mcap), ...])；板块未命中返回 (None, [])。
    内容：板块名/成分股数/总市值Top8/板块温度(涨跌中位数+涨跌家数+Top8 PE中位数)/持仓定位。
    """
    board = _resolve_board(query, scanner_engine)
    if not board:
        return None, []
    board_name, board_type, codes = board
    lines = [f"【行业板块解析】{query} -> 同花顺{board_type}板块「{board_name}」（成分股{len(codes)}只）"]
    top_stocks = []
    snap = None
    # 名称/市值/涨跌/PE：从全市场快照join（失败降级只列代码）
    try:
        snapshot = scanner_engine.market_cache.get_all_stocks()
        if "代码" in snapshot.columns:
            snap = snapshot[snapshot["代码"].isin(codes)].copy()
            if len(snap) and "总市值" in snap.columns:
                snap["总市值"] = _to_float(snap["总市值"])
                snap = snap.sort_values("总市值", ascending=False)
                top_stocks = [
                    (str(r["代码"]), str(r.get("名称", r["代码"])), r["总市值"])
                    for _, r in snap.head(8).iterrows()
                ]
    except Exception as e:
        logger.warning(f"成分股快照join失败（降级只列代码）: {e}")

    if top_stocks:
        mcap_str = ", ".join(
            f"{c} {n}" + (f"(市值{m/1e8:.0f}亿)" if m == m and m else "")
            for c, n, m in top_stocks)
        lines.append(f"总市值Top8: {mcap_str}")
    else:
        lines.append(f"成分股代码样本: {', '.join(list(codes)[:10])}")

    # 板块温度：涨跌中位数/涨跌家数/Top8 PE中位数（快照可用才有）
    if snap is not None and len(snap) and "涨跌幅" in snap.columns:
        try:
            chg = _to_float(snap["涨跌幅"]).dropna()
            if len(chg):
                up = int((chg > 0).sum())
                down = int((chg < 0).sum())
                stat = f"板块温度: 成分股涨跌中位数 {chg.median():+.2f}%（涨{up}/跌{down}）"
                if top_stocks and "市盈率-动态" in snap.columns:
                    top8_codes = [c for c, _, _ in top_stocks]
                    pe = _to_float(
                        snap[snap["代码"].isin(top8_codes)]["市盈率-动态"]).dropna()
                    pe = pe[(pe > 0) & (pe < 500)]
                    if len(pe):
                        stat += f"；市值Top8 PE中位数 {pe.median():.1f}"
                lines.append(stat)
        except Exception:
            pass

    # 持仓交叉定位
    if positions:
        code_set = {str(c) for c in codes}
        hits = []
        for p in positions:
            pc = str(p.get("stock_code", "")).split(".")[0]
            if pc in code_set:
                ratio = p.get("current_ratio")
                ratio_s = f" 仓位{ratio*100:.0f}%" if isinstance(ratio, (int, float)) else ""
                hits.append(f"{pc}{ratio_s}")
        if hits:
            lines.append(f"用户持仓在该板块内: {'、'.join(hits)}")
    return "\n".join(lines), top_stocks


def build_generic_report(query: str, positions: list | None = None,
                         scanner_engine=None, board=None) -> str:
    """全行业通用引擎（图谱外行业的分析路径）。

    组成：THS板块成分股(市值Top8+板块温度+持仓定位) + 主营构成抽样(市值前5+持仓股)
    + 商品价格锚(关键词映射命中才有) + 需求兜底 + 宏观底色 + 分析指引。
    与精链路径数据对等；差别仅在环节结构由AI现场梳理（随后可经 save_chain_graph 沉淀为图谱）。
    """
    parts = []

    section, top_stocks = _board_section(query, positions, scanner_engine)
    if section:
        parts.append(section)
    else:
        parts.append(f"【行业板块解析】未匹配到同花顺行业/概念板块（query清洗后为'{_clean_query(query)}'）。"
                     "可换更通用的板块名（如'白酒'、'军工'、'半导体'），或用具体股票代码提问。")

    # 主营构成抽样：市值前5 + 持仓在板块内的股（链条环节判断证据）
    if top_stocks:
        sample_codes = [c for c, _, _ in top_stocks[:5]]
        parts.append("")
        parts.append("【主营构成抽样（链条环节判断证据，市值前5+持仓股）】")
        for code in sample_codes:
            try:
                ok, text = _fetch_with_timeout(lambda c=code: fetch_main_business(c))
                parts.append(text if ok else f"{_MISSING}（{code} 主营获取失败: {text}）")
            except Exception:
                parts.append(f"{_MISSING}（{code} 主营获取失败）")

    # 商品价格锚
    parts.append("")
    parts.append(f"【商品价格锚】\n{get_generic_commodity_section(query)}")

    # 需求兜底（行业关键词猜数据源）
    parts.append("")
    parts.append(f"【需求端】\n{generic_demand_section(query)}")

    # 宏观底色（通用）
    parts.append("")
    parts.append(f"【宏观底色】\n{format_macro()}")

    parts.append("")
    parts.append(
        "【分析指引】请基于上方成分股+板块温度+主营构成抽样梳理上下游结构"
        "（环节/代表公司/成本利润特征），套用知识库\"供需平衡表与周期分析框架\"的四阶段方法作答；"
        "完成分析后调用 save_chain_graph 把你梳理的产业链结构沉淀为图谱（只写工具结果里出现过的代码，"
        "不确定的公司不要写），供下次直接复用；数据缺口如实说明。"
    )
    return "\n".join(parts)


def _to_float(series):
    import pandas as pd
    return pd.to_numeric(series, errors="coerce")


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


def get_demand_section(chain_cfg: dict, cache_key: str = None) -> str:
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

    return _cached(f"demand:{cache_key or chain_cfg.get('aliases', [''])[0]}", _pull)


def generic_demand_section(query: str) -> str:
    """通用需求兜底：图谱链未绑定需求数据时，按关键词猜可用数据源。

    汽车类->NEV渗透率+乘用车产销；电力/煤炭类->用电量；其余仅诚实标注缺口。
    """
    q = _clean_query(query)
    wanted = []
    if any(k in q for k in ("汽车", "新能源车", "整车", "电动车", "锂电")):
        wanted = ["nev_penetration", "car_sales"]
    elif any(k in q for k in ("电力", "煤炭", "煤", "用电", "工业")):
        wanted = ["electricity"]
    return get_demand_section({"demand_data": wanted}, cache_key=f"generic_demand:{wanted}")


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


def build_industry_report(query: str, positions: list | None = None,
                          scanner_engine=None) -> str:
    """组装行业数据包（chat analyze_industry 工具入口）。

    两级路由，数据待遇对等：
    - 图谱命中（手写6条 + AI自举N条）：图谱结构 + 专属数据绑定；
      绑定缺失时自动兜底（商品锚走关键词映射、需求走通用兜底）；
      自举图谱额外附板块解析节（成分股是动态的，图谱结构是静态的）
    - 未命中：通用引擎（板块成分股+主营抽样+商品锚+需求兜底+宏观+指引）
    板块和商品锚都未命中才返回 [工具失败]（ISS-059 语义）。
    """
    matched = match_chain(query)
    if matched:
        name, cfg = matched
        src = graph_source(name)
        graph_text = format_chain_graph(name, cfg, positions)
        if src == "auto":
            graph_text = (
                "（AI自举图谱：由AI基于板块数据梳理沉淀，未经人工复核）\n" + graph_text)
        # 数据绑定兜底：手写链都有绑定；自举链多半没有 -> 关键词映射兜底
        if cfg.get("commodity"):
            com_text = get_commodity_section(cfg)
        else:
            com_text = get_generic_commodity_section(
                name + " " + " ".join(cfg.get("aliases", [])))
        dem_text = (get_demand_section(cfg) if cfg.get("demand_data")
                    else generic_demand_section(name + " " + " ".join(cfg.get("aliases", []))))
        sections = [
            graph_text,
            "",
            f"【商品价格】\n{com_text}",
            "",
            f"【需求端】\n{dem_text}",
            "",
            f"【供给端】\n{cfg.get('supply_note', '供给端细分数据以公告新闻为主（get_news），库存代理见商品价格节')}",
            "",
            f"【宏观底色】\n{format_macro()}",
        ]
        # 自举图谱：附板块解析节（成分股动态，补图谱静态公司列表的时效性）
        if src == "auto":
            board_section, _ = _board_section(query, positions, scanner_engine)
            if board_section:
                sections.insert(2, "\n" + board_section)
        return "\n".join(sections)

    # 图谱未命中 -> 全行业通用引擎
    board = _resolve_board(query, scanner_engine)
    has_anchor = match_commodity(query) is not None
    if board is None and not has_anchor:
        chains = load_chains()
        supported = "、".join(
            f"{n}({','.join(list(c.get('aliases', []))[:3])})" for n, c in chains.items())
        return (
            f"[工具失败] 未识别行业'{query}'（既不在图谱，也未匹配到板块/商品锚）。"
            f"图谱支持: {supported}；其他行业可换常用板块名（如'白酒'、'军工'、'生猪'）重试。"
        )
    return build_generic_report(query, positions, scanner_engine)


def clear_cache():
    """清空 TTL 缓存（测试用）。"""
    _cache.clear()
