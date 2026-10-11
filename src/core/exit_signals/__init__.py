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
                            stock_data=None, analysis_date=None, mode=None):
    """Q1/Q-R1 消费边界资格门：exit 资格的发现须通过本门才能驱动清仓。

    核对（全部通过才授权）：
    1. signal_id 在白名单（当前仅三倍定律）；
    2. 策略绑定与注册版本**精确相等**（禁止子串冒充）；
    3. 适用证券包含当前标的；
    4. 数据质量 OK、verified=True、来源非空；
    5. **计划适用性**：气宗(mode=qizong)按既有纪律跳过三倍定律——当前计划不适用
       该规则时，硬资格不成立（缓存/转述不能借不适用的规则清仓）；
    6. **独立分析日期**：证据时点与数据时点都不得晚于本次分析的确定日期
       （回测=bar 日期、live=当天，均由调用方独立传入，不从待核验数据自身取——
       防"2026 年行情配 2020 年分析"自我背书）；
    7. **复算纯函数确认**：用当前 StockData 重跑三倍定律判定——缓存/转述的发现
       在当前数据上不成立即拒绝（研究缓存不能冒用硬信号身份）。

    失败降为 research 并保留诊断 reason；缓存重放走同一门。verified 布尔与
    来源文字本身不是核验凭据。合法三倍正例不受影响（纯函数复算即确认）。
    """
    from src.core.source_time import parse_source_time, source_time_in_future
    authorized, rejected = [], []

    # 分析日期（独立确定）：回测由调用方传 bar 日期；live 由调用方传当天。
    # 严格解析完整字符串——非法非空值不截断不回退，直接拒绝全部 exit 资格。
    # 缺失时 live 回退墙钟当天；回测缺失 → 硬资格拒绝（不静默 fail-open，
    # 否则 backtest_validator 等不传 today 的重放路径时点核验整体缺失）。
    if analysis_date is not None and str(analysis_date).strip():
        ref_parsed = parse_source_time(str(analysis_date).strip())
        if ref_parsed is None:
            ref_dt = None
            _bad_analysis_date = str(analysis_date)
        else:
            ref_dt = ref_parsed
            _bad_analysis_date = None
    else:
        ref_dt = None
        _bad_analysis_date = None
    if ref_dt is None and live and not _bad_analysis_date:
        from datetime import datetime as _dt
        analysis_date = _dt.now().strftime("%Y-%m-%d")
        ref_dt = parse_source_time(analysis_date)
    _missing_analysis_date = ref_dt is None and not _bad_analysis_date

    # 数据时点资格：StockData 自带的行情/指标快照**日期**不得晚于分析日期
    #（Q-R5 典型场景：分析 2020 年却拿到 2026 年行情——数据整体降资格）。
    # 日级比较：分析日期是日精度，不做盘中时点对比（当天 21:30 的快照对当天分析合法）。
    # Q-R5 追加（live）：行情快照日期不得早于独立市场交易日历的最近交易日——
    # 陈旧行情（如 2000-01-01）降为研究提醒，不授权三倍定律。日历不可用时
    # fail-closed（UNKNOWN → research），不阻断其他独立退出。
    data_qual_reason = None
    data_asof_raw = getattr(stock_data, "quote_as_of", None) if stock_data is not None else None
    if _bad_analysis_date:
        data_qual_reason = f"分析日期非法: {_bad_analysis_date!r}"
    elif ref_dt is not None and data_asof_raw:
        _dp = parse_source_time(str(data_asof_raw))
        if _dp is None:
            data_qual_reason = "行情/指标快照时点不可解析"
        elif _dp[0].date() > ref_dt[0].date():
            data_qual_reason = "行情/指标快照时点晚于本次分析日期（数据与场景错配）"
        elif live:
            # Q-R5（QR4 覆盖合同）：行情快照日期不得早于「最近已完成交易日」，
            # 且分析日必须落在**已确认覆盖**内。日历来源是只读严格查询
            #（trading_days(allow_refresh=False)，绝不登录/刷新/按星期猜测）：
            # 新 schema 带完整自然日覆盖记录（每个日期明确交易/非交易；生产端
            # 核验供应商返回与查询区间完全一致才落缓存，消费端逐自然日复核）。
            # 旧 schema（缺覆盖元数据）不得授权——「列表里没有这天」不能证明休市，
            # 未来一行不能证明中间已覆盖（QR4 实证：max 未来行使 gap 为负即放行、
            # 缓存只到周五周一仍授权）。12 天猜测覆盖与靠一个未来交易日作证明的
            # 路径删除。覆盖待确认时三倍纪律降为提醒（提示可见），价格止损/ST/
            # 期限等独立保护不受影响。
            _cal, _cal_diag = None, ""
            try:
                from src.core.fear_index import history as fear_history_module
                from src.core.fear_index.history import trading_days as _tdays
                _cal = _tdays(allow_refresh=False)
            except Exception as _cal_err:
                _cal_diag = str(_cal_err) or type(_cal_err).__name__
            if not _cal:
                data_qual_reason = (f"交易日历覆盖待确认（{_cal_diag}），"
                                    f"三倍纪律暂为提醒，行情时点资格未知")
            else:
                from datetime import timedelta as _tdelta
                _quote_day = _dp[0].date().isoformat()
                _ref_day = ref_dt[0].date()
                # 场次（与 recent_trade_date 展示口径一致）：交易日当日 15:00 前
                # 尚未收盘，当日行情尚不存在——参照日前移一天再取「最近已完成
                # 交易日」，盘前/盘中不再把昨收误判陈旧。analysis_date 为日精度，
                # 场次以墙钟时刻判定（测试可冻结时钟）；分析历史日期（回放）不
                # 前移。datetime.now 恒可用（含测试冻结时钟），无需异常兜底。
                _now_dt = fear_history_module.datetime.now()
                if (_ref_day.isoformat() == _now_dt.strftime("%Y-%m-%d")
                        and _now_dt.hour < 15):
                    _ref_day = _ref_day - _tdelta(days=1)
                if not _cal.get("qualified"):
                    # 旧 schema：不得授权（QR4）。保留已知交易日陈旧预检作为诊断
                    #（陈旧行情提示可见），覆盖待确认为本因。
                    _legacy = _cal.get("legacy_dates") or []
                    _recent_known = max((d for d in _legacy if d <= _ref_day.isoformat()),
                                        default=None)
                    if _recent_known and _quote_day < _recent_known:
                        data_qual_reason = (f"行情快照日期({_dp[0].date()})早于已知"
                                            f"最近交易日({_recent_known})，行情已陈旧；"
                                            f"且交易日历缺覆盖元数据，覆盖待确认，"
                                            f"三倍纪律暂为提醒")
                    else:
                        data_qual_reason = ("交易日历缺覆盖元数据，覆盖待确认，"
                                            "行情时点资格未知，三倍纪律暂为提醒")
                elif not (_cal["start"] <= _ref_day.isoformat() <= _cal["end"]):
                    data_qual_reason = ("交易日历覆盖待确认（分析日期不在已确认覆盖"
                                        "边界内），三倍纪律暂为提醒，行情时点资格未知")
                else:
                    # 覆盖记录已经 strict 读取核验为「键集合与声称边界精确相等」
                    #（QR5：缺行/范围外多行均拒）。交易日选取仍加明确范围防护
                    #（下界=覆盖起点）：消费者只从已核定区间取最近已完成交易日。
                    _trading_prior = [d for d, f in _cal["days"].items()
                                      if f == 1
                                      and _cal["start"] <= d <= _ref_day.isoformat()]
                    if not _trading_prior:
                        data_qual_reason = ("交易日历覆盖内无已完成交易日，"
                                            "行情时点资格未知，三倍纪律暂为提醒")
                    else:
                        _recent_td = max(_trading_prior)
                        if _quote_day < _recent_td:
                            data_qual_reason = (f"行情快照日期({_dp[0].date()})早于"
                                                f"最近已完成交易日({_recent_td})，"
                                                f"行情已陈旧")

    for f in findings:
        if f.action_scope != "exit":
            # research/none 条目从未申请硬权限——原样透传（reason 保留原语义，
            # 不被资格门噪声覆盖）；diag 条目借此保持可见性
            rejected.append(f)
            continue
        reasons = []
        if data_qual_reason:
            reasons.append(data_qual_reason)
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
        if not (f.source or "").strip():
            reasons.append("来源未知")

        # 时点资格（Q-R5）：证据时点须严格可解析且不得晚于独立分析日期。
        # 坏字符串（如 "not-a-date"）经 parse_source_time 返回 None → 拒绝，
        # 不经 source_time_in_future 的 False 语义漏过。
        if ref_dt is not None:
            if not f.as_of:
                reasons.append("证据时点缺失")
            else:
                _ev = parse_source_time(f.as_of)
                if _ev is None:
                    reasons.append(f"证据时点不可解析: {f.as_of!r}")
                elif source_time_in_future(f.as_of, ref_dt[0]):
                    reasons.append("证据时点晚于本次分析日期")

        # 时点参照缺失（回测未传 bar 日期）→ exit 一律拒绝并显式诊断（不默许）
        if _missing_analysis_date:
            reasons.append("分析日期缺失（回测无时点参照，不授权硬退出）")

        # 计划适用性 + 复算（Q-R1/QR2 收口）：三项独立核验，缺一不可——
        # 1) 模式适用性不依赖行情对象存在（气宗直接拒绝）；
        # 2) 当前行情对象缺失 → 复算无从进行 → 不授权（不以「复算非 None」放行）；
        # 3) 复算结果须具有与申请发现相同的合格身份/证券/质量——复算返回 UNKNOWN
        #    数据资格（如缺 quote_as_of，stock.py :278）或主体不符时同样拒绝。
        if not reasons:
            if mode == "qizong":
                reasons.append("当前持有计划为气宗：三倍定律纪律不适用该计划")
            elif stock_data is None:
                reasons.append("当前行情对象缺失，复算无法确认（不授权硬退出）")
            else:
                # Q-R1（QR4）：行情对象**主体证券**独立核对——复算的 securities 由
                # 申请参数写入，「复算证券匹配」是自证；须先核 stock_data.stock_code
                # 与本次请求证券一致（沿既有 split(".")[0] 规范化口径），缺失/非法/
                # 不一致一律拒绝并保留诊断。不改行情 stock_code、也不只改复算返回
                # securities 来「修复」。
                _data_code = str(getattr(stock_data, "stock_code", "") or "").strip().split(".")[0]
                _req_code = str(stock_code or "").strip().split(".")[0]
                if not _data_code or not _req_code or _data_code != _req_code:
                    reasons.append(f"当前行情主体({_data_code or '缺失'})与请求证券"
                                   f"({_req_code or '缺失'})不一致，不授权硬退出")
                else:
                    from src.core.exit_signals.stock import _check_triple_up_rule
                    # 复算传行情自身代码：复算结果的 securities 独立来自数据主体，
                    # 不再由申请参数回填（消除自证链路）
                    recomputed = _check_triple_up_rule(stock_data, _data_code)
                    if recomputed is None:
                        reasons.append("复算未确认：当前数据不满足三倍定律条件（缓存/转述不作为依据）")
                    elif (getattr(recomputed, "data_quality", None) != "OK"
                          or getattr(recomputed, "signal_id", "") != f.signal_id
                          or _req_code not in (getattr(recomputed, "securities", None) or [])):
                        reasons.append("复算结果资格不足（数据质量/信号身份/证券与申请不符）")

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
