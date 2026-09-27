"""账户确认提交协议（plan/fusion iteration3 J2，DELIVERY_PLAN「成交提交协议」）。

一个账户事件日志 = 已确认成交的唯一事实源；portfolio/proposals/today 读模型为
可重建投影。复用 JSONL，暂不引入数据库服务：

1. `confirm_fill(fill_id, expected_account_version, payload)` 在**同账户文件锁内**执行
   （Windows msvcrt.locking + 进程内 threading 键锁——跨进程/同进程争写都串行）
2. 相同 fill_id + 相同规范化 payload → 返回原回执（幂等）；同 ID 不同内容 →
   CONFLICT（不能当重复成功）
3. 校验数量/价格/费用/主计划引用/可卖批次（T+1）/账户版本；实际成交与旧建议
   不同允许记录真实结果并标偏离——不能为建议吻合篡改事实
4. 单个事务事件包含现金和份额变化；append + flush + fsync 成功后才发布新版本
   （账本内容 hash）；失败不提交半个事件（replay 端整事件原子生效同口径）
5. 崩溃恢复：截断尾行快照隔离留痕（G14+K0a/D2：进 isolated_events/PARTIAL，写侧
   拒绝向损坏尾部追加，损坏原件与行号/偏移保留供人工恢复）；按已持久化事件幂等重建投影
6. 期初导入 OPENING 事件记录用户确认的数量/成本/现金时点；仅百分比旧仓位
   无法推确切股数 → 保留 RATIO_ONLY 只补问阻塞字段
7. 无券商下单功能——本协议只记录**已发生**的成交

绝不写真实用户账户文件：测试必须传隔离路径。
"""

import hashlib
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.data.account_snapshot import (
    AccountEvent,
    AccountEventLog,
    AccountSnapshot,
    AccountLedgerCorruptError,
    EventType,
)

logger = logging.getLogger(__name__)

FILL_PROTOCOL_VERSION = "j2.fill_v1"
TRADE_DATE_LEN = 10

# 生产默认账本路径（测试必须传隔离路径——绝不读写真实 HOME）
DEFAULT_LEDGER_PATH = Path.home() / ".muyun" / "account_events.jsonl"

# 进程内路径键锁（同进程多实例串行；跨进程由文件锁兜底）
_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


class _AccountFileLock:
    """账户文件锁（Windows msvcrt.locking；非 Windows 回退 fcntl——本项目 win32）。

    锁文件 <path>.lock 独立于账本（不污染事件 JSONL）；进程内先抢键锁再抢文件锁，
    双层保证同进程/跨进程都串行。文件锁获取失败（>10s）抛 OSError → 回执 REJECTED。"""

    def __init__(self, path: Path):
        self.lock_path = Path(str(path) + ".lock")
        self._fh = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.lock_path, "a+b")
        try:
            import msvcrt
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
        except ImportError:  # 非 Windows：fcntl 全长锁
            import fcntl
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            try:
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except ImportError:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
        return False


class FillInput(BaseModel):
    """一笔已成交的规范化输入（confirm_fill payload——规范化后参与幂等指纹）。"""
    model_config = ConfigDict(extra="allow")

    security_id: str = Field(description="证券代码（6 位数字）")
    action: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, description="成交股数（正整数）")
    price: float = Field(gt=0, description="成交价（元）")
    fee: float = Field(default=0.0, ge=0.0, description="本笔费用（元，已含在 cash_delta 内）")
    trade_date: str = Field(description="成交日期 YYYY-MM-DD")
    plan_ref: str = Field(default="", description="关联持仓主计划 plan_id（空=未关联）")
    deviation_from_proposal: bool = Field(
        default=False, description="实际成交与旧建议不同（记录真实结果并标偏离——不为吻合篡改事实）")
    note: str = Field(default="", description="用户注记（偏离原因等）")

    def normalized(self) -> dict:
        """规范化 payload（幂等指纹：数值统一 float、日期原样、字符串去空白）。"""
        return {
            "security_id": str(self.security_id).strip(),
            "action": self.action,
            "quantity": int(self.quantity),
            "price": round(float(self.price), 6),
            "fee": round(float(self.fee), 6),
            "trade_date": str(self.trade_date).strip(),
            "plan_ref": str(self.plan_ref).strip(),
            "deviation_from_proposal": bool(self.deviation_from_proposal),
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(
            self.normalized(), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


class FillReceipt(BaseModel):
    """确认回执（K0a 回执三分：未写=REJECTED/STALE/CONFLICT；已持久化=ACCEPTED/
    DUPLICATE；写入状态待核对=PERSIST_UNKNOWN）。REJECTED/STALE 带 reason 不写任何状态。"""
    model_config = ConfigDict(extra="allow")

    status: Literal["ACCEPTED", "DUPLICATE", "CONFLICT", "REJECTED", "STALE",
                    "PERSIST_UNKNOWN"]
    ok: bool = Field(description="是否达到用户意图（ACCEPTED/DUPLICATE=True——重复提交算成功幂等；"
                                 "PERSIST_UNKNOWN=False：写入状态待核对，重跑同一确认可核对）")
    fill_id: str
    event_id: str = ""
    account_version: str = Field(default="", description="回执时点的账本内容版本")
    reason: str = Field(default="", description="REJECTED/CONFLICT/STALE 人话原因")
    cash_delta: float = Field(default=0.0, description="本笔入账的现金变动（ACCEPTED/DUPLICATE 时）")


class AccountService:
    """账户事务服务（确认提交协议 + 期初导入 + 快照读取）。

    - log_path：账户事件 JSONL（测试传隔离路径；绝不写真实 HOME）
    - plans_store：HorizonPlanStore 注入（主计划引用校验用；None=跳过计划校验并注记）
    """

    def __init__(self, log_path: Path, plans_store=None):
        self.log = AccountEventLog(Path(log_path))
        self.plans_store = plans_store

    # ── 读取 ──

    def snapshot(self) -> AccountSnapshot:
        """当前账户快照（重放——投影可幂等重建）。"""
        return self.log.replay()

    def account_version(self) -> str:
        return self.log.content_version()

    # ── 成交确认（事务）──

    def confirm_fill(self, fill_id: str, expected_account_version: str,
                     payload: FillInput) -> FillReceipt:
        """确认一笔已成交（唯一事务入口）。

        锁内流程：版本核对 → fill_id 幂等/冲突判定 → 业务校验（数量/价格/费用/
        主计划引用/可卖批次 T+1/持仓充足）→ 构造单事件（现金+份额一体）→
        append + flush + fsync → 发布新版本回执。任一校验不过 → REJECTED，
        不写半个事件。"""
        fid = str(fill_id or "").strip()
        if not fid:
            return FillReceipt(status="REJECTED", ok=False, fill_id="",
                               reason="fill_id 缺失")
        key = str(self.log.path.resolve())
        with _PATH_LOCKS_GUARD:
            lock = _PATH_LOCKS.setdefault(key, threading.Lock())
        with lock:  # 进程内串行
            try:
                with _AccountFileLock(self.log.path):  # 跨进程串行
                    return self._confirm_locked(fid, expected_account_version, payload)
            except OSError as e:
                logger.warning(f"账户文件锁获取失败（>10s 占用）: {e}")
                return FillReceipt(status="REJECTED", ok=False, fill_id=fid,
                                   account_version=self.account_version(),
                                   reason=f"账户写锁获取失败（其他进程占用）——稍后重试: {e}")

    def _confirm_locked(self, fid: str, expected_account_version: str,
                        payload: FillInput) -> FillReceipt:
        events, corrupt_segments, tail_corrupt = self.log._load_detailed()
        current_version = self.account_version()
        prior = next((e for e in events if e.event_id == f"fill_{fid}"), None)
        if prior is not None:
            prior_fp = _event_fingerprint(prior)
            if prior_fp == payload.fingerprint():
                return FillReceipt(status="DUPLICATE", ok=True, fill_id=fid,
                                   event_id=prior.event_id,
                                   account_version=current_version,
                                   cash_delta=prior.cash_delta)
            return FillReceipt(status="CONFLICT", ok=False, fill_id=fid,
                               event_id=prior.event_id,
                               account_version=current_version,
                               reason="同 fill_id 不同内容——不能当重复成功（疑似重复提交或改单），请人工核对")
        # 乐观锁：调用方基于旧版本发起——版本不符拒绝（不静默覆盖）
        if str(expected_account_version or "").strip() and \
                expected_account_version != current_version:
            return FillReceipt(status="STALE", ok=False, fill_id=fid,
                               account_version=current_version,
                               reason=f"账户版本已变化（期望 {expected_account_version}，"
                                      f"当前 {current_version}）——重读快照后重试")
        # ── K0a/D2 完整性先于写入：幂等/冲突/STALE 是只读判定可照常回答；
        #    新写入前必须过完整性门——截断 JSON 尾部会把本事件粘在损坏行上、
        #    重放整行丢失（D2 主反例），损坏原件不动、给可操作回执 ──
        if tail_corrupt:
            c = corrupt_segments[-1]
            return FillReceipt(status="REJECTED", ok=False, fill_id=fid,
                               account_version=current_version,
                               reason=f"账本尾部损坏（行 {c['line_no']}，偏移 {c['byte_offset']}，"
                                      f"sha256:{c['sha256']}）——本次未写入（原件未动）。"
                                      "请先备份账户事件账本，再人工修复或删除损坏尾段后重试")
        # ── 业务校验 ──
        err = self._validate(payload)
        if err:
            return FillReceipt(status="REJECTED", ok=False, fill_id=fid,
                               account_version=current_version, reason=err)
        snap = self.log.replay()
        if payload.action == "SELL":
            holding = next((h for h in snap.holdings
                            if h.security_id == payload.security_id), None)
            held = holding.quantity if holding else 0
            if held <= 0:
                return FillReceipt(status="REJECTED", ok=False, fill_id=fid,
                                   account_version=current_version,
                                   reason=f"无持仓可卖（{payload.security_id}）——先期初导入或确认买入")
            if payload.quantity > held:
                return FillReceipt(status="REJECTED", ok=False, fill_id=fid,
                                   account_version=current_version,
                                   reason=f"超卖拒绝：卖出 {payload.quantity} 股 > 持仓 {held} 股")
            # T+1：当日买入批次当日不可卖（acquired_at < trade_date 才可卖）
            sellable = sum(lot.quantity for h in snap.holdings if h.security_id == payload.security_id
                           for lot in h.lots if lot.acquired_at < payload.trade_date)
            if payload.quantity > sellable:
                return FillReceipt(status="REJECTED", ok=False, fill_id=fid,
                                   account_version=current_version,
                                   reason=f"可卖批次不足（T+1）：可卖 {sellable} 股 < 卖出 {payload.quantity} 股"
                                          "（当日买入次一交易日方可卖出）")
        if payload.action == "BUY" and payload.plan_ref and self.plans_store is not None:
            plan = self.plans_store.get(payload.security_id, "MID") or \
                self.plans_store.get(payload.security_id, "LONG")
            if plan is not None and plan.plan_id != payload.plan_ref:
                return FillReceipt(status="REJECTED", ok=False, fill_id=fid,
                                   account_version=current_version,
                                   reason=f"主计划引用不匹配（plan_ref={payload.plan_ref!r}，"
                                          f"当前计划 {plan.plan_id!r}）——核对后重试")
        # ── 单事务事件（现金+份额一体）──
        amount = round(payload.quantity * payload.price, 2)
        if payload.action == "BUY":
            cash_delta = -(amount + payload.fee)
        else:
            cash_delta = amount - payload.fee
        event = AccountEvent(
            event_id=f"fill_{fid}",
            event_type=EventType[payload.action],
            security_id=payload.security_id,
            trade_date=payload.trade_date,
            quantity=payload.quantity,
            price=payload.price,
            cash_delta=cash_delta,
            fee=payload.fee,
            lot_cost_price=payload.price if payload.action == "BUY" else None,
            related_fill_id=fid,
            plan_ref=payload.plan_ref,  # extra 字段随事件保留（投影/指纹往返用）
            deviation_from_proposal=payload.deviation_from_proposal,
            fill_note=payload.note,
        )
        try:
            appended = self.log.append(event)
        except OSError as e:
            # K0a：追加失败=零写入（半个事件都没有）——诚实回执，修复后重试干净入账
            logger.warning(f"账本追加失败（零写入）: {e}")
            return FillReceipt(status="REJECTED", ok=False, fill_id=fid,
                               account_version=current_version,
                               reason=f"账本写入失败（{e}）——本次未写入（未产生半个事件），"
                                      "请检查磁盘/权限后重试")
        except AccountLedgerCorruptError as e:
            # 完整性门前置正常不会到这里（兜底）：尾部在锁内被并发改坏
            logger.warning(f"账本完整性兜底拒绝追加: {e}")
            return FillReceipt(status="REJECTED", ok=False, fill_id=fid,
                               account_version=current_version,
                               reason=f"账本尾部损坏（{e}）——本次未写入（原件未动）。"
                                      "请先备份账户事件账本，再人工修复损坏尾段后重试")
        if not appended:
            # append 幂等拒绝（并发同 id 已入账）——按幂等成功回执
            return FillReceipt(status="DUPLICATE", ok=True, fill_id=fid,
                               event_id=event.event_id,
                               account_version=self.account_version(),
                               cash_delta=cash_delta)
        if not _fsync_ledger(self.log.path):
            # K0a：fsync 失败≠普通成功——写入状态待核对（数据可能已在盘上）；
            # 重跑同一确认会命中 event_id 幂等返回 DUPLICATE，可安全核对
            return FillReceipt(status="PERSIST_UNKNOWN", ok=False, fill_id=fid,
                               event_id=event.event_id,
                               account_version=self.account_version(),
                               cash_delta=cash_delta,
                               reason="事件已写入但磁盘落盘确认失败（fsync）——写入状态待核对。"
                                      "请重跑同一确认核对：已入账会返回 DUPLICATE（幂等不重复入账）")
        new_version = self.account_version()
        dev = "（偏离已标注——记录真实结果）" if payload.deviation_from_proposal else ""
        logger.info(f"成交确认入账 {payload.security_id} {payload.action} {payload.quantity}股"
                    f" @ {payload.price} cash_delta={cash_delta}{dev} 版本 {new_version}")
        return FillReceipt(status="ACCEPTED", ok=True, fill_id=fid,
                           event_id=event.event_id, account_version=new_version,
                           cash_delta=cash_delta)

    def _validate(self, p: FillInput) -> str:
        """业务校验（格式/范围；返回人话错误或空）。"""
        code = str(p.security_id or "").strip()
        if not (code.isdigit() and len(code) == 6):
            return f"证券代码非法: {p.security_id!r}（须 6 位数字）"
        if p.quantity <= 0 or p.quantity != int(p.quantity):
            return f"数量须为正整数，收到 {p.quantity}"
        if p.price <= 0:
            return f"价格须 > 0，收到 {p.price}"
        if p.fee < 0:
            return f"费用不能为负，收到 {p.fee}"
        td = str(p.trade_date or "").strip()
        try:
            datetime.strptime(td, "%Y-%m-%d")
        except ValueError:
            return f"成交日期非法: {p.trade_date!r}（须 YYYY-MM-DD）"
        if p.plan_ref and self.plans_store is None:
            logger.info("fill 带主计划引用但未注入计划库——引用校验跳过（注记）")
        return ""

    # ── 期初导入 ──

    def opening_import(self, *, opening_cash: Optional[float] = None,
                       lots: Optional[list[dict]] = None,
                       trade_date: Optional[str] = None) -> list[FillReceipt]:
        """期初导入（用户确认的数量/成本/可卖批次/现金时点）。

        - opening_cash：期初现金（元，≥0）→ OPENING 现金锚定事件
        - lots：[{security_id, quantity, cost_price, acquired_at}] → 每批一个
          OPENING 事件；**字段缺失/非法一律 ValueError 显式拒绝**——静默折算为 0
          会把现金谎报成「确定的 0」（None≠0 红线，J2 审查 P2）
        - 幂等：opening fill_id 由内容派生（同内容重复导入返回原回执）
        """
        td = trade_date or datetime.now().strftime("%Y-%m-%d")
        try:
            datetime.strptime(td, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"期初日期非法: {td!r}（须 YYYY-MM-DD）")
        if opening_cash is not None:
            oc = float(opening_cash)
            if oc < 0:
                raise ValueError(f"期初现金不能为负: {opening_cash!r}")
        validated_lots: list[dict] = []
        for lot in (lots or []):
            code = str(lot.get("security_id") or "").strip()
            if not (code.isdigit() and len(code) == 6):
                raise ValueError(f"期初批次 security_id 非法: {code!r}（须 6 位数字）")
            raw_qty = lot.get("quantity")
            try:
                qty = int(raw_qty)
            except (TypeError, ValueError):
                raise ValueError(
                    f"期初批次数量缺失/非法: {raw_qty!r}——缺字段必须显式补录，不得折算为 0")
            if qty <= 0:
                raise ValueError(f"期初批次数量须为正整数，收到 {raw_qty!r}")
            cost = lot.get("cost_price")
            if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost <= 0:
                raise ValueError(f"期初批次成本价缺失/非法: {cost!r}")
            acq = str(lot.get("acquired_at") or td)
            try:
                datetime.strptime(acq, "%Y-%m-%d")
            except ValueError:
                raise ValueError(f"期初批次日期非法: {acq!r}（须 YYYY-MM-DD——T+1 可卖判定依赖）")
            validated_lots.append({"security_id": code, "quantity": qty,
                                   "cost_price": float(cost), "acquired_at": acq})
        receipts: list[FillReceipt] = []
        if opening_cash is not None:
            fid = "opening_cash_" + hashlib.sha256(
                f"{float(opening_cash):.2f}".encode()).hexdigest()[:12]
            receipts.append(self._append_opening(fid, "", 0, None, float(opening_cash), td))
        for lot in validated_lots:
            fid = "opening_lot_" + hashlib.sha256(
                f"{lot['security_id']}|{lot['quantity']}|{lot['cost_price']}|"
                f"{lot['acquired_at']}".encode()).hexdigest()[:12]
            receipts.append(self._append_opening(fid, lot["security_id"], lot["quantity"],
                                                 lot["cost_price"], 0.0, td,
                                                 acquired_at=lot["acquired_at"]))
        return receipts

    def _append_opening(self, fid: str, security_id: str, quantity: int,
                        cost_price: Optional[float], cash_delta: float,
                        trade_date: str, acquired_at: str = "") -> FillReceipt:
        key = str(self.log.path.resolve())
        with _PATH_LOCKS_GUARD:
            lock = _PATH_LOCKS.setdefault(key, threading.Lock())
        try:
            with lock:
                with _AccountFileLock(self.log.path):
                    return self._append_opening_locked(fid, security_id, quantity,
                                                       cost_price, cash_delta,
                                                       trade_date, acquired_at)
        except OSError as e:
            logger.warning(f"期初导入写锁获取失败（零写入）: {e}")
            return FillReceipt(status="REJECTED", ok=False, fill_id=fid,
                               account_version=self.account_version(),
                               reason=f"账户写锁获取失败（其他进程占用）——稍后重试: {e}")

    def _append_opening_locked(self, fid: str, security_id: str, quantity: int,
                               cost_price: Optional[float], cash_delta: float,
                               trade_date: str, acquired_at: str = "") -> FillReceipt:
        events, corrupt_segments, tail_corrupt = self.log._load_detailed()
        prior = next((e for e in events if e.event_id == f"fill_{fid}"), None)
        if prior is not None:
            return FillReceipt(status="DUPLICATE", ok=True, fill_id=fid,
                               event_id=prior.event_id,
                               account_version=self.account_version(),
                               cash_delta=prior.cash_delta)
        if tail_corrupt:  # K0a/D2：完整性先于写入（期初导入同样冻结）
            c = corrupt_segments[-1]
            return FillReceipt(status="REJECTED", ok=False, fill_id=fid,
                               account_version=self.account_version(),
                               reason=f"账本尾部损坏（行 {c['line_no']}，偏移 {c['byte_offset']}，"
                                      f"sha256:{c['sha256']}）——本次未写入（原件未动）。"
                                      "请先备份账户事件账本，再人工修复或删除损坏尾段后重试")
        event = AccountEvent(
            event_id=f"fill_{fid}", event_type=EventType.OPENING,
            security_id=security_id,
            trade_date=acquired_at or trade_date,
            quantity=quantity, price=cost_price,
            cash_delta=cash_delta, fee=0.0,
            lot_cost_price=cost_price if security_id else None,
            related_fill_id=fid)
        self.log.append(event)
        if not _fsync_ledger(self.log.path):
            return FillReceipt(status="PERSIST_UNKNOWN", ok=False, fill_id=fid,
                               event_id=event.event_id,
                               account_version=self.account_version(),
                               cash_delta=cash_delta,
                               reason="事件已写入但磁盘落盘确认失败（fsync）——写入状态待核对。"
                                      "请重跑同一期初导入核对：已入账会返回 DUPLICATE（幂等）")
        return FillReceipt(status="ACCEPTED", ok=True, fill_id=fid,
                           event_id=event.event_id,
                           account_version=self.account_version(),
                           cash_delta=cash_delta)


def _fill_seq(related_fill_id: str) -> int:
    """fill_id 的数字后缀（"proposal#12" → 12；解析失败回退 0——J3/J5 审查 P1-1：
    字典序比较在 ≥10 笔时失灵（"9">"10"），序号必须按数字取最大）。"""
    rid = str(related_fill_id or "")
    if "#" not in rid:
        return 0
    tail = rid.rsplit("#", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return 0


def pick_reusable_fill_id(events: list, prefix: str, payload_fingerprint: str,
                          *, has_fill_fn=None) -> str:
    """崩溃恢复判定（J2 协议，纯函数——可单测）：同前缀已入账事件中，取**数字序号
    最大**者；若其指纹与本笔 payload 一致且投影未消费（has_fill_fn 为假）→ 判定
    「上次尝试未完成」，复用该 fill_id（账本 DUPLICATE + 投影补做）；否则返回
    `prefix{max_seq+1}`（新的真实成交）。"""
    prior = [e for e in events if str(getattr(e, "related_fill_id", "") or "").startswith(prefix)]
    if prior:
        last = max(prior, key=lambda e: _fill_seq(e.related_fill_id))
        consumed = has_fill_fn(last.related_fill_id) if has_fill_fn else False
        if not consumed and _event_fingerprint(last) == payload_fingerprint:
            return str(last.related_fill_id)
        return f"{prefix}{_fill_seq(last.related_fill_id) + 1}"
    return f"{prefix}1"


def _event_fingerprint(event: AccountEvent) -> str:
    """已入账事件 → 规范化指纹（与 FillInput.fingerprint 同算法——幂等判定用）。"""
    quantity = int(event.quantity or 0)
    price = float(event.price or 0.0)
    fee = float(event.fee or 0.0)
    action = getattr(event.event_type, "value", str(event.event_type))
    if action not in ("BUY", "SELL"):
        return ""
    return hashlib.sha256(json.dumps({
        "security_id": str(event.security_id or "").strip(),
        "action": action, "quantity": quantity,
        "price": round(price, 6), "fee": round(fee, 6),
        "trade_date": str(event.trade_date or "").strip(),
        "plan_ref": str((event.model_dump().get("plan_ref")) or "").strip(),
        "deviation_from_proposal": bool(event.model_dump().get("deviation_from_proposal")),
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _fsync_ledger(path: Path) -> bool:
    """追加后 fsync（崩溃点契约：持久化确认才发布确定成功——J2 协议第 4 条）。

    K0a：返回 False=落盘未确认（调用方必须给 PERSIST_UNKNOWN 回执，不得当普通成功）。"""
    try:
        with open(path, "ab") as f:
            f.flush()
            import os
            os.fsync(f.fileno())
        return True
    except OSError as e:
        logger.warning(f"账本 fsync 失败（数据可能未落盘，写入状态待核对——"
                       f"重跑同一确认可幂等核对）: {e}")
        return False
