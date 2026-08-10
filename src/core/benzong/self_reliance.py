"""自主可控概念检测（笨总教学九 / 视频理念优化报告 1.3）。

笨总：自主可控逻辑的标的（芯片半导体为主的信息产业，追赶类）"波动大、暴涨暴跌、
炒短期情绪，绝不能长期持有，必须懂进退" -> 建仓 mode 判定时强制剑宗（不长持），
即使笨总 grade=A + Weinstein S2 上升期也不给气宗。

这与 ISS-051 的 stage 闸门互补：stage 闸门治"建仓时点已在顶部/下跌"，
本闸门治"标的类型本身就不该长持"（自主可控 = 炒短期情绪，非长期格局）。

检测方式：行业名/股票名 关键词匹配（heuristic，非 AI 判断）。
关键词覆盖笨总明确的"东大自主可控（追赶）"领域。注意区分：
- 自主可控（追赶，本模块检测 -> 强制剑宗）：芯片设计/制造/设备/材料/EDA/光刻等
- 遥遥领先（已领先，不长持禁令不适用）：稀土/锂电/光伏/新能源车等（笨总可气宗）

诚实声明：关键词 heuristic 会误伤个别有真实盈利、可长持的半导体标的（如部分设备股）。
用户可通过手动编辑 portfolio.yaml 的 trade_plan.mode 覆盖（设为 qizong）。
关键词表可按笨总教学调整。
"""

from typing import Optional

# 自主可控（追赶类）关键词 -- 笨总教学九明确的"东大自主可控"领域
# 取行业/股票名命中即判 True（保守取向：宁可多判剑宗，不长持风险 < 错误长持风险）
_SELF_RELIANCE_KEYWORDS = (
    # 概念标签
    "自主可控", "国产替代", "卡脖子", "自主化", "国产化",
    # 半导体设计
    "半导体", "芯片", "集成电路", "GPU", "CPU", "算力芯片", "存储芯片", "HBM",
    "功率半导体", "模拟芯片", "射频芯片",
    # 半导体制造/设备/材料（追赶类核心）
    "光刻", "刻蚀", "薄膜沉积", "EDA", "IP授权", "晶圆", "代工", "封测", "封装测试",
    "半导体设备", "半导体材料", "光掩模", "硅片",
)


def detect_self_reliance(
    industry_name: str = "",
    stock_name: str = "",
    concept: str = "",
) -> bool:
    """检测是否属自主可控（追赶）概念 -- 笨总教学九，强制剑宗的依据。

    Args:
        industry_name: 行业名（如 baostock 返回的证监会行业分类）
        stock_name: 股票名称
        concept: 概念/板块标签（如有）

    Returns:
        True = 命中自主可控关键词（建仓 mode 判定时强制剑宗）
    """
    text = f"{industry_name or ''} {stock_name or ''} {concept or ''}"
    if not text.strip():
        return False
    return any(kw in text for kw in _SELF_RELIANCE_KEYWORDS)


def detect_self_reliance_from_summary(data_summary: Optional[dict]) -> bool:
    """从 auto_score 的 data_summary 检测自主可控（封装便捷入口）。

    data_summary["industry"] = {industry_name, stock_name, ...}（data_provider.get_industry_info 结构）
    """
    if not isinstance(data_summary, dict):
        return False
    industry_info = data_summary.get("industry") or {}
    if not isinstance(industry_info, dict):
        return False
    return detect_self_reliance(
        industry_name=industry_info.get("industry_name", ""),
        stock_name=industry_info.get("stock_name", ""),
    )
