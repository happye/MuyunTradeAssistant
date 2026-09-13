"""卖点规则 — Chandelier Exit / 趋势破坏 / 止盈"""

from typing import Optional
from dataclasses import dataclass
from src.data.models import StockData


@dataclass
class ExitSignal:
    """卖出信号"""
    triggered: bool
    exit_type: Optional[str] = None        # "chandelier_stop" / "trend_break" / "take_profit"
    exit_price: Optional[float] = None
    exit_action: Optional[str] = None      # "TRIM" / "EXIT" / "STOP"
    exit_ratio: float = 0.0               # 减仓比例
    reason: str = ""

    # Chandelier Exit 辅助
    atr_value: Optional[float] = None
    highest_since_entry: Optional[float] = None
    chandelier_stop_price: Optional[float] = None


def check_chandelier(
    data: StockData,
    config: dict,
    position_tier: str = "pilot",
    high_since_entry: Optional[float] = None,
    entry_price: Optional[float] = None,
) -> Optional[ExitSignal]:
    """Chandelier Exit 移动止损检查

    规则: 若 price <= highest_since_entry - N * ATR，则触发卖出
    三重 N 值：试探=3 / 基础=2.5 / 重仓=2（v0.8.3收尾优化后）

    新增（ISS-027）：
    - require_ma_confirm: 仅 MA5<MA20 时才允许触发，避免正常回调误判
    - adaptive_n: N值按 (1 + ATR/close) 自适应放大，高波动股更宽容

    Args:
        data: 股票数据（含 atr_14）
        config: 配置参数
        position_tier: 仓位档位（pilot/base/full）
        high_since_entry: 持仓期间最高价
        entry_price: 开仓均价
    """
    chandelier_cfg = config.get("chandelier", {})
    n_map = {
        "pilot": chandelier_cfg.get("n_pilot", 3),
        "base": chandelier_cfg.get("n_base", 2.5),
        "full": chandelier_cfg.get("n_full", 2),
    }
    n_mult = n_map.get(position_tier, 3)

    # v0.8.3: MA确认条件
    require_ma_confirm = chandelier_cfg.get("require_ma_confirm", True)
    if require_ma_confirm:
        if data.ma5 is None or data.ma20 is None:
            # v0.8.7.5 审计修复 A22：原代码 MA 缺失时会落到下面的 ATR 检查直接清仓，
            # 与本注释"无MA数据时不检查"相反——绕过 require_ma_confirm，正常回调被误判破位
            return None
        if data.ma5 >= data.ma20:
            # MA5还>=MA20，短期趋势未破坏，跳过Chandelier检查
            return None

    atr = data.atr_14
    if atr is None:
        return None

    # v0.8.3: 自适应N值 — 高波动股放大N值
    adaptive_n = chandelier_cfg.get("adaptive_n", True)
    if adaptive_n and data.price and data.price > 0:
        atr_ratio = atr / data.price
        n_mult = min(n_mult * (1 + atr_ratio * 5), chandelier_cfg.get("adaptive_n_cap", 2.0) * n_map.get(position_tier, 3))

    # 持仓期间最高价
    # v0.8.7.5 审计修复 A23：删除"当日最高/现价"兜底——没有持仓锚点(high_since_entry/
    # entry_price)时 Chandelier 无从算起，退化为当日 high 会让 stop 偏低失真，
    # 且 portfolio 会把这个失真值写回 high_since_entry 污染后续
    highest = high_since_entry or entry_price
    if highest is None:
        return None

    stop_price = highest - n_mult * atr

    # 触发检查
    if data.price > stop_price:
        return None  # 未触发

    exit_action = "EXIT" if position_tier == "pilot" else "TRIM"
    exit_ratio = 1.0 if exit_action == "EXIT" else 0.5

    ma_info = ""
    if require_ma_confirm and data.ma5 and data.ma20 and data.ma5 < data.ma20:
        ma_info = f", MA5({data.ma5:.2f})<MA20({data.ma20:.2f})确认"

    return ExitSignal(
        triggered=True,
        exit_type="chandelier_stop",
        exit_price=data.price,
        exit_action=exit_action,
        exit_ratio=exit_ratio,
        reason=f"Chandelier Exit触发: 价格{data.price:.2f}<=止损价{stop_price:.2f} "
               f"(最高{highest:.2f}-{n_mult:.1f}×ATR{atr:.2f}){ma_info}",
        atr_value=atr,
        highest_since_entry=highest,
        chandelier_stop_price=round(stop_price, 2),
    )


def check_trend_break(data: StockData, config: dict) -> Optional[ExitSignal]:
    """趋势破坏检查

    条件（OR）：
    1. 短期均线死叉: ma5 < ma20（快速趋势破坏）
    2. 中期均线死叉: ma10 < ma60（中期趋势破坏）

    短期死叉 → TRIM（减仓40%）
    中期死叉 → EXIT（清仓）

    Args:
        data: 股票数据
        config: 配置参数
    """
    trend_cfg = config.get("trend_break", {})
    trim_ratio = trend_cfg.get("trim_ratio", 0.4)
    # 死叉间距阈值：
    # 这里只看“已经死叉”还不够，因为一旦 MA10 < MA60 或 MA5 < MA20 成立，
    # 这个状态会持续很多天。如果不做额外过滤，scan 每次都会把同一批老死叉
    # 股票反复列为卖点，用户看到的会是重复告警，而不是“今天刚坏掉”的有效提示。
    # 因此这里再用两条均线的偏离幅度做一次新鲜度判断，只有偏离还不算太大时才触发。
    max_gap_pct = trend_cfg.get("max_gap_pct", 3.0)

    # 中期趋势破坏: EXIT
    # A40 修复：补 ma60>0 除零防（脏数据 ma60=0 时 ZeroDivisionError 会被上层
    # except: logger.debug 吞掉，卖出检查静默失效）
    if (data.ma10 is not None and data.ma60 is not None and data.ma60 > 0
            and data.ma10 < data.ma60):
        # 中期趋势破坏：以 MA60 为基准，确认这不是一个早已形成的长期死叉。
        gap_pct = (data.ma60 - data.ma10) / data.ma60 * 100
        if gap_pct <= max_gap_pct:
            return ExitSignal(
                triggered=True,
                exit_type="trend_break",
                exit_price=data.price,
                exit_action="EXIT",
                exit_ratio=1.0,
                reason=f"中期趋势破坏: MA10({data.ma10:.2f})<MA60({data.ma60:.2f}), 偏离{gap_pct:.1f}%"
            )

    # 短期趋势破坏: TRIM
    if (data.ma5 is not None and data.ma20 is not None and data.ma20 > 0
            and data.ma5 < data.ma20):
        # 短期趋势破坏：同样以 MA20 为基准，避免短期死叉长期刷屏。
        gap_pct = (data.ma20 - data.ma5) / data.ma20 * 100
        if gap_pct <= max_gap_pct:
            return ExitSignal(
                triggered=True,
                exit_type="trend_break",
                exit_price=data.price,
                exit_action="TRIM",
                exit_ratio=trim_ratio,
                reason=f"短期趋势破坏: MA5({data.ma5:.2f})<MA20({data.ma20:.2f}), 偏离{gap_pct:.1f}%"
            )

    return None


def check_take_profit(
    data: StockData,
    config: dict,
    entry_price: Optional[float] = None,
    high_since_entry: Optional[float] = None,
) -> Optional[ExitSignal]:
    """止盈检查

    三层止盈：
    1. 第一档: 盈利 >= tier1_pct(10%) → TRIM 30%
    2. 第二档: 盈利 >= tier2_pct(20%) → TRIM 40%
    3. 移动止盈: 从最高点回撤 trail_pct(30%) → EXIT 剩余

    Args:
        data: 股票数据
        config: 配置参数
        entry_price: 开仓均价
        high_since_entry: 持仓期间最高价
    """
    take_profit_cfg = config.get("take_profit", {})
    tier1_pct = take_profit_cfg.get("tier1_pct", 10)
    tier1_trim = take_profit_cfg.get("tier1_trim", 0.3)
    tier2_pct = take_profit_cfg.get("tier2_pct", 20)
    tier2_trim = take_profit_cfg.get("tier2_trim", 0.4)
    trail_pct = take_profit_cfg.get("trail_pct", 0.3)
    # v0.8.9.5（彻查批 P3）：trail_pct 配置单位归一——tier*_pct 用百分数（10=10%），
    # trail_pct 历史上是小数（0.3=30%），同一配置表两种口径，用户照 tier 口径填
    # `trail_pct: 10`（想表达 10% 回撤）会被算成 1000% 而静默永不触发。
    # 归一规则：值 >1 视为百分数（与 tier*_pct 口径一致），<=1 保持小数口径，
    # 旧配置（0.3）行为完全不变。
    if trail_pct > 1:
        trail_pct = trail_pct / 100

    if entry_price is None or entry_price <= 0:
        return None

    profit_pct = (data.price - entry_price) / entry_price * 100

    # 移动止盈（最高优先级）: 从最高点回撤超过 trail_pct
    if high_since_entry and high_since_entry > entry_price:
        drawdown = (high_since_entry - data.price) / high_since_entry * 100
        if drawdown >= trail_pct * 100:  # trail_pct=0.3 → 30%回撤
            return ExitSignal(
                triggered=True,
                exit_type="take_profit",
                exit_price=data.price,
                exit_action="EXIT",
                exit_ratio=1.0,
                reason=f"移动止盈触发: 从最高{high_since_entry:.2f}回撤{drawdown:.1f}%"
            )

    # 第二档止盈
    if profit_pct >= tier2_pct:
        return ExitSignal(
            triggered=True,
            exit_type="take_profit",
            exit_price=data.price,
            exit_action="TRIM",
            exit_ratio=tier2_trim,
            reason=f"止盈T2: 盈利{profit_pct:.1f}%>={tier2_pct}% → 减仓{tier2_trim*100:.0f}%"
        )

    # 第一档止盈
    if profit_pct >= tier1_pct:
        return ExitSignal(
            triggered=True,
            exit_type="take_profit",
            exit_price=data.price,
            exit_action="TRIM",
            exit_ratio=tier1_trim,
            reason=f"止盈T1: 盈利{profit_pct:.1f}%>={tier1_pct}% → 减仓{tier1_trim*100:.0f}%"
        )

    return None