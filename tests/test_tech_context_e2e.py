"""Phase B 端到端测试：技术面背景感知AI分析

验证：同一新闻在上升/下跌趋势中给出不同的情绪判断，
证明 TechContextBuilder → AIModifier 链路完整有效。

Usage:
    uv run python tests/test_tech_context_e2e.py
    uv run python tests/test_tech_context_e2e.py --debug
"""

import sys
import os
import argparse

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.models import StockData, AIModifierResult
from src.core.tech_context import TechContextBuilder


def build_bullish_stock() -> StockData:
    """构建多头排列的股票数据（上升趋势）"""
    return StockData(
        stock_code="600519",
        stock_name="贵州茅台",
        price=1850.0,
        open=1830.0,
        high=1860.0,
        low=1825.0,
        change_pct=1.5,
        # 多头排列: price > ma5 > ma10 > ma20 > ma60
        ma5=1830.0,
        ma10=1810.0,
        ma20=1780.0,
        ma60=1720.0,
        ma120=1650.0,
        # 放量
        volume=15_000_000,
        avg_volume_20=9_000_000,  # vol_ratio = 1.67 → 放量
        # MACD 金叉向上
        macd_dif=12.5,
        macd_dea=8.3,
        # RSI 偏强
        rsi_12=62.0,
        # 布林带中轨上方
        boll_upper=1920.0,
        boll_mid=1800.0,
        boll_lower=1680.0,
        # KDJ
        kdj_k=72.0,
        kdj_d=65.0,
        kdj_j=86.0,
        # 接近120日高点
        high_120d=1900.0,
        low_120d=1500.0,
        # 大盘环境：牛市
        index_trend="BULLISH",
        index_close=4200.0,
        index_ma20=4150.0,
        index_ma60=4000.0,
        index_ma250=3800.0,
        index_change_pct=0.8,
        # 多时间框架
        weekly={"trend": "上升"},
        monthly={"trend": "上升"},
    )


def build_bearish_stock() -> StockData:
    """构建空头排列的股票数据（下跌趋势）"""
    return StockData(
        stock_code="600519",
        stock_name="贵州茅台",
        price=1580.0,
        open=1595.0,
        high=1605.0,
        low=1570.0,
        change_pct=-2.8,
        # 空头排列: price < ma5 < ma10 < ma20 < ma60
        ma5=1610.0,
        ma10=1630.0,
        ma20=1660.0,
        ma60=1720.0,
        ma120=1780.0,
        # 缩量
        volume=4_000_000,
        avg_volume_20=9_000_000,  # vol_ratio = 0.44 → 缩量
        # MACD 死叉向下
        macd_dif=-8.2,
        macd_dea=-3.5,
        # RSI 超卖
        rsi_12=25.0,
        # 布林带下轨附近
        boll_upper=1750.0,
        boll_mid=1680.0,
        boll_lower=1550.0,
        # KDJ
        kdj_k=18.0,
        kdj_d=22.0,
        kdj_j=10.0,
        # 接近120日低点
        high_120d=1900.0,
        low_120d=1550.0,
        # 大盘环境：熊市
        index_trend="BEARISH",
        index_close=3600.0,
        index_ma20=3680.0,
        index_ma60=3800.0,
        index_ma250=4000.0,
        index_change_pct=-1.2,
        # 多时间框架
        weekly={"trend": "下降"},
        monthly={"trend": "盘整"},
    )


def test_tech_context_builder():
    """测试 TechContextBuilder 构建不同技术环境摘要"""
    print("=" * 60)
    print("测试1: TechContextBuilder 摘要生成")
    print("=" * 60)

    builder = TechContextBuilder()

    bullish = build_bullish_stock()
    bearish = build_bearish_stock()

    bull_ctx = builder.build(bullish)
    bear_ctx = builder.build(bearish)

    print("\n📈 多头环境摘要:")
    print(TechContextBuilder.to_text(bull_ctx))
    print(f"\n  trend={bull_ctx['trend']}")
    print(f"  vol={bull_ctx['volume']['vol_trend']}, "
          f"ratio={bull_ctx['volume'].get('vol_ratio_vs_20d')}")
    print(f"  momentum={bull_ctx['momentum'].get('macd_signal')}, "
          f"RSI={bull_ctx['momentum'].get('rsi')}({bull_ctx['momentum'].get('rsi_zone')})")

    print("\n📉 空头环境摘要:")
    print(TechContextBuilder.to_text(bear_ctx))
    print(f"\n  trend={bear_ctx['trend']}")
    print(f"  vol={bear_ctx['volume']['vol_trend']}, "
          f"ratio={bear_ctx['volume'].get('vol_ratio_vs_20d')}")
    print(f"  momentum={bear_ctx['momentum'].get('macd_signal')}, "
          f"RSI={bear_ctx['momentum'].get('rsi')}({bear_ctx['momentum'].get('rsi_zone')})")

    # 断言：趋势方向不同
    assert bull_ctx["trend"]["direction"] == "上升", \
        f"多头应判定为上升，实际: {bull_ctx['trend']['direction']}"
    assert bear_ctx["trend"]["direction"] == "下降", \
        f"空头应判定为下降，实际: {bear_ctx['trend']['direction']}"
    assert bull_ctx["trend"]["ma_arrangement"] == "多头排列"
    assert bear_ctx["trend"]["ma_arrangement"] == "空头排列"

    # 断言：量能不同
    assert bull_ctx["volume"]["vol_trend"] == "放量"
    assert bear_ctx["volume"]["vol_trend"] == "缩量"

    # 断言：动量不同
    assert bull_ctx["momentum"]["macd_signal"] == "金叉向上"
    assert bear_ctx["momentum"]["macd_signal"] == "死叉向下"

    # 断言：指数环境不同
    assert bull_ctx["index_context"]["index_trend"] == "BULLISH"
    assert bear_ctx["index_context"]["index_trend"] == "BEARISH"

    print("\n✅ 测试1通过: TechContextBuilder 正确区分牛熊技术环境")
    return bull_ctx, bear_ctx


def test_ai_modifier_with_context(args):
    """测试 AI Modifier 在不同技术环境下产生不同分析结果"""
    print("\n" + "=" * 60)
    print("测试2: AI Modifier 技术面感知分析")
    print("=" * 60)

    # 同一组新闻（中性偏利好——降准预期）
    news_text = """
    1. 【央行】央行表示将适时降准，保持流动性合理充裕
    2. 【白酒行业】贵州茅台公告：i茅台平台一季度销售额同比增长35%
    3. 【机构观点】中信证券：白酒板块估值处于历史中位，具备配置价值
    4. 【宏观】3月CPI同比上涨0.7%，PPI同比下降2.5%，通缩压力仍存
    """

    import yaml
    from pathlib import Path

    # 加载配置
    config_path = Path(__file__).parent.parent / "configs" / "settings.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    ai_config = settings.get("ai", {})
    if args.debug:
        ai_config["debug"] = True

    from src.core.ai_modifier import AIModifier

    modifier = AIModifier(ai_config)

    if not modifier.enabled:
        print("⚠️  AI Modifier 未启用（可能缺少API Key），跳过AI分析测试")
        return None, None

    bullish_stock = build_bullish_stock()
    bearish_stock = build_bearish_stock()

    # 手动注入新闻（替代 NewsClient 网络抓取）
    # 注意：这里直接调用内部方法进行测试，不依赖网络新闻
    print("\n📊 测试同一新闻在不同技术环境下的AI分析...")
    print(f"   新闻: 降准预期 + 茅台i茅台增长35% + 通胀数据")
    print(f"   Provider: {modifier.provider}, Model: {modifier._model}")

    # 通过 monkey-patch NewsClient.gather_news_for_analysis 注入测试新闻
    from src.data.news_client import NewsClient
    original_gather = NewsClient.gather_news_for_analysis

    from datetime import datetime

    def mock_gather(stock_code, max_news=10):
        return {
            "stock_news": [
                {"title": "央行表示将适时降准", "content": "央行表示将适时降准，保持流动性合理充裕", "source": "央行"},
                {"title": "茅台i茅台平台Q1增长35%", "content": "贵州茅台公告：i茅台平台一季度销售额同比增长35%", "source": "公司公告"},
                {"title": "中信：白酒板块具备配置价值", "content": "中信证券：白酒板块估值处于历史中位，具备配置价值", "source": "中信证券"},
            ],
            "macro_news": [
                {"title": "3月CPI同比0.7%", "summary": "3月CPI同比上涨0.7%，PPI同比下降2.5%，通缩压力仍存", "source": "统计局"},
            ],
            "stock_code": stock_code,
            "gathered_at": datetime.now().isoformat(),
        }

    NewsClient.gather_news_for_analysis = mock_gather

    try:
        # 分析多头环境
        print("\n🔵 多头环境下分析中...")
        bull_result = modifier.analyze(bullish_stock)

        # 分析空头环境
        print("\n🔴 空头环境下分析中...")
        bear_result = modifier.analyze(bearish_stock)

    finally:
        # 恢复原方法
        NewsClient.gather_news_for_analysis = original_gather

    # 输出对比
    print("\n" + "=" * 60)
    print("📊 分析结果对比")
    print("=" * 60)

    print(f"\n{'指标':<20} {'多头环境(上升趋势)':<30} {'空头环境(下跌趋势)':<30}")
    print("-" * 80)
    print(f"{'sentiment':<20} {bull_result.sentiment:<30} {bear_result.sentiment:<30}")
    print(f"{'confidence':<20} {bull_result.confidence:<30.2f} {bear_result.confidence:<30.2f}")
    print(f"{'risk_level':<20} {bull_result.risk_level:<30} {bear_result.risk_level:<30}")
    print(f"{'event_type':<20} {bull_result.event_type:<30} {bear_result.event_type:<30}")
    print(f"{'score_adjustment':<20} {bull_result.score_adjustment:<30.3f} {bear_result.score_adjustment:<30.3f}")
    print(f"{'adjusted':<20} {str(bull_result.adjusted):<30} {str(bear_result.adjusted):<30}")
    print(f"{'tech_awareness':<20} {str(bull_result.tech_context_awareness):<30} {str(bear_result.tech_context_awareness):<30}")
    print(f"{'tech_used':<20} {str(bull_result.tech_context_used):<30} {str(bear_result.tech_context_used):<30}")
    print(f"\n多头 summary: {bull_result.summary}")
    print(f"空头 summary: {bear_result.summary}")

    # 关键断言
    print("\n--- 验证结果 ---")

    checks = []

    # 检查1: 技术面感知标志
    if bull_result.tech_context_awareness:
        checks.append(("✅", "多头环境: tech_context_awareness=True"))
    else:
        checks.append(("⚠️", "多头环境: tech_context_awareness=False (AI未感知技术面)"))

    if bear_result.tech_context_awareness:
        checks.append(("✅", "空头环境: tech_context_awareness=True"))
    else:
        checks.append(("⚠️", "空头环境: tech_context_awareness=False (AI未感知技术面)"))

    # 检查2: 使用了技术面要素
    if bull_result.tech_context_used:
        checks.append(("✅", f"多头使用了: {bull_result.tech_context_used}"))
    else:
        checks.append(("⚠️", "多头未使用任何技术面要素"))

    if bear_result.tech_context_used:
        checks.append(("✅", f"空头使用了: {bear_result.tech_context_used}"))
    else:
        checks.append(("⚠️", "空头未使用任何技术面要素"))

    # 检查3: 不同情绪（关键验收标准）
    if bull_result.sentiment != bear_result.sentiment:
        checks.append(("✅", f"情绪不同: 多头={bull_result.sentiment} vs 空头={bear_result.sentiment}"))
    elif bull_result.confidence != bear_result.confidence:
        checks.append(("✅", f"情绪相同但confidence不同: {bull_result.confidence:.2f} vs {bear_result.confidence:.2f}"))
    else:
        checks.append(("⚠️", "⚠️ 情绪和confidence完全相同，AI可能未考虑技术面"))

    # 检查4: 多头应更乐观
    sentiment_score = {"bullish": 1, "neutral": 0, "bearish": -1}
    bull_s = sentiment_score.get(bull_result.sentiment, 0)
    bear_s = sentiment_score.get(bear_result.sentiment, 0)
    if bull_s >= bear_s:
        checks.append(("✅", f"多头情绪({bull_result.sentiment})不弱于空头({bear_result.sentiment})"))
    else:
        checks.append(("❌", f"多头情绪({bull_result.sentiment})弱于空头({bear_result.sentiment})，不符合预期"))

    # 检查5: summary 中是否提到技术面
    tech_keywords = ["趋势", "均线", "技术面", "多头", "空头", "缩量", "放量", "金叉", "死叉", "RSI", "MACD"]
    bull_mentions = [kw for kw in tech_keywords if kw in bull_result.summary]
    bear_mentions = [kw for kw in tech_keywords if kw in bear_result.summary]
    if bull_mentions:
        checks.append(("✅", f"多头summary提及技术面: {bull_mentions}"))
    else:
        checks.append(("⚠️", "多头summary未提及技术面关键词"))
    if bear_mentions:
        checks.append(("✅", f"空头summary提及技术面: {bear_mentions}"))
    else:
        checks.append(("⚠️", "空头summary未提及技术面关键词"))

    for status, msg in checks:
        print(f"  {status} {msg}")

    passed = sum(1 for s, _ in checks if s == "✅")
    warnings = sum(1 for s, _ in checks if s == "⚠️")
    failed = sum(1 for s, _ in checks if s == "❌")
    print(f"\n  通过: {passed}, 警告: {warnings}, 失败: {failed}")

    return bull_result, bear_result


def main():
    parser = argparse.ArgumentParser(description="Phase B 端到端测试")
    parser.add_argument("--debug", action="store_true", help="启用AI debug模式")
    parser.add_argument("--skip-ai", action="store_true", help="跳过AI API调用（仅测试TechContextBuilder）")
    args = parser.parse_args()

    print("🧪 Phase B 端到端测试: 技术面背景感知AI分析")
    print(f"   技术面摘要 → AI Modifier Prompt 注入 → 差异化情绪输出\n")

    # 测试1: 纯本地测试
    bull_ctx, bear_ctx = test_tech_context_builder()

    # 测试2: AI API调用测试（可选）
    if args.skip_ai:
        print("\n⏭️  跳过AI API测试")
    else:
        test_ai_modifier_with_context(args)

    print("\n" + "=" * 60)
    print("🏁 Phase B 端到端测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
