"""高位止盈信号模块（跳法A 阶段2 / v0.8.6.4 + 报告2.1 板块层补全）

笨总"何时该止盈离场"体系的工程化--三层大顶信号（笨总教学八，各层内部"或"关系）：
- 宏观层（macro.py）：成交额破 10 万亿 + assess_liquidity_state 流动性温度（报告1.1）
- 个股层（stock.py）：换手率超 40% / 缩量加速上涨 / 实控人减持（+ 报告2.2 三倍定律等）
- 板块层（sector.py）：渗透率 30% 魔咒 / 旗手滞涨 / 新赛道虹吸（报告2.1）

设计原则（绕开 AI 天花板）：全部是**客观硬规则**，不依赖 AI 实时判断"该不该卖"。
触发即作为 P1 信号（仅次于致命止损），PlanGuard 不可压制--即使气宗持有期内
出现大顶信号也强制离场。
v0.8.28.1 例外：板块层渗透率 30+ 标注降级为研究提醒（标注是无「不适用」出口的
AI 猜测，曾致无渗透率语义行业新开仓当天被反复强制清仓），见 sector.py。
"""

import logging

from src.data.models import SignalFinding

logger = logging.getLogger(__name__)

from src.core.exit_signals.macro import check_macro_top_signal, assess_liquidity_state, assess_market_breadth
from src.core.exit_signals.stock import check_stock_top_signal
from src.core.exit_signals.sector import check_sector_top_signal

__all__ = [
    "check_macro_top_signal", "assess_liquidity_state", "assess_market_breadth",
    "check_stock_top_signal", "check_sector_top_signal", "check_top_signals",
]


# Q1/Q-R1：允许硬退出的 signal_id 白名单 + 策略绑定（精确版本注册映射，禁止子串）
_HARD_SIGNAL_IDS = {"exit.stock.triple_up"}
_HARD_BINDING_EXACT = "jiaoxue6-8/mode-rule（既有周期策略纪律）"


def authorize_hard_findings(findings, stock_code: str, *, live: bool = False,
                            stock_data=None):
    """Q1/Q-R1 消费边界资格门：exit 资格的发现须通过本门才能驱动清仓。

    核对（全部通过才授权）：
    1. signal_id 在白名单（当前仅三倍定律）；
    2. 策略绑定与注册版本**精确相等**（禁止子串冒充）；
    3. 适用证券包含当前标的；
    4. 数据质量 OK；
    5. 来源时点可严格解析且不晚于本次分析的数据时点（Q-R5：复用
       source_time.parse_source_time/source_time_in_future，坏字符串不截断洗白；
       回测参照当前 bar 日期，live 参照行情/指标快照时点）；
    6. **复算纯函数确认**：用当前 StockData 重跑三倍定律判定——缓存/转述的发现
       在当前数据上不成立即拒绝（研究缓存不能冒用硬信号身份）。

    失败降为 research 并保留诊断 reason；缓存重放走同一门。verified 布尔与
    来源文字本身不是核验凭据。合法三倍正例不受影响（纯函数复算即确认）。
    """
    from datetime import datetime
    from src.core.source_time import parse_source_time, source_time_in_future
    authorized, rejected = [], []
    for f in findings:
        if f.action_scope != "exit":
            # research/none 条目从未申请硬权限——原样透传（reason 保留原语义，
            # 不被资格门噪声覆盖）；diag 条目借此保持可见性
            rejected.append(f)
            continue
        reasons = []
        if f.signal_id not in _HARD_SIGNAL_IDS:
            reasons.append(f"signal_id {f.signal_id!r} 不在允许硬退出清单")
        if (f.strategy_binding or "") != _HARD_BINDING_EXACT:
            reasons.append(f"策略绑定不符: {f.strategy_binding!r}")
        if stock_code not in (f.securities or []):
            reasons.append("适用证券不含当前标的")
        if f.data_quality != "OK":
            reasons.append(f"数据资格 {f.data_quality}")
        if f.verified is not True:
            reasons.append("未经事实核验")

        # Q-R5：时点资格——参照点 = 本次分析的数据时点（quote_as_of），
        # 证据不得晚于参照点（回测防未来、live 防乱序）；解析失败即拒。
        ref = None
        ref_raw = getattr(stock_data, "quote_as_of", None) if stock_data is not None else None
        if ref_raw:
            ref = parse_source_time(ref_raw)
        if ref is None:
            reasons.append("本次分析数据时点不可解析/缺失（无参照点，不授权硬退出）")
        elif not f.as_of:
            reasons.append("证据时点缺失")
        elif source_time_in_future(f.as_of, ref[0]):
            reasons.append("证据时点晚于本次分析数据时点（回测防未来/live 防乱序）")

        # Q-R1：复算纯函数——当前数据上三倍定律确实成立才授权（研究缓存不能冒用）
        if not reasons and stock_data is not None:
            from src.core.exit_signals.stock import _check_triple_up_rule
            recomputed = _check_triple_up_rule(stock_data, stock_code)
            if recomputed is None:
                reasons.append("复算未确认：当前数据不满足三倍定律条件（缓存/转述不作为依据）")

        if reasons:
            downgraded = f.model_dump()
            downgraded.update({
                "action_scope": "research",
                "reason": "硬退出资格不足（Q1 消费边界门）: " + "; ".join(reasons),
            })
            rejected.append(SignalFinding(**downgraded))
        else:
            authorized.append(f)
    return authorized, rejected


def check_top_signals(stock_data, code, *, announcements=None,
                      market_turnover_trillion=None, trade_plan=None, live: bool = False) -> list:
    """汇总宏观+板块+个股高位止盈发现，返回 list[SignalFinding]（ISS-117 S0 权限收口）。

    检查顺序：宏观(全市场) -> 板块(赛道) -> 个股(操作层)。消费方按 action_scope 分流：
    exit=强制退出（仅三倍定律等既有纪律）；research=研究提醒（进 warnings，不清仓）。

    Args:
        stock_data: StockData（个股缩量加速/近20日涨幅判定用）
        code: 股票代码
        announcements: 近期公告/新闻列表 [{title,...}]，live 路径可传；回测传 None 跳过
        market_turnover_trillion: 全市场成交额(万亿)，live 传 None 时 macro 层自行拉取；
                    回测(live=False)传 None 则跳过宏观层（不实时拉取，防未来信息+网络拖慢）
        trade_plan: TradePlan（板块层取 flagbearer_code / penetration_stage）；None 时板块层跳过
        live: True 时启用股东户数/融资余额信号（akshare 网络+日缓存）。回测传 False。

    Returns:
        list[SignalFinding]（可能为空；同层多条也可并存，不再首个非空短路）
    """
    findings = []
    # Q6：逐层隔离——单个来源异常只损失该来源的诊断，不得吞掉其余已合格发现
    #（含独立硬纪律）：失败显式记为 none 资格诊断条目，由消费方转提示。
    # 宏观层门控（与个股层 live 门控同款纪律）：live=False（回测）且未显式传入成交额时
    # 跳过，绝不在回测里实时拉取全市场成交额——拉取失败会拖垮回测速度，
    # 拉取成功则是把"今天"的成交额注入历史bar（未来信息）。回测无该字段，恒为 None。
    if live or market_turnover_trillion is not None:
        try:
            macro = check_macro_top_signal(market_turnover_trillion=market_turnover_trillion)
            if macro:
                findings.append(macro)
        except Exception as e:
            logger.warning(f"宏观层信号检查异常（隔离，不影响其他层）: {e}")
            findings.append(SignalFinding(
                signal_id="diag.macro.error", source="check_macro_top_signal",
                data_quality="UNKNOWN", action_scope="none",
                detail=f"宏观层信号检查失败（已隔离）: {type(e).__name__}: {e}"))

    # v0.8.7.6 审计修复 B06：板块层补 live 门控——三层唯独这里漏了，sector.py 内部
    # 用 datetime.now() 拉 baostock 旗手K线（回测=未来信息+网络）。回测下 TradePlan
    # 的 flagbearer_code 本就无 point-in-time 数据源，直接跳过。
    if live:
        try:
            findings.extend(check_sector_top_signal(
                trade_plan=trade_plan, stock_data=stock_data, code=code))
        except Exception as e:
            logger.warning(f"板块层信号检查异常（隔离，不影响其他层）: {e}")
            findings.append(SignalFinding(
                signal_id="diag.sector.error", source="check_sector_top_signal",
                data_quality="UNKNOWN", action_scope="none",
                detail=f"板块层信号检查失败（已隔离）: {type(e).__name__}: {e}"))

    try:
        findings.extend(check_stock_top_signal(
            stock_data, code, announcements=announcements,
            live=live, mode=trade_plan.mode if trade_plan else None
        ))
    except Exception as e:
        logger.warning(f"个股层信号检查异常（隔离，不影响其他层）: {e}")
        findings.append(SignalFinding(
            signal_id="diag.stock.error", source="check_stock_top_signal",
            data_quality="UNKNOWN", action_scope="none",
            detail=f"个股层信号检查失败（已隔离）: {type(e).__name__}: {e}"))
    return findings
