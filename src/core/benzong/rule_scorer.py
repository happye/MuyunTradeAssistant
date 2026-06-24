"""笨总规则版评分器（ISS-041 回测 fallback 前置验证）

目的：回测不能每只每天调 6 次 AI，需规则版近似。本模块用技术面+财务快照
近似 5 个 AI 维（行业景气度/业务纯度/细分龙头/辨识度/风险），历史估值位置
维直接复用纯算法版。

⚠ 本模块是"规则版↔AI版相关性验证"的产物，刻意求简洁：
- 代理信号粗糙（如行业景气用个股20日涨幅代理），目的是验证"规则版排序能否
  近似 AI 版排序"，不是生产级精度
- 验证脚本会对比 AI 版与规则版的 Spearman 相关性，达标才 full-scale 接入回测

返回结构与 auto_score 兼容：{dim: {score, confidence, reasoning, sources, ...}}
"""

import logging
from typing import Optional

from src.core.benzong.dimensions.valuation_position import score as vp_score

logger = logging.getLogger(__name__)


# 利空关键词（个股风险维度用，不调 AI）
RISK_KEYWORDS = [
    "减持", "解禁", "违规", "处罚", "立案", "警示", "问询",
    "质押", "爆仓", "亏损", "下滑", "下降", "退市", "ST",
]


def _get_spot(code: str) -> Optional[dict]:
    """从新浪全市场快照拿单股行情（市值/换手率/成交额）。失败返回 None。"""
    try:
        from src.scanner.market_cache import MarketCache
        import pandas as pd
        mc = MarketCache()
        df = mc.get_all_stocks()
        if df is None or df.empty:
            return None
        row = df[df["代码"] == code]
        if row.empty:
            return None
        r = row.iloc[0]
        def _num(col):
            v = r.get(col)
            try:
                return float(v) if pd.notna(v) else None
            except (TypeError, ValueError):
                return None
        return {
            "总市值": _num("总市值"),
            "流通市值": _num("流通市值"),
            "换手率": _num("换手率"),
            "成交额": _num("成交额"),
        }
    except Exception as e:
        logger.warning(f"规则版拿快照失败 {code}: {e}")
        return None


def _industry_prosperity_rule(code: str, data_summary: dict) -> dict:
    """行业景气度规则版：个股近 20 日涨幅代理（粗糙）。

    涨>20%→90, 10-20%→70, 0-10%→55, -10~0%→35, <-10%→15。
    """
    kline = data_summary.get("kline")
    if kline is None or kline.empty:
        return {"score": 50, "confidence": 0.0, "reasoning": "无K线，规则版无法算", "sources": []}
    try:
        close_col = next((c for c in ["收盘", "close"] if c in kline.columns), None)
        if close_col is None:
            return {"score": 50, "confidence": 0.0, "reasoning": "K线无收盘列", "sources": []}
        close = kline[close_col].astype(float)
        if len(close) < 21:
            return {"score": 50, "confidence": 0.3, "reasoning": "K线不足20日", "sources": []}
        gain = (close.iloc[-1] / close.iloc[-21] - 1) * 100
        if gain > 20:
            s = 90
        elif gain > 10:
            s = 70
        elif gain > 0:
            s = 55
        elif gain > -10:
            s = 35
        else:
            s = 15
        return {"score": float(s), "confidence": 0.6,
                "reasoning": f"近20日涨幅 {gain:+.1f}% → 规则版 {s}（代理行业景气）",
                "sources": ["近20日K线涨幅"]}
    except Exception as e:
        return {"score": 50, "confidence": 0.0, "reasoning": f"计算异常: {e}", "sources": []}


def _business_purity_rule(code: str, data_summary: dict) -> dict:
    """业务纯度规则版：主营介绍文本长度+是否含核心业务词。

    有主营介绍且描述聚焦（长度适中、含"主营"/"专注"）→ 高分；无 → 中性。
    规则版无法像 AI 那样判"蹭概念"，只做粗略文本信号。
    """
    intro = data_summary.get("business_intro") or ""
    if not intro:
        return {"score": 50, "confidence": 0.0, "reasoning": "无主营介绍", "sources": []}
    focus_words = ["主营", "专注", "专业从事", "致力于", "核心业务"]
    focus_hits = sum(1 for w in focus_words if w in intro)
    # 长度适中（100-800字）且含聚焦词 → 高分
    length = len(intro)
    base = 60
    if focus_hits >= 2:
        base = 80
    elif focus_hits >= 1:
        base = 70
    if length < 50:
        base -= 10  # 太短，信息不足
    return {"score": float(base), "confidence": 0.4,
            "reasoning": f"主营介绍 {length}字，聚焦词命中 {focus_hits} → 规则版 {base}",
            "sources": ["ths 主营介绍文本"]}


def _industry_leader_rule(code: str, data_summary: dict, spot: Optional[dict]) -> dict:
    """细分龙头规则版：总市值绝对值映射（无行业排名时的代理）。

    >2000亿→90, 500-2000亿→72, 100-500亿→55, 30-100亿→40, <30亿→28。
    大市值≈行业地位强（粗糙代理）。
    """
    if not spot or not spot.get("总市值"):
        return {"score": 50, "confidence": 0.0, "reasoning": "无市值数据", "sources": []}
    mcap = spot["总市值"]  # 元
    mcap_yi = mcap / 1e8  # 亿元
    if mcap_yi > 2000:
        s = 90
    elif mcap_yi > 500:
        s = 72
    elif mcap_yi > 100:
        s = 55
    elif mcap_yi > 30:
        s = 40
    else:
        s = 28
    return {"score": float(s), "confidence": 0.5,
            "reasoning": f"总市值 {mcap_yi:.0f}亿 → 规则版 {s}（市值代理龙头）",
            "sources": [f"总市值 {mcap_yi:.0f}亿"]}


def _market_recognition_rule(code: str, spot: Optional[dict]) -> dict:
    """市场辨识度规则版：换手率 + 成交额绝对值。

    换手率高+成交额大≈市场关注度高≈辨识度高。
    """
    if not spot:
        return {"score": 50, "confidence": 0.0, "reasoning": "无行情数据", "sources": []}
    turnover = spot.get("换手率") or 0
    amount = spot.get("成交额") or 0  # 元
    # 换手率映射：>5%→高, 2-5%→中, <2%→低
    if turnover > 5:
        t_score = 85
    elif turnover > 2:
        t_score = 65
    else:
        t_score = 40
    # 成交额映射：>5亿→高, 1-5亿→中, <1亿→低
    amt_yi = amount / 1e8
    if amt_yi > 5:
        a_score = 85
    elif amt_yi > 1:
        a_score = 60
    else:
        a_score = 35
    s = round(t_score * 0.5 + a_score * 0.5)
    return {"score": float(s), "confidence": 0.4,
            "reasoning": f"换手率 {turnover:.1f}% + 成交额 {amt_yi:.1f}亿 → 规则版 {s}",
            "sources": [f"换手率 {turnover:.1f}%", f"成交额 {amt_yi:.1f}亿"]}


def _risk_deduction_rule(code: str, data_summary: dict) -> dict:
    """个股风险值规则版：公告利空关键词 + 是否跌破 MA60。

    无利空→0（最好）；有利空关键词→每词 +15；跌破 MA60→ +20。
    规则版不做一票否决（无法判红线）。
    """
    announcements = data_summary.get("announcements") or []
    kline = data_summary.get("kline")

    risk = 0
    reasons = []
    # 利空关键词
    kw_hits = 0
    for a in announcements:
        title = (a.get("title") or "") + " " + (a.get("content") or "")
        for kw in RISK_KEYWORDS:
            if kw in title:
                kw_hits += 1
                break  # 每条公告最多算1次
    if kw_hits > 0:
        risk += min(kw_hits * 15, 60)
        reasons.append(f"利空关键词命中 {kw_hits} 条")

    # 跌破 MA60
    if kline is not None and not kline.empty:
        try:
            close_col = next((c for c in ["收盘", "close"] if c in kline.columns), None)
            if close_col and len(kline) >= 60:
                close = kline[close_col].astype(float)
                ma60 = close.tail(60).mean()
                if close.iloc[-1] < ma60:
                    risk += 20
                    reasons.append("跌破 MA60")
        except Exception:
            pass

    conf = 0.5 if announcements else 0.3
    return {"score": float(min(risk, 100)), "confidence": conf,
            "reasoning": f"规则版风险 {risk}（{'; '.join(reasons) or '无明显利空'}）",
            "sources": [f"公告 {len(announcements)} 条"]}


def rule_score(code: str, name: str = "", data_summary: Optional[dict] = None) -> dict:
    """规则版 6 维评分（不调 AI）。

    Args:
        code: 股票代码
        name: 股票名称
        data_summary: data_provider.get_data_summary 的结果；None 时自动拉

    Returns:
        {dim_name: {score, confidence, reasoning, sources, ...}, ..., "total_score", "grade"}
        结构与 auto_score 的 dimensions_meta 兼容，便于对比
    """
    from src.core.benzong import data_provider
    if data_summary is None:
        data_summary = data_provider.get_data_summary(code)

    spot = _get_spot(code)

    dims = {
        "industry_prosperity": _industry_prosperity_rule(code, data_summary),
        "business_purity": _business_purity_rule(code, data_summary),
        "valuation_position": vp_score(code, name, data_summary=data_summary),  # 复用纯算法版
        "industry_leader": _industry_leader_rule(code, data_summary, spot),
        "market_recognition": _market_recognition_rule(code, spot),
        "risk_deduction": _risk_deduction_rule(code, data_summary),
    }

    # 用 scorer 算总分 + 流动性系数（复用公式，保证与 AI 版可比）
    from src.core.benzong.scorer import score_one
    turnover = data_summary.get("market_turnover") or 1.0
    bs = score_one(
        industry_prosperity=dims["industry_prosperity"]["score"],
        business_purity=dims["business_purity"]["score"],
        valuation_position=dims["valuation_position"]["score"],
        industry_leader=dims["industry_leader"]["score"],
        market_recognition=dims["market_recognition"]["score"],
        risk_deduction=dims["risk_deduction"]["score"],
        market_turnover_trillion=turnover,
        stock_code=code, stock_name=name,
    )
    dims["total_score"] = bs.total_score
    dims["grade"] = bs.grade()
    return dims
