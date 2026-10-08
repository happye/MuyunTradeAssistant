"""板块层高位止盈信号（跳法A 阶段5 -> 报告2.1 实现，笨总教学八）

笨总教学：板块见顶三信号 = 渗透率30%魔咒 + 旗手滞涨 + 新赛道虹吸。
各层内部信号是"或"关系（笨总原话："不是and，而是或"），一条触发即准备撤退。

实现现状（诚实声明）：
- 渗透率30%魔咒：⚠ 已降级（v0.8.28.1，架构师裁决）——'30+' 标注不再触发强制清仓，改为 penetration_research_reminder 研究提醒（原标注无数据核实、无「不适用」出口，银行/基建被误标 30+ 曾致新开仓当天反复 CLOSE_ALL）
- 旗手滞涨：✅ 已实现（建仓时 AI 标注 flagbearer_code，持有期 baostock 拉旗手近20日涨幅对比）
- 新赛道虹吸：❌ TODO（需全板块资金流对比基础设施，复杂度高，留后续）

数据依赖：flagbearer_code / penetration_stage 由建仓时 AI 标注写入 TradePlan（报告2.1）。
"""

import logging
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 旗手滞涨判定：旗手近20日涨幅 < 标的涨幅 × 1/3 视为滞涨（"带头大哥涨不动了，跟风小弟更没戏"）
FLAGBEARER_LAG_RATIO = 0.33
FLAGBEARER_LOOKBACK_DAYS = 20
# 标的需已有可观涨幅(>10%)才有"旗手滞涨"语义；低位盘整不算板块见顶
FLAGBEARER_MIN_STOCK_GAIN = 10.0


def check_sector_top_signal(trade_plan=None, stock_data=None, code: str = "") -> Optional[str]:
    """检查板块层大顶信号（报告2.1，笨总教学八）。

    v0.8.28.1（架构师裁决 2026-10-08，决策简报 docs/2026-10-08_决策简报_渗透率30+_
    误触发强制清仓.md）：penetration_stage=="30+" 不再作为强制清仓信号——该标注是
    建仓时 AI 对四选一枚举的猜测（无数据核实、prompt 原无「不适用」出口，银行/基建
    等无渗透率语义行业也会被标 30+），曾导致新开仓当天即被反复 CLOSE_ALL。
    降级为研究提醒（penetration_research_reminder，由 orchestrator 追加到
    warnings/决策理由，与强制退出原因分开展示）。旧标签立即受新规则约束，原值保留
    可追溯。旗手滞涨有 20 日涨幅数据核实，保持强制信号不变。

    Args:
        trade_plan: TradePlan（取 flagbearer_code / penetration_stage）。None 跳过板块层
        stock_data: StockData（标的近20日涨幅，旗手滞涨对比用）
        code: 标的代码

    Returns:
        Optional[str]: 触发信号描述，无则 None
    """
    if trade_plan is None:
        return None

    # 信号1（v0.8.28.1 降级）：渗透率30%魔咒 → 不再强制清仓，见 penetration_research_reminder()

    # 信号2：旗手滞涨（教学八）--"最大最核心的股是板块脸面，带头大哥涨不动=板块顶"
    if trade_plan.flagbearer_code:
        sig = _check_flagbearer_lag(trade_plan.flagbearer_code, stock_data, code)
        if sig:
            return sig

    # 信号3：新赛道虹吸 -- TODO（需全板块资金流对比基础设施，留后续）
    return None


def penetration_research_reminder(trade_plan=None) -> Optional[str]:
    """渗透率 30+ 标注的研究提醒（v0.8.28.1 降级后的展示形态，架构师裁决）。

    只提示需要复核，不参与任何强制退出；与强制退出原因分开展示
    （提醒进 warnings/风险提示，退出原因进 原因/strategy_reasons）。
    """
    if trade_plan is not None and getattr(trade_plan, "penetration_stage", None) == "30+":
        return ("行业研究提醒：原计划的「渗透率 30%+」缺少可核实依据，"
                "本次不据此触发清仓，请复核该行业增长情况。")
    return None


def _check_flagbearer_lag(flagbearer_code: str, stock_data, code: str) -> Optional[str]:
    """旗手滞涨：旗手近20日涨幅明显落后于标的 -> 板块见顶预警。

    判定：标的近20日涨幅 > 10%（已有可观涨幅）且 旗手近20日涨幅 < 标的涨幅 × 1/3。
    低位盘整（标的涨幅<10%）不判"见顶"，避免误触发。
    """
    try:
        stock_20d = _get_20d_change_pct(stock_data) if stock_data else None
        if stock_20d is None or stock_20d < FLAGBEARER_MIN_STOCK_GAIN:
            return None  # 标的涨幅不足/无数据 -> 不判板块见顶
        flag_20d = _fetch_20d_change_pct(flagbearer_code)
        if flag_20d is None:
            return None  # 旗手数据获取失败，fail-open 跳过
        if flag_20d < stock_20d * FLAGBEARER_LAG_RATIO:
            return (f"板块:旗手{flagbearer_code}滞涨"
                    f"(旗手20日{flag_20d:.1f}% vs 标的{stock_20d:.1f}%)")
    except Exception as e:
        logger.debug(f"旗手滞涨判定异常({flagbearer_code}): {e}")
    return None


def _get_20d_change_pct(stock_data) -> Optional[float]:
    """从 StockData 取近20日涨幅（ISS-057 已加 change_20d 字段）"""
    v = getattr(stock_data, "change_20d", None)
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return None


def _fetch_20d_change_pct(code: str) -> Optional[float]:
    """拉取某股近20日涨幅（baostock，fail-open）。

    用于旗手滞涨对比。仅持仓检查时调用（has_position 门控），每次 analyze 多一次
    baostock K线调用（~1-2s，同 announcements fetch，可接受）。失败返回 None -> 跳过。
    """
    try:
        from src.data.akshare_client import _ensure_baostock_login, _call_with_timeout, AKShareClient
        if not _ensure_baostock_login():
            return None
        import baostock as bs
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")  # 多取防停牌
        prefix, _ = AKShareClient._normalize_stock_code(code)
        rs = bs.query_history_k_data_plus(
            f"{prefix}.{code}", "date,close",
            start_date=start, end_date=end,
        )
        if rs.error_code != '0':
            logger.debug(f"旗手{code}K线查询错误: {rs.error_msg}")
            return None
        rows = []
        def _drain():
            while rs.next():
                rows.append(rs.get_row_data())
        _call_with_timeout(_drain, timeout=20)  # bs.next() 读取防 hang
        if len(rows) < 2:
            return None
        # v0.8.7.6 审计修复 B21：窗口口径统一——标的侧用 change_20d（20个交易日），
        # 旗手侧原取40个自然日(≈28交易日)却同样称"近20日涨幅"直接对比。截取最后21行
        # （20个交易日间隔）对齐标的口径；40自然日窗口保留作停牌缓冲。
        rows = rows[-21:]
        try:
            first = float(rows[0][1])
            last = float(rows[-1][1])
        except (ValueError, IndexError):
            return None
        if first <= 0:
            return None
        return (last - first) / first * 100
    except Exception as e:
        logger.debug(f"旗手{code}近20日涨幅获取失败: {e}")
        return None
