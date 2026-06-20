"""维度 3：历史估值位置（纯算法，不调 AI）

笨总教学 2 公式：
- 当前股价 vs 近 3 年最低价涨幅 < 20% → 满分 100
- 涨幅每超 1% → 扣 1 分
- 涨幅 > 120% → 0 分

数据源：data_provider.get_recent_kline(code, lookback_days=750)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def score(code: str, name: str, *, data_summary: dict,
          ai_client=None, rag_service=None) -> dict:
    """计算历史估值位置评分（0-100）。

    Args:
        code: 股票代码
        name: 股票名称
        data_summary: dict 含 kline (DataFrame) 与 fetch_status
        ai_client: 不使用（纯算法）
        rag_service: 不使用

    Returns:
        dict {score, confidence, sources, reasoning, data_freshness, warnings}
    """
    kline = data_summary.get("kline")

    if kline is None or kline.empty:
        return {
            "score": 50,
            "confidence": 0.0,
            "sources": [],
            "reasoning": "近 3 年 K 线数据不可用，无法计算历史估值位置",
            "data_freshness": "N/A",
            "warnings": ["历史 K 线数据缺失（akshare/baostock 拉取失败）"],
        }

    try:
        # K 线列名兼容多种来源
        # AKShare stock_zh_a_hist: 日期/开盘/收盘/最高/最低/...
        # Baostock query_history_k_data: date/open/high/low/close/...
        possible_close_cols = ["收盘", "close", "Close"]
        possible_low_cols = ["最低", "low", "Low"]
        possible_date_cols = ["日期", "date", "Date"]

        close_col = next((c for c in possible_close_cols if c in kline.columns), None)
        low_col = next((c for c in possible_low_cols if c in kline.columns), None)
        date_col = next((c for c in possible_date_cols if c in kline.columns), None)

        if close_col is None or low_col is None:
            return {
                "score": 50,
                "confidence": 0.0,
                "sources": [],
                "reasoning": f"K 线列名异常（收盘/最低列缺失），列：{list(kline.columns)[:8]}",
                "data_freshness": "N/A",
                "warnings": ["K 线数据格式异常"],
            }

        # 当前价 = 最后一行收盘价
        current_price = float(kline[close_col].iloc[-1])

        # 近 3 年最低价
        three_year_low = float(kline[low_col].min())

        if three_year_low <= 0 or current_price <= 0:
            return {
                "score": 50,
                "confidence": 0.0,
                "sources": [],
                "reasoning": f"价格数据异常: current={current_price}, 3y_low={three_year_low}",
                "data_freshness": "N/A",
                "warnings": ["价格数据异常"],
            }

        # 涨幅计算
        gain_from_low_pct = (current_price - three_year_low) / three_year_low * 100

        # 笨总公式：< 20% 满分；每多 1% 扣 1 分
        if gain_from_low_pct < 20:
            value_score = 100.0
        elif gain_from_low_pct >= 120:
            value_score = 0.0
        else:
            # 20% 时 100 分，120% 时 0 分（每 1% 扣 1 分）
            value_score = max(0.0, 100.0 - (gain_from_low_pct - 20))

        value_score = round(value_score, 1)

        # 数据时效
        latest_date = "未知"
        if date_col:
            try:
                latest_date = str(kline[date_col].iloc[-1])[:10]
            except Exception:
                pass

        warnings = []
        if gain_from_low_pct >= 100:
            warnings.append(f"距离 3 年最低价已涨 {gain_from_low_pct:.0f}%，估值位置偏高")

        return {
            "score": value_score,
            "confidence": 1.0,  # 纯算法，confidence 高
            "sources": [f"近 3 年 K 线 (n={len(kline)} 条，截至 {latest_date})"],
            "reasoning": (
                f"当前价 ¥{current_price:.2f} vs 3 年最低价 ¥{three_year_low:.2f}，"
                f"涨幅 {gain_from_low_pct:+.1f}%。"
                f"按笨总教学 2 公式（涨幅 <20% 满分，每多 1% 扣 1 分）→ {value_score} 分。"
            ),
            "data_freshness": latest_date,
            "warnings": warnings,
        }
    except Exception as e:
        logger.error(f"valuation_position 计算失败 {code}: {e}", exc_info=True)
        return {
            "score": 50,
            "confidence": 0.0,
            "sources": [],
            "reasoning": f"计算异常: {type(e).__name__}: {str(e)[:80]}",
            "data_freshness": "N/A",
            "warnings": [f"计算异常 {type(e).__name__}"],
        }
