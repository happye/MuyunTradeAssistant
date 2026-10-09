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
from typing import Optional, Tuple

from src.data.models import SignalFinding

logger = logging.getLogger(__name__)

# 业绩预告利空类型（baostock profitForcastType 枚举子集）
# 预亏/预减=本期亏/减；续亏=连亏；首亏=首次亏损。略增/续盈/扭亏等不算利空
LOSS_FORECAST_TYPES = {"预亏", "预减", "续亏", "首亏"}

# 自然日缓存：key=(code, today, entry_date)，value=触发描述或 None
# 含 entry_date 防同股不同建仓日的 stale HIT 误判；自然日粒度--次日重查
_CACHE: dict[tuple[str, str, str], Optional[str]] = {}


def check_fundamental_alert(code: str, *, entry_date: Optional[str] = None) -> Optional[str]:
    """建仓后基本面恶化判定（ISS-053）——v0.8.29 拆分权限（ISS-117 S0）。

    兼容入口：返回 hard 部分（仅 ST）的触发描述或 None。预告类别已按 ISS-117 A12
    降级为 research（ SignalFinding），走 check_fundamental_findings。
    """
    hard, _ = check_fundamental_findings(code, entry_date=entry_date)
    return hard


def check_fundamental_findings(code: str, *, entry_date: Optional[str] = None) -> Tuple[Optional[str], list]:
    """基本面恶化判定，按权限拆分（ISS-117 S0，架构师裁决）。

    Returns:
        (hard, findings)：
        - hard: ST 触发描述或 None——当前状态排除政策，保持强制退出资格
        - findings: 预告类别等 research 发现（A12 降级：预亏/预减类别本身不等于暴雷，
          不单独构成清仓依据；时序不明/日期资格不足时如实标注）
    """
    today = date.today().strftime("%Y-%m-%d")
    cache_key = (code, today, entry_date or "")
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    hard, findings = _check_fundamental_findings_uncached(code, entry_date=entry_date)
    _CACHE[cache_key] = (hard, findings)
    return hard, findings


def _check_fundamental_findings_uncached(code: str, *, entry_date: Optional[str] = None):
    """实际判定（无缓存）。ST 保持 hard；预告类别降级 research。"""
    st = _check_st(code)
    if st:
        return st, []
    fc = _check_loss_forecast(code, entry_date=entry_date)
    if not fc:
        return None, []
    ftype = fc.get("type")
    if ftype not in LOSS_FORECAST_TYPES:
        return None, []
    abstract = (fc.get("abstract") or "")[:30]
    ambiguous = bool(fc.get("_pub_order_ambiguous"))
    finding = SignalFinding(
        signal_id="research.fundamental.loss_forecast",
        source="baostock 业绩预告（profitForcastType，pub_date 已过资格门）",
        securities=[code],
        as_of=fc.get("pub_date"), data_quality="UNKNOWN" if ambiguous else "OK",
        action_scope="research", verified=False,
        strategy_binding="iss117-s0/ruling-2026-10-09",
        detail=f"基本面研究提醒:业绩预告{ftype}({abstract})",
        reason=("预告类别本身不等于暴雷（ISS-117 A12：预减≠亏损，无幅度/原因/预期偏差），"
                "降级为研究提醒不强制清仓"
                + ("；公告日与建仓同日，时序不明（日精度无法证明晚于建仓）" if ambiguous else "")))
    return None, [finding]


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


def _check_loss_forecast(code: str, *, entry_date: Optional[str] = None) -> Optional[dict]:
    """取最新合格利空预告 dict（供 findings 构造，ISS-117 A12 降级后不再拼字符串）。

    日期资格门（缺失/非法/未来/建仓前）在 get_latest_forecast 内完成；
    entry_date 为 None 时跳过（无法区分建仓前后，避假退出）。
    非利空类别（略增/续盈/扭亏等）返回 None。
    """
    if entry_date is None:
        return None
    try:
        from src.data.akshare_client import AKShareClient
        fc = AKShareClient.get_latest_forecast(code, since_date=entry_date)
        if not fc:
            return None
        if fc.get("type") in LOSS_FORECAST_TYPES:
            return fc
        return None
    except Exception as e:
        logger.warning(f"[FundamentalAlert] 业绩预告检查异常 {code}: {e}")
    return None


def clear_cache() -> None:
    """清缓存（测试用）。"""
    _CACHE.clear()
