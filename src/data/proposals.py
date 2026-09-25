"""分析建议与成交确认记录（plan/fusion F1，DESIGN ADR-F03）：分析不写成交。

背景：旧 `PortfolioManager.update_from_strategy_decision` 把策略建议（目标仓位、
生命周期、清仓）整包当真实持仓回写——"建议已卖被当成实际已卖"（用户根本没卖，
系统却记成仓位 0 + 进入冷却期，还把 EXIT 意图吞掉 5 天）。F1 拆成三层：

1. 观察量（PortfolioManager.record_analysis_observation）：研究观察与信号历史；
   不动已确认数量/成本/开仓日期/lifecycle/冷却
2. 建议存储（本模块 ProposalStore.record_proposal）：状态 PROPOSED，用户可见
   "这是建议，尚未记为成交"
3. 成交确认（PortfolioManager.confirm_fill + 本模块）：用户录入实际成交才改变
   持仓事实；重复 fill_id 幂等拒绝

建议状态机（DESIGN ADR-F03）：PROPOSED -> CONFIRMED / PARTIAL / REJECTED / EXPIRED。
部分成交保留剩余待办；同股新建议取代旧未决建议（旧 EXPIRED，不堆积）。

存储 ~/.muyun/proposals.json，原子写（M2 同款模式：先序列化 → mkstemp 临时文件 →
os.replace，失败保留旧文件）。数据层不 import CLI 层，故本地实现该 helper
（与 src/cli/session_state._atomic_write_json 语义一致，改动需两处同步）。
"""

import hashlib
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import NamedTuple, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _file_fingerprint(path: Path) -> Optional[str]:
    """文件内容指纹（sha256 hex）；文件不存在/不可读返回 None（M5 同款）。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None

# 默认建议存储路径（跨会话状态，与 benzong_cache 同级，不污染仓库）
DEFAULT_PROPOSALS_PATH = os.path.join(
    os.path.expanduser("~"), ".muyun", "proposals.json"
)

# 未决建议保留天数：超时自动 EXPIRED（价格/证据已过期，确认前应重新分析）
PROPOSAL_TTL_DAYS = 7

# 建议存储上限（防无限膨胀；fill 账本保留更多供幂等去重）
MAX_PROPOSALS = 200
MAX_FILLS = 500


class ProposalStatus(str, Enum):
    """建议状态（DESIGN ADR-F03）。"""

    PROPOSED = "PROPOSED"      # 待用户确认
    CONFIRMED = "CONFIRMED"    # 已按建议全额确认成交
    PARTIAL = "PARTIAL"        # 部分成交，剩余待办保留
    REJECTED = "REJECTED"      # 用户明确拒绝
    EXPIRED = "EXPIRED"        # 超时未确认 / 被新建议取代


class Proposal(BaseModel):
    """一条分析建议（用户可见"这是建议，尚未记为成交"）。"""

    proposal_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    stock_code: str
    stock_name: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    source: str = ""                       # 触发来源：l / la / chat / web / tui
    position_action: str                   # OPEN/ADD/REDUCE/CLOSE_ALL（HOLD/STAY_OUT 不入账）
    action_semantic: Optional[str] = None
    sell_path: Optional[str] = None
    target_ratio: Optional[float] = None   # 建议目标仓位 0-1；CLOSE_ALL=0.0
    current_ratio: float = 0.0             # 建议时的已确认仓位（快照）
    reason: str = ""                       # 人话一句话（终态语义，取策略层理由）
    cooldown_days: int = 0                 # 确认清仓后应启动的冷却天数（策略层建议值）
    status: ProposalStatus = ProposalStatus.PROPOSED
    decided_at: Optional[str] = None       # CONFIRMED/PARTIAL/REJECTED/EXPIRED 时间
    note: str = ""                         # 状态变化说明（如"被新建议取代"）


class FillRecord(BaseModel):
    """一条已确认成交（用户录入；fill_id 幂等键）。"""

    fill_id: str
    stock_code: str
    date: str                              # 成交日期 YYYY-MM-DD（用户可补录历史日期）
    action: str                            # BUY / SELL
    ratio_change: float                    # 仓位比例变化绝对值 0-1
    price: Optional[float] = None
    proposal_id: Optional[str] = None      # 关联建议（可无）
    note: str = ""
    recorded_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class FillResult(NamedTuple):
    """confirm_fill 结果：ok=持仓已按本次请求落到目标状态；duplicate=重复 fill_id 幂等跳过。"""

    ok: bool
    duplicate: bool = False
    message: str = ""

    def __bool__(self) -> bool:  # 调用方 bool 判断保持"成功才 True"直觉
        return self.ok


def _atomic_write_json(path: Path, data, indent: int = 2) -> bool:
    """原子写 JSON（M2 同款：序列化失败不碰磁盘 → mkstemp 唯一临时名 → os.replace）。

    与 src/cli/session_state._atomic_write_json 语义一致（数据层不 import CLI 层，
    故本地实现；两处改动需同步）。
    """
    try:
        text = json.dumps(data, ensure_ascii=False, indent=indent)
    except (TypeError, ValueError) as e:
        logger.warning(f"{path.name} 序列化失败（旧文件保留）: {e}")
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_name, path)
            return True
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
    except OSError as e:
        logger.warning(f"{path.name} 写入失败（旧文件保留）: {e}")
        return False


class ProposalStore:
    """建议/成交账本（~/.muyun/proposals.json）。

    单进程写入 + 原子替换（DESIGN ADR-F03 初版口径：跨进程写锁待正式需求出现
    再评估窄范围 SQLite）；读取时惰性过期（PROPOSED 超 TTL → EXPIRED）。
    """

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path or DEFAULT_PROPOSALS_PATH)
        self._proposals: list[Proposal] = []
        self._fills: list[FillRecord] = []
        # M5 同款内容指纹基线：_save 前比对磁盘，防陈旧实例整体覆盖较新账本
        # （TASKS F1"持久化需串行写锁+版本冲突拒绝"；单进程写入+冲突拒绝为
        # DESIGN ADR-F03 初版口径，跨进程写锁待正式需求再评估）
        self._loaded_fingerprint: Optional[str] = None
        self._corrupted = False
        self._load()

    # ── 加载/保存 ──

    def _load(self):
        """加载账本；坏文件整体拒绝（保持空账本 + 告警，不静默清空用户待办）。"""
        if not self.path.exists():
            self._loaded_fingerprint = None
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._proposals = [Proposal(**p) for p in raw.get("proposals", [])]
            self._fills = [FillRecord(**f) for f in raw.get("fills", [])]
            self._loaded_fingerprint = _file_fingerprint(self.path)
        except Exception as e:
            logger.warning(
                f"建议账本 {self.path} 解析失败（已忽略，本次不覆盖原文件）: {e}")
            self._proposals = []
            self._fills = []
            self._corrupted = True
            self._loaded_fingerprint = None
            return
        self._corrupted = False
        self._expire_stale()

    def refresh(self) -> None:
        """写前/读前重同步：磁盘内容与本实例基线不一致 → 重读（长持实例防陈旧快照）。"""
        disk_fp = _file_fingerprint(self.path)
        if disk_fp != self._loaded_fingerprint:
            self._proposals = []
            self._fills = []
            self._load()

    def _save(self) -> bool:
        if getattr(self, "_corrupted", False):
            logger.warning("建议账本此前解析失败，本次写入被拒绝（请人工检查文件）")
            return False
        # M5 同款冲突拒绝：磁盘在加载后被外部/其他实例改过 → 拒绝写入并重读磁盘版，
        # 绝不让长持实例（web/TUI）的陈旧快照整体覆盖较新建议
        disk_fp = _file_fingerprint(self.path)
        if disk_fp != self._loaded_fingerprint:
            self._proposals = []
            self._fills = []
            self._load()   # 内存回滚：与磁盘真值重新同步
            logger.warning(
                "建议账本已被其他会话修改，本次写入被拒绝（未覆盖外部改动），"
                "内存已恢复为磁盘最新版本——请重跑一次分析重新生成建议")
            return False
        data = {
            "proposals": [p.model_dump() for p in self._proposals[-MAX_PROPOSALS:]],
            "fills": [f.model_dump() for f in self._fills[-MAX_FILLS:]],
        }
        if not _atomic_write_json(self.path, data):
            return False
        # 自己的写入不算会话外修改：基线取刚写入的指纹
        self._loaded_fingerprint = _file_fingerprint(self.path)
        return True

    def _expire_stale(self):
        """惰性过期：PROPOSED 超过 TTL → EXPIRED（只改内存，下次写入落盘）。"""
        cutoff = (datetime.now() - timedelta(days=PROPOSAL_TTL_DAYS)).isoformat(timespec="seconds")
        changed = False
        for p in self._proposals:
            if p.status is ProposalStatus.PROPOSED and p.created_at < cutoff:
                p.status = ProposalStatus.EXPIRED
                p.decided_at = datetime.now().isoformat(timespec="seconds")
                p.note = f"超过{PROPOSAL_TTL_DAYS}天未确认，自动过期（价格/证据可能已变化，请重新分析）"
                changed = True

    # ── 建议 ──

    def add_proposal(self, prop: Proposal) -> Proposal:
        """登记新建议。同股同动作同日已有 PROPOSED → 原地更新（同日重复分析不堆积）；
        同股不同动作的旧 PROPOSED → EXPIRED（新建议取代）。"""
        now = datetime.now().isoformat(timespec="seconds")
        today = prop.created_at[:10]
        for old in self._proposals:
            if old.status is not ProposalStatus.PROPOSED or old.stock_code != prop.stock_code:
                continue
            if old.position_action == prop.position_action and old.created_at[:10] == today:
                # 同日重复分析：幂等合并，保留原 proposal_id/created_at
                old.target_ratio = prop.target_ratio
                old.current_ratio = prop.current_ratio
                old.reason = prop.reason or old.reason
                old.sell_path = prop.sell_path
                old.action_semantic = prop.action_semantic
                old.cooldown_days = prop.cooldown_days
                old.source = prop.source or old.source
                return old
            # 不同动作（如昨天建议 ADD 今天建议 CLOSE_ALL）：旧建议过期
            old.status = ProposalStatus.EXPIRED
            old.decided_at = now
            old.note = "被新建议取代"
        self._proposals.append(prop)
        return prop

    def get_pending(self, stock_code: str) -> Optional[Proposal]:
        """该股最新一条待办建议（PROPOSED 或 PARTIAL——部分成交的剩余待办仍可见）。"""
        pending = [p for p in self._proposals
                   if p.status in (ProposalStatus.PROPOSED, ProposalStatus.PARTIAL)
                   and p.stock_code == stock_code]
        return pending[-1] if pending else None

    def list_pending(self, stock_code: Optional[str] = None) -> list[Proposal]:
        if stock_code is None:
            return [p for p in self._proposals
                    if p.status in (ProposalStatus.PROPOSED, ProposalStatus.PARTIAL)]
        return [p for p in self._proposals
                if p.status in (ProposalStatus.PROPOSED, ProposalStatus.PARTIAL)
                and p.stock_code == stock_code]

    def mark_confirmed(self, proposal_id: str, partial: bool = False) -> Optional[Proposal]:
        for p in reversed(self._proposals):
            if p.proposal_id == proposal_id:
                if p.status is ProposalStatus.PROPOSED or p.status is ProposalStatus.PARTIAL:
                    p.status = ProposalStatus.PARTIAL if partial else ProposalStatus.CONFIRMED
                    p.decided_at = datetime.now().isoformat(timespec="seconds")
                return p
        return None

    def mark_rejected(self, proposal_id: str) -> Optional[Proposal]:
        for p in reversed(self._proposals):
            if p.proposal_id == proposal_id and p.status is ProposalStatus.PROPOSED:
                p.status = ProposalStatus.REJECTED
                p.decided_at = datetime.now().isoformat(timespec="seconds")
                return p
        return None

    # ── 成交 ──

    def record_fill(self, fill: FillRecord) -> bool:
        """记录成交；重复 fill_id 拒绝重复入账（返回 False，调用方幂等跳过）。"""
        if any(f.fill_id == fill.fill_id for f in self._fills):
            logger.info(f"fill_id {fill.fill_id} 已入账过，幂等跳过")
            return False
        self._fills.append(fill)
        return True

    def has_fill(self, fill_id: str) -> bool:
        return any(f.fill_id == fill_id for f in self._fills)
