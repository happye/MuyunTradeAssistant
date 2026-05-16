"""技术面摘要生成器 — v0.8.3 Phase B

从 StockData 提取结构化技术面摘要，供 AI Modifier 在分析新闻时注入上下文。
让 AI 知道当前技术面状态（趋势方向/均线位置/成交量/动量/大盘环境），
从而更准确地判断新闻的真实影响。

架构位置（纯数据转换，无副作用）：
  StockData → TechContextBuilder → tech_context dict → AI Modifier prompt
"""

import logging
from typing import Optional

from src.data.models import StockData

logger = logging.getLogger(__name__)


class TechContextBuilder:
    """技术面摘要构建器

    接收一个 StockData 快照，输出结构化的技术面摘要 dict。
    仅做数据读取和简单计算，不产生副作用。

    Usage:
        builder = TechContextBuilder()
        tech_context = builder.build(stock_data)
    """

    # ---- 趋势判断 ----

    @staticmethod
    def _judge_ma_arrangement(data: StockData) -> str:
        """判断均线排列状态

        Returns:
            "多头排列" / "空头排列" / "交叉纠缠" / "数据不足"
        """
        mas = [
            ("ma5", data.ma5),
            ("ma10", data.ma10),
            ("ma20", data.ma20),
            ("ma60", data.ma60),
        ]
        valid_mas = [(n, v) for n, v in mas if v is not None]

        if len(valid_mas) < 3:
            return "数据不足"

        values = [v for _, v in valid_mas]
        # 多头排列：价格 > MA5 > MA10 > MA20 > MA60
        is_bullish = all(
            values[i] > values[i + 1] for i in range(len(values) - 1)
        )
        if is_bullish and data.price > values[0]:
            return "多头排列"

        # 空头排列：价格 < MA5 < MA10 < MA20 < MA60
        is_bearish = all(
            values[i] < values[i + 1] for i in range(len(values) - 1)
        )
        if is_bearish and data.price < values[0]:
            return "空头排列"

        return "交叉纠缠"

    @staticmethod
    def _judge_trend_direction(data: StockData) -> str:
        """判断趋势方向（粗粒度）

        综合 MA 排列和价格位置判断。

        Returns:
            "上升" / "下降" / "盘整" / "无法判断"
        """
        arrangement = TechContextBuilder._judge_ma_arrangement(data)
        if arrangement == "多头排列":
            return "上升"
        if arrangement == "空头排列":
            return "下降"

        # MA 纠缠时，看价格与 MA60 的关系
        if data.ma20 is not None and data.ma60 is not None:
            if data.price > data.ma20 and data.ma20 > data.ma60:
                return "上升"
            if data.price < data.ma20 and data.ma20 < data.ma60:
                return "下降"

        # 最后看涨跌幅
        if data.change_pct is not None:
            if data.change_pct > 1.0:
                return "上升"
            if data.change_pct < -1.0:
                return "下降"

        return "盘整"

    # ---- 成交量分析 ----

    @staticmethod
    def _judge_vol_trend(data: StockData) -> str:
        """判断量能状态

        Returns:
            "放量" / "缩量" / "正常" / "数据不足"
        """
        if data.avg_volume_20 is None or data.volume <= 0:
            return "数据不足"

        ratio = data.volume / data.avg_volume_20
        if ratio > 1.5:
            return "放量"
        if ratio < 0.5:
            return "缩量"
        return "正常"

    # ---- MACD 信号 ----

    @staticmethod
    def _judge_macd_signal(data: StockData) -> str:
        """判断 MACD 信号状态

        Returns:
            "金叉向上" / "死叉向下" / "零轴上方" / "零轴下方" / "数据不足"
        """
        if data.macd_dif is None or data.macd_dea is None:
            return "数据不足"

        dif, dea = data.macd_dif, data.macd_dea

        if dif > dea and dif > 0:
            return "金叉向上"
        if dif < dea and dif < 0:
            return "死叉向下"
        if dif > 0:
            return "零轴上方"
        return "零轴下方"

    # ---- 主构建方法 ----

    def build(self, data: StockData) -> dict:
        """从 StockData 构建技术面摘要

        Args:
            data: 股票数据快照

        Returns:
            结构化的技术面摘要 dict，可直接 JSON 序列化
        """
        ctx = {
            "stock_code": data.stock_code,
            "stock_name": data.stock_name or data.stock_code,
            # 价格
            "price": data.price,
            "change_pct": data.change_pct,
            # ===== 趋势 =====
            "trend": {
                "direction": self._judge_trend_direction(data),
                "ma_arrangement": self._judge_ma_arrangement(data),
            },
            # ===== 均线位置 =====
            "price_position": self._build_price_position(data),
            # ===== 成交量 =====
            "volume": self._build_volume(data),
            # ===== 动量指标 =====
            "momentum": self._build_momentum(data),
            # ===== 大盘环境 =====
            "index_context": self._build_index_context(data),
        }

        return ctx

    def _build_price_position(self, data: StockData) -> dict:
        """构建价格位置摘要"""
        pos = {
            "close": data.price,
            "ma5": data.ma5,
            "ma10": data.ma10,
            "ma20": data.ma20,
            "ma60": data.ma60,
        }

        # 价格与均线的偏离
        if data.ma20 is not None and data.ma20 > 0:
            pos["above_ma20_pct"] = round(
                (data.price - data.ma20) / data.ma20 * 100, 1
            )
        else:
            pos["above_ma20_pct"] = None

        if data.ma60 is not None and data.ma60 > 0:
            pos["above_ma60_pct"] = round(
                (data.price - data.ma60) / data.ma60 * 100, 1
            )
        else:
            pos["above_ma60_pct"] = None

        # 接近高低点（用120日高/低点作为52周代理）
        if data.high_120d is not None and data.high_120d > 0:
            pos["near_120d_high"] = data.price >= data.high_120d * 0.95
        else:
            pos["near_120d_high"] = None

        if data.low_120d is not None and data.low_120d > 0:
            pos["near_120d_low"] = data.price <= data.low_120d * 1.05
        else:
            pos["near_120d_low"] = None

        # 布林带位置
        if data.boll_upper is not None:
            pos["boll_position"] = self._calc_boll_position(data)

        return pos

    @staticmethod
    def _calc_boll_position(data: StockData) -> Optional[str]:
        """计算布林带内的相对位置"""
        if data.boll_upper is None or data.boll_lower is None:
            return None

        band_width = data.boll_upper - data.boll_lower
        if band_width <= 0:
            return None

        relative = (data.price - data.boll_lower) / band_width
        if relative > 0.8:
            return "上轨附近"
        if relative < 0.2:
            return "下轨附近"
        if 0.4 <= relative <= 0.6:
            return "中轨附近"
        return "中轨偏上" if relative > 0.5 else "中轨偏下"

    def _build_volume(self, data: StockData) -> dict:
        """构建成交量摘要"""
        vol = {
            "current_volume": data.volume,
            "avg_volume_20": data.avg_volume_20,
            "vol_trend": self._judge_vol_trend(data),
        }

        if data.avg_volume_20 is not None and data.avg_volume_20 > 0:
            vol["vol_ratio_vs_20d"] = round(
                data.volume / data.avg_volume_20, 2
            )
        else:
            vol["vol_ratio_vs_20d"] = None

        return vol

    def _build_momentum(self, data: StockData) -> dict:
        """构建动量指标摘要"""
        momentum = {}

        # RSI（优先使用 14 日，没有则用 12 日）
        rsi_value = data.rsi_12 if data.rsi_12 is not None else data.rsi_6
        if rsi_value is not None:
            momentum["rsi"] = round(rsi_value, 1)

            if rsi_value > 70:
                momentum["rsi_zone"] = "超买"
            elif rsi_value < 30:
                momentum["rsi_zone"] = "超卖"
            elif rsi_value >= 50:
                momentum["rsi_zone"] = "偏强"
            else:
                momentum["rsi_zone"] = "偏弱"

        # MACD
        momentum["macd_signal"] = self._judge_macd_signal(data)
        if data.macd_dif is not None:
            momentum["macd_dif"] = round(data.macd_dif, 4)
        if data.macd_dea is not None:
            momentum["macd_dea"] = round(data.macd_dea, 4)

        # KDJ
        if data.kdj_k is not None:
            momentum["kdj_k"] = round(data.kdj_k, 1)
            momentum["kdj_d"] = round(data.kdj_d, 1) if data.kdj_d is not None else None
            momentum["kdj_j"] = round(data.kdj_j, 1) if data.kdj_j is not None else None

        # 多时间框架
        if data.weekly:
            momentum["weekly_trend"] = data.weekly.get("trend", "未知")
        if data.monthly:
            momentum["monthly_trend"] = data.monthly.get("trend", "未知")

        return momentum

    @staticmethod
    def _build_index_context(data: StockData) -> dict:
        """构建大盘环境摘要"""
        ctx = {"index_name": "沪深300"}

        if data.index_trend is not None:
            ctx["index_trend"] = data.index_trend  # BULLISH/BEARISH/NEUTRAL
        if data.index_close is not None:
            ctx["index_close"] = data.index_close
        if data.index_ma250 is not None and data.index_ma250 > 0:
            ctx["index_ma250"] = data.index_ma250
        if data.index_ma20 is not None:
            ctx["index_ma20"] = data.index_ma20
        if data.index_ma60 is not None:
            ctx["index_ma60"] = data.index_ma60
        if data.index_change_pct is not None:
            ctx["index_change_pct"] = data.index_change_pct

        # 指数偏离年线
        if (data.index_close is not None and data.index_ma250 is not None
                and data.index_ma250 > 0):
            ctx["above_ma250_pct"] = round(
                (data.index_close - data.index_ma250)
                / data.index_ma250 * 100, 1
            )

        return ctx

    # ---- 便捷：文本格式化 ----

    @staticmethod
    def to_text(tech_context: dict) -> str:
        """将技术面摘要转为人类可读的文本（用于 debug 或纯文本 Prompt）

        Args:
            tech_context: build() 的输出

        Returns:
            格式化的文本摘要
        """
        lines = [
            f"股票: {tech_context.get('stock_name', 'N/A')} ({tech_context.get('stock_code', 'N/A')})",
            f"价格: {tech_context.get('price', 'N/A')}",
        ]

        trend = tech_context.get("trend", {})
        lines.append(
            f"趋势方向: {trend.get('direction', 'N/A')}, "
            f"均线排列: {trend.get('ma_arrangement', 'N/A')}"
        )

        pp = tech_context.get("price_position", {})
        lines.append(
            f"均线: MA5={pp.get('ma5', 'N/A')}, MA10={pp.get('ma10', 'N/A')}, "
            f"MA20={pp.get('ma20', 'N/A')}, MA60={pp.get('ma60', 'N/A')}"
        )
        above_20 = pp.get("above_ma20_pct")
        above_60 = pp.get("above_ma60_pct")
        if above_20 is not None and above_60 is not None:
            sign_20 = "+" if above_20 >= 0 else ""
            sign_60 = "+" if above_60 >= 0 else ""
            lines.append(
                f"偏离: MA20 {sign_20}{above_20}%, MA60 {sign_60}{above_60}%"
            )

        vol = tech_context.get("volume", {})
        lines.append(
            f"成交量: {vol.get('vol_trend', 'N/A')}"
            f"（20日均量 {vol.get('avg_volume_20', 'N/A')}）"
        )

        mom = tech_context.get("momentum", {})
        rsi = mom.get("rsi")
        macd = mom.get("macd_signal")
        parts = []
        if rsi is not None:
            parts.append(f"RSI={rsi}({mom.get('rsi_zone', 'N/A')})")
        if macd:
            parts.append(f"MACD={macd}")
        if parts:
            lines.append("动量: " + ", ".join(parts))

        idx = tech_context.get("index_context", {})
        if idx:
            above_250 = idx.get("above_ma250_pct")
            trend = idx.get("index_trend", "N/A")
            sign = "+" if (above_250 or 0) >= 0 else ""
            extra = f", 偏离年线{sign}{above_250}%" if above_250 is not None else ""
            lines.append(f"大盘({idx.get('index_name', 'N/A')}): {trend}{extra}")

        return "\n".join(lines)
