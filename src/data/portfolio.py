"""持仓记录管理器 - 方案B（自动+手动混合）

功能：
1. 读取 portfolio.yaml → 初始化 StrategyState
2. 分析后记录观察量与建议（F1：不再把建议当持仓事实回写）
3. 用户确认实际成交（confirm_fill）才改变持仓
4. 支持手动添加/删除/列出持仓

设计原则：
- 持仓数据必须真实，系统只建议不强制
- YAML格式透明可编辑，用户随时可手动修改
- 系统自动维护strategy_state，用户一般不需碰
- 并发写保护（M5，v0.8.15）：保存前比对磁盘内容指纹（sha256），被外部/其他
  实例改过 → 拒绝写入 + 内存回滚磁盘版，绝不静默覆盖较新改动；mutator 返回
  bool 如实上报保存成败
- 事实/建议分离（F1，plan/fusion ADR-F03）：分析生成的 new_state 不再整包当
  真实持仓回写——record_analysis_observation 只写观察量与信号历史；
  建议入 ProposalStore（PROPOSED）；confirm_fill 是唯一把建议变成持仓事实的
  入口。旧 update_from_strategy_decision 的"建议即成交"路径已删除（回滚可回
  旧建议，不恢复分析自动当成交）
"""

import hashlib
import math
import os
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from typing import NamedTuple, Optional

import yaml

from src.data.models import (
    StrategyState, TradeLifecycle, SignalType, TradePlan
)
from src.data.proposals import (
    FillRecord, FillResult, Proposal, ProposalStore,
)

logger = logging.getLogger(__name__)

# 默认持仓文件路径（项目根目录）
DEFAULT_PORTFOLIO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "portfolio.yaml"
)

# F1 持仓来源标记（ADR-F03：旧记录无成交来源 → LEGACY_UNVERIFIED，保持原值提示一次核对）
VERIFICATION_LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"
VERIFICATION_USER_ENTERED = "USER_ENTERED"
VERIFICATION_CONFIRMED_FILL = "CONFIRMED_FILL"
VERIFICATION_USER_VERIFIED = "USER_VERIFIED"  # 用户肉眼核对旧记录后确认（2026-09-26 走查缺口）

# 进程级一次性提示标记（LEGACY_UNVERIFIED 核对提醒只打一次，不刷屏）
_legacy_notice_shown = False

# F1 观察量白名单：record_analysis_observation 允许写入的 strategy_state 键。
# 白名单制（而非黑名单）——StrategyState 未来新增字段默认不落盘，防止新字段
# 静默变成"持仓事实"；确需持久化须评审后显式加入。
# 注意冷却/保护期计数的语义分界见 record_analysis_observation docstring。
_OBSERVATION_STATE_KEYS = (
    "recent_signals",
    "inertia_counter",
    "last_decision",
    "signal_stability_score",
    "reverse_count",
    "last_tick_date",
    "min_hold_remaining",
    "add_protection_remaining",
    "reduce_protection_remaining",
)


class SaveResult(NamedTuple):
    """持仓保存结果（M5，plan/TECHNICAL_HANDOFF §6）。

    ok=True 已写入磁盘；ok=False 且 conflict=True = 磁盘被外部/其他实例修改，
    本次改动被拒绝且内存已回滚为磁盘版本（防旧快照覆盖较新改动）；
    ok=False 且 conflict=False = 损坏保护/IO 失败，内存不变。
    """
    ok: bool
    conflict: bool = False


def _file_fingerprint(path) -> Optional[str]:
    """文件内容指纹（sha256 hex）；文件不存在/不可读返回 None。"""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _validate_ratio(value: float, field: str = "仓位比例") -> float:
    """持仓比例合法性校验（ISS-078）：必须为有限数值且落在 [0, 1]。

    背景：add 路径此前零校验（-0.5/NaN 实测可落盘，总仓位可被污染成负数）；
    NaN/Inf 因 `nan < 0` 为 False 绕过 update 路径的简单比较。入口多为 AI
    供参的 manage_portfolio，数据层必须兜底。
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{field}需为有限数值，收到 {value!r}")
    if value < 0 or value > 1:
        raise ValueError(f"{field}需在 0-1 之间（0%-100%），收到 {value}")
    return float(value)


def _validate_price(value: float, field: str = "开仓价") -> float:
    """开仓价合法性校验（ISS-078）：必须为有限正数。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{field}需为有限数值，收到 {value!r}")
    if value <= 0:
        raise ValueError(f"{field}需为正数，收到 {value}")
    return float(value)


def _notice_legacy_unverified_once(stock_code: str) -> None:
    """F1：旧记录（无成交来源标记）进程内提示一次核对，不刷屏。"""
    global _legacy_notice_shown
    if not _legacy_notice_shown:
        _legacy_notice_shown = True
        logger.info(
            f"持仓 {stock_code} 等旧记录无成交来源标记（holding_verification 缺失），"
            f"已按 LEGACY_UNVERIFIED 处理：数值保持原样；建议跑 `pos` 核对一遍实际持仓")


class PositionRecord:
    """单只股票的持仓记录"""

    def __init__(
        self,
        stock_code: str,
        stock_name: str = "",
        entry_date: Optional[str] = None,
        entry_price: Optional[float] = None,
        current_ratio: float = 0.0,
        last_action: str = "OPEN",
        last_action_semantic: Optional[str] = None,
        last_sell_path: Optional[str] = None,
        last_action_date: Optional[str] = None,
        high_since_entry: Optional[float] = None,
        lifecycle: str = "FLAT",
        strategy_state: Optional[dict] = None,
        trade_plan: Optional[TradePlan] = None,
        trade_plan_raw: Optional[dict] = None,
        holding_verification: Optional[str] = None,
        ratio_stale: bool = False,
        quantity_fact: Optional[dict] = None,
    ):
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.current_ratio = current_ratio
        self.last_action = last_action
        self.last_action_semantic = last_action_semantic
        self.last_sell_path = last_sell_path
        self.last_action_date = last_action_date
        self.high_since_entry = high_since_entry
        self.lifecycle = lifecycle
        self.strategy_state = strategy_state or {}
        self.trade_plan = trade_plan  # v0.8.5 TradePlan 子系统
        # ISS-078：加载时无法解析的 trade_plan 原始 dict 原样带回（保存时原样写回），
        # 防"加载失败→保存→静默抹除计划数据"的第二层丢失
        self.trade_plan_raw = trade_plan_raw
        # F1（ADR-F03）：持仓来源标记——CONFIRMED_FILL=用户确认成交 / USER_ENTERED=
        # 手动录入 / LEGACY_UNVERIFIED=F1 之前的旧记录（数值保持原样，提示一次核对）
        self.holding_verification = holding_verification
        # K0a/D4：股数是事实——数量级成交后比例视图待重估标记 + 账户快照事实留痕
        # （quantity_fact={quantity, as_of, avg_cost}，事实源=账户事件账本）
        self.ratio_stale = ratio_stale
        self.quantity_fact = quantity_fact

    @property
    def quantity_held(self) -> int:
        """数量事实股数（无数量事实/非法=0）——有仓判定用（L1：数量决定有无仓，
        旧比例 0 不等于无仓）。"""
        try:
            return int((self.quantity_fact or {}).get("quantity") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def weight_unknown(self) -> bool:
        """权重未知（L1）：数量账户（quantity>0）且比例视图过期——消费者不得拿
        current_ratio 冒充已知权重。"""
        return self.ratio_stale and self.quantity_held > 0

    def to_dict(self) -> dict:
        """转为YAML可序列化的字典"""
        d = {
            "stock_name": self.stock_name,
            "entry_date": self.entry_date,
            "entry_price": self.entry_price,
            "current_ratio": self.current_ratio,
            "last_action": self.last_action,
            "last_action_semantic": self.last_action_semantic,
            "last_sell_path": self.last_sell_path,
            "last_action_date": self.last_action_date,
            "high_since_entry": self.high_since_entry,
            "lifecycle": self.lifecycle,
            "strategy_state": self.strategy_state,
        }
        if self.trade_plan is not None:
            # Pydantic v2: model_dump() 转 dict；YAML 兼容
            d["trade_plan"] = self.trade_plan.model_dump()
        elif self.trade_plan_raw is not None:
            d["trade_plan"] = self.trade_plan_raw
        if self.holding_verification:
            d["holding_verification"] = self.holding_verification
        if self.ratio_stale:
            d["ratio_stale"] = True
        if self.quantity_fact is not None:
            d["quantity_fact"] = self.quantity_fact
        return d

    @classmethod
    def from_dict(cls, stock_code: str, data: dict) -> "PositionRecord":
        """从YAML字典创建"""
        plan_data = data.get("trade_plan")
        trade_plan = None
        trade_plan_raw = None
        if plan_data:
            # ISS-078：残缺 trade_plan 不再让整个持仓加载崩溃（此前 TradePlan(**plan_data)
            # 的 ValidationError 会拖垮 get_position/list_positions 全体），原始数据保留
            try:
                trade_plan = TradePlan(**plan_data)
            except Exception as e:
                logger.error(
                    f"持仓 {stock_code} 的 trade_plan 无法解析（字段残缺或类型不对），"
                    f"原始数据已保留、分析时按无计划处理；修复字段后可恢复: {e}")
                trade_plan_raw = plan_data if isinstance(plan_data, dict) else None
        # F1：无来源标记的旧记录 → LEGACY_UNVERIFIED（数值保持原样；进程内提示一次核对）
        verification = data.get("holding_verification")
        if not verification:
            verification = VERIFICATION_LEGACY_UNVERIFIED
            _notice_legacy_unverified_once(stock_code)
        return cls(
            stock_code=stock_code,
            stock_name=data.get("stock_name", ""),
            entry_date=data.get("entry_date"),
            entry_price=data.get("entry_price"),
            current_ratio=data.get("current_ratio", 0.0),
            last_action=data.get("last_action", "OPEN"),
            last_action_semantic=data.get("last_action_semantic"),
            last_sell_path=data.get("last_sell_path"),
            last_action_date=data.get("last_action_date"),
            high_since_entry=data.get("high_since_entry"),
            lifecycle=data.get("lifecycle", "FLAT"),
            strategy_state=data.get("strategy_state", {}),
            trade_plan=trade_plan,
            trade_plan_raw=trade_plan_raw,
            holding_verification=verification,
            ratio_stale=bool(data.get("ratio_stale", False)),
            quantity_fact=data.get("quantity_fact"),
        )


# ── M0（plan/fusion iteration6）：账户事实到最终行动的同源上下文 ──────────────
# 账户事实三态：HELD=有仓事实（数量/比例）；NONE=已知空仓；UNKNOWN=未对账或读取异常
POSITION_HELD = "HELD"
POSITION_NONE = "NONE"
POSITION_UNKNOWN = "UNKNOWN"


def _parse_source_time(raw):
    """来源时点**严格解析**（N2/R13 X3）：返回 (交易所时区 aware datetime, 是否
    时刻精度)；非法 → None（明确未知——不截取坏字符串救回日期）。

    - 带偏移的 aware 时刻 → 归一 Asia/Shanghai（同一实际时刻不同偏移等价）
    - naive 带时刻（新浪本地墙钟形态）→ 显式按 Asia/Shanghai 解释
    - 纯日期（YYYY-MM-DD，日历合法）→ 当日 00:00 上海（日精度——按日期判未来，
      不伪造盘中时点）
    - 非法日历（2026-02-30）/非法时刻（99:99:99）/垃圾尾巴/空 → None"""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        from zoneinfo import ZoneInfo
        sh = ZoneInfo("Asia/Shanghai")
    except Exception:
        return None
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return None  # 非法日历（如 2026-02-30）
        return datetime(d.year, d.month, d.day, tzinfo=sh), False
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        dt = (dt.replace(tzinfo=sh) if dt.tzinfo is None else dt.astimezone(sh))
    except Exception:
        return None
    return dt, True


def _qualified_weight(quantity, price, price_as_of, nav, nav_as_of) -> tuple[Optional[float], str]:
    """数量×价格/NAV 的合格估值门（L1 资格语义的共享核心——to_strategy_state 与
    AccountContext 装配共用同一判据，消费者不再各自判读）。

    N2 口径（R13 X3）：真实解析区分合法日精度与时刻精度；**先按实际时刻比较
    未来、再取交易所日**（当天未来时刻拒绝）；缺失/非法/未来拒绝（不靠墙钟
    补齐）；finite 检查防 NaN/Inf 混入资格；降级旧日收盘与当天 NAV 不拼当前权重。

    Returns:
        (weight, reason)：weight=None 时 reason 为人话资格缺口（不拿过期比例
        冒充、不用 0 假装未知）。"""
    if quantity is None or int(quantity) <= 0:
        return None, "无数量事实"
    if price is None or not math.isfinite(float(price)) or price <= 0 \
            or nav is None or not math.isfinite(float(nav)) or nav <= 0:
        return None, "缺价格或NAV（或非有限数值）"
    p = _parse_source_time(price_as_of)
    n = _parse_source_time(nav_as_of)
    if p is None or n is None:
        return None, "行情或NAV时点不可解析/缺失"
    try:
        from zoneinfo import ZoneInfo
        now_sh = datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        now_sh = datetime.now().astimezone()
    for label, (dt, has_time) in (("行情", p), ("NAV", n)):
        # 时刻精度先比实际时刻（当天未来时刻拒绝）；日精度按交易所日期判未来
        if (dt > now_sh) if has_time else (dt.date() > now_sh.date()):
            return None, f"{label}时点在未来"
    if p[0].date() != n[0].date():
        return None, "价格与NAV时点不一致（保守同日窗）"
    derived = int(quantity) * float(price) / float(nav)
    if derived > 1.0:
        return None, f"数量×价格/NAV={derived:.3f}>1（杠杆/数据异常）"
    return derived, "合格估值锁定"


@dataclass(frozen=True)
class AccountContext:
    """一次分析请求内装配的单股账户事实上下文（M0，修 W1 根因）。

    显式区分「有无持仓」（数量事实/比例）与「权重资格」（合格估值）——
    策略→最终包→周期策略→影子→摘要/证据/diff/建议消费同一份，不再各自从
    pos.current_ratio 重复判读（W1：旧比例 0 被当成无仓/已知权重吞掉清仓）。
    confirmed_weight=None 表示权重未知——不是 0。"""

    security_id: str
    position_state: str = POSITION_UNKNOWN   # HELD / NONE / UNKNOWN
    quantity: Optional[int] = None           # 数量事实股数（RATIO_ONLY/未知=None）
    confirmed_weight: Optional[float] = None
    weight_reason: str = ""                  # 资格/缺口原因（人话）
    account_version: str = ""                # 账本内容版本（同请求一致）


class RequestAccountFacts:
    """一次命令请求的账户事实装配器（M0/B2）。

    构造时读账户快照**一次**（内容版本+NAV+各股数量），同请求所有股票共享
    同一账户版本；下一命令新建本实例重新读——**禁止全局长寿命缓存**（B2：
    请求内 30 次消费同版本，下一请求才刷新）。"""

    def __init__(self, pm: "PortfolioManager"):
        self._pm = pm
        self.account_version = ""
        self.nav: Optional[float] = None
        self.nav_day: Optional[str] = None
        self._quantities: dict[str, int] = {}
        self._ledger_present = False
        self._read_failed = False      # N0：读取失败≠明确空仓（UNKNOWN 待对账）
        self._ledger_partial = False   # N0：隔离事件/PARTIAL——冻结精确权重
        try:
            from src.data.account_service import DEFAULT_LEDGER_PATH, AccountService
            if Path(DEFAULT_LEDGER_PATH).exists():
                snap = AccountService(DEFAULT_LEDGER_PATH).snapshot()
                self._ledger_present = True
                self.account_version = str(snap.account_version or "")
                self.nav = snap.nav
                # N2：NAV 原时点保留偏移传资格门（不先 strftime 丢时区）
                self.nav_day = (snap.nav_priced_at.isoformat(timespec="seconds")
                                if snap.nav_priced_at else None)
                self._quantities = {h.security_id: int(h.quantity or 0)
                                    for h in snap.holdings}
                if snap.isolated_events or snap.data_completeness == "PARTIAL":
                    self._ledger_partial = True
        except Exception as e:
            self._read_failed = True
            logger.warning(f"请求级账户事实读取失败（本次按未对账处理，不当作空仓）: {e}")

    def _ledger_quantity(self, stock_code: str) -> int:
        """账本数量事实（代码形态归一：600519 / 600519.SZ / sh600519 都能命中）。"""
        raw = str(stock_code or "").strip()
        candidates = [raw, raw.split(".")[0]]
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) >= 6:
            candidates.append(digits[-6:])
        for c in candidates:
            if c in self._quantities:
                return self._quantities[c]
        return 0

    def _weight_for(self, quantity: int, price, price_as_of) -> tuple[Optional[float], str]:
        w, reason = _qualified_weight(quantity, price, price_as_of, self.nav, self.nav_day)
        if self._ledger_partial:
            # N0：隔离事件（PARTIAL）——精确权重一律冻结，原因保留原缺口说明
            return None, f"{reason}；账本含隔离事件——待对账"
        return w, reason

    def context_for(self, stock_code: str, *, price: Optional[float] = None,
                    price_as_of: Optional[str] = None) -> AccountContext:
        """装配单股上下文（数量事实决定持仓；RATIO_ONLY 保留比例判持仓；
        冲突/未对账明确 UNKNOWN——不编造数量；账本读取失败≠明确空仓）。"""
        pm = self._pm
        version = self.account_version

        def _ctx(state, quantity=None, weight=None, reason="") -> AccountContext:
            return AccountContext(security_id=stock_code, position_state=state,
                                  quantity=quantity, confirmed_weight=weight,
                                  weight_reason=reason, account_version=version)

        if getattr(pm, "_corrupted", False):
            return _ctx(POSITION_UNKNOWN, reason="持仓文件读取异常——账户未对账")
        try:
            pos = pm.get_position(stock_code)
        except Exception as e:
            logger.debug(f"持仓读取失败 {stock_code}（按未对账处理）: {e}")
            return _ctx(POSITION_UNKNOWN, reason="持仓记录读取失败——账户未对账")

        if self._read_failed:
            # N0（X1）：账本快照读取失败——不能当作明确空仓（NONE/0）；
            # 投影记录独立成立的比例语义仍有效（下方分支处理）
            if pos is None:
                return _ctx(POSITION_UNKNOWN,
                            reason="账户账本读取失败——未对账（不当作空仓）")
            if pos.quantity_fact is not None:
                # 数量账户投影在、账本读不到——无法核对，保守未对账
                q = pos.quantity_held
                if q > 0:
                    return _ctx(POSITION_UNKNOWN, quantity=q,
                                reason="账本读取失败且存在数量投影——未对账")
                return _ctx(POSITION_NONE, quantity=0, weight=0.0,
                            reason="账本读取失败；投影记录亦无持仓")
            # RATIO_ONLY 落到下方既有分支（投影比例语义不依赖账本）

        ledger_q = self._ledger_quantity(stock_code)
        if pos is None:
            if ledger_q > 0:
                w, reason = self._weight_for(ledger_q, price, price_as_of)
                if w is None:
                    reason += "（账本数量事实；比例视图待重估）"
                return _ctx(POSITION_HELD, quantity=ledger_q, weight=w, reason=reason)
            return _ctx(POSITION_NONE, quantity=0, weight=0.0, reason="无持仓记录")

        proj_q = pos.quantity_held if pos.quantity_fact is not None else None
        if ledger_q > 0:
            # 账本数量事实优先（事实源=事件账本；含 RATIO_ONLY 记录+期初导入未同步
            # quantity_fact 的形态——账本有股不吞持仓）
            if proj_q is not None and proj_q != ledger_q:
                return _ctx(POSITION_HELD, quantity=ledger_q, weight=None,
                            reason="数量投影与账本不一致——待对账（不编造数量）")
            w, reason = self._weight_for(ledger_q, price, price_as_of)
            return _ctx(POSITION_HELD, quantity=ledger_q, weight=w, reason=reason)
        if self._ledger_present and proj_q is not None and proj_q > 0:
            # 记录说有仓、账本说无仓——持仓事实本身矛盾 → 保守未对账
            return _ctx(POSITION_UNKNOWN, quantity=proj_q,
                        reason="数量记录与账本冲突（记录有仓/账本无仓）——待对账")
        if proj_q is not None:
            # 数量账户投影事实（账本缺席或无该股事件时以投影为准）
            if proj_q > 0:
                w, reason = self._weight_for(proj_q, price, price_as_of)
                return _ctx(POSITION_HELD, quantity=proj_q, weight=w, reason=reason)
            return _ctx(POSITION_NONE, quantity=0, weight=0.0, reason="记录与账本均无持仓")

        # RATIO_ONLY：比例判持仓（既有语义零变化）
        if (pos.current_ratio or 0) > 0:
            return _ctx(POSITION_HELD, weight=pos.current_ratio,
                        reason="RATIO_ONLY 比例直通")
        return _ctx(POSITION_NONE, quantity=0, weight=0.0, reason="比例 0（无数量事实）")


class PortfolioManager:
    """持仓记录管理器

    用法：
        pm = PortfolioManager()
        # 读取持仓
        pos = pm.get_position("002192")
        # 转为StrategyState
        state = pm.to_strategy_state("002192")
        # 分析后更新
        # 手动操作
        pm.add_position("002192", stock_name="融捷股份", entry_price=35.20, ratio=0.20)
        pm.list_positions()
        pm.remove_position("002192")
    """

    def __init__(self, portfolio_path: Optional[str] = None, proposals_path: Optional[str] = None):
        self.portfolio_path = portfolio_path or DEFAULT_PORTFOLIO_PATH
        self._data: dict = {}
        self._corrupted = False  # G01：加载失败置位，禁止 _save 覆盖真实持仓
        # F1：建议/成交账本（~/.muyun/proposals.json；测试可注入临时路径）
        self._proposals = ProposalStore(proposals_path)
        self._load()

    DEFAULT_MIN_HOLD_DAYS = 5

    def _load(self):
        """加载portfolio.yaml

        v0.8.7.8 裁决修复 G01：解析失败不再 fail-open 清空（原实现把损坏文件当
        "空仓"继续跑，下一次 _save 就把真实持仓静默抹掉——实测 1 只 → 0）。
        现置 _corrupted=True，_save 拒绝覆盖，损坏文件原样保留供人工修复。
        G04：键规范化——无引号数字键被 YAML 解析为 int，has_position("600519")
        查不到但总仓位照算（隐形持仓），统一转 str。
        """
        if not os.path.exists(self.portfolio_path):
            logger.info(f"持仓文件不存在，将创建: {self.portfolio_path}")
            self._data = {"positions": {}}
            self._loaded_fingerprint = None
            return

        try:
            with open(self.portfolio_path, 'r', encoding='utf-8') as f:
                self._data = yaml.safe_load(f) or {"positions": {}}
            positions = self._data.get("positions") or {}
            self._data["positions"] = {
                (str(k).strip().zfill(6) if str(k).strip().isdigit() else str(k).strip()): v
                for k, v in positions.items()
            }
            self._corrupted = False
            # M5（升级 ISS-078 的 mtime 基线）：记录加载时的内容指纹，
            # _save 据此检测会话外修改（内容级比对，touch 不误报）
            self._loaded_fingerprint = _file_fingerprint(self.portfolio_path)
            logger.info(f"持仓文件已加载: {self.portfolio_path}")
        except Exception as e:
            logger.error(
                f"持仓文件加载失败（已置损坏保护：后续保存将被拒绝，"
                f"请修复或删除 {self.portfolio_path} 后重试）: {e}")
            self._data = {"positions": {}}
            self._corrupted = True
            self._loaded_fingerprint = None

    def _save(self) -> SaveResult:
        """保存到portfolio.yaml

        v0.8.7.8 裁决修复 G01：
        - _corrupted 置位时拒绝写入（宁可不保存也不静默清掉真实持仓）
        - 原子写：先写 tmp 再 os.replace，进程被杀/磁盘满不再产生半截文件
        - 每次保存前保留 .bak 单份滚动备份；批量删仓（≥4→减半）额外留
          portfolio.yaml.before_<时间戳> 快照（合法操作放行，但数据可追溯）

        M5（v0.8.15）并发写保护：保存前比对磁盘内容指纹——磁盘文件在本实例
        加载后被外部/其他实例改过（内容变化）→ 拒绝写入并回滚内存为磁盘版，
        宁可让用户重做一次操作也不静默覆盖较新改动（原 ISS-078 实现只告警仍
        覆盖，形同虚设）。内容未变的 touch（编辑器保存/复制）不算冲突。
        """
        if getattr(self, "_corrupted", False):
            logger.error("G01 损坏保护生效：portfolio.yaml 此前加载失败，本次保存被拒绝。"
                         "请人工修复该文件（或删除后重试），期间持仓改动仅保留在内存")
            return SaveResult(False, False)

        # M5 外部修改检测：磁盘有文件且内容 ≠ 本实例加载时的内容 → 冲突拒绝。
        # 磁盘无文件 = 无外部内容可覆盖（含"外部删除后重建"工作流），照常写入。
        disk_fp = _file_fingerprint(self.portfolio_path)
        if disk_fp is not None and disk_fp != getattr(self, "_loaded_fingerprint", None):
            self._load()   # 内存回滚：与磁盘真值重新同步（先回滚再告警，措辞才属实）
            logger.warning(
                "持仓文件已被其他会话或编辑器修改，本次改动被拒绝（未覆盖外部改动），"
                "内存已恢复为磁盘最新版本——请核对后重新操作")
            return SaveResult(False, True)

        os.makedirs(os.path.dirname(self.portfolio_path) or ".", exist_ok=True)

        # 快照保护：已有 4 只以上持仓、本次要写掉一半以上时，先留快照（不阻断合法删仓）
        try:
            with open(self.portfolio_path, 'r', encoding='utf-8') as f:
                old_data = yaml.safe_load(f) or {}
            old_count = len(old_data.get("positions", {}) or {})
            new_count = len(self._data.get("positions", {}) or {})
            if old_count >= 4 and new_count < old_count / 2:
                snap = f"{self.portfolio_path}.before_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                import shutil
                shutil.copy2(self.portfolio_path, snap)
                logger.warning(
                    f"G01 快照保护：本次保存持仓数 {old_count}→{new_count} 减半以上，"
                    f"保存前快照已留 {snap}（合法批量删仓请忽略此告警）")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"G01 快照保护读取旧文件失败（不影响保存）: {e}")

        # 备份上一版（滚动单份）
        try:
            if os.path.exists(self.portfolio_path):
                import shutil
                shutil.copy2(self.portfolio_path, self.portfolio_path + ".bak")
        except OSError as e:
            logger.warning(f"G01 .bak 备份失败（继续保存）: {e}")

        tmp_path = self.portfolio_path + ".tmp"
        try:
            header = (
                "# ============================================================\n"
                "# 暮云思辨投资助手 - 持仓记录本\n"
                "# ============================================================\n"
                "#\n"
                "# 使用说明：\n"
                "# 1. 实时分析时系统会自动读取此文件，获取你的持仓状态\n"
                "# 2. 分析完成后，系统会建议更新持仓，输入Y确认/N跳过\n"
                "# 3. 你也可以手动编辑此文件（如在外部做了交易操作）\n"
                "#\n"
                "# 重要：系统决策依赖此数据的准确性！\n"
                "#   - 如果你在券商APP做了买卖，请及时手动更新此文件\n"
                "#   - lifecycle字段说明：FLAT=空仓 OPEN=新开仓 HOLD=持仓 EXIT=退出过程 COOLDOWN=冷却期\n"
                "#   - strategy_state由系统自动维护，一般不需要手动修改\n"
                "#\n"
                "# ============================================================\n\n"
            )
            body = yaml.dump(
                self._data,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
            content = header + body
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write(content)
            # G01 原子替换：tmp 完整写完后一次性替换，杜绝半截文件
            os.replace(tmp_path, self.portfolio_path)
            self._corrupted = False
            # 自己的写入不算"会话外修改"：指纹直接取刚写入的字节（不重读文件，
            # 消除重读被杀软/备份工具瞬时独占返回 None 造成的下轮误拒窗口）。
            # 文本模式写入时 '\n' 会被翻译成 os.linesep（Windows=CRLF），
            # 哈希必须对翻译后的实际字节算，否则下轮加载比对必假冲突
            self._loaded_fingerprint = hashlib.sha256(
                content.replace("\n", os.linesep).encode("utf-8")).hexdigest()
            logger.info(f"持仓文件已保存: {self.portfolio_path}")
            return SaveResult(True, False)
        except Exception as e:
            logger.error(f"持仓文件保存失败: {e}")
            return SaveResult(False, False)

    # ===== 读取操作 =====

    def get_position(self, stock_code: str) -> Optional[PositionRecord]:
        """获取指定股票的持仓记录"""
        positions = self._data.get("positions", {})
        pos_data = positions.get(stock_code)
        if pos_data is None:
            return None
        return PositionRecord.from_dict(stock_code, pos_data)

    def has_position(self, stock_code: str) -> bool:
        """是否持有指定股票"""
        return stock_code in self._data.get("positions", {})

    def list_positions(self) -> list[PositionRecord]:
        """列出所有持仓"""
        positions = self._data.get("positions", {})
        return [
            PositionRecord.from_dict(code, data)
            for code, data in positions.items()
        ]

    def get_total_position_ratio(self) -> Optional[float]:
        """所有持仓 current_ratio 之和（0-1+，可能>1表示满仓+杠杆）。

        L1（T01）：任一数量账户持仓权重未知（ratio_stale 且 quantity>0）→ 返回
        None——不拿部分已知和冒充总仓位（过期比例不得给风险预算）。

        报告2.3 笨总教学十"永不满仓留底牌"：建议总仓位<=80%，预留20%底牌。
        """
        positions = self.list_positions()
        if any(p.weight_unknown for p in positions):
            return None
        return sum(p.current_ratio for p in positions)

    def account_nav(self) -> tuple[Optional[float], Optional[str]]:
        """账户 NAV 与定价时点（L1 权重资格估值的 NAV 来源）。

        读默认账户账本快照（惰性 import 防循环）；无账本/NAV 未录入 → (None, None)
        ——不发明数字。"""
        try:
            from src.data.account_service import DEFAULT_LEDGER_PATH, AccountService
            if not DEFAULT_LEDGER_PATH.exists():
                return None, None
            snap = AccountService(DEFAULT_LEDGER_PATH).snapshot()
            nav = snap.nav
            # N2（X3）：NAV 原时点保留偏移传资格门（不先 strftime 丢时区）
            nav_day = (snap.nav_priced_at.isoformat(timespec="seconds")
                       if snap.nav_priced_at else None)
            return nav, nav_day
        except Exception as e:
            logger.debug(f"账户 NAV 读取失败（权重按未知处理）: {e}")
            return None, None

    def strategy_state_for(self, stock_code: str, stock_data, *,
                           nav: Optional[float] = None,
                           nav_as_of: Optional[str] = None) -> StrategyState:
        """装配边界便捷入口（L1）：自动接估值输入（行情价/行情时点 + 账户 NAV）
        → 权重资格判定。scanner/TUI/Web 等无请求上下文的消费方走此入口。

        M0 起 l/la/chat 走 RequestAccountFacts 一次装配 + to_strategy_state 直调
        （请求内同版本、不逐股回读账本——B2）；本入口缺省行为不变（逐股读
        默认账本 NAV）。"""
        price = getattr(stock_data, "price", None)
        price_as_of = getattr(stock_data, "quote_as_of", None)
        if nav is None and nav_as_of is None:
            nav, nav_day = self.account_nav()
        else:
            nav_day = nav_as_of
        return self.to_strategy_state(stock_code, price=price, price_as_of=price_as_of,
                                      nav=nav, nav_as_of=nav_day)

    def request_account_facts(self) -> RequestAccountFacts:
        """请求级账户事实装配器（M0）：l/la/chat 每次命令开头调用一次，
        逐股 `context_for` 共享同一账户版本；下一命令重新调用刷新。"""
        return RequestAccountFacts(self)

    def to_strategy_state(self, stock_code: str, *, price: Optional[float] = None,
                          price_as_of: Optional[str] = None,
                          nav: Optional[float] = None,
                          nav_as_of: Optional[str] = None,
                          account_context: Optional[AccountContext] = None) -> StrategyState:
        """将持仓记录转为StrategyState（供策略层使用）

        如果没有持仓记录且未提供账户上下文，返回默认的FLAT状态。

        L1（R11/V3）数量账户读取资格：
        - 数量事实>0 → 有仓（数量决定有无仓；旧比例 0 不等于无仓）
        - 权重需合格估值：价格与 NAV **同日窗**（保守相等判定，DESIGN_VALIDATION：
          不自行放宽容差）且数量×价格/NAV 可算 → 用已知数字锁定；
          否则 current_position_ratio=None（权重未知）——不拿过期比例冒充，
          也不用 0 假装未知。派生只读不改账（迁移不反造历史）。
        - RATIO_ONLY 账户（无数量事实）→ 既有 float 比例直通，语义零变化。

        N0（R13 X1）：提供 account_context 时**ctx 是持仓/权重的唯一事实源**——
        策略层不再从投影比例二次判读（旧比例 .1 与账本事实并存的形态必须给
        ctx 合格权重）；账本有仓而无投影不生成 FLAT/0（重建仓语义、成本未知
        不伪造）。生命周期/保护期等元数据仍来自投影（缺失时按 ctx 派生）。
        """
        pos = self.get_position(stock_code)
        if pos is None and account_context is None:
            return StrategyState()  # 默认FLAT（旧调用方无上下文——既有语义）

        # N0：ctx 为事实源时的派生（投影缺失也要给合法 StrategyState）
        if pos is None:
            if account_context.position_state == POSITION_HELD:
                # 账本有仓、投影缺失——重建仓语义（X1 主反例：不得 FLAT/0）
                return StrategyState(
                    lifecycle=TradeLifecycle.OPEN,
                    entry_price=None,  # 成本未知——不从价格伪造
                    current_position_ratio=account_context.confirmed_weight,
                )
            if account_context.position_state == POSITION_UNKNOWN:
                # 持仓未知——不声称有仓生命周期；权重未知（None），
                # 退出方向由 has_position（ctx 驱动）保守保留
                return StrategyState(
                    lifecycle=TradeLifecycle.FLAT,
                    current_position_ratio=account_context.confirmed_weight,
                )
            return StrategyState()  # 已知空仓

        # 解析lifecycle
        try:
            lifecycle = TradeLifecycle(pos.lifecycle)
        except ValueError:
            lifecycle = TradeLifecycle.FLAT

        # 从strategy_state字典恢复
        ss = pos.strategy_state or {}

        # 解析last_decision
        last_decision = None
        if ss.get("last_decision"):
            try:
                last_decision = SignalType(ss["last_decision"])
            except ValueError:
                last_decision = None

        # L1：权重资格（数量账户 → 合格估值才给数字，否则 None=未知）
        # M0：资格判据抽为 _qualified_weight 共享核心（to_strategy_state 与
        # AccountContext 同一判据——消费者不再各自判读）
        # N0（X1）：提供 ctx 时 ctx 合格权重是唯一事实源——不再从投影比例
        # 二次判读（旧比例 .1 与账本事实并存的形态必须给 ctx 权重）
        if account_context is not None:
            current_ratio: Optional[float] = account_context.confirmed_weight
        else:
            current_ratio = pos.current_ratio
            if pos.weight_unknown:
                current_ratio = None
                q = pos.quantity_held
                qualified, reason = _qualified_weight(q, price, price_as_of, nav, nav_as_of)
                if qualified is not None:
                    current_ratio = qualified  # 合格估值——用已知数字锁定
                elif "杠杆" in reason or "异常" in reason:
                    # 杠杆异常（>100%）超出比例字段语义——按未知处理并留痕，不截断伪造
                    logger.info(
                        f"to_strategy_state {stock_code}: {reason}——权重按未知处理，"
                        "请核对估值输入")
        return StrategyState(
            lifecycle=lifecycle,
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            current_position_ratio=current_ratio,
            recent_signals=ss.get("recent_signals", []),
            inertia_counter=ss.get("inertia_counter", 0),
            last_decision=last_decision,
            cooldown_remaining=ss.get("cooldown_remaining", 0),
            cooldown_reason=ss.get("cooldown_reason"),
            reverse_count=ss.get("reverse_count", 0),
            total_commission_paid=ss.get("total_commission_paid", 0.0),
            signal_stability_score=ss.get("signal_stability_score", 1.0),
            reduce_protection_remaining=ss.get("reduce_protection_remaining", 0),
            min_hold_remaining=ss.get("min_hold_remaining", 0),
            add_protection_remaining=ss.get("add_protection_remaining", 0),
            last_tick_date=ss.get("last_tick_date"),
        )

    # ===== 写入操作 =====

    def add_position(
        self,
        stock_code: str,
        stock_name: str = "",
        entry_price: Optional[float] = None,
        ratio: float = 0.20,
        lifecycle: str = "OPEN",
    ) -> bool:
        """手动添加持仓

        ISS-078：ratio/entry_price 走数据层校验——此前本路径零校验，
        负数/NaN/Inf 仓位实测可落盘并污染总仓位计算（AI 供参入口必须兜底）。

        Returns (M5): True 保存成功；False 保存失败（冲突被拒绝/IO 失败，
            详见告警——调用方不得报假成功）
        """
        ratio = _validate_ratio(ratio)
        if entry_price is not None:
            entry_price = _validate_price(entry_price)
        else:
            # ISS-091：无开仓价的持仓，止损/止盈分级判定（依赖未实现收益%）全部
            # 失效，SELL 退化为可被 PlanGuard 压制的 weak_sell——建仓时必须警示
            logger.warning(
                f"add_position {stock_code}: 未提供开仓价——该持仓的止损/止盈分级判定"
                f"将失效（仅剩 trade_plan.current_stop 兜底），强烈建议补录价格")
        today = datetime.now().strftime("%Y-%m-%d")
        record = PositionRecord(
            stock_code=stock_code,
            stock_name=stock_name,
            entry_date=today,
            entry_price=entry_price,
            current_ratio=ratio,
            last_action="OPEN",
            last_action_date=today,
            lifecycle=lifecycle,
            holding_verification=VERIFICATION_USER_ENTERED,
            strategy_state={
                "cooldown_remaining": 0,
                "cooldown_reason": None,
                "reverse_count": 0,
                "inertia_counter": 1,
                "last_decision": "BUY",
                "recent_signals": ["BUY"],
                "signal_stability_score": 1.0,
                "reduce_protection_remaining": 0,
                "min_hold_remaining": self.DEFAULT_MIN_HOLD_DAYS if lifecycle == "OPEN" else 0,
                "add_protection_remaining": 0,
                "last_tick_date": None,
            }
        )

        if "positions" not in self._data:
            self._data["positions"] = {}
        self._data["positions"][stock_code] = record.to_dict()
        return self._save().ok

    def remove_position(self, stock_code: str) -> bool:
        """删除持仓记录。

        Returns (M5): True 删除并保存成功；False 无此记录或保存失败（详见告警）
        """
        positions = self._data.get("positions", {})
        if stock_code in positions:
            del positions[stock_code]
            return self._save().ok
        return False

    def update_position_fields(
        self,
        stock_code: str,
        current_ratio: Optional[float] = None,
        entry_price: Optional[float] = None,
        stock_name: Optional[str] = None,
    ) -> bool:
        """字段级修改已有持仓（v0.8.8 chat 持仓编辑）。

        只改用户可感知的三字段，strategy_state/lifecycle 等系统维护字段不动。
        走 _save()（原子写 + .bak 备份 + 损坏保护）。

        Returns:
            True 修改成功；False 持仓不存在 / 没有可改项 / 保存失败
            （M5：冲突被拒绝或 IO 失败，详见告警）
        """
        positions = self._data.get("positions", {})
        if stock_code not in positions:
            return False

        rec = positions[stock_code]
        # ISS-078 监督审查 P3：先全部校验再落内存——此前 ratio 校验通过已写入、
        # price 校验再抛异常会留下内存/磁盘短暂 desync
        new_ratio = _validate_ratio(current_ratio) if current_ratio is not None else None
        new_price = _validate_price(entry_price) if entry_price is not None else None
        changed = False
        if new_ratio is not None:
            rec["current_ratio"] = new_ratio
            changed = True
        if new_price is not None:
            rec["entry_price"] = new_price
            changed = True
        if stock_name is not None and stock_name.strip():
            rec["stock_name"] = stock_name.strip()
            changed = True

        if not changed:
            return False
        return self._save().ok

    def attach_plan(self, stock_code: str, plan: TradePlan) -> bool:
        """为已存在持仓附加 TradePlan（v0.8.5）。

        Args:
            stock_code: 股票代码
            plan: TradePlan 实例（建议由 TradePlanGenerator 生成 + 用户编辑后传入）

        Returns:
            True 表示附加成功；False 表示持仓不存在或保存失败（M5 起如实传播）
        """
        if "positions" not in self._data or stock_code not in self._data["positions"]:
            return False
        self._data["positions"][stock_code]["trade_plan"] = plan.model_dump()
        return self._save().ok

    # ===== F1：分析观察量 / 建议存储 / 成交确认（ADR-F03 三拆）=====

    @staticmethod
    def _fresh_strategy_state(min_hold_days: int = 0) -> dict:
        """新建持仓的策略状态初始化（add_position 与 confirm_fill 共用一份语义）。"""
        return {
            "cooldown_remaining": 0,
            "cooldown_reason": None,
            "reverse_count": 0,
            "inertia_counter": 1,
            "last_decision": "BUY",
            "recent_signals": ["BUY"],
            "signal_stability_score": 1.0,
            "reduce_protection_remaining": 0,
            "min_hold_remaining": min_hold_days,
            "add_protection_remaining": 0,
            "last_tick_date": None,
        }

    def verify_holding(self, stock_code: str) -> bool:
        """用户核对确认（2026-09-26 走查缺口 ISS-111）：LEGACY_UNVERIFIED 旧记录经
        用户核对后标记 USER_VERIFIED（消除「建议核对」提醒）。

        不改任何数值（数量/成本/日期原样）；CONFIRMED_FILL 的成交溯源不覆盖；
        无记录返回 False；已核对幂等返回 True。
        Returns: True=已核对且保存成功；False=无记录或保存失败（M5 冲突/IO，
        失败时恢复内存旧值——否则 CLI「重试一次」会撞幂等短路假成功，审查 P1）。
        """
        rec = self._data.get("positions", {}).get(stock_code)
        if rec is None:
            return False
        old = rec.get("holding_verification")
        if old == VERIFICATION_USER_VERIFIED:
            return True
        if old == VERIFICATION_CONFIRMED_FILL:
            return True  # 成交溯源已确认——核对标记不覆盖（溯源语义保留）
        rec["holding_verification"] = VERIFICATION_USER_VERIFIED
        result = self._save()
        if bool(getattr(result, "ok", result)):
            return True
        rec["holding_verification"] = old  # 保存失败恢复旧值（IO 路径不自动回滚）
        return False

    def record_analysis_observation(
        self,
        stock_code: str,
        stock_name: str,
        strategy_decision,  # StrategyDecision
        stock_data,  # StockData
        price: Optional[float] = None,
    ) -> bool:
        """记录分析观察量（F1，ADR-F03）：不改变已确认数量/成本/开仓日期/生命周期。

        写入范围（白名单 _OBSERVATION_STATE_KEYS + high_since_entry）：
        - high_since_entry：已存值 / entry_exit 算的 / 当前价 三者取大（原语义保留）
        - 信号历史与纪律计数：recent_signals / inertia_counter / last_decision /
          signal_stability_score / reverse_count / last_tick_date / min_hold /
          add_protection / reduce_protection（随交易日推进的倒数，系统自身记忆）
        - 冷却期：**只在已有冷却时**持久化 strategy_layer 的逐日倒数（tick_cooldown
          已按日去重）；冷却归零且已确认清仓 → 删除记录（既有语义）。
          观察绝不"发起"冷却/清仓——那是 confirmed 成交才有的后果（G03：受阻的
          EXIT 不能启动"已经清仓"的冷却）

        无持仓记录时返回 False（分析入口不得伪造持仓）；同日重复分析各字段为
        绝对值写入，无变化时不落盘（幂等，交易日计数不会重复推进）。

        Returns (M5): True 无变化或保存成功；False 保存失败（冲突被拒绝/IO 失败）
        """
        positions = self._data.get("positions", {})
        rec = positions.get(stock_code)
        if rec is None:
            return False  # 无持仓不伪造（TASKS F1 验收：所有分析入口都不能伪造持仓）
        new_state = strategy_decision.new_state
        price = price if price is not None else getattr(stock_data, "price", None)

        changed = False

        # 名称回填：仅记录还没有名字且传入名有效时（防覆盖用户维护的名称，旧防御保留）
        if not rec.get("stock_name") and stock_name and stock_name != stock_code:
            rec["stock_name"] = stock_name
            changed = True

        # high_since_entry 三者取大（原 update_from_strategy_decision 语义，观察量）
        _ee = getattr(strategy_decision, "entry_exit", None) or {}
        _ee_highest = _ee.get("highest_since_entry") if isinstance(_ee, dict) else None
        prev_high = rec.get("high_since_entry")
        _cands = [v for v in [prev_high, _ee_highest, price]
                  if v is not None and isinstance(v, (int, float)) and v > 0]
        new_high = max(_cands) if _cands else None
        if new_high != prev_high:
            rec["high_since_entry"] = new_high
            changed = True

        # 信号历史与纪律计数（白名单键，绝对值写入 → 同日重复分析天然幂等）
        # F1 审查 P1-1：手工编辑可能留下 `strategy_state:`（null）——setdefault 会把
        # None 当"已有键"返回，后续 .get 直接崩；显式归一为 dict
        ss = rec.get("strategy_state")
        if not isinstance(ss, dict):
            ss = rec["strategy_state"] = {}
        new_ss = {
            "recent_signals": new_state.recent_signals,
            "inertia_counter": new_state.inertia_counter,
            "last_decision": new_state.last_decision.value if new_state.last_decision else None,
            "signal_stability_score": new_state.signal_stability_score,
            "reverse_count": new_state.reverse_count,
            "last_tick_date": getattr(new_state, "last_tick_date", None),
            "min_hold_remaining": new_state.min_hold_remaining,
            "add_protection_remaining": new_state.add_protection_remaining,
            "reduce_protection_remaining": new_state.reduce_protection_remaining,
        }
        for k in _OBSERVATION_STATE_KEYS:
            if ss.get(k) != new_ss.get(k):
                ss[k] = new_ss.get(k)
                changed = True

        # 已有冷却的倒数推进（既成事实的衰减，非新冷却的发起）
        if (ss.get("cooldown_remaining") or 0) > 0:
            if ss.get("cooldown_remaining") != new_state.cooldown_remaining:
                ss["cooldown_remaining"] = new_state.cooldown_remaining
                changed = True
            if ss.get("cooldown_reason") != new_state.cooldown_reason:
                ss["cooldown_reason"] = new_state.cooldown_reason
                changed = True
            # 冷却结束 + 已确认清仓（仓位 0）→ 交易完全结束，删除记录（既有语义）
            if (new_state.cooldown_remaining <= 0
                    and new_state.lifecycle == TradeLifecycle.FLAT
                    and (rec.get("current_ratio") or 0) <= 0):
                del positions[stock_code]
                return self._save().ok

        if not changed:
            return True
        return self._save().ok

    def record_proposal(
        self,
        stock_code: str,
        stock_name: str,
        strategy_decision,  # StrategyDecision
        source: str = "",
    ) -> Optional[Proposal]:
        """把分析建议入账（F1，ADR-F03）：状态 PROPOSED，用户可见"这是建议，尚未记为成交"。

        只对持仓股的 ADD/REDUCE/CLOSE_ALL 出建议；HOLD_POSITION/STAY_OUT 与非持仓股
        （含"开仓候选"）返回 None——候选类建议属 F4 候选谱系范围，F1 不伪造
        "已可建仓"的建议。建议写入独立账本（~/.muyun/proposals.json），不改 portfolio.yaml。
        """
        pos_action = strategy_decision.position_action.value
        if pos_action not in ("ADD", "REDUCE", "CLOSE_ALL"):
            return None
        existing = self.get_position(stock_code)
        # L1（V3）：非持仓判定数量感知——重建仓（旧比例 0、数量>0）已持股，
        # 不得因旧比例 0 漏建议；真非持仓 = 无记录或（比例 0 且无数量事实）
        if existing is None or ((existing.current_ratio or 0) <= 0
                                and existing.quantity_held <= 0):
            return None  # 非持仓不产生建议（TASKS F1：所有分析入口都不能伪造持仓）
        # L1：position_ratio=None（权重未知）→ 建议不带伪造目标（target_ratio 本就可空）
        raw_ratio = strategy_decision.position_ratio
        if pos_action == "CLOSE_ALL":
            target_ratio: Optional[float] = 0.0
        elif raw_ratio is not None and raw_ratio > 0:
            target_ratio = raw_ratio
        else:
            target_ratio = strategy_decision.new_state.current_position_ratio
        reasons = getattr(strategy_decision, "strategy_reasons", None) or []
        prop = Proposal(
            stock_code=stock_code,
            stock_name=existing.stock_name if existing and existing.stock_name else (stock_name or stock_code),
            source=source,
            position_action=pos_action,
            action_semantic=strategy_decision.action_semantic,
            sell_path=getattr(strategy_decision, "sell_path", None),
            target_ratio=target_ratio,
            current_ratio=existing.current_ratio if existing else 0.0,
            reason=reasons[0] if reasons else (strategy_decision.action_semantic or pos_action),
            cooldown_days=(strategy_decision.new_state.cooldown_remaining or 0)
                          if pos_action == "CLOSE_ALL" else 0,
        )
        self._proposals.refresh()  # M5 同款：写前重同步，防长持实例陈旧快照覆盖
        prop = self._proposals.add_proposal(prop)
        if not self._proposals._save():
            logger.warning(f"建议账本写入失败（{stock_code} {pos_action}），建议仅保留在内存")
            return None
        logger.info(
            f"建议已记录（未确认）: {stock_code} {pos_action}"
            f"{f' -> 目标仓位 {target_ratio:.0%}' if target_ratio is not None else ''}"
            f"（proposal_id={prop.proposal_id[:8]}，`pos confirm {stock_code}` 确认实际成交）")
        return prop

    # ── K0a/D3：投影消费水位（与投影值同一次 _save 原子落盘）──

    def _applied_fills(self) -> dict:
        """fill 消费水位（存在 portfolio.yaml 内，与持仓值同文件同次保存——
        「投影写后/水位前」从结构上不可出现）。"""
        return self._data.setdefault("applied_fills", {})

    def _has_applied_fill(self, fill_id: str) -> bool:
        return str(fill_id or "") in self._data.get("applied_fills", {})

    def has_applied_fill(self, fill_id: str) -> bool:
        """该 fill_id 是否已被比例投影消费（K0a/D3 水位——崩溃恢复判定用）。"""
        return self._has_applied_fill(fill_id)

    def confirm_fill(
        self,
        stock_code: str,
        action: str,
        ratio_change: float,
        *,
        fill_id: Optional[str] = None,
        price: Optional[float] = None,
        date: Optional[str] = None,
        stock_name: str = "",
        proposal_id: Optional[str] = None,
        note: str = "",
    ) -> FillResult:
        """用户确认实际成交（F1）：唯一把建议变成持仓事实的入口（比例路径）。

        - BUY：无记录则建仓（entry_date/entry_price 取成交日/成交价）；有记录则加仓
        - SELL：减仓；清仓时按关联建议的 cooldown_days 进入冷却（保留记录），
          无冷却可走则删除记录（与既有手动删除同语义）
        - 重复 fill_id 幂等拒绝（不重复入账）；部分卖出关联建议转 PARTIAL
        - 被确认成交的持仓标记 holding_verification=CONFIRMED_FILL

        写入顺序（K0a/D3）：持仓值+消费水位**同一次** _save 落盘（投影写后/水位前
        不可出现）→ 后写建议/成交账本（簿记；失败如实告警，重试按水位幂等补记，
        不重复变动持仓）。数量级成交走 apply_quantity_fill（股数是事实，见该方法）。
        """
        action = (action or "").upper()
        if action not in ("BUY", "SELL"):
            return FillResult(False, False, f"action 需为 BUY/SELL，收到 {action!r}")
        try:
            ratio_change = _validate_ratio(ratio_change, "成交仓位变化")
        except ValueError as e:
            return FillResult(False, False, str(e))
        if ratio_change <= 0:
            return FillResult(False, False, f"成交仓位变化需 > 0，收到 {ratio_change}")
        fill_id = fill_id or uuid.uuid4().hex
        self._proposals.refresh()  # M5 同款：写前重同步（幂等判定基于磁盘最新账本）
        already_applied = self._has_applied_fill(fill_id)
        in_ledger = self._proposals.has_fill(fill_id)
        if already_applied and in_ledger:
            return FillResult(True, True,
                              f"fill_id {fill_id} 已入账过，幂等跳过（持仓未重复变动）")
        # P2-1：价格校验两分支统一走数据层兜底（FillResult 错误契约，不裸抛）
        if price is not None:
            try:
                price = _validate_price(price, "成交价")
            except ValueError as e:
                return FillResult(False, False, str(e))

        today = date or datetime.now().strftime("%Y-%m-%d")
        positions = self._data.setdefault("positions", {})
        rec = positions.get(stock_code)

        if already_applied or in_ledger:
            # K0a/D3 崩溃恢复：水位或账本任一在案 → 投影不重复变动（写入顺序是
            # 持仓+水位先、账本后，两处记录在案即「已应用过」）；只补齐缺失的簿记。
            # 旧数据（无水位时代）账本有记录 = 持仓当时已更新——回填水位，不重放。
            proposal = self._match_proposal(stock_code, proposal_id, action)
            if proposal is not None and rec is not None:
                new_ratio = rec.get("current_ratio") or 0.0
                partial = False
                if proposal.target_ratio is not None:
                    partial = (new_ratio > proposal.target_ratio + 1e-9) if action == "SELL" \
                        else (new_ratio < proposal.target_ratio - 1e-9)
                self._proposals.mark_confirmed(proposal.proposal_id, partial=partial)
            repaired = False
            if not in_ledger:
                self._proposals.record_fill(FillRecord(
                    fill_id=fill_id, stock_code=stock_code, date=today, action=action,
                    ratio_change=ratio_change, price=price,
                    proposal_id=(proposal.proposal_id if proposal else None), note=note))
                repaired = True
            if not already_applied:
                # 水位回填（与投影值同文件保存——历史数据补齐水位标记）
                self._applied_fills()[fill_id] = {
                    "action": action, "date": today,
                    "recorded_at": datetime.now().isoformat(timespec="seconds")}
                if not self._save().ok:
                    logger.warning(
                        f"fill 消费水位回填失败（{stock_code} fill_id={fill_id[:8]}）——"
                        f"持仓未变动；下次确认同 fill_id 会再次尝试回填")
            if repaired and not self._proposals._save():
                logger.warning(
                    f"成交账本补记写入失败（{stock_code} {action} fill_id={fill_id[:8]}）——"
                    f"持仓未重复变动，重复提交同 fill_id 可再次补记，请检查 {self._proposals.path}")
                return FillResult(True, True,
                                  f"fill_id {fill_id} 已入账过，幂等跳过（持仓未重复变动）；"
                                  "账本补记写入失败，重跑可再补")
            return FillResult(True, True,
                              f"fill_id {fill_id} 已入账过，幂等跳过（持仓未重复变动）"
                              + ("；账本已补记" if repaired else ""))

        # F1 审查 P1-3：BUY 确认同样匹配建议（ADD 建议→BUY），状态机对加仓腿不断裂
        proposal = self._match_proposal(stock_code, proposal_id, action)

        if action == "BUY":
            if rec is None:
                record = PositionRecord(
                    stock_code=stock_code,
                    stock_name=stock_name or stock_code,
                    entry_date=today,
                    entry_price=price,
                    current_ratio=ratio_change,
                    last_action="CONFIRMED_BUY",
                    last_action_semantic=note or "用户确认买入成交",
                    last_action_date=today,
                    lifecycle="OPEN",
                    holding_verification=VERIFICATION_CONFIRMED_FILL,
                    strategy_state=self._fresh_strategy_state(
                        min_hold_days=self.DEFAULT_MIN_HOLD_DAYS),
                )
                positions[stock_code] = record.to_dict()
                new_ratio = ratio_change
            else:
                was_empty = (rec.get("current_ratio") or 0.0) <= 1e-9
                new_ratio = (rec.get("current_ratio") or 0.0) + ratio_change
                if new_ratio > 1 + 1e-9:
                    return FillResult(False, False,
                                      f"买入后仓位 {new_ratio:.0%} 超过 100%，请核对比例")
                # P2-2：从空仓/冷却期记录上确认买入 = 重新建仓——重置生命周期与
                # 策略状态（否则 COOLDOWN+冷却残留会继续冻结策略层对该股的处理）
                if was_empty:
                    rec["lifecycle"] = "OPEN"
                    rec["entry_date"] = today
                    # 重新建仓：成本基线取本次成交价；未传价则置 None（不残留旧持有期价）
                    rec["entry_price"] = price if price is not None else None
                    rec["strategy_state"] = self._fresh_strategy_state(
                        min_hold_days=self.DEFAULT_MIN_HOLD_DAYS)
                rec["current_ratio"] = new_ratio
                rec["last_action"] = "CONFIRMED_BUY"
                rec["last_action_semantic"] = note or "用户确认买入成交"
                rec["last_action_date"] = today
                rec["holding_verification"] = VERIFICATION_CONFIRMED_FILL
                if price is not None:
                    rec["entry_price"] = price
                if was_empty:
                    rec["entry_date"] = today  # 重新建仓：开仓日期取本次成交日
        else:  # SELL
            if rec is None:
                return FillResult(False, False,
                                  f"{stock_code} 无持仓记录，无法确认卖出（请核对代码）")
            cur = rec.get("current_ratio") or 0.0
            new_ratio = round(cur - ratio_change, 10)
            if new_ratio < -1e-9:
                return FillResult(False, False,
                                  f"卖出 {ratio_change:.0%} 超过当前仓位 {cur:.0%}，请核对")
            if new_ratio <= 1e-9:
                # 全额确认清仓：按建议进冷却（保留记录）否则删除记录（既有手动语义）
                cooldown_days = proposal.cooldown_days if proposal else 0
                if cooldown_days > 0:
                    rec["current_ratio"] = 0.0
                    rec["lifecycle"] = "COOLDOWN"
                    rec["last_action"] = "CONFIRMED_SELL"
                    rec["last_action_semantic"] = note or "用户确认清仓成交"
                    rec["last_action_date"] = today
                    rec["holding_verification"] = VERIFICATION_CONFIRMED_FILL
                    # F1 审查 P1-1 同款：null strategy_state 归一为 dict
                    ss = rec.get("strategy_state")
                    if not isinstance(ss, dict):
                        ss = rec["strategy_state"] = {}
                    ss["cooldown_remaining"] = cooldown_days
                    ss["cooldown_reason"] = "confirmed_close"
                    # P2-4：成交日即冷却第 0 天——同步 last_tick_date，防止成交日
                    # 再被 strategy_layer 当新交易日多扣一天冷却
                    ss["last_tick_date"] = today
                else:
                    del positions[stock_code]
            else:
                rec["current_ratio"] = new_ratio
                rec["last_action"] = "CONFIRMED_SELL"
                rec["last_action_semantic"] = note or "用户确认减仓成交"
                rec["last_action_date"] = today
                rec["holding_verification"] = VERIFICATION_CONFIRMED_FILL

        # K0a/D3：消费水位与投影值同一次原子保存——「投影写后/水位前」不可出现
        self._applied_fills()[fill_id] = {
            "action": action, "date": today,
            "recorded_at": datetime.now().isoformat(timespec="seconds")}
        if not self._save().ok:
            return FillResult(False, False, "持仓文件保存失败（详见告警），本次成交未入账")

        # 簿记：成交账本 + 建议状态推进（portfolio 已落盘；账本失败如实告警不回滚事实）
        fill = FillRecord(
            fill_id=fill_id, stock_code=stock_code, date=today, action=action,
            ratio_change=ratio_change, price=price,
            proposal_id=(proposal.proposal_id if proposal else None), note=note,
        )
        ledger_ok = self._proposals.record_fill(fill)
        # F1 审查 P1-4：partial 按"是否达到建议目标"判，不是"仓位剩没剩"——
        # REDUCE 目标 10%、恰好卖到 10% 时应转 CONFIRMED（否则留一条永远办不完的假待办）
        partial = False
        if proposal is not None and proposal.target_ratio is not None:
            if action == "SELL":
                partial = new_ratio > proposal.target_ratio + 1e-9
            else:  # BUY
                partial = new_ratio < proposal.target_ratio - 1e-9
        if proposal:
            self._proposals.mark_confirmed(proposal.proposal_id, partial=partial)
        if not self._proposals._save():
            logger.warning(
                f"建议/成交账本写入失败（{stock_code} {action} {ratio_change:.0%}）——"
                f"持仓已更新但幂等账本缺失，重复提交同一 fill_id 可按水位幂等补记"
                f"（不会重复变动持仓），请检查 {self._proposals.path}")
        msg = (f"已确认{('买入' if action == 'BUY' else '卖出')} {ratio_change:.0%}"
               f"（{stock_code}，{today}，fill_id={fill_id[:8]}）"
               + ("" if ledger_ok else "｜⚠ 幂等账本写入失败"))
        return FillResult(True, False, msg)

    def apply_quantity_fill(
        self,
        stock_code: str,
        action: str,
        *,
        quantity: int,
        price: Optional[float],
        trade_date: str,
        fill_id: str,
        proposal_id: Optional[str] = None,
        note: str = "",
        quantity_before: Optional[int] = None,
        quantity_after: Optional[int] = None,
        avg_cost: Optional[float] = None,
    ) -> FillResult:
        """数量级成交的比例视图同步（K0a/D4：股数是事实，建议是意图）。

        调用方（CLI 数量路径）先经 AccountService 事件账本入账（事实源），再把
        账户快照派生的事实（成交/剩余股数、批次均价、真实成交日）传入本方法；
        本方法只把这些事实同步到比例视图，**不用建议目标反推仓位变化**：

        - 部分卖出：保留持仓记录（不删、不清仓语义），标记 ratio_stale（NAV 未知
          不得把比例冒充事实）+ quantity_fact 事实留痕
        - 剩余 0 股：事实清仓才走既有清仓语义（冷却/删除）
        - 买入加仓：成本取批次均价（不覆盖为最新买价）
        - 建议：只有事实达成（清仓完成）才 CONFIRMED，否则保持待确认+偏离注记

        写入顺序与 confirm_fill 相同：水位+持仓值同次 _save → 账本幂等补记。
        """
        action = (action or "").upper()
        if action not in ("BUY", "SELL"):
            return FillResult(False, False, f"action 需为 BUY/SELL，收到 {action!r}")
        if int(quantity) <= 0:
            return FillResult(False, False, f"成交数量需 > 0，收到 {quantity}")
        self._proposals.refresh()
        if self._has_applied_fill(fill_id):
            # 水位在=投影已消费（水位与投影值同次落盘）——不重复变动，只补簿记
            proposal = self._match_proposal(stock_code, proposal_id, action)
            repaired = False
            if not self._proposals.has_fill(fill_id):
                self._proposals.record_fill(FillRecord(
                    fill_id=fill_id, stock_code=stock_code, date=trade_date,
                    action=action, ratio_change=None, price=price,
                    quantity=int(quantity),
                    proposal_id=(proposal.proposal_id if proposal else None), note=note))
                repaired = True
            if proposal is not None:
                self._mark_proposal_after_quantity(
                    proposal, action, int(quantity), quantity_after)
            if repaired and not self._proposals._save():
                logger.warning(
                    f"成交账本补记写入失败（{stock_code} {action} fill_id={fill_id[:8]}）——"
                    f"持仓未重复变动，重复提交同 fill_id 可再次补记，请检查 {self._proposals.path}")
                return FillResult(True, True,
                                  f"fill_id {fill_id} 已入账过，幂等跳过（持仓未重复变动）；"
                                  "账本补记写入失败，重跑可再补")
            return FillResult(True, True,
                              f"fill_id {fill_id} 已入账过，幂等跳过（持仓未重复变动）"
                              + ("；账本已补记" if repaired else ""))

        positions = self._data.setdefault("positions", {})
        rec = positions.get(stock_code)
        if rec is None:
            # 不从数量事实伪造比例视图（比例需 NAV；视图缺记录必须显式补建）
            return FillResult(False, False,
                              f"数量事实已入账（fill_id={fill_id[:8]}），但 {stock_code} 无持仓"
                              "比例记录——不伪造比例视图；请先 pos add 建立比例视图再重跑确认同步")
        proposal = self._match_proposal(stock_code, proposal_id, action)

        if action == "SELL":
            if quantity_after is not None and quantity_after <= 0:
                # 剩余 0 股=事实清仓 → 既有清仓语义（冷却/删除），日期用真实成交日
                cooldown_days = proposal.cooldown_days if proposal else 0
                if cooldown_days > 0:
                    rec["current_ratio"] = 0.0
                    rec["lifecycle"] = "COOLDOWN"
                    rec["last_action"] = "CONFIRMED_SELL"
                    rec["last_action_semantic"] = note or f"实际卖出{quantity}股清仓"
                    rec["last_action_date"] = trade_date
                    rec["holding_verification"] = VERIFICATION_CONFIRMED_FILL
                    ss = rec.get("strategy_state")
                    if not isinstance(ss, dict):
                        ss = rec["strategy_state"] = {}
                    ss["cooldown_remaining"] = cooldown_days
                    ss["cooldown_reason"] = "confirmed_close"
                    ss["last_tick_date"] = trade_date
                    rec.pop("ratio_stale", None)
                    rec.pop("quantity_fact", None)
                else:
                    del positions[stock_code]
            else:
                rec["last_action"] = "CONFIRMED_SELL"
                rec["last_action_semantic"] = note or f"已成交{quantity}股，剩余{quantity_after}股"
                rec["last_action_date"] = trade_date
                rec["holding_verification"] = VERIFICATION_CONFIRMED_FILL
                rec["ratio_stale"] = True
                rec["quantity_fact"] = {"quantity": quantity_after, "as_of": trade_date,
                                        "avg_cost": avg_cost}
        else:  # BUY
            # K0a 审查 P2：空仓/冷却期记录上数量级买入 = 重新建仓——与比例路径同
            # 口径重置生命周期与策略状态（否则冷却残留继续冻结已重建仓的处理）
            was_empty = (rec.get("current_ratio") or 0.0) <= 1e-9
            if was_empty:
                rec["lifecycle"] = "OPEN"
                rec["entry_date"] = trade_date
                rec["strategy_state"] = self._fresh_strategy_state(
                    min_hold_days=self.DEFAULT_MIN_HOLD_DAYS)
            rec["last_action"] = "CONFIRMED_BUY"
            rec["last_action_semantic"] = note or f"已成交{quantity}股，现持有{quantity_after}股"
            rec["last_action_date"] = trade_date
            rec["holding_verification"] = VERIFICATION_CONFIRMED_FILL
            # K0a-4：加仓成本派生自批次均价——不用最新买价覆盖历史成本
            #（重建仓无历史成本，直接取本次批次均价）
            if avg_cost and (not was_empty or rec.get("entry_price") is None):
                rec["entry_price"] = avg_cost
            rec["ratio_stale"] = True
            rec["quantity_fact"] = {"quantity": quantity_after, "as_of": trade_date,
                                    "avg_cost": avg_cost}

        # 水位与投影值同一次原子保存（K0a/D3）
        self._applied_fills()[fill_id] = {
            "action": action, "date": trade_date, "quantity": int(quantity),
            "recorded_at": datetime.now().isoformat(timespec="seconds")}
        if not self._save().ok:
            return FillResult(False, False,
                              "持仓文件保存失败（详见告警），比例投影未同步"
                              "（数量事实已在账户事件账本，重跑同一确认可幂等恢复投影）")
        # 簿记 + 建议状态（可重建派生结果；失败如实告警，重试按水位幂等补记）
        if not self._proposals.has_fill(fill_id):
            self._proposals.record_fill(FillRecord(
                fill_id=fill_id, stock_code=stock_code, date=trade_date,
                action=action, ratio_change=None, price=price,
                quantity=int(quantity),
                proposal_id=(proposal.proposal_id if proposal else None), note=note))
        if proposal is not None:
            self._mark_proposal_after_quantity(proposal, action, int(quantity), quantity_after)
        if not self._proposals._save():
            logger.warning(
                f"建议/成交账本写入失败（{stock_code} {action} {quantity}股）——"
                f"持仓与水位已落盘，重复提交同 fill_id 可幂等补记（不会重复变动持仓），"
                f"请检查 {self._proposals.path}")
        msg = (f"已记录{('买入' if action == 'BUY' else '卖出')} {quantity} 股"
               f"（{stock_code}，{trade_date}，fill_id={fill_id[:8]}）")
        return FillResult(True, False, msg)

    def _mark_proposal_after_quantity(self, proposal: Proposal, action: str,
                                      quantity: int, quantity_after: Optional[int]) -> None:
        """数量事实 → 建议状态（K0a/D4）：只有事实达成（清仓完成）才 CONFIRMED，
        否则保持待确认+偏离注记——不虚判「建议全额完成」。"""
        pending = getattr(proposal.status, "value", None) in ("PROPOSED", "PARTIAL")
        if action == "SELL" and quantity_after is not None and quantity_after <= 0:
            if pending:
                self._proposals.mark_confirmed(proposal.proposal_id, partial=False)
            proposal.note = f"实际清仓达成（本笔卖出{quantity}股，剩余0股）"
        elif pending:
            verb = "卖出" if action == "SELL" else "买入"
            proposal.note = (f"本笔{verb}{quantity}股，现持有{quantity_after}股"
                             f"——无法以股数事实核验建议（{proposal.position_action}）"
                             "的完成度，建议保持待确认；如继续请再次确认成交")

    def _match_proposal(self, stock_code: str, proposal_id: Optional[str],
                        action: str) -> Optional[Proposal]:
        """按 proposal_id 精确匹配，否则取该股最新 PROPOSED 同向建议（无则 None）。"""
        if proposal_id:
            for p in self._proposals._proposals:
                if p.proposal_id == proposal_id:
                    return p
            return None
        for p in reversed(self._proposals._proposals):
            if (p.stock_code == stock_code
                    and getattr(p.status, "value", None) in ("PROPOSED", "PARTIAL")):
                # 方向粗校验：卖出确认配卖出类建议，买入确认配买入类建议
                sell_like = p.position_action in ("REDUCE", "CLOSE_ALL")
                if (action == "SELL") == sell_like:
                    return p
        return None

    def pending_proposals(self, stock_code: Optional[str] = None) -> list[Proposal]:
        """待确认建议列表（pos list / chat 提示用）。"""
        self._proposals.refresh()
        return self._proposals.list_pending(stock_code)

    def reject_pending_proposal(self, stock_code: str, note: str = "") -> bool:
        """拒绝该股最新待确认建议（REJECTED；pos confirm 发现持仓已删除时调用）。"""
        self._proposals.refresh()
        target = self._proposals.get_pending(stock_code)
        if target is None:
            return False
        if note:
            target.note = note
        return bool(self._proposals.mark_rejected(target.proposal_id)
                    and self._proposals._save())

