# -*- coding: utf-8 -*-
"""第五轮对抗审查 E 区块回归测试（延后裁决清单执行轮）

覆盖：
- E01（=D-TC-01）：`_judge_ma_arrangement` 少于 4 条 MA 时不得宣布多头/空头排列
  —— docstring 契约是「价格 > MA5 > MA10 > MA20 > MA60」，缺 MA60 时用
  3 条 MA 宣布「多头排列」违反契约（14.6% 的 K 线判定在有无 MA60 间翻转）。
  消费面：TechContextBuilder 只喂 AI Modifier 的 prompt（live 专属），
  不直接进决策信号——属 prompt 质量 / 语义正确性修复。

每个测试对应一条审查发现，改代码前先跑本文件确认绿灯。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.tech_context import TechContextBuilder
from src.data.models import StockData


def _stock(**overrides) -> StockData:
    """默认：完全多头排列（价格 11.2 > MA5 10.8 > MA10 10.5 > MA20 10.2 > MA60 9.8）"""
    base = dict(
        stock_code="sh600000", stock_name="测试", price=11.2,
        open=10.9, high=11.3, low=10.8, change_pct=1.5,
        ma5=10.8, ma10=10.5, ma20=10.2, ma60=9.8, ma120=9.5,
        volume=1e6, avg_volume_20=1e6,
    )
    base.update(overrides)
    return StockData(**base)


# ==========================================================================
# E01  MA 排列判定：契约是 4 条 MA，缺任何一条必须「数据不足」
# ==========================================================================

def test_ma_arrangement_full_bullish():
    """4 条 MA 齐全且依次下行 → 多头排列（回归保护，修 E01 不许误伤）"""
    assert TechContextBuilder._judge_ma_arrangement(_stock()) == "多头排列"


def test_ma_arrangement_full_bearish():
    """4 条 MA 齐全且依次上行 → 空头排列"""
    s = _stock(price=9.0, ma5=9.5, ma10=10.0, ma20=10.5, ma60=11.0, ma120=11.5)
    assert TechContextBuilder._judge_ma_arrangement(s) == "空头排列"


def test_ma_arrangement_missing_ma60_is_insufficient():
    """缺 MA60 时不得用 3 条 MA 宣布「多头排列」。

    修复前：MA5>MA10>MA20 且价格>MA5 → 返回「多头排列」，
    但 MA60（年线方向）未验证——次新股/数据缺口下 prompt 会被喂假排列。
    """
    s = _stock(ma60=None)
    assert TechContextBuilder._judge_ma_arrangement(s) == "数据不足", (
        "缺 MA60 必须判数据不足（契约：价格>MA5>MA10>MA20>MA60）"
    )


def test_ma_arrangement_missing_ma5_is_insufficient():
    """缺 MA5 同理（任何一条声明中的 MA 缺失都不得判排列）"""
    s = _stock(ma5=None)
    assert TechContextBuilder._judge_ma_arrangement(s) == "数据不足"


def test_ma_arrangement_only_two_mas_is_insufficient():
    """只 2 条 → 数据不足（原 <3 守卫本就该拦，回归保护）"""
    s = _stock(ma20=None, ma60=None)
    assert TechContextBuilder._judge_ma_arrangement(s) == "数据不足"


def test_trend_direction_degrades_gracefully_when_ma60_missing():
    """排列判「数据不足」后，趋势方向应走既有降级链（MA20/MA60 检查 → 涨跌幅），不崩溃。

    ma60=None 时 line 83 的 `ma20 is not None and ma60 is not None` 不成立，
    应回落到 change_pct 判定，此处 change_pct=1.5 → 「上升」。
    """
    s = _stock(ma60=None)
    assert TechContextBuilder._judge_trend_direction(s) == "上升"


# ==========================================================================
# E02（=D-TC-02） web 端 fallback StockData 丢弃 quote 已返回的 OHLC/volume
# ==========================================================================

def test_web_fallback_stockdata_maps_full_quote_fields():
    """`_analyze_work` 的 quote→StockData 兜底必须带上 open/high/low/volume。

    `AKShareClient.get_realtime_quote` 三条策略全部返回 open/high/low/volume
    （akshare_client.py:256-259 / 338-341 / 619-623），但 web/app.py 的兜底
    构造只映射 price/change_pct——指标计算与量能分析拿到的永远是 None。
    结构性断言：每处兜底构造内必须出现这四个字段的映射。
    """
    root = Path(__file__).resolve().parents[2]
    # R4 审查只点了 web/app.py，同类点扫描（铁律 1③）发现同类漏映射共 3 处
    sites = {
        "web/app.py": "src/web/app.py",
        "chat/tools.py": "src/chat/tools.py",
        "tui/app.py": "src/tui/app.py",
    }
    for label, rel in sites.items():
        src = (root / rel).read_text(encoding="utf-8")
        assert "get_realtime_quote" in src, f"{label} 未找到行情兜底调用点（结构已变？）"

        # 取「get_realtime_quote 之后最近的一个 StockData( 构造块」
        q_idx = src.find("get_realtime_quote")
        s_idx = src.find("StockData(", q_idx)
        assert s_idx != -1, f"{label} 未找到 quote→StockData 兜底构造"
        block = src[s_idx:s_idx + 700]

        for field in ("open", "high", "low", "volume"):
            assert f"{field}=" in block, (
                f"{label} 的 quote 兜底 StockData 缺 {field} 映射（quote 明明已返回该字段）"
            )
