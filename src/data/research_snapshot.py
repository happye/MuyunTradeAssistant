"""研究证据快照与时点（PIT）数据资格（plan/fusion F3 + iteration2 R1，DESIGN ADR-F05 + DATA_TRUST §1/§3）。

三件事：
1. **EvidenceRecord**：带完整时点语义的证据记录——published_at（来源公布）/available_at
   （系统可用）/fetched_at（本次抓取）三分立；period_end ≠ available；元/万元/亿元
   与累计/单季口径显式字段；来源 hash 与 revision 支持后发重述。R1 版本化扩展：
   knowledge_basis（历史版本可得性四级）/document_version_id/first_seen_at/
   version_available_at/timestamp_precision/provenance_evidence_ids +
   semantic_status（单位与指标定义核验，SUSPECT 隔离）+ raw_value/raw_unit +
   value_kind/period_basis（经济含义与时间基准，DATA_TRUST §3 五类）。
   **三个轴分离**：来源类别（source_kind）/内容核验（R2）/历史版本可得性
   （knowledge_basis）——不堆进一个 quality_status 字符串
2. **EvidenceSnapshot**：某证券某截止时点的证据集合——**strict 构建强制 PIT 资格**：
   统一经 assess_evidence_eligibility（DATA_TRUST §1 算法）——SUSPECT 隔离；
   AS_PUBLISHED_ARCHIVE 按 version_available_at 入选（原始历史文档可合法晚抓）；
   CONTEMPORANEOUS_CAPTURE 按 first_seen_at（只支持其后决策）；
   LATEST_WITH_PUBLICATION_DATE / UNKNOWN 不进 strict 历史（live 可用）；
   有效时点晚于 as_of 一律拒（未来公告/后发重述不进旧快照）；被拒数量、id 与
   原因如实记录。snapshot_id 内容 hash（同快照重放稳定）
3. **qualify**：按必需证据清单判定研究资格（COMPLETE/INCOMPLETE/CONFLICTED）——
   缺失是显式状态，不落 0 或 50；RAG 方法文本不当公司事实（默认排除 source_kind=rag）；
   SUSPECT（单位/口径异常）记录隔离并给具体原因

数据接入（窄适配，不重建数据层）：record 构造函数消费现有 provider 的普通 dict；
live 抓取 helper 惰性 import（模块 import 与纯函数路径**零实时网络**，回测安全）。
真实可得性矩阵见 plan/fusion/DATA_COVERAGE.md；历史版本资格分级见 DATA_TRUST.md。
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

    # ── R1 版本可得性与语义（DATA_TRUST §1/§3；缺省保守，旧记录迁移为 UNKNOWN 不自动补绿）──
    knowledge_basis: Literal[
        "AS_PUBLISHED_ARCHIVE", "CONTEMPORANEOUS_CAPTURE",
        "LATEST_WITH_PUBLICATION_DATE", "UNKNOWN"] = Field(
        default="UNKNOWN",
        description="历史版本可得性：原始档案（引用原始版本+公开证明）/当时捕获（first_seen 起前瞻）/"
                    "最新版带公布日（不进 strict 历史）/未知。与 source_kind/内容核验三轴分离")
    document_version_id: str = Field(
        default="", description="内容版本标识（内容 hash+来源版本身份）；空=版本身份未登记")
    first_seen_at: Optional[datetime] = Field(
        default=None, description="本系统首次见到该版本内容的时点（追加存储，不可回拨）")
    version_available_at: Optional[datetime] = Field(
        default=None, description="有来源证明的该版本公开时点（仅 AS_PUBLISHED_ARCHIVE 填；不是无条件复制发布日）")
    timestamp_precision: Literal["datetime", "day", "unknown"] = Field(
        default="unknown", description="时间精度；日级采用保守的披露尾时点")
    provenance_evidence_ids: list[str] = Field(
        default_factory=list, description="支持版本/时间认定的原始档案引用（research/raw 归档id等）")
    semantic_status: Literal["VERIFIED", "SUSPECT", "UNKNOWN"] = Field(
        default="UNKNOWN", description="单位与指标定义核验状态（非投资观点）；SUSPECT=异常隔离待核对")
    semantic_note: str = Field(default="", description="语义核验注记（SUSPECT 原因/人工核对结论）")
    raw_value: EvidenceValue = Field(default=None, description="源数据原貌（与 canonical value 并存）")
    raw_unit: str = Field(default="", description="源数据原始单位")
    value_kind: Literal["FLOW", "STOCK", "RATIO", "GROWTH", "PER_SHARE_TTM", "UNKNOWN"] = Field(
        default="UNKNOWN", description="经济含义类别（DATA_TRUST §3）——派生规则按此分派，不按 metric 名猜")
    period_basis: Literal["POINT_IN_TIME", "YTD", "SINGLE_QUARTER", "TTM",
                          "COMPARATIVE", "UNKNOWN"] = Field(
        default="UNKNOWN", description="时间基准；旧 period_kind=cumulative 不自动推断为本字段（迁移兼容）")
    underlying_period_basis: Optional[Literal[
        "POINT_IN_TIME", "YTD", "SINGLE_QUARTER", "TTM", "COMPARATIVE"]] = Field(
        default=None, description="比率分子分母 underlying 期间基准（RATIO 类登记）")
    metric_definition_version: str = Field(
        default="", description="指标定义版本（经济含义/量纲/时间基准/合并范围/派生公式）")

    @field_validator("security_id", mode="before")
    @classmethod
    def _norm_security_id(cls, v):
        if v is None:
            return ""
        code = str(v).strip()
        if code.isdigit() and 1 <= len(code) <= 6:
            return code.zfill(6)
        return code  # 行业ID等非证券代码原样保留

    @field_validator("published_at", "available_at", "occurred_at", "fetched_at",
                     "version_available_at", "first_seen_at")
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
        """内容指纹（不含 evidence_id/fetched_at/first_seen_at——重放与去重的稳定键；
        first_seen_at 是采集侧元数据，同一版本内容重抓会变——版本身份由
        document_version_id 承载，进指纹）。"""
        d = self.model_dump()
        d.pop("evidence_id", None)
        d.pop("fetched_at", None)
        d.pop("first_seen_at", None)
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


# ──────────────── 统一证据资格评估（R1，DATA_TRUST §1 算法）────────────────

EligibilityStatus = Literal["STRICT_ELIGIBLE", "LIVE_ONLY", "SUSPECT", "REJECTED"]

# strict 拒收/隔离原因代码（drop_reasons 键；qualify 据此给人话）
DROP_SUSPECT = "suspect_semantic"
DROP_NO_TIME = "no_trustworthy_time"
DROP_AFTER_ASOF = "after_as_of"
DROP_LATEST_ONLY = "latest_only_unverifiable"
DROP_KB_UNKNOWN = "knowledge_basis_unknown"
DROP_ARCHIVE_NO_TIME = "archive_missing_version_time"
DROP_CAPTURE_NO_SEEN = "contemporaneous_missing_first_seen"


class EvidenceEligibility(BaseModel):
    """一条证据的资格评估结果（EvidenceSnapshot.build 与研究资格共用）。"""

    status: EligibilityStatus
    reasons: list[str] = Field(default_factory=list)
    effective_available_at: Optional[datetime] = None


def assess_evidence_eligibility(record: EvidenceRecord, *, as_of: datetime,
                                strict: bool) -> EvidenceEligibility:
    """统一证据资格评估（DATA_TRUST §1 严格历史资格算法）。

    strict=True（回放/回测/历史决策）：
      1. 语义 SUSPECT → SUSPECT（隔离，不进 strict）
      2. knowledge_basis 定有效时点：
         AS_PUBLISHED_ARCHIVE → version_available_at（原始历史文档可合法晚抓；
             缺该时点 = 档案声明不完整 → REJECTED）
         CONTEMPORANEOUS_CAPTURE → first_seen_at（只支持其后决策，不能回填）
         LATEST_WITH_PUBLICATION_DATE / UNKNOWN → 不进 strict 历史（最新版带
             公布日≠当时发布的版本；UNKNOWN 无任何版本证明）
      3. 有效时点晚于 as_of → REJECTED（未来公告/后发重述不进旧快照）
    strict=False（live 现场诊断）：可使用「当前可得但无法证明历史版本」的证据；
      **未来时间检查与 SUSPECT 隔离不绕过**（live ≠ 降低内容真实性要求）。
    """
    reasons: list[str] = []
    if record.semantic_status == "SUSPECT":
        reasons.append(f"语义异常隔离: {record.semantic_note or '单位/口径待人工核对'}")
        return EvidenceEligibility(status="SUSPECT", reasons=reasons,
                                   effective_available_at=record.available_at)
    if not strict:
        # live：只用「当前可得」——未来时间仍拒（不绕过未来检查）
        if record.available_at is not None and record.available_at > as_of:
            reasons.append("available_at 晚于截止（未来证据，live 也不可用）")
            return EvidenceEligibility(status="REJECTED", reasons=reasons)
        return EvidenceEligibility(status="LIVE_ONLY", reasons=list(reasons),
                                   effective_available_at=record.available_at)

    kb = record.knowledge_basis
    effective: Optional[datetime]
    if kb == "AS_PUBLISHED_ARCHIVE":
        effective = record.version_available_at
        if effective is None:
            reasons.append("档案证据缺 version_available_at（版本公开时点未证明）")
            return EvidenceEligibility(status="REJECTED", reasons=reasons)
    elif kb == "CONTEMPORANEOUS_CAPTURE":
        effective = record.first_seen_at
        if effective is None:
            reasons.append("当时捕获证据缺 first_seen_at")
            return EvidenceEligibility(status="REJECTED", reasons=reasons)
    elif kb == "LATEST_WITH_PUBLICATION_DATE":
        reasons.append("latest-only（最新版带公布日，无法证明 as_of 时已发布该版本）")
        return EvidenceEligibility(status="LIVE_ONLY", reasons=reasons,
                                   effective_available_at=record.available_at)
    else:  # UNKNOWN
        reasons.append("版本可得性未知（无历史版本证明）")
        return EvidenceEligibility(status="LIVE_ONLY", reasons=reasons,
                                   effective_available_at=record.available_at)
    if record.available_at is None:
        # 档案/捕获证据连 available_at 都没有——时间轴不完整，不能排序
        reasons.append("证据无可信 available_at")
        return EvidenceEligibility(status="REJECTED", reasons=reasons)
    if effective > as_of:
        reasons.append(f"有效可用时点晚于截止 {as_of.isoformat()}")
        return EvidenceEligibility(status="REJECTED", reasons=reasons)
    return EvidenceEligibility(status="STRICT_ELIGIBLE", reasons=reasons,
                               effective_available_at=effective)


class EvidenceSnapshot(BaseModel):
    """某证券某截止时点的证据集合（不可变集合；重建产生新 snapshot_id）。"""

    model_config = ConfigDict(extra="allow")

    security_id: str
    as_of: datetime
    records: list[EvidenceRecord] = Field(default_factory=list)
    dropped_pit: int = Field(default=0, description="strict 构建时被拒的证据数（含隔离/资格不足；live 模式拒未来证据也计入）")
    dropped_ids: list[str] = Field(default_factory=list, description="被拒证据的 id（可追溯）")
    drop_reasons: dict[str, list[str]] = Field(
        default_factory=dict,
        description="被拒原因代码→去重 metric 名（no_trustworthy_time/after_as_of/"
                    "latest_only_unverifiable/knowledge_basis_unknown/suspect_semantic/"
                    "archive_missing_version_time/contemporaneous_missing_first_seen）")
    suspect_ids: list[str] = Field(
        default_factory=list,
        description="语义异常（SUSPECT）被隔离的证据 id——strict 不入、live 可见但标记")
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
        """构建快照。资格统一经 assess_evidence_eligibility（DATA_TRUST §1）：
        strict=True（回放/回测/历史决策）——SUSPECT 隔离、档案按 version_available_at、
        当时捕获按 first_seen_at、latest-only/未知版本资格不进 strict、有效时点晚于
        as_of 拒收；strict=False（live 现场诊断）——最新版/未知资格证据可用，但未来
        时间与 SUSPECT 隔离不绕过。被拒数量、id 与原因如实记录（不静默丢弃）。"""
        kept: list[EvidenceRecord] = []
        dropped, dropped_ids = 0, []
        drop_reasons: dict[str, list[str]] = {}
        suspect_ids: list[str] = []

        def _reason_code(r: EvidenceRecord, el: EvidenceEligibility) -> str:
            """拒收原因码——**按结构判定**（status/knowledge_basis/字段缺失），
            不做 reason 文本子串匹配（文本措辞漂移不改变分类，P2 加固）。"""
            if el.status == "SUSPECT":
                return DROP_SUSPECT
            if el.status == "LIVE_ONLY":
                return (DROP_LATEST_ONLY if r.knowledge_basis == "LATEST_WITH_PUBLICATION_DATE"
                        else DROP_KB_UNKNOWN)
            # REJECTED：按声明与字段缺失分类
            if r.knowledge_basis == "AS_PUBLISHED_ARCHIVE" and r.version_available_at is None:
                return DROP_ARCHIVE_NO_TIME
            if r.knowledge_basis == "CONTEMPORANEOUS_CAPTURE" and r.first_seen_at is None:
                return DROP_CAPTURE_NO_SEEN
            if r.available_at is None:
                return DROP_NO_TIME
            return DROP_AFTER_ASOF

        def _drop(r: EvidenceRecord, el: EvidenceEligibility) -> None:
            nonlocal dropped
            dropped += 1
            dropped_ids.append(r.evidence_id)
            code = _reason_code(r, el)
            if r.metric_or_claim and r.metric_or_claim not in drop_reasons.setdefault(code, []):
                drop_reasons[code].append(r.metric_or_claim)

        for r in records:
            el = assess_evidence_eligibility(r, as_of=as_of, strict=strict)
            if el.status == "STRICT_ELIGIBLE" or (not strict and el.status == "LIVE_ONLY"):
                kept.append(r)
            elif el.status == "SUSPECT":
                suspect_ids.append(r.evidence_id)
                if strict:
                    _drop(r, el)
                elif r.metric_or_claim:
                    # live 保留但记录隔离名单（诊断可见，派生侧隔离）
                    drop_reasons.setdefault(DROP_SUSPECT, [])
                    if r.metric_or_claim not in drop_reasons[DROP_SUSPECT]:
                        drop_reasons[DROP_SUSPECT].append(r.metric_or_claim)
            else:
                _drop(r, el)  # REJECTED（未来/资格不足）与 strict 下的 LIVE_ONLY
        snap = cls(security_id=str(security_id), as_of=as_of, records=kept,
                   dropped_pit=dropped, dropped_ids=dropped_ids,
                   drop_reasons=drop_reasons, suspect_ids=suspect_ids)
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
                     and r.semantic_status != "SUSPECT"  # 语义异常隔离（R1：只隔离本指标，不冻结全产品）
                     and (req.period_kind is None or r.period_kind == req.period_kind)]
            if not cands:
                # 区分「确实没有」与「有证据但资格不足/被隔离」——用户看到具体原因
                if req.metric in self.drop_reasons.get(DROP_LATEST_ONLY, []):
                    problems.append(
                        f"版本不可追溯: {req.metric}（latest-only 财务证据无法证明 as_of 时点"
                        "已发布该版本——不进严格判定；待原始历史文档或版本分级后恢复）")
                    continue
                if req.metric in self.drop_reasons.get(DROP_KB_UNKNOWN, []):
                    problems.append(
                        f"版本资格未知: {req.metric}（证据无历史版本可得性分级——不进严格判定）")
                    continue
                if req.metric in self.drop_reasons.get(DROP_SUSPECT, []):
                    problems.append(
                        f"证据可疑: {req.metric}（单位/口径异常隔离中，待人工核对——"
                        "不进资格判定，其余字段不受累）")
                    continue
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


# ──────────────── 派生守卫与语义筛查（R1，DATA_TRUST §2/§3）────────────────

_VALID_QUARTER_ENDS = {(3, 31), (6, 30), (9, 30), (12, 31)}


def _quarter_index(period_end: Optional[str]) -> Optional[tuple[int, int]]:
    """'2024-03-31' → (2024, 1)；非季末/不可解析 → None。"""
    if not period_end:
        return None
    try:
        d = datetime.strptime(str(period_end), "%Y-%m-%d").date()
    except ValueError:
        return None
    if (d.month, d.day) not in _VALID_QUARTER_ENDS:
        return None
    return (d.year, (d.month - 1) // 3 + 1)


def _derivation_lineage_guard(records: list[EvidenceRecord], op: str) -> None:
    """派生输入血统守卫（R1，DATA_TRUST §3 + 审查 P1-3）：可疑/跨主体/跨修订拒绝。

    - semantic_status=SUSPECT 的输入拒绝（隔离标记不得经派生洗白）
    - security_id 必须一致（跨公司相减无意义且危险）
    - revision_id 必须一致（首发与后发重述相减被禁——DATA_TRUST §3「不同修订/
      合并范围相减」；两者皆空视为同源 latest-only 血统一致）
    - knowledge_basis 必须一致（档案与捕获血统不混派生）
    """
    if any(r.semantic_status == "SUSPECT" for r in records):
        raise ValueError(
            f"{op}: 输入含 SUSPECT 隔离记录——可疑记录不参与派生（先人工核对原始财报）")
    sids = {r.security_id for r in records}
    if len(sids) > 1:
        raise ValueError(f"{op}: 输入跨主体 {sorted(sids)}——跨公司差分拒绝")
    revs = {r.revision_id for r in records}
    if len(revs) > 1:
        raise ValueError(
            f"{op}: 输入修订版本不一致 {sorted(revs)}——首发与后发重述相减被禁（DATA_TRUST §3）")
    kbs = {r.knowledge_basis for r in records}
    if len(kbs) > 1:
        raise ValueError(f"{op}: 输入版本资格血统不一致 {sorted(kbs)}——档案/捕获不混派生")


def _derive_version_provenance(records: list[EvidenceRecord]) -> tuple[Optional[datetime], Optional[datetime]]:
    """派生记录的版本资格继承（审查 P1-2：不能让「资格随输入继承」沦为空承诺）：

    - AS_PUBLISHED_ARCHIVE：version_available_at = max(inputs)——派生值在两份输入都
      公开时即可得（可证明、不回拨）；缺失项置 None（诚实不可入选，不自动补绿）
    - CONTEMPORANEOUS_CAPTURE：first_seen_at = max(inputs)——派生内容在全部输入被
      系统见到后才存在（前瞻起点，不回填）
    返回 (version_available_at, first_seen_at)；派生时刻的 first_seen 兜底 now 由调用方定。
    """
    kb = records[0].knowledge_basis
    if kb == "AS_PUBLISHED_ARCHIVE":
        vaas = [r.version_available_at for r in records]
        return (max(vaas) if all(v is not None for v in vaas) else None, None)
    if kb == "CONTEMPORANEOUS_CAPTURE":
        seens = [r.first_seen_at for r in records]
        return (None, max(seens) if all(s is not None for s in seens) else None)
    return (None, None)  # LATEST/UNKNOWN 血统：无版本资格可继承（诚实留空）


def derive_single_quarter_from_cumulative(prev: EvidenceRecord,
                                          curr: EvidenceRecord) -> EvidenceRecord:
    """同年相邻累计 FLOW 差分出单季（G15 允许的唯一财务派生）。

    拒绝（ValueError，不猜口径）：
    - 任一记录 value_kind 不是 FLOW（比率/存量/增长率/TTM 禁止累计差分——
      DATA_TRUST §3「两个累计比率相减得到单季比率」被拒）
    - 非同年 / 季度不相邻（跨年 Q4→Q1 直接相减是跨报告期混算）
    - metric/unit/period_basis/period_kind 不一致；任一值缺失
    - 血统守卫拒绝：SUSPECT 输入 / 跨主体 / 跨修订（首发×重述相减）/ 血统混派生
      （_derivation_lineage_guard）
    - 旧 period_kind=cumulative 但 period_basis=UNKNOWN（语义未登记的记录不参与派生）

    版本资格继承（审查 P1-2）：档案输入 → version_available_at=max(inputs)；
    捕获输入 → first_seen_at=max(inputs)；派生内容本身首次存在于本时刻。
    """
    _derivation_lineage_guard([prev, curr], "累计差分")
    for r in (prev, curr):
        if r.value_kind != "FLOW":
            raise ValueError(
                f"累计差分只允许 FLOW 类字段，收到 value_kind={r.value_kind!r}"
                f"（metric={r.metric_or_claim}）——比率/存量/增长率/TTM 禁止差分（DATA_TRUST §3）")
        if r.period_basis != "YTD":
            raise ValueError(
                f"差分要求 period_basis=YTD（时间基准已登记），收到 {r.period_basis!r}"
                "——旧 ambiguous cumulative 不自动推断（迁移兼容）")
        if r.value is None:
            raise ValueError(f"缺失值不参与派生（metric={r.metric_or_claim} period_end={r.period_end}）")
    if prev.metric_or_claim != curr.metric_or_claim:
        raise ValueError("差分双方 metric 不一致")
    if prev.unit != curr.unit:
        raise ValueError(f"差分双方单位不一致: {prev.unit!r} vs {curr.unit!r}（不猜换算）")
    qp, qc = _quarter_index(prev.period_end), _quarter_index(curr.period_end)
    if qp is None or qc is None or qp[0] != qc[0] or qc[1] != qp[1] + 1:
        raise ValueError(
            f"差分要求同年相邻季度，收到 {prev.period_end} → {curr.period_end}"
            "（跨年/跨期直接相减被拒——DATA_TRUST §3）")
    vaa, seen_inherited = _derive_version_provenance([prev, curr])
    single = float(curr.value) - float(prev.value)
    derived_hash = _hash_content({"prev": prev.source_document_hash,
                                  "curr": curr.source_document_hash})
    return EvidenceRecord(
        source_kind="financial",
        source_uri=f"derived:quarter_diff({prev.source_uri}|{curr.source_uri})",
        source_document_hash=derived_hash,
        security_id=curr.security_id,
        metric_or_claim=curr.metric_or_claim,
        value=single,
        unit=curr.unit,
        period_kind="single",
        period_end=curr.period_end,
        published_at=curr.published_at,
        available_at=curr.available_at,
        fetched_at=_now_aware(),
        source_version=f"derived_from:{curr.source_version}",
        revision_id=curr.revision_id,
        lineage_ids=[prev.evidence_id, curr.evidence_id],
        knowledge_basis=curr.knowledge_basis,
        version_available_at=vaa,
        first_seen_at=seen_inherited if seen_inherited is not None else _now_aware(),
        document_version_id=derived_hash,
        timestamp_precision=curr.timestamp_precision,
        value_kind="FLOW",
        period_basis="SINGLE_QUARTER",
        metric_definition_version=curr.metric_definition_version,
        semantic_note=(
            f"由相邻累计差分派生（{prev.period_end}→{curr.period_end}）；"
            + ("版本资格继承输入档案（version_available_at=两输入公开时点较晚者）"
               if vaa is not None else
               "first_seen=两输入被系统见到的较晚者（前瞻起点）"
               if seen_inherited is not None else
               "输入血统无版本资格（LATEST/UNKNOWN）——派生值同样不进 strict 历史")),
    )


def derive_ttm_from_single_quarters(records: list[EvidenceRecord]) -> EvidenceRecord:
    """连续 4 个单季 FLOW 拼 TTM；输入非单季（含把 YTD/TTM 混入）→ ValueError。

    TTM 与 YTD 不混（DATA_TRUST §3）：本函数只接受 period_basis=SINGLE_QUARTER 的
    FLOW 记录；季度连续（按 (year, quarter) 排序后逐季 +1）；单位/metric 一致；
    血统守卫拒绝（SUSPECT/跨主体/跨修订/血统混派生）；period_end 非法显式
    ValueError（不是 TypeError——审查 P2 加固）。
    """
    if len({r.metric_or_claim for r in records}) != 1:
        raise ValueError("TTM 拼接要求同一 metric")
    if len({r.unit for r in records}) != 1:
        raise ValueError("TTM 拼接要求同一单位")
    _derivation_lineage_guard(records, "TTM 拼接")
    for r in records:
        if r.value_kind != "FLOW" or r.period_basis != "SINGLE_QUARTER":
            raise ValueError(
                f"TTM 拼接只接受单季 FLOW 记录，收到 value_kind={r.value_kind!r} "
                f"period_basis={r.period_basis!r}（metric={r.metric_or_claim}）"
                "——把 TTM EPS 当 YTD 利润差分、或拿 YTD 拼 annual 均被拒")
        if r.value is None:
            raise ValueError(f"缺失值不参与 TTM 拼接（period_end={r.period_end}）")
        if _quarter_index(r.period_end) is None:
            raise ValueError(
                f"TTM 输入 period_end 非法/非季末: {r.period_end!r}——不猜报告期")
    if len(records) != 4:
        raise ValueError(f"TTM 需要 4 个连续单季，收到 {len(records)} 条")
    qs = sorted((_quarter_index(r.period_end) for r in records), key=lambda t: (t[0], t[1]))
    for a, b in zip(qs, qs[1:]):
        y, q = a
        ny, nq = (y + 1, 1) if q == 4 else (y, q + 1)
        if (b[0], b[1]) != (ny, nq):
            raise ValueError(f"TTM 单季不连续: {a} → {b}")
    vaa, seen_inherited = _derive_version_provenance(records)
    ordered = sorted(records, key=lambda x: _quarter_index(x.period_end))
    total = sum(float(r.value) for r in records)
    newest = max(records, key=lambda r: (_quarter_index(r.period_end),
                                         r.available_at or datetime.min.replace(tzinfo=timezone.utc)))
    derived_hash = _hash_content([r.source_document_hash for r in ordered])
    return EvidenceRecord(
        source_kind="financial",
        source_uri=f"derived:ttm({'+'.join(r.source_uri for r in ordered)})",
        source_document_hash=derived_hash,
        security_id=newest.security_id,
        metric_or_claim=newest.metric_or_claim,
        value=total,
        unit=newest.unit,
        period_kind="cumulative",
        period_end=newest.period_end,
        published_at=newest.published_at,
        available_at=newest.available_at,
        fetched_at=_now_aware(),
        source_version=f"derived_ttm:{newest.source_version}",
        lineage_ids=[r.evidence_id for r in ordered],
        knowledge_basis=newest.knowledge_basis,
        version_available_at=vaa,
        first_seen_at=seen_inherited if seen_inherited is not None else _now_aware(),
        document_version_id=derived_hash,
        timestamp_precision=newest.timestamp_precision,
        value_kind="FLOW",
        period_basis="TTM",
        metric_definition_version=newest.metric_definition_version,
        semantic_note=(
            "由 4 个连续单季拼接的 TTM；"
            + ("版本资格继承输入档案（version_available_at=四输入公开时点最晚者）"
               if vaa is not None else
               "first_seen=四输入被系统见到的最晚者（前瞻起点）"
               if seen_inherited is not None else
               "输入血统无版本资格（LATEST/UNKNOWN）——派生值同样不进 strict 历史")),
    )


def screen_semantic_anomalies(records: list[EvidenceRecord], *,
                              jump_factor: float = 10.0) -> list[EvidenceRecord]:
    """同公司同指标相邻期量级跳变筛查 → 较新记录标 SUSPECT（隔离标记，**不纠偏不换算**）。

    ISS-114（DATA_TRUST §2）：万科 liabilityToAsset 跨期量级漂移——>10 倍只作本事故
    的异常筛查初值，非正确性证明；**不自动 ×100**，真值以原始财报交叉核对为准。
    范围（v1，按事故类别限定误报）：
    - 筛查 RATIO / STOCK / PER_SHARE_TTM（资产负债率/股数/每股类——ISS-114 同类）
    - GROWTH 不筛查（同比增长率合法跨零）；FLOW 不筛查（累计流量相邻期天然波动大，
      10x 误报率高——登记为待专门分支，不默默放行）
    - 零值/缺失不做分母（跳变检测跳过——零值语义单独分支，本版仅登记不隔离）
    - 符号翻转（两值非零且异号）视为异常一并 SUSPECT
    同 (security_id, metric, value_kind)，period_end 为相邻季度才比较；其余组合不动。
    """
    by_key: dict[tuple, list[EvidenceRecord]] = {}
    for r in records:
        if r.value_kind not in ("RATIO", "STOCK", "PER_SHARE_TTM"):
            continue
        qi = _quarter_index(r.period_end)
        if qi is None or r.value is None or r.value == 0:
            continue
        by_key.setdefault((r.security_id, r.metric_or_claim, r.value_kind), []).append(r)
    suspect_ids: set[str] = set()
    for key, rs in by_key.items():
        rs_sorted = sorted(rs, key=lambda r: (_quarter_index(r.period_end), r.available_at or datetime.min.replace(tzinfo=timezone.utc)))
        for a, b in zip(rs_sorted, rs_sorted[1:]):
            qa, qb = _quarter_index(a.period_end), _quarter_index(b.period_end)
            if (qb[0], qb[1]) != ((qa[0] + 1, 1) if qa[1] == 4 else (qa[0], qa[1] + 1)):
                continue  # 非相邻期不比（跳变无意义）
            va, vb = float(a.value), float(b.value)
            if va == 0 or vb == 0:
                continue  # 零值不分母（单独分支，不隔离）
            if va * vb < 0 or abs(vb) > abs(va) * jump_factor or abs(va) > abs(vb) * jump_factor:
                suspect_ids.add(b.evidence_id)  # 跳变方（较新期）隔离
    out: list[EvidenceRecord] = []
    for r in records:
        if r.evidence_id in suspect_ids:
            out.append(r.model_copy(update={
                "semantic_status": "SUSPECT",
                "semantic_note": (
                    f"相邻期量级跳变筛查命中（>{jump_factor:g}x 或符号翻转，"
                    f"{r.period_end}）——单位/口径可疑，隔离待人工核对原始财报；"
                    "不自动纠偏（ISS-114 纪律）"),
            }))
        else:
            out.append(r)
    return out


# ──────────────── 窄适配：现有 provider dict → EvidenceRecord ────────────────

def _now_aware() -> datetime:
    return datetime.now().astimezone()


def _hash_content(content) -> str:
    if isinstance(content, str):
        raw = content.encode("utf-8")
    else:
        raw = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def market_bar_record(security_id: str, bar: dict, source_uri: str = "baostock.query_history_k_data",
                      adjust: Literal["none", "forward", "unknown"] = "unknown") -> EvidenceRecord:
    """行情 bar → 证据。

    复权口径**显式声明**（R1：bar 日期≠天然 PIT——前复权历史会受以后分红拆股影响，
    DATA_TRUST §5）：
    - adjust="none"（不复权）：交易所当日原始发布 → AS_PUBLISHED_ARCHIVE，
      version_available_at = bar 收盘时刻（保守取当日 23:59 避免时区边界假阴性）
    - adjust="forward"（前复权）：序列值随后续公司行动变化，历史版本不可证明 →
      UNKNOWN（live 可用；严格历史回放须以未复权价+公司行动台账重放，R6 承接）
    - adjust="unknown"：按未知处理（不进 strict——调用方须显式声明口径）
    """
    close = bar.get("close")
    date = bar.get("date") or bar.get("period_end")
    available = datetime.fromisoformat(f"{date}T23:59:00+08:00") if date else None
    if adjust == "none":
        kb, vaa = "AS_PUBLISHED_ARCHIVE", available
    else:
        kb, vaa = "UNKNOWN", None
    note = "" if adjust == "none" else (
        "前复权序列值随后续公司行动变化——不进严格历史快照；严格回放用未复权价+公司行动台账"
        if adjust == "forward" else "复权口径未声明——不进严格历史快照")
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
        available_at=available,
        fetched_at=_now_aware(),
        source_version="daily_bar",
        knowledge_basis=kb,
        version_available_at=vaa,
        first_seen_at=_now_aware(),  # 系统本次见到该 bar 内容的时刻——不可回拨到 bar 日（审查 P2）
        document_version_id=_hash_content(bar),
        timestamp_precision="day",
        semantic_note=note,
    )


def forecast_record(security_id: str, forecast: dict) -> EvidenceRecord:
    """业绩预告（baostock query_forecast_report）→ 证据。

    profitForcastExpPubDate 是官方公布日——PIT 可信（DATA_COVERAGE 矩阵：可PIT类）。
    """
    pub = forecast.get("published_at") or forecast.get("profitForcastExpPubDate")
    stat_date = forecast.get("period_end") or forecast.get("profitForcastExpStatDate")
    published = datetime.fromisoformat(f"{pub}T23:59:00+08:00") if pub else None  # 披露日尾（P2-2 保守：公告常盘后发布）
    h = _hash_content(forecast)
    return EvidenceRecord(
        source_kind="forecast",
        source_uri="baostock.query_forecast_report",
        source_document_hash=h,
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
        # 预告行带官方 pubDate（行本身即当时发布的版本，DATA_COVERAGE 可PIT 类）
        knowledge_basis="AS_PUBLISHED_ARCHIVE" if published else "UNKNOWN",
        version_available_at=published,
        first_seen_at=_now_aware(),
        document_version_id=h,
        timestamp_precision="day" if published else "unknown",
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
    h = _hash_content({"title": ann.get("title"), "content": content,
                       "date": ann.get("date")})
    return EvidenceRecord(
        source_kind="announcement",
        source_uri=ann.get("source", "announcement"),
        source_document_hash=h,
        security_id=security_id,
        metric_or_claim=ann.get("metric") or "announcement",
        value=ann.get("title") or "",
        unit="",
        period_end=None,
        published_at=published,
        available_at=published,
        fetched_at=_now_aware(),
        source_version="cninfo" if official_date else "news_em",
        # 官方披露源的公告=当时发布的原始文档（可档案核验）；新闻无可靠单条日期
        knowledge_basis=("AS_PUBLISHED_ARCHIVE" if (official_date and published)
                         else "UNKNOWN"),
        version_available_at=published if official_date else None,
        first_seen_at=_now_aware(),
        document_version_id=h,
        timestamp_precision="day" if published else "unknown",
        semantic_note=("" if published else "无可靠单条日期（最近N天接口）——不进严格快照"),
    )


def financial_record(security_id: str, fin: dict, *,
                     knowledge_basis: Optional[str] = None,
                     version_available_at: Optional[datetime] = None,
                     first_seen_at: Optional[datetime] = None,
                     provenance_evidence_ids: Optional[list[str]] = None) -> EvidenceRecord:
    """财务项 → 证据。unit/period_kind（累计/单季）必须显式传入——缺口径不造假（G15）。

    历史版本资格（R1，DATA_TRUST §1）：
    - 缺省 knowledge_basis="LATEST_WITH_PUBLICATION_DATE"——本构造器消费的是
      latest-only 接口响应（financial_data.py 已登记「只返回最新一版」），带 pubDate
      不等于「as_of 时已发布该版本」，不进 strict 历史快照（live 可用）
    - 原始历史文档（年报 PDF/公告原文等）路径：显式传
      knowledge_basis="AS_PUBLISHED_ARCHIVE" + version_available_at=真实公开时点
      （+ 原始内容经 ResearchStore.archive_raw 归档，provenance 指向归档id）
      ——今天下载的可验证原始历史文档按真实公开日进历史快照（R1 验收1）
    - value_kind/period_basis/metric_definition_version 从 fin dict 显式传入
      （financial_data 字段登记表）；缺省 UNKNOWN——旧 cumulative 不自动推断
    """
    if fin.get("period_kind") not in ("cumulative", "single"):
        raise ValueError(
            f"financial_record 必须显式给 period_kind（cumulative/single），收到 {fin.get('period_kind')!r}"
            "——累计/单季口径混算是 G15 登记的验收红线")
    pub = fin.get("published_at")
    published = datetime.fromisoformat(f"{pub}T23:59:00+08:00") if pub else None  # 披露日尾（P2-2 保守）
    kb = knowledge_basis or fin.get("knowledge_basis") or "LATEST_WITH_PUBLICATION_DATE"
    if kb == "AS_PUBLISHED_ARCHIVE" and version_available_at is None:
        raise ValueError(
            "AS_PUBLISHED_ARCHIVE 必须显式给 version_available_at（该版本公开时点的来源证明）"
            "——档案资格不能只凭 pubDate 自称")
    if kb == "CONTEMPORANEOUS_CAPTURE" and first_seen_at is None:
        raise ValueError(
            "CONTEMPORANEOUS_CAPTURE 必须显式给 first_seen_at（本系统首次见到该版本的时点）"
            "——当时捕获资格不能只凭 pubDate 自称")
    h = _hash_content(fin)
    value = fin.get("value")
    unit = fin.get("unit", "元")
    seen = first_seen_at or _now_aware()
    return EvidenceRecord(
        source_kind="financial",
        source_uri=fin.get("source_uri", "financial"),
        source_document_hash=h,
        security_id=security_id,
        metric_or_claim=fin["metric"],
        value=value,
        unit=unit,
        period_kind=fin["period_kind"],
        period_end=fin.get("period_end"),
        published_at=published,
        # 无可信公布日（如只有报告期）时 available_at 置 None：不进严格快照——
        # "今天下载的重述财报不能回填过去"（DESIGN §5.1）
        available_at=published,
        fetched_at=_now_aware(),
        source_version=fin.get("source_version", "financial"),
        revision_id=str(fin.get("revision_id") or ""),
        knowledge_basis=kb,
        version_available_at=version_available_at,
        first_seen_at=seen,
        document_version_id=h,
        timestamp_precision="day" if published else "unknown",
        provenance_evidence_ids=list(provenance_evidence_ids or []),
        raw_value=fin.get("raw_value", value),
        raw_unit=fin.get("raw_unit", unit),
        semantic_note=fin.get("semantic_note", ""),
        value_kind=fin.get("value_kind", "UNKNOWN"),
        period_basis=fin.get("period_basis", "UNKNOWN"),
        underlying_period_basis=fin.get("underlying_period_basis"),
        metric_definition_version=fin.get("metric_definition_version", ""),
    )


def user_asserted_record(security_id: str, metric: str, value, *, unit: str = "",
                         asserted_by: str, note: str = "",
                         period_end: Optional[str] = None,
                         available_at: Optional[datetime] = None) -> EvidenceRecord:
    """人工证据通道（DESIGN §5.1：分部营收难自动取得时允许用户给来源的人工证据）。

    USER_ASSERTED 明确标记输入者/时间/适用期——不冒充 OBSERVED。
    available_at 缺省取当前时刻（回放测试可显式传历史时点）。

    R1 版本资格（DATA_TRUST §1）：knowledge_basis=UNKNOWN——用户仅凭自填过去日期
    不进严格历史效果样本（当前人工辅助计划可用）；用户上传可验证原始历史文件后
    按同一档案核验规则升级（显式构造 AS_PUBLISHED_ARCHIVE 记录）。
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
        knowledge_basis="UNKNOWN",
        first_seen_at=_now_aware(),
        timestamp_precision="unknown",
        semantic_note="人工证据——仅凭自填时点不构成历史版本证明（DATA_TRUST §1）",
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
        knowledge_basis="UNKNOWN",
        first_seen_at=_now_aware(),
        timestamp_precision="unknown",
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
