"""产业关键词研究提示。命中不代表已核实，不授权改变 mode、期限或止损。"""

from typing import Optional

# 自主可控（追赶类）关键词 -- 笨总教学九明确的"东大自主可控"领域
# 取行业/股票名命中即判 True（保守取向：命中仅提示，不改变持有计划）
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
    """检测是否属自主可控（追赶）概念 -- 笨总教学九，仅作研究线索。

    Args:
        industry_name: 行业名（如 baostock 返回的证监会行业分类）
        stock_name: 股票名称
        concept: 概念/板块标签（如有）

    Returns:
        True = 命中自主可控关键词（仅研究标签）
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
