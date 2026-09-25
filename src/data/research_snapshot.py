"""研究证据快照与时点（PIT）数据资格（plan/fusion F3，DESIGN ADR-F05）。

三件事：
1. **EvidenceRecord**：带完整时点语义的证据记录——published_at（来源公布）/available_at
   （系统可用）/fetched_at（本次抓取）三分立；period_end ≠ available；元/万元/亿元
   与累计/单季口径显式字段；来源 hash 与 revision 支持后发重述
2. **EvidenceSnapshot**：某证券某截止时点的证据集合——**strict 构建强制 PIT 资格**：
   available_at 缺失（不可信公布时间）或晚于 as_of 的证据一律不进快照（未来公告和
   后发重述不进入旧快照，DESIGN §5.1）；snapshot_id 内容 hash（同快照重放稳定）
3. **qualify**：按必需证据清单判定研究资格（COMPLETE/INCOMPLETE/CONFLICTED）——
   缺失是显式状态，不落 0 或 50；RAG 方法文本不当公司事实（默认排除 source_kind=rag）

数据接入（窄适配，不重建数据层）：record 构造函数消费现有 provider 的普通 dict；
live 抓取 helper 惰性 import（模块 import 与纯函数路径**零实时网络**，回测安全）。
真实可得性矩阵见 plan/fusion/DATA_COVERAGE.md（行情=天然PIT；业绩预告=baostock
pubDate 可PIT；公告=巨潮有官方日期可PIT/东财新闻仅live；详细三表=接口存在未接线；
分部营收=不可得走 USER_ASSERTED 人工通道）。
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.decision_contract import FactStatus, ResearchStatus

logger = logging.getLogger(__name__)

# 单位换算表（到"元"的倍率；DESIGN §5.1：口径显式，不做隐式混算）
_UNIT_TO_YUAN = {"元": 1.0, "万元": 1e4, "亿元": 1e8}

EvidenceValue = Union[float, int, str, bool, None]


class EvidenceRecord(BaseModel):
    """一条证据（DESIGN §5.1 EvidenceRecord 最小实现：只给决策需要的证据加字段）。"""

    model_config = ConfigDict(extra="allow")  # ADR-F09：未知字段保留

    evidence_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    source_kind: str = Field(
        description="来源类别：market/announcement/forecast/financial/rag/user_asserted")
    source_uri: str = Field(default="", description="来源定位（接口名+参数或文件路径）")
    source_document_hash: str = Field(default="", description="来源内容 sha256（有原文时）")
    security_id: str = Field(default="", description="证券ID（纯数字6位）")
    metric_or_claim: str = Field(description="指标/事实ID（如 close/revenue/forecast_netprofit/segment_revenue）")
    value: EvidenceValue = Field(default=None, description="原始值；MISSING 证据 value=None")
    unit: str = Field(default="", description="单位（元/万元/亿元/股/%/倍/None 文本）；空=无量纲")
    currency: str = Field(default="CNY")
    period_kind: Optional[Literal["cumulative", "single"]] = Field(
        default=None, description="财务口径：累计/单季——不区分口径的混算被禁止（G15）")
    period_end: Optional[str] = Field(default=None, description="报告期/事件归属期 YYYY-MM-DD")
    occurred_at: Optional[datetime] = Field(default=None, description="事件发生时点")
    published_at: Optional[datetime] = Field(default=None, description="来源公布时点（可信时才填）")
    available_at: Optional[datetime] = Field(default=None, description="系统可用时点——严格PIT资格的判据")
    fetched_at: Optional[datetime] = Field(default=None, description="本次实际抓取时点")
    source_version: str = Field(default="", description="数据源版本标识")
    revision_id: str = Field(default="", description="修订版本（后发重述为新 revision，不被去重吞掉）")
    quality_status: FactStatus = Field(default=FactStatus.OBSERVED)
    lineage_ids: list[str] = Field(default_factory=list, description="谱系引用（事件ID/上游证据ID）")

    @field_validator("security_id", mode="before")
    @classmethod
    def _norm_security_id(cls, v):
        if v is None:
            return ""
        code = str(v).strip()
        if code.isdigit() and 1 <= len(code) <= 6:
            return code.zfill(6)
        return code  # 行业ID等非证券代码原样保留

    @field_validator("published_at", "available_at", "occurred_at", "fetched_at")
    @classmethod
    def _aware_datetimes(cls, v):
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("证据时点必须带时区（严格PIT比较需要）")
        return v

    @property
    def pit_confident(self) -> bool:
        """是否有可信可用时点——严格 PIT 快照的唯一入场券（DESIGN §5.1）。"""
        return self.available_at is not None

    def content_fingerprint(self) -> str:
        """内容指纹（不含 evidence_id/fetched_at——重放与去重的稳定键）。"""
        d = self.model_dump()
        d.pop("evidence_id", None)
        d.pop("fetched_at", None)
        return hashlib.sha256(
            json.dumps(d, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()


class RequiredEvidence(BaseModel):
    """资格判定的一条必需证据要求。"""

    metric: str = Field(description="必需的指标/事实ID")
    max_age_days: Optional[float] = Field(
        default=None, description="时效上限（天，按 available_at 距 as_of）；None=不判过期")
    allow_source_kinds: Optional[list[str]] = Field(
        default=None, description="允许的来源类别；None=默认排除 rag（方法文本不当公司事实）")
    period_kind: Optional[Literal["cumulative", "single"]] = Field(
        default=None, description="要求的财务口径；None=不限定但返回时带出口径")


class EvidenceSnapshot(BaseModel):
    """某证券某截止时点的证据集合（不可变集合；重建产生新 snapshot_id）。"""

    model_config = ConfigDict(extra="allow")

    security_id: str
    as_of: datetime
    records: list[EvidenceRecord] = Field(default_factory=list)
    dropped_pit: int = Field(default=0, description="strict 构建时被拒的未来/不可信时点证据数")
    dropped_ids: list[str] = Field(default_factory=list, description="被拒证据的 id（可追溯）")
    snapshot_id: str = Field(default="", description="内容 hash（同输入重放必相同）")

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, v):
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("as_of 必须带时区")
        return v

    @classmethod
    def build(cls, security_id: str, as_of: datetime, records: list[EvidenceRecord],
              strict: bool = True) -> "EvidenceSnapshot":
        """构建快照。strict=True（回放/回测/历史决策）执行 PIT 资格闸门：
        available_at 缺失或晚于 as_of 的证据一律不进——未来公告与后发重述不会
        污染旧快照；被拒数量与 id 如实记录（不静默丢弃）。
        strict=False 仅用于 live 现场诊断视图（当前 as_of=now 时两者等价）。"""
        kept: list[EvidenceRecord] = []
        dropped, dropped_ids = 0, []
        for r in records:
            if strict and not r.pit_confident:
                dropped += 1
                dropped_ids.append(r.evidence_id)
                continue
            if strict and r.available_at > as_of:
                dropped += 1
                dropped_ids.append(r.evidence_id)
                continue
            kept.append(r)
        snap = cls(security_id=str(security_id), as_of=as_of, records=kept,
                   dropped_pit=dropped, dropped_ids=dropped_ids)
        snap.snapshot_id = snap._content_hash()
        return snap

    def _content_hash(self) -> str:
        """规范序列化 hash：按 (metric, available_at, 内容指纹) 排序——入序、
        evidence_id/fetched_at（每次抓取都变）都不影响 snapshot_id（重放稳定）。"""
        canon = sorted(
            ((r.metric_or_claim or "",
              r.available_at.isoformat() if r.available_at else "",
              r.content_fingerprint()) for r in self.records),
            key=lambda t: (t[0], t[1], t[2]))
        payload = json.dumps({"security_id": self.security_id, "as_of": self.as_of.isoformat(),
                              "records": canon}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ── 查询 ──

    @staticmethod
    def _avail_key(r: EvidenceRecord):
        """available_at 排序键：None 排首（strict=False live 视图含无时点记录不崩），
        同刻并列用 content_fingerprint 决胜（重放入序无关，P2-5）。"""
        return (r.available_at is None,
                r.available_at or datetime(1970, 1, 1, tzinfo=timezone.utc),
                r.content_fingerprint())

    def latest(self, metric: str, source_kinds: Optional[list[str]] = None) -> Optional[EvidenceRecord]:
        """该指标 available_at 最新的证据（可按来源类别过滤；None 时点排最后）。"""
        cands = [r for r in self.records if r.metric_or_claim == metric
                 and (source_kinds is None or r.source_kind in source_kinds)]
        if not cands:
            return None
        return max(cands, key=self._avail_key)

    def revisions_of(self, metric: str, period_end: Optional[str] = None) -> list[EvidenceRecord]:
        """该指标（可限报告期）的全部修订版本，按 available_at 升序——重述审计用。

        None 时点记录排最后（不参与严格回放语义，live 诊断可见）。"""
        rs = [r for r in self.records if r.metric_or_claim == metric
              and (period_end is None or r.period_end == period_end)]
        return sorted(rs, key=self._avail_key)

    # ── 资格判定 ──

    def qualify(self, required: list[RequiredEvidence]) -> tuple[ResearchStatus, list[str]]:
        """按必需清单判定研究资格。

        返回 (status, problems)；problems 是人话缺失/冲突/过期说明（空=COMPLETE）。
        缺失/过期/冲突是显式状态——绝不产生 0 或 50 的替代值（DESIGN §5.1）。

        冲突语义（F3 审查 P1-2 裁决）：**同一事实** = 同 (metric, period_end,
        period_kind)。同事实内 available_at 晚者**替代**早者（重述/更正是新版本，
        ADR-F06）——不判冲突；只有**同一可用时点并存的不同归一值**（典型=跨源
        同日矛盾观测）才 CONFLICTED。不同报告期是不同事实，永不互判冲突。
        """
        problems: list[str] = []
        conflicted = False
        for req in required:
            allowed = req.allow_source_kinds
            if allowed is None:
                # 默认排除 rag：RAG 提供方法依据与历史研究，不当当前公司事实（F3 验收）
                allowed = ["market", "announcement", "forecast", "financial",
                           "user_asserted", "derived"]
            cands = [r for r in self.records
                     if r.metric_or_claim == req.metric and r.source_kind in allowed
                     and r.quality_status not in (FactStatus.MISSING, FactStatus.NOT_APPLICABLE)
                     and (req.period_kind is None or r.period_kind == req.period_kind)]
            if not cands:
                kind_cn = {"cumulative": "累计", "single": "单季"}.get(req.period_kind, "")
                problems.append(f"缺失必需证据: {req.metric}"
                                + (f"（要求口径={kind_cn}）" if kind_cn else ""))
                continue
            no_time = [r for r in cands if r.available_at is None]
            timed = [r for r in cands if r.available_at is not None]
            if no_time and not timed:
                problems.append(f"无可信时点: {req.metric}（证据存在但公布时间不可信，不进严格判定）")
                continue
            if no_time:
                problems.append(f"可疑证据已忽略: {req.metric} 有 {len(no_time)} 条无可信时点记录")
            rec = max(timed, key=self._avail_key)
            if self._is_stale(rec, req):
                age = (self.as_of - rec.available_at).days
                problems.append(f"证据过期: {req.metric} 可用时点距今 {age} 天（上限 {req.max_age_days}）")
                continue
            # 冲突检测（重述语义）：同事实分组，组内 max available_at 并存不同归一值才冲突
            fresh = [r for r in timed if not self._is_stale(r, req)]
            if self._has_true_conflict(fresh):
                conflicted = True
                problems.append(f"证据冲突: {req.metric} 同一时点存在跨源不一致观测")
        if conflicted:
            return ResearchStatus.CONFLICTED, problems
        if problems:
            return ResearchStatus.INCOMPLETE, problems
        return ResearchStatus.COMPLETE, []

    @staticmethod
    def _normalized_value_key(r: EvidenceRecord):
        """归一值键：金额类单位换算到元再比（100万元 == 1000000元）；其余原值。"""
        v = r.value
        if isinstance(v, (int, float)) and not isinstance(v, bool) and r.unit in _UNIT_TO_YUAN:
            v = v * _UNIT_TO_YUAN[r.unit]
        return (v, r.period_kind)

    def _has_true_conflict(self, fresh: list[EvidenceRecord]) -> bool:
        """重述语义下的真冲突：同 (period_end, period_kind) 事实组内，available_at
        最大值处并存的不同归一值（跨源同日矛盾）。"""
        groups: dict[tuple, list[EvidenceRecord]] = {}
        for r in fresh:
            groups.setdefault((r.period_end, r.period_kind), []).append(r)
        for group in groups.values():
            group = sorted(group, key=self._avail_key)
            top = [r for r in group if group[-1].available_at == r.available_at]
            if len(top) >= 2 and len({self._normalized_value_key(r) for r in top}) > 1:
                return True
        return False

    def _is_stale(self, r: EvidenceRecord, req: RequiredEvidence) -> bool:
        if req.max_age_days is None:
            return False
        return (self.as_of - r.available_at) > timedelta(days=req.max_age_days)


# ──────────────── 单位与口径 helper ────────────────

def normalize_amount(value: float, unit: str, target: str = "元") -> float:
    """金额单位换算（元/万元/亿元→目标单位）；未知单位抛错——不猜口径（G15）。"""
    if unit not in _UNIT_TO_YUAN or target not in _UNIT_TO_YUAN:
        raise ValueError(f"不支持的金额单位换算: {unit!r} → {target!r}（支持 {'/'.join(_UNIT_TO_YUAN)}）")
    return value * _UNIT_TO_YUAN[unit] / _UNIT_TO_YUAN[target]


# ──────────────── 窄适配：现有 provider dict → EvidenceRecord ────────────────

def _now_aware() -> datetime:
    return datetime.now().astimezone()


def _hash_content(content) -> str:
    if isinstance(content, str):
        raw = content.encode("utf-8")
    else:
        raw = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def market_bar_record(security_id: str, bar: dict, source_uri: str = "baostock.query_history_k_data") -> EvidenceRecord:
    """行情 bar → 证据。行情天然 PIT：available_at = 该 bar 收盘时刻（bar 自带日期）。"""
    close = bar.get("close")
    date = bar.get("date") or bar.get("period_end")
    return EvidenceRecord(
        source_kind="market",
        source_uri=source_uri,
        source_document_hash=_hash_content(bar),
        security_id=security_id,
        metric_or_claim="close",
        value=float(close) if close is not None else None,
        unit="元",
        period_end=str(date) if date else None,
        occurred_at=None,
        # 收盘数据当日 15:00 后可得（保守取当日 23:59，避免时区边界假阴性）
        available_at=datetime.fromisoformat(f"{date}T23:59:00+08:00") if date else None,
        fetched_at=_now_aware(),
        source_version="daily_bar",
    )


def forecast_record(security_id: str, forecast: dict) -> EvidenceRecord:
    """业绩预告（baostock query_forecast_report）→ 证据。

    profitForcastExpPubDate 是官方公布日——PIT 可信（DATA_COVERAGE 矩阵：可PIT类）。
    """
    pub = forecast.get("published_at") or forecast.get("profitForcastExpPubDate")
    stat_date = forecast.get("period_end") or forecast.get("profitForcastExpStatDate")
    published = datetime.fromisoformat(f"{pub}T23:59:00+08:00") if pub else None  # 披露日尾（P2-2 保守：公告常盘后发布）
    return EvidenceRecord(
        source_kind="forecast",
        source_uri="baostock.query_forecast_report",
        source_document_hash=_hash_content(forecast),
        security_id=security_id,
        metric_or_claim="profit_forecast",
        value=forecast.get("value") or forecast.get("profitForcastAbstract") or "",
        unit=forecast.get("unit", ""),
        period_end=str(stat_date) if stat_date else None,
        published_at=published,
        available_at=published,  # 官方公布即可用
        fetched_at=_now_aware(),
        source_version="baostock_forecast",
        revision_id=str(forecast.get("revision_id") or ""),
    )


def announcement_record(security_id: str, ann: dict, official_date: bool) -> EvidenceRecord:
    """公告/新闻 → 证据。

    official_date=True（巨潮等官方披露源，带公告日期）→ published/available 可信、可进
    严格快照；official_date=False（东财新闻等，"最近N天"接口无可靠单条日期）→
    available_at 置 None：**live 视图可见，严格 PIT 快照必拒**（与 ISS-052 回测不填
    公告的既有纪律同构，机器化到证据层）。
    """
    pub = ann.get("date")
    published = datetime.fromisoformat(f"{pub}T23:59:00+08:00") if (official_date and pub) else None  # 披露日尾（P2-2 保守）
    content = ann.get("content") or ann.get("title") or ""
    return EvidenceRecord(
        source_kind="announcement",
        source_uri=ann.get("source", "announcement"),
        source_document_hash=_hash_content({"title": ann.get("title"), "content": content,
                                            "date": ann.get("date")}),
        security_id=security_id,
        metric_or_claim=ann.get("metric") or "announcement",
        value=ann.get("title") or "",
        unit="",
        period_end=None,
        published_at=published,
        available_at=published,
        fetched_at=_now_aware(),
        source_version="cninfo" if official_date else "news_em",
    )


def financial_record(security_id: str, fin: dict) -> EvidenceRecord:
    """财务项 → 证据。unit/period_kind（累计/单季）必须显式传入——缺口径不造假（G15）。"""
    if fin.get("period_kind") not in ("cumulative", "single"):
        raise ValueError(
            f"financial_record 必须显式给 period_kind（cumulative/single），收到 {fin.get('period_kind')!r}"
            "——累计/单季口径混算是 G15 登记的验收红线")
    pub = fin.get("published_at")
    published = datetime.fromisoformat(f"{pub}T23:59:00+08:00") if pub else None  # 披露日尾（P2-2 保守）
    return EvidenceRecord(
        source_kind="financial",
        source_uri=fin.get("source_uri", "financial"),
        source_document_hash=_hash_content(fin),
        security_id=security_id,
        metric_or_claim=fin["metric"],
        value=fin.get("value"),
        unit=fin.get("unit", "元"),
        period_kind=fin["period_kind"],
        period_end=fin.get("period_end"),
        published_at=published,
        # 无可信公布日（如只有报告期）时 available_at 置 None：不进严格快照——
        # "今天下载的重述财报不能回填过去"（DESIGN §5.1）
        available_at=published,
        fetched_at=_now_aware(),
        source_version=fin.get("source_version", "financial"),
        revision_id=str(fin.get("revision_id") or ""),
    )


def user_asserted_record(security_id: str, metric: str, value, *, unit: str = "",
                         asserted_by: str, note: str = "",
                         period_end: Optional[str] = None,
                         available_at: Optional[datetime] = None) -> EvidenceRecord:
    """人工证据通道（DESIGN §5.1：分部营收难自动取得时允许用户给来源的人工证据）。

    USER_ASSERTED 明确标记输入者/时间/适用期——不冒充 OBSERVED。
    available_at 缺省取当前时刻（回放测试可显式传历史时点）。
    """
    return EvidenceRecord(
        source_kind="user_asserted",
        source_uri=f"user://{asserted_by}",
        security_id=security_id,
        metric_or_claim=metric,
        value=value,
        unit=unit,
        period_end=period_end,
        available_at=available_at or _now_aware(),  # 录入即可用（用户对时点真实性负责）
        fetched_at=_now_aware(),
        quality_status=FactStatus.USER_ASSERTED,
        source_version="user_input",
    )


def rag_method_record(doc_uri: str, excerpt: str,
                      available_at: Optional[datetime] = None) -> EvidenceRecord:
    """RAG 检索块 → 证据（**方法依据**，不是当前公司事实——qualify 默认排除）。

    available_at 缺省取当前时刻（回放测试可显式传历史时点）。
    """
    return EvidenceRecord(
        source_kind="rag",
        source_uri=doc_uri,
        source_document_hash=_hash_content(excerpt),
        metric_or_claim="method_reference",
        value=excerpt[:200],
        unit="",
        available_at=available_at or _now_aware(),
        fetched_at=_now_aware(),
        source_version="rag",
    )


# ──────────────── live 抓取 helper（惰性 import——本模块纯函数路径零实时网络）────────────────

def fetch_announcement_evidence_live(security_id: str, official_only: bool = True) -> list[EvidenceRecord]:
    """live 公告证据（仅 official_only=True 的官方披露源可进严格快照）。

    惰性 import benzong.data_provider（复用既有巨潮/东财多源降级），不在模块层
    引入网络依赖——回测 import 本模块不会触发任何连接。
    """
    from src.core.benzong.data_provider import get_recent_announcements
    out = []
    for ann in get_recent_announcements(security_id) or []:
        src_name = str(ann.get("source") or "")
        is_official = "巨潮" in src_name or "cninfo" in src_name.lower()
        if official_only and not is_official:
            continue
        out.append(announcement_record(security_id, ann, official_date=is_official))
    return out


def fetch_forecast_evidence_live(security_id: str) -> list[EvidenceRecord]:
    """live 业绩预告证据（baostock query_forecast_report，带官方 pubDate）。

    复用 AKShareClient.get_latest_forecast（{type, abstract, pub_date, stat_date}），
    字段映射到 forecast_record 的通用键。
    """
    from src.data.akshare_client import AKShareClient
    fc = AKShareClient.get_latest_forecast(security_id)
    if not fc:
        return []
    return [forecast_record(security_id, {
        "published_at": fc.get("pub_date"),
        "period_end": fc.get("stat_date"),
        "value": fc.get("abstract") or fc.get("type") or "",
        "unit": "",
    })]
