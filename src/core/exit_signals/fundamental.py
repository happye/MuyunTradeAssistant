"""基本面恶化硬退出信号（ISS-053 / 跳法A 持有期侧）

建仓后冻结基本面的"气宗死扛"漏洞补丁：持有期被 ST / 业绩预告预亏预减时强制离场。
全客观硬规则（baostock 结构化数据），不依赖 AI 实时判断，不重跑笨总评分。

走独立 fundamental_alert 通道（非 top_signal），PlanGuard 规则4.5 不可压制，
urgency 在致命止损(规则4)后、高位止盈(规则P1)前--暴雷比技术顶更急。

回测 gap（诚实声明）：ST 为当前状态（query_stock_basic 返回现 code_name），
回测历史有前瞻偏差；业绩预告带 pub_date + entry_date 过滤，point-in-time 正确。
"""

import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

# 业绩预告利空类型（baostock profitForcastType 枚举子集）
# 预亏/预减=本期亏/减；续亏=连亏；首亏=首次亏损。略增/续盈/扭亏等不算利空
LOSS_FORECAST_TYPES = {"预亏", "预减", "续亏", "首亏"}

# 自然日缓存：key=(code, today, entry_date)，value=触发描述或 None
# 含 entry_date 防同股不同建仓日的 stale HIT 误判；自然日粒度--次日重查
_CACHE: dict[tuple[str, str, str], Optional[str]] = {}


def check_fundamental_alert(code: str, *, entry_date: Optional[str] = None) -> Optional[str]:
    """建仓后基本面恶化硬退出（ISS-053）。硬规则非AI，自然日缓存。

    Args:
        code: 股票代码（6 位）
        entry_date: 建仓日期 YYYY-MM-DD。业绩预告只认建仓后发布的新预告
            （建仓前发布的已定价，触发是假退出）；为 None 时跳过预告检查
            （无法过滤建仓前后，避假退出），仅 ST 仍查（当前状态无需 entry_date）

    Returns:
        触发描述（如 "基本面恶化:被ST(ST星源)"）或 None。ST 优先于预告（更致命）。
    """
    today = date.today().strftime("%Y-%m-%d")
    cache_key = (code, today, entry_date or "")
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    result = _check_fundamental_alert_uncached(code, entry_date=entry_date)
    _CACHE[cache_key] = result
    return result


def _check_fundamental_alert_uncached(code: str, *, entry_date: Optional[str] = None) -> Optional[str]:
    """实际判定（无缓存）。ST 优先于业绩预告（更致命）。"""
    st = _check_st(code)
    if st:
        return st
    return _check_loss_forecast(code, entry_date=entry_date)


def _check_st(code: str) -> Optional[str]:
    """被 ST/*ST 判定：baostock code_name 前缀。当前状态（非历史，回测有前瞻，见模块 docstring）。"""
    try:
        from src.data.akshare_client import AKShareClient
        name = AKShareClient.get_stock_basic_name(code)
        if name and (name.startswith("ST") or name.startswith("*ST")):
            return f"基本面恶化:被ST({name})"
    except Exception as e:
        logger.warning(f"[FundamentalAlert] ST 检查异常 {code}: {e}")
    return None


def _check_loss_forecast(code: str, *, entry_date: Optional[str] = None) -> Optional[str]:
    """业绩预告预亏/预减判定：profitForcastType in 利空集 且 pub_date >= entry_date。

    entry_date 为 None 时跳过（无法区分建仓前后，避假退出）。
    """
    if entry_date is None:
        return None
    try:
        from src.data.akshare_client import AKShareClient
        fc = AKShareClient.get_latest_forecast(code, since_date=entry_date)
        if not fc:
            return None
        ftype = fc.get("type")
        if ftype in LOSS_FORECAST_TYPES:
            abstract = (fc.get("abstract") or "")[:30]
            return f"基本面恶化:业绩预告{ftype}({abstract})"
    except Exception as e:
        logger.warning(f"[FundamentalAlert] 业绩预告检查异常 {code}: {e}")
    return None


def clear_cache() -> None:
    """清缓存（测试用）。"""
    _CACHE.clear()
