"""结构化事实提取与事件谱系（plan/fusion F6，DESIGN ADR-F06）：AI 共享事实、按事件增量。

边界（DESIGN ADR-F06 原文约束）：
- AI 调用两类：①带来源的结构化提取/反证，②最终结果解释。提取产出 **ClaimRecord**
  （谁、何事、发生时间、公开时间、影响业务、是否新增、支持/反驳哪条逻辑），
  由规则验证来源与时点——**不凭 LLM 自报 confidence 加仓**（confidence 只存诊断）
- 失败规则：解析失败、无引用、引用不存在、未来日期、单位不合法 → 拒收对应 claim
  （VerificationRejected）
- 事件去重：先源公告 ID/正文 hash，跨来源按（主体、事件类型、发生时间、关键数值）
  聚类——同一公告被转载十次仍是一份经济事件（event_id 谱系）；**更正公告是新版本**
  （revision），不被去重吞掉
- 一个经济事件可关联多个公司，但公司业务暴露不同——**不复制同一影响分**
- 提取器协议（ClaimExtractor）：注入式（无 AI 时确定性通道可用，模板行动卡不受影响）；
  同输入不重复付费（extract 缓存键 = 输入内容 hash + 提取器标识）
- 注入文本免疫：正文里的指令性文字按数据处理（verifier 只认引用与结构化字段，
  正文指令不改变提取结果——标注集有锁定用例）
- expect（事前预期）/events（事后事实）可关联同一 event_id 但状态不同；fear 只作
  环境字段进组合预算/研究优先级，不给另一份买卖票（接线随 F7/F8）

离线纯净：本模块零网络零 AI 调用；LLM 提取器以注入协议接入（真实调用方随
external/AI opt-in 评测跑，见 tests/core/test_claim_extraction.py 的 mock 用例与
标注集 tests/ai_eval/claim_annotations.json）。
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.decision_contract import FactStatus

logger = logging.getLogger(__name__)

CLAIM_SCHEMA_VERSION = "f6.v1"
# R2 分级核验协议（DATA_TRUST §4）——历史 f6.v1/v2/cv2 标签和结果保留，新协议单独命名。
# K0b/D1：cv3 = 绑定元组核验（同一原文局部：主体×谓词×带符号数值×单位×期间×阶段×
# 否定对象）；cv2 时代 FACT_CHECKED 结果按旧协议标签保留，重验按 cv3 语义降级。
CLAIM_VERIFICATION_PROTOCOL_VERSION = "cv3"
# J0/K0b 类型化事实核验范围规则版本（DELIVERY_PLAN J0a/K0b-1：只有支持该类型的确定性
# 规则完整验证才进 FACT_CHECKED；改动关键词表/绑定规则必须 bump——旧核验结果不冒充新语义）
TYPED_RULE_VERSION = "k0b.binding_v1"

# 核验范围闭集（verification_scope；""=自由文本——最高只能 EXCERPT_GROUNDED）
SCOPE_NUMERIC_EVENT = "typed_numeric_event"
SCOPE_NEGATION = "typed_negation"
_VERIFICATION_SCOPES = ("", SCOPE_NUMERIC_EVENT, SCOPE_NEGATION)

# 类型化数值事件的谓词关键词（按 event_type；主张与摘录须共享同一指标词——
# 数字不能脱离指标语境背书，扩展反例「数字为其他指标」）
_SCOPED_NUMERIC_PREDICATES: dict[str, tuple[str, ...]] = {
    "order": ("订单", "合同", "中标", "销售"),
    "earnings": ("净利", "利润", "盈利", "营收", "收入", "业绩", "亏损", "预增", "预减"),
    "exposure": ("暴露", "占比", "份额", "收入占比", "业务收入"),
}
# 数值可能属于其他主体的表述标记（「数字为其他主体」——归属存疑转人工）
_OTHER_SUBJECT_MARKERS = ("同行", "竞争对手", "竞争对手公司", "友商", "同业公司")


class ClaimVerificationError(ValueError):
    """非法 claim 拒收（无引用/引用不存在/未来日期/单位不合法/正文注入指令）。"""


class ClaimRelation(str, Enum):
    """claim 与投资逻辑的关系（支持/反驳/中性新事实）。"""

    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    NEUTRAL = "NEUTRAL"


class ClaimRecord(BaseModel):
    """一条结构化事实主张（MODEL_INFERRED——由提取器产出，规则验证后入库）。"""

    model_config = ConfigDict(extra="allow")  # ADR-F09

    claim_id: str = Field(default_factory=lambda: uuid_hex())
    schema_version: str = CLAIM_SCHEMA_VERSION
    event_id: str = Field(default="", description="事件谱系 ID（同事件多 claim/多公司共享）")
    security_id: str = Field(default="", description="主体公司（可空=行业级事件）")
    subject: str = Field(default="", description="事件主体（公司/行业/宏观）")
    event_type: str = Field(default="other", description="order/earnings/forecast/penalty/mgmt_change/other...")
    statement: str = Field(description="人话主张（谁、何事、影响业务）")
    relation: ClaimRelation = ClaimRelation.NEUTRAL
    occurred_at: Optional[datetime] = Field(default=None, description="事件发生时点")
    published_at: Optional[datetime] = Field(default=None, description="公开时点")
    value: Optional[float] = Field(default=None, description="关键数值（如订单金额）")
    unit: str = Field(default="", description="数值单位（元/万元/%；不合法单位拒收）")
    # 引用（必须可核验——无引用/引用不存在拒收）
    citation_uri: str = Field(default="", description="来源定位（URL/公告ID/文件路径#锚点）")
    citation_hash: str = Field(default="", description="来源内容 sha256（须与证据库/原文一致）")
    excerpt_locator: str = Field(default="", description="原文定位（页码/段落）")
    # R2 内容核验扩展（DATA_TRUST §4；缺省空=未提供，内容检查按缺失处理不进 FACT_CHECKED）
    quote_text: str = Field(default="", description="摘录原文片段（EXCERPT_GROUNDED 的依据——标题不能冒充已读全文）")
    quote_span: Optional[list[int]] = Field(default=None, description="摘录在正文中的字符区间 [start, end]（给了则精确校验）")
    fact_stage: str = Field(
        default="", description="事实阶段：intention(意向/框架)/signed(签约)/delivering(交付)/"
                                "revenue_recognized(收入确认)/unknown——框架意向不得当已确认收入")
    negation_flag: Optional[bool] = Field(default=None, description="主张是否为否定性事实（如『未获得订单』）；None=未声明")
    conditional_flag: bool = Field(default=False, description="主张是否带条件（如『拟』『预计』）")
    relation_basis: str = Field(default="", description="support/refute 判断的依据注记——研究推论与原始事实分开存（DATA_TRUST §4）")
    # J0 类型化事实（DELIVERY_PLAN J0a）：核验范围声明——空=自由文本（最高
    # EXCERPT_GROUNDED）；typed_numeric_event/typed_negation 走对应确定性规则
    verification_scope: str = Field(
        default="", description="核验范围（J0 类型化事实）：typed_numeric_event/typed_negation；"
                                "空=自由文本——摘录真实存在但结论不被自动支持")
    # 谱系与诊断
    extracted_by: str = Field(default="", description="提取器标识（deterministic/<model>@<version>）")
    model_confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="LLM 自报置信度——**仅诊断字段，禁止进仓位/评分计算**（ADR-F06）")
    revision: int = Field(default=1, description="更正版本（更正公告是新版本，不被去重吞掉）")
    lineage_ids: list[str] = Field(default_factory=list, description="上游证据/事件引用")

    @field_validator("occurred_at", "published_at")
    @classmethod
    def _aware_datetimes(cls, v):
        """F6 审查 P1-3：naive datetime 在未来日期比较处 TypeError 崩溃——构造期即拒
        （EvidenceRef 同款纪律；LLM 提取器解析裸日期是常态，失败规则=拒收不是崩溃）。"""
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("claim 时点必须带时区（naive datetime 拒收）")
        return v

    def content_fingerprint(self) -> str:
        """内容指纹（claim_id/时间戳无关）——重复新闻/转载去重的稳定键。"""
        d = self.model_dump()
        d.pop("claim_id", None)
        d.pop("model_confidence", None)
        return hashlib.sha256(
            json.dumps(d, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()


def uuid_hex() -> str:
    import uuid
    return uuid.uuid4().hex


def _normalize_value_for_key(value, unit: str = ""):
    """聚类键的值归一：金额类单位换算到元（5亿元==50000万元），数值统一 float。"""
    _to_yuan = {"元": 1.0, "万元": 1e4, "亿元": 1e8}
    if isinstance(value, (int, float)) and not isinstance(value, bool) and unit in _to_yuan:
        return round(float(value) * _to_yuan[unit], 6)
    return value


def event_key(claim: "ClaimRecord") -> tuple:
    """跨来源聚类键（DESIGN ADR-F06 第二阶段）：（主体、事件类型、发生日、归一值）。
    同键 = 同一经济事件（转载/改写不计新票）；值缺失（None）不参与键匹配——
    两笔值都缺失的同日订单**不**自动合并（防误并，F6 审查 P2）。"""
    occurred_day = claim.occurred_at.date().isoformat() if claim.occurred_at else ""
    val = _normalize_value_for_key(claim.value, claim.unit) if claim.value is not None else None
    return (claim.security_id or claim.subject or "", claim.event_type, occurred_day, val)


def derive_event_id(security_id: str, event_type: str, occurred_at: Optional[datetime],
                    value: Optional[float], source_announcement_id: str = "",
                    content_hash: str = "", unit: str = "") -> str:
    """事件谱系 ID（DESIGN ADR-F06 去重规则，两阶段）：
    第一阶段优先源公告 ID / 正文 hash（同一文档的转载/重发）；第二阶段跨来源按
    （主体、事件类型、发生日、归一值）聚类——dedup_claims 用 event_key 执行。
    更正公告 revision 不同 → fingerprint 不同不被第一阶段吞；跨事件关联经
    lineage_ids 登记（自动回填属 F7/F8 接线，当前需提取器显式给）。"""
    basis = source_announcement_id or content_hash
    if not basis:
        basis = "|".join([security_id or "", event_type,
                          occurred_at.date().isoformat() if occurred_at else "",
                          str(_normalize_value_for_key(value, unit) if value is not None else "")])
    return "evt_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _unit_allowed(unit: str) -> bool:
    """单位白名单（G15：不合法单位拒收；空=无量纲允许）。"""
    if unit == "":
        return True
    return unit in {"元", "万元", "亿元", "%", "股", "万股", "亿股", "倍"}


class VerifiedClaim(BaseModel):
    """通过规则核验的 claim（附核验元数据；confidence 不进任何计算字段）。"""

    model_config = ConfigDict(extra="allow")

    claim: ClaimRecord
    fact_status: FactStatus = FactStatus.MODEL_INFERRED
    verified_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    checks: list[str] = Field(default_factory=list, description="通过的核验项（审计用）")


def verify_claim(claim: ClaimRecord, evidence_pool: Optional[list] = None,
                 as_of: Optional[datetime] = None) -> VerifiedClaim:
    """规则核验（结构层，DATA_TRUST §4：R2 起调用方应消费 verify_claim_tiered 的分级结果，
    本函数保留为底层结构检查）：
    ① 必须带引用（citation_uri 或 citation_hash）；
    ② 引用必须存在（evidence_pool 提供时：条目按 uri/hash 匹配，且条目带 security_id
       时必须与 claim 主体一致——**错误实体**（把别家公告安到自家头上）在此拦截）。
       **无 evidence_pool 时不声称 citation_resolves**（R2 修正探针 P3：未执行的来源
       解析不得记为通过；无池结果只有结构检查，内容核验无从谈起）。
       ⚠️ 拦截强度的诚实边界（F6 审查 P1-2）：条目只有来源级 uri（无 hash/无
       security_id）时为**弱验证**——uri 是"巨潮公告"这类来源名时，伪造归属可穿透。
       强验证要求文档级引用（每份公告独立 uri 或带内容 hash + 归属方）；claim 引用
       带 citation_hash 时按 hash 精确匹配，不受此限；
    ③ 公开时点不得晚于截止（as_of 显式传入用 as_of——回放必须传；缺省用当前时刻，
       仅 live 语义合法）；
    ④ 单位合法（G15 白名单）；
    ⑤ statement 非空；正文注入防护=核验只依赖结构化字段与引用，statement 中的
       指令性文字不改变任何核验结果（标注集用例锁定）。

    evidence_pool 条目形态：{"uri": str, "hash": Optional[str], "security_id": Optional[str]}
    （hash/security_id 给了就参与匹配——核验强度随证据库元数据完整度提升）。"""
    checks = []
    if not claim.citation_uri and not claim.citation_hash:
        raise ClaimVerificationError("claim 无引用（citation_uri/citation_hash 均空）——拒收")
    checks.append("citation_present")
    if evidence_pool is not None:
        matched = False
        for e in evidence_pool:
            if not isinstance(e, dict):
                continue
            uri_ok = claim.citation_uri and e.get("uri") == claim.citation_uri
            hash_ok = claim.citation_hash and e.get("hash") and e.get("hash") == claim.citation_hash
            if not (uri_ok or hash_ok):
                continue
            # 实体归属：条目标注了归属主体时必须与 claim 主体一致
            if e.get("security_id") and claim.security_id and \
                    e["security_id"] != claim.security_id:
                continue
            # 内容归属：条目标注了内容 hash 时必须与 claim 引用一致
            if e.get("hash") and claim.citation_hash and e["hash"] != claim.citation_hash:
                continue
            matched = True
            break
        if not matched:
            raise ClaimVerificationError(
                f"引用不存在（uri={claim.citation_uri!r} hash={claim.citation_hash[:12]!r} "
                f"不在证据库或归属不符）——拒收")
        checks.append("citation_resolves")
    # 无池时**不**追加 citation_resolves——结构层未执行来源解析，如实缺项（R2/探针 P3）
    cutoff = as_of or datetime.now(timezone.utc)
    if getattr(cutoff, "tzinfo", None) is None:
        raise ClaimVerificationError("as_of 必须带时区（naive datetime 拒收——回放资格比较需要）")
    if claim.published_at is not None and claim.published_at > cutoff:
        raise ClaimVerificationError(
            f"未来日期拒收：published_at={claim.published_at.isoformat()} "
            f"晚于截止 {cutoff.isoformat()}")
    # 无时间不声称 no_future_date 已证实（DATA_TRUST §4 明文——审查 P1：与 P3 同原则）
    checks.append("no_future_date" if claim.published_at is not None
                  else "no_future_date_unchecked")
    if not _unit_allowed(claim.unit):
        raise ClaimVerificationError(f"单位不合法拒收: {claim.unit!r}")
    checks.append("unit_allowed")
    if not claim.statement.strip():
        raise ClaimVerificationError("statement 为空——拒收")
    checks.append("statement_present")
    return VerifiedClaim(claim=claim, checks=checks)


class ClaimExtractor:
    """提取器协议（注入式）。真实 LLM 提取器实现本协议并在 AI opt-in 评测中启用；
    无 AI 时走 DeterministicExtractor（确定性通道：直接结构化现有公告/预告数据，
    不产生未引用的主张）。同输入不重复付费：基类缓存 extract 结果（输入内容 hash 键）。"""

    name: str = "base"

    def __init__(self):
        self._cache: dict[str, list[ClaimRecord]] = {}
        self.calls = 0  # 付费次数计数（评测费用/耗时对比用）

    def _cache_key(self, source_doc: dict) -> str:
        """缓存键（R2 版本化，DATA_TRUST §4）：协议版本 + 提取器标识 + 输入内容 hash。
        子类可叠加 prompt/model 版本（换提示词即换键——旧缓存不冒充新语义）。"""
        return "|".join([CLAIM_SCHEMA_VERSION, self.name,
                         hashlib.sha256(json.dumps(
                             source_doc, ensure_ascii=False, sort_keys=True,
                             default=str).encode("utf-8")).hexdigest()])

    def extract(self, source_doc: dict) -> list[ClaimRecord]:
        """source_doc: {title, content, date, source, security_id, ...}。

        缓存键 = 协议版本 + name + 输入内容 hash（实例级缓存；调用方应复用同一实例——
        每次 new 实例缓存即失效，F6 审查 P2 备案）。"""
        key = self._cache_key(source_doc)
        if key in self._cache:
            return self._cache[key]  # 同输入不重复付费
        self.calls += 1
        claims = self._extract_impl(source_doc)
        self._cache[key] = claims
        return claims

    def _extract_impl(self, source_doc: dict) -> list[ClaimRecord]:  # pragma: no cover - 协议
        raise NotImplementedError

    def reset_cache(self):
        self._cache.clear()


# ──────────────── R2 分级核验（DATA_TRUST §4：从「格式合法」到「内容有依据」）────────────────

class VerificationLevel(str, Enum):
    """核验等级（逐级递进；停在已达等级并给 failures；语义类缺口 NEEDS_REVIEW）。

    PARSED          JSON/字段合法（结构层通过）
    SOURCE_RESOLVED 文档级引用、hash、实体均匹配（在 as_of 截止前已公开）
    EXCERPT_GROUNDED 摘录定位准确，原文片段确实存在（quote_text 在正文中）
    FACT_CHECKED    主体、否定词、数值/单位、阶段与**摘录原文**一致（确定性检查全过）
    NEEDS_REVIEW    无法核实/人工判断（正文不可得、摘录不符、数值未定位、阶段错位、
                    更正混用——TASKS R2 暂停点：正文不可得/语义复杂保留 NEEDS_REVIEW）
    REJECTED        已发现错误（主体不符、hash 与文档版本冲突、与摘录正文相反、单位错位）
    """

    PARSED = "PARSED"
    SOURCE_RESOLVED = "SOURCE_RESOLVED"
    EXCERPT_GROUNDED = "EXCERPT_GROUNDED"
    FACT_CHECKED = "FACT_CHECKED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"


class SourceDocument(BaseModel):
    """证据库中的一份来源文档（核验池条目——标题不能冒充已读取全文）。

    content_hash 只对 **body 正文** 计算（LLM 提取器历史用 title+content hash——
    两者不同键不互认，文档级强验证以 body hash 为准）。"""
    model_config = ConfigDict(extra="allow")

    canonical_uri: str = Field(description="文档级唯一来源定位（每份公告独立 uri——弱来源名不算）")
    content_hash: str = Field(description="**正文 body** 内容 sha256")
    security_ids: list[str] = Field(default_factory=list, description="归属主体（归属方元数据）")
    published_at: Optional[datetime] = Field(default=None, description="发表/版本时点（as_of 资格判定）")
    body: str = Field(default="", description="正文全文（或能取回原文的定位引用，见 body_locator）")
    body_locator: str = Field(default="", description="归档对象引用（research/raw sha256 等——正文不在内存时）")
    title: str = Field(default="", description="标题（不参与内容 hash，不作已读全文的证据）")
    is_correction: bool = Field(default=False, description="是否更正/修订公告（后发更正对抗案例）")

    @field_validator("published_at")
    @classmethod
    def _aware_published(cls, v):
        if v is not None and (v.tzinfo is None or v.tzinfo.utcoffset(v) is None):
            raise ValueError("SourceDocument.published_at 必须带时区")
        return v

    @staticmethod
    def body_hash(body: str) -> str:
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    @classmethod
    def from_source_doc(cls, doc: dict) -> "SourceDocument":
        """从提取器输入的 source_doc 构造（hash 约定与 LLM/确定性提取器一致：
        sha256(title+content)）——同一文档在 claim.citation_hash 与池条目两侧
        用同一算法，uri+hash 双匹配才不误触版本冲突（R3 接线用此构造）。"""
        title = str(doc.get("title") or "")
        content = str(doc.get("content") or "")
        h = hashlib.sha256((title + content).encode("utf-8")).hexdigest()
        pub = None
        if doc.get("date"):
            try:
                from datetime import datetime as _dt, timedelta as _td, timezone as _tz
                pub = _dt.fromisoformat(f"{doc['date']}T23:59:00+08:00")
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=_tz(_td(hours=8)))
            except ValueError:
                pub = None
        return cls(canonical_uri=str(doc.get("source") or ""),
                   content_hash=h, security_ids=([str(doc["security_id"])]
                                                  if doc.get("security_id") else []),
                   body=content, title=title, published_at=pub)

    @classmethod
    def from_legacy_pool_entry(cls, e: dict) -> "SourceDocument":
        """旧 dict 池条目（{uri, hash, security_id|security_ids, body?, published_at?}）
        → SourceDocument（兼容适配）。
        无 body 的旧条目=纯来源级弱引用——只能到 SOURCE_RESOLVED，内容核验不可达；
        naive published_at 降为 None（不让适配崩——核验器契约是返回结果不抛）。"""
        sids = e.get("security_ids") or ([e["security_id"]] if e.get("security_id") else [])
        pub = e.get("published_at")
        if pub is not None:
            try:
                if getattr(pub, "tzinfo", None) is None:
                    pub = None  # naive → 按缺失处理（不让 validator 在核验器内炸）
            except AttributeError:
                pub = None
        return cls(canonical_uri=str(e.get("uri") or ""),
                   content_hash=str(e.get("hash") or ""),
                   security_ids=[str(s) for s in sids],
                   body=str(e.get("body") or ""),
                   published_at=pub)


class ClaimVerificationResult(BaseModel):
    """分级核验结果（levels 通过 checks 呈现阶梯；failures 人话原因，用户可见）。"""
    model_config = ConfigDict(extra="allow")

    claim: ClaimRecord
    level: VerificationLevel
    checks: list[str] = Field(default_factory=list, description="已通过的阶梯检查（审计）")
    failures: list[str] = Field(default_factory=list, description="未达/失败项（人话——用户可见的具体原因）")
    verified_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    protocol_version: str = CLAIM_VERIFICATION_PROTOCOL_VERSION
    rule_version: str = Field(default=TYPED_RULE_VERSION,
                              description="类型化事实规则版本（J0——核验范围门语义）")
    as_of: Optional[datetime] = None
    fact_status: FactStatus = FactStatus.MODEL_INFERRED


_NEGATION_MARKERS = ("无新", "没有新", "未获", "未取得", "未签", "取消", "终止", "并无", "不存在",
                     "no new order", "not won", "did not")
_FRAMEWORK_MARKERS = ("框架协议", "意向", "拟签", "拟与", "合作备忘", "战略协议", "框架合作")


def _numeric_text_variants(value: float, unit: str) -> list[str]:
    """(value, unit) 在原文中的候选文本形态（金额单位换算族 + 千分位）。
    非有限值（NaN/inf——JSON 非标扩展可产生）返回空（不做数值检查，审查 P2）。"""
    import math
    if not isinstance(value, (int, float)) or isinstance(value, bool) \
            or not math.isfinite(value):
        return []
    out: list[str] = []
    to_yuan = {"元": 1.0, "万元": 1e4, "亿元": 1e8}
    base_forms = [f"{value:g}"]
    if value == int(value):
        base_forms.append(f"{int(value):,}")
    if unit in to_yuan:
        yuan = value * to_yuan[unit]
        for u, k in to_yuan.items():
            v = yuan / k
            if v == int(v) and v > 0:
                out.append(f"{int(v):,}{u}")
                out.append(f"{int(v)}{u}")
                if v < 1:  # 小数元值（0.5亿）
                    out.append(f"{v:g}{u}")
    for f in base_forms:
        out.append(f"{f}{unit}" if unit else f)
        if unit:
            out.append(f"{f} {unit}")
    return [s for s in out if s.strip()]


# K0b/D1：绑定元组核验的原文局部（子句）切分——数值/谓词/否定对象必须在同一子句内
_CLAUSE_SPLIT_RE = re.compile(r"[。！？；;!?，,、\n\r]")
# 词元边界补充（K0b/D1c）：前一字符为小数点（半角/全角）= 小数尾部，不是独立数值
_DECIMAL_BOUNDARY_CHARS = ".．"
_NEGATIVE_SIGNS = "-－−"


def _clauses_with_spans(text: str) -> list[tuple[str, int, int]]:
    """摘录 → 子句列表 [(子句文本, 起始偏移, 结束偏移)]——绑定元组的「同一原文局部」。

    千分位不切分：分隔符两侧均为数字（"1,900"）时是数字内部逗号，不是子句边界。"""
    out: list[tuple[str, int, int]] = []
    start = 0
    for m in _CLAUSE_SPLIT_RE.finditer(text):
        if m.start() > start:
            out.append((text[start:m.start()], start, m.start()))
        start = m.end()
    if start < len(text):
        out.append((text[start:], start, len(text)))
    # 合并被数字内分隔符误切的相邻子句（"1,900"：分隔符两侧均为数字）
    merged: list[tuple[str, int, int]] = []
    for seg, s, e in out:
        if merged:
            prev_seg, ps, pe = merged[-1]
            between = text[pe:s]
            if between and pe > 0 and text[pe - 1].isdigit() and s < len(text) \
                    and text[s].isdigit():
                merged[-1] = (prev_seg + between + seg, ps, e)
                continue
        merged.append((seg, s, e))
    return merged


def _find_value_occurrences(quote: str, variants: list[str]) -> list[dict]:
    """在摘录中定位 (value, unit) 的文本形态（K0b/D1 绑定核验的定位层）。

    每个命中返回 {variant, start, end, text_negative}：
    - 词元边界（J0 继承+K0b 收紧）：前一字符不得是数字或小数点（「0.900 万元」
      不得命中「900 万元」；「1900 万元」不得命中「900 万元」）；token 以数字结尾
      时后一字符不得是数字（「900」不得命中「9000」）
    - 符号绑定（K0b/D1b）：token 自带负号，或跳过空白后前一字符是负号（-／－／−）
      → text_negative=True
    """
    out: list[dict] = []
    for token in variants:
        if not token:
            continue
        token_negative = token.lstrip().startswith(tuple(_NEGATIVE_SIGNS))
        start = 0
        while True:
            i = quote.find(token, start)
            if i < 0:
                break
            j = i + len(token)
            prev = quote[i - 1] if i > 0 else ""
            nxt = quote[j] if j < len(quote) else ""
            # 注意：prev 为空串时 `prev in X` 恒真（空串是任何串的子串）——
            # 边界字符判定必须带非空前置
            if prev.isdigit() or (prev and prev in _DECIMAL_BOUNDARY_CHARS) or \
                    (nxt.isdigit() and token[-1].isdigit()):
                start = i + 1
                continue
            k = i - 1
            while k >= 0 and quote[k] in " 　":
                k -= 1
            text_negative = token_negative or (
                k >= 0 and quote[k] in _NEGATIVE_SIGNS
                # K0b 审查 P2-5：负号自身前后都是数字（"100-200万"）= 区间连接符，
                # 不是负值——不得把合法区间上界误判成负数
                and not (k > 0 and quote[k - 1].isdigit()
                         and k + 1 < len(quote) and quote[k + 1].isdigit()))
            out.append({"variant": token, "start": i, "end": j,
                        "text_negative": text_negative})
            start = j
    return out


def _resolve_verification_scope(claim: "ClaimRecord") -> str:
    """核验范围解析（J0）：显式声明优先；未声明时按**已结构化组件**保守推导
    （value+unit+注册表 event_type → 数值事件；negation_flag → 否定型）——
    推导不出来=自由文本（最高 EXCERPT_GROUNDED，不发明类型）。
    显式声明在范围门内**回验组件齐备**（声明与组件不符 → 封顶，自报范围不构成
    绕过——J0 审查 P1）。"""
    if claim.verification_scope in (SCOPE_NUMERIC_EVENT, SCOPE_NEGATION):
        return claim.verification_scope
    if claim.value is not None and claim.unit and \
            claim.event_type in _SCOPED_NUMERIC_PREDICATES:
        return SCOPE_NUMERIC_EVENT
    if claim.negation_flag is True:
        return SCOPE_NEGATION
    return ""


def verify_claim_tiered(claim: ClaimRecord, documents: Optional[list],
                        *, as_of: datetime) -> ClaimVerificationResult:
    """分级核验（R2 主入口，DATA_TRUST §4 五级；**强制显式 as_of** 且必须带时区——
    回放资格不依赖机器当前日期，R3 回放调用方传 naive 直接拒收不崩）。

    阶梯：PARSED → SOURCE_RESOLVED → EXCERPT_GROUNDED → FACT_CHECKED；
    停在已达等级并给 failures；语义类缺口落 NEEDS_REVIEW（正文不可得/摘录不符/
    数值未定位/阶段错位/更正混用/数值归属存疑——TASKS R2 暂停点）；发现错误落
    REJECTED（主体不符/hash 版本冲突/与摘录相反/单位错位——**纯时点未到不算错误**，
    统一停 PARSED 给回放话术）。

    J0 类型化事实范围门（N1 根因修复，TYPED_RULE_VERSION）：错误/存疑检测器照旧
    先跑；FACT_CHECKED 只有在核验范围（verification_scope：typed_numeric_event /
    typed_negation，显式声明或按已结构化组件保守推导）的确定性规则**完整验证**
    后才可达——主体、谓词指标、数值/单位（词元边界）、阶段、否定与摘录一致 +
    来源公开时点已知。自由文本/缺组件最高停在 EXCERPT_GROUNDED（摘录真实存在
    但结论不被自动支持——「未发现矛盾」不等于「整句成立」）。

    确定性内容检查（**只在 quote_text 内判定**——quote 是主张自选的支撑摘录，
    正文其余部分可能是套话/澄清/无关段落，全文扫描会误杀真实主张，审查 P1）：
    - 引用解析：文档级 uri 匹配 + **hash 与文档版本一致**（冲突=篡改/版本错配→
      REJECTED，审查 P1）+ 归属主体一致 + 文档在 as_of 前已公开
    - 摘录定位：quote_text 必须真实存在于正文（quote_span 给了则按区间精确校验）
    - 数值一致：value+unit 的原文形态须出现在摘录内（**词元边界**——数字子串
      恰巧出现不算）；差 100 倍的形态出现在摘录而原值形态缺席 → 单位错位
      REJECTED；否则数值未定位 → NEEDS_REVIEW
    - 谓词指标一致：主张与摘录共享同一指标关键词（数值不能脱离指标语境背书）
    - 否定一致：摘录内否定标记 vs 主张否定旗标冲突 → REJECTED（否定丢失）
    - 阶段一致：摘录内框架/意向标记 + 主张带数值且非意向阶段 → NEEDS_REVIEW
    - 后发更正：来源是更正公告而 claim.revision==1 → NEEDS_REVIEW
    - 注入免疫：全部检查是确定性字符串/数值比较，正文指令不进入任何判定路径

    documents：SourceDocument 列表（旧 dict 池条目自动适配；适配失败按缺失处理）。
    documents=None/空 → 停在 PARSED（无池不声称引用与内容已核实——探针 P3 语义）。
    """
    if as_of is None or getattr(as_of, "tzinfo", None) is None:
        raise ClaimVerificationError(
            "as_of 必须显式传入且带时区（回放资格不依赖机器当前日期；naive datetime 拒收）")
    checks: list[str] = ["parsed"]
    failures: list[str] = []
    level = VerificationLevel.PARSED

    def _reject(reason: str) -> ClaimVerificationResult:
        failures.append(reason)
        return ClaimVerificationResult(claim=claim, level=VerificationLevel.REJECTED,
                                       checks=checks, failures=failures, as_of=as_of)

    def _review(reason: str) -> ClaimVerificationResult:
        failures.append(reason)
        return ClaimVerificationResult(claim=claim, level=VerificationLevel.NEEDS_REVIEW,
                                       checks=checks, failures=failures, as_of=as_of)

    def _cap_excerpt_grounded(reason: str) -> ClaimVerificationResult:
        """封顶已达等级（EXCERPT_GROUNDED）——摘录真实但类型化核验不可达（诚实缺项，
        不是人工判断不了：材料缺口属提取侧，J0 范围门语义）。"""
        failures.append(reason)
        return ClaimVerificationResult(claim=claim, level=VerificationLevel.EXCERPT_GROUNDED,
                                       checks=checks, failures=failures, as_of=as_of)

    if not claim.citation_uri and not claim.citation_hash:
        return _reject("无引用（citation_uri/citation_hash 均空）")
    if not claim.statement.strip():
        return _reject("statement 为空")
    if not _unit_allowed(claim.unit):
        return _reject(f"单位不合法: {claim.unit!r}")

    docs: list[SourceDocument] = []
    for d in (documents or []):
        if not d:
            continue
        if isinstance(d, SourceDocument):
            docs.append(d)
        else:
            try:
                docs.append(SourceDocument.from_legacy_pool_entry(d))
            except Exception:  # 旧条目字段非法（如 naive 时点）→ 按缺失处理，不让核验器崩
                continue
    if not docs:
        failures.append("无证据库——引用与内容均未核实（不声称 citation_resolves）")
        return ClaimVerificationResult(claim=claim, level=level, checks=checks,
                                       failures=failures, as_of=as_of)

    # 阶梯 2：来源解析（文档级 + hash 版本一致 + 归属 + 截止资格）
    matched: Optional[SourceDocument] = None
    subject_conflict = False
    hash_conflict = False
    cited_soon = False
    for d in docs:
        uri_ok = bool(claim.citation_uri) and d.canonical_uri == claim.citation_uri
        hash_ok = bool(claim.citation_hash) and bool(d.content_hash) \
            and d.content_hash == claim.citation_hash
        if not (uri_ok or hash_ok):
            continue
        # uri 命中但 hash 属于另一版本（更正前后/被篡改引用）——已知错误（审查 P1）
        if uri_ok and claim.citation_hash and d.content_hash \
                and claim.citation_hash != d.content_hash:
            hash_conflict = True
            continue
        if claim.security_id and d.security_ids and claim.security_id not in d.security_ids:
            subject_conflict = True  # 引用命中但归属别家——已知错误（REJECTED），不是未解析
            continue
        if d.published_at is not None and d.published_at > as_of:
            cited_soon = True  # 文档在截止时未公开——对本 as_of 不存在（回放资格）
            continue
        matched = d
        break
    if matched is None:
        if hash_conflict:
            return _reject("引用 hash 与文档内容版本不符（更正前后混用或引用被篡改）")
        if subject_conflict:
            return _reject("主体不符：引用命中的公告归属别家（错公司对抗案例）")
        reason = ("引用文档晚于回放截止（as_of 时未公开——同一主张在截止后可解析）"
                  if cited_soon else "引用不在证据库或归属不符")
        failures.append(reason)
        return ClaimVerificationResult(claim=claim, level=level, checks=checks,
                                       failures=failures, as_of=as_of)
    if claim.published_at is not None and claim.published_at > as_of:
        failures.append("主张公开时点晚于回放截止（as_of 时该主张尚不存在）")
        return ClaimVerificationResult(claim=claim, level=level, checks=checks,
                                       failures=failures, as_of=as_of)
    if claim.published_at is not None:
        checks.append("no_future_date")  # 有时点才声称时点已核（无时间不声称——DATA_TRUST §4）
    checks.append("source_resolved")
    level = VerificationLevel.SOURCE_RESOLVED

    # 阶梯 3：摘录定位（标题不算已读全文）。doc 侧正文缺失 → NEEDS_REVIEW（TASKS
    # R2 暂停点「正文不可得」）；claim 侧未提供摘录 → 停 SOURCE_RESOLVED（已达等级
    # +失败项——材料缺口属提取侧，不是人工判断不了内容）
    if not matched.body:
        return _review(f"来源文档无正文（body 缺失，locator={matched.body_locator or '未登记'}）"
                       "——摘录与内容核验不可达，人工核对或补归档")
    if not claim.quote_text.strip():
        failures.append("主张无摘录（quote_text 缺失）——引用形式匹配≠内容有据，摘录由提取侧补齐")
        return ClaimVerificationResult(claim=claim, level=level, checks=checks,
                                       failures=failures, as_of=as_of)
    if claim.quote_span is not None and len(claim.quote_span) != 2:
        return _review("quote_span 非法（须 [start, end] 两元素）——摘录定位不可靠")
    span_ok = True
    if claim.quote_span is not None:
        s, e = claim.quote_span
        span_ok = (0 <= s <= e <= len(matched.body)
                   and matched.body[s:e] == claim.quote_text)
    if claim.quote_text not in matched.body or not span_ok:
        return _review("摘录未在原文定位到（quote_text/quote_span 与正文不符）"
                       "——可能摘录错误或引用错版，人工核对")
    checks.append("excerpt_grounded")
    level = VerificationLevel.EXCERPT_GROUNDED

    # 阶梯 4：确定性内容检查——**只在 quote_text 内判定**（摘录是主张自选的支撑；
    # 正文其他部分的套话/澄清段不参与，防误杀真实主张，审查 P1）。
    # 本阶梯先跑**错误/存疑检测器**（主体/否定/数值/阶段/更正——REJECTED/NEEDS_REVIEW
    # 语义与 R2 一致），最后过 **J0 类型化事实范围门**：只有支持该类型的确定性规则
    # 完整验证才进 FACT_CHECKED；自由文本/缺组件最高停在已达等级（EXCERPT_GROUNDED）
    # ——不可因没有命中否定词就认为整句成立（N1 根因）。
    if (claim.security_id and matched.security_ids
            and claim.security_id not in matched.security_ids):
        return _reject("主体不符（别家公司的公告不能支撑本主体主张）")
    has_negation_quote = any(m in claim.quote_text for m in _NEGATION_MARKERS)
    if has_negation_quote and claim.negation_flag is not True:
        return _reject("与摘录正文相反：支撑摘录含否定表述而主张为肯定性事实（否定丢失）")
    if claim.negation_flag is True and not has_negation_quote:
        return _review("主张为否定性事实但摘录未见否定表述——需人工复核")
    if claim.value is not None:
        # K0b/D1：数值定位带符号与边界——返回出现点列表（含 text_negative），
        # 供数值一致、符号绑定与同子句谓词绑定共用
        direct_occ = _find_value_occurrences(
            claim.quote_text, _numeric_text_variants(claim.value, claim.unit))
        scaled_occ = _find_value_occurrences(
            claim.quote_text,
            _numeric_text_variants(claim.value * 100, claim.unit)
            + _numeric_text_variants(claim.value / 100, claim.unit))
        if not direct_occ and scaled_occ:
            return _reject("数值与摘录相差 100 倍（单位错位）——按摘录应为 "
                           f"{scaled_occ[0]['variant']!r}（若非同一事项请人工修正摘录）")
        if not direct_occ:
            return _review("关键数值未能在摘录定位（数值/单位组合未出现）——需人工复核")
        # K0b/D1b：符号绑定——摘录数值带负号而主张为正 → 主张与原文矛盾（REJECTED）；
        # 主张为负而摘录数值无负号 → 「亏损=负值」的语义改写不可靠，降摘录级
        claim_negative = claim.value < 0
        sign_consistent = [o for o in direct_occ if o["text_negative"] == claim_negative]
        if not sign_consistent:
            if claim_negative:
                return _cap_excerpt_grounded(
                    "主张为负值而摘录数值未见负号（不做『亏损=负值』语义改写）——需人工核对")
            return _reject("数值符号与摘录相反（摘录该数值为负值）——主张与原文矛盾")
        checks.append("numeric_consistent")
    if (any(m in claim.quote_text for m in _FRAMEWORK_MARKERS)
            and claim.value is not None
            and claim.fact_stage not in ("intention",)):
        return _review("摘录为框架/意向表述而主张按已确认数值处理（阶段错位）——需人工核对合同性质")
    if matched.is_correction and claim.revision == 1:
        return _review("来源为更正/修订公告而主张未声明版本（revision=1）——新旧版本混用待人工定版")

    # ── J0 类型化事实范围门（N1 根因修复）──────────────────────
    scope = _resolve_verification_scope(claim)
    if scope == SCOPE_NUMERIC_EVENT and not (
            claim.value is not None and claim.unit
            and claim.event_type in _SCOPED_NUMERIC_PREDICATES):
        # 显式声明也须组件齐备——自报范围不构成绕过（J0 审查 P1：LLM 自报
        # scope 不能让未结构化主张拿到 FACT_CHECKED）
        return _cap_excerpt_grounded(
            "声明数值事件核验范围但类型化组件不齐（缺数值/单位/注册表事件类型）——需人工核对")
    if scope == SCOPE_NEGATION and claim.negation_flag is not True:
        return _cap_excerpt_grounded(
            "声明否定型核验范围但 negation_flag 未置真——需人工核对")
    if scope in (SCOPE_NUMERIC_EVENT, SCOPE_NEGATION) and matched.published_at is None:
        # 时点/实体资格与内容核验分开：来源未登记公开时点 → 类型化事实缺时点资格
        return _cap_excerpt_grounded(
            "缺公开时点（来源文档未登记公布时间）——类型化事实无法确认时点资格，需人工核对")
    if scope == SCOPE_NUMERIC_EVENT:
        # 谓词/指标一致 + 绑定元组（K0b/D1a）：主张谓词与**数值所在同一子句**必须
        # 共享指标关键词——数字不能脱离指标语境背书，同句其他指标的数值不能背书本主张
        predicates = _SCOPED_NUMERIC_PREDICATES.get(claim.event_type, ())
        kw_stmt = [k for k in predicates if k in claim.statement]
        if not kw_stmt:
            return _cap_excerpt_grounded(
                "主张谓词与摘录指标不一致（数值不能脱离指标语境背书）——需人工核对")
        clauses = _clauses_with_spans(claim.quote_text)
        value_bound = any(
            any(k in c_text for k in kw_stmt)
            for o in sign_consistent
            for c_text, c_s, c_e in clauses
            if c_s <= o["start"] and o["end"] <= c_e)
        if not value_bound:
            return _cap_excerpt_grounded(
                "数值与主张谓词未绑定到同一原文局部（数值可能属于同句其他指标/主体）"
                "——需人工核对")
        if any(m in claim.quote_text for m in _OTHER_SUBJECT_MARKERS):
            return _review("摘录含同行/竞对表述——数值可能属于其他主体，需人工核对归属")
        checks.append(SCOPE_NUMERIC_EVENT)
    elif scope == SCOPE_NEGATION:
        # 否定型（K0b/D1d）：主张文本自身要有否定语义，且**否定对象绑定**——主张与
        # 摘录须共享同一事件类型谓词（「无新订单」不能背书「不存在偿债风险」）；
        # 谓词类型未注册=无可靠解析句式 → 摘录级（不靠加关键词扩大闭集）
        if not any(m in claim.statement for m in _NEGATION_MARKERS):
            return _cap_excerpt_grounded(
                "主张文本未见否定表述（与摘录否定语义不一致）——需人工核对")
        neg_predicates = _SCOPED_NUMERIC_PREDICATES.get(claim.event_type, ())
        if not neg_predicates:
            return _cap_excerpt_grounded(
                "否定对象的事件类型未注册确定性句式（无可靠解析→摘录级）——需人工核对")
        if not any(k in claim.statement for k in neg_predicates):
            return _cap_excerpt_grounded(
                "主张谓词与摘录指标不一致（否定对象不同）——需人工核对")
        if not any(k in claim.quote_text for k in neg_predicates):
            return _cap_excerpt_grounded(
                "摘录未见主张所属指标（否定对象不同）——需人工核对")
        checks.append(SCOPE_NEGATION)
    else:
        return _cap_excerpt_grounded(
            "主张无确定性核验类型（自由文本）——摘录真实存在但结论不被自动支持，需人工核验")
    checks.append("content_consistent")
    level = VerificationLevel.FACT_CHECKED
    return ClaimVerificationResult(claim=claim, level=level, checks=checks,
                                   failures=failures, as_of=as_of)


class DeterministicExtractor(ClaimExtractor):
    """确定性提取器（零 AI）：把现有公告/业绩预告 dict 直接结构化为带引用的 claim。
    不发明事实：只转写 source_doc 已有的字段；无法结构化的输入产出空列表（不硬凑）。"""

    name = "deterministic"

    def _extract_impl(self, source_doc: dict) -> list[ClaimRecord]:
        title = str(source_doc.get("title") or "")
        content = str(source_doc.get("content") or "")
        if not title and not content:
            return []
        # F6 审查 P1-5：外部数据源日期格式五花八门——解析失败降级 None（不炸不硬凑）
        date = str(source_doc.get("date") or "").strip()
        try:
            published = (datetime.fromisoformat(f"{date}T23:59:00+08:00")
                         if date else None)  # 23:59+08=披露日尾约定（DATA_COVERAGE §4）
        except ValueError:
            published = None
        content_hash = hashlib.sha256((title + content).encode("utf-8")).hexdigest()
        event_type = str(source_doc.get("event_type") or "other")
        # relation 归一：上游中文标签映射，非法值降级 NEUTRAL（不炸）
        raw_rel = str(source_doc.get("relation") or "")
        rel_map = {"利好": "SUPPORTS", "看多": "SUPPORTS", "bullish": "SUPPORTS",
                   "利空": "REFUTES", "看空": "REFUTES", "bearish": "REFUTES"}
        relation = raw_rel if raw_rel in ("SUPPORTS", "REFUTES", "NEUTRAL") \
            else rel_map.get(raw_rel.lower(), "NEUTRAL")
        # value 非数值降级 None（不炸）
        raw_value = source_doc.get("value")
        if isinstance(raw_value, str):
            try:
                raw_value = float(raw_value)
            except ValueError:
                raw_value = None
        try:
            claim = ClaimRecord(
                event_id=derive_event_id(str(source_doc.get("security_id") or ""), event_type,
                                          published, raw_value,
                                          content_hash=content_hash,
                                          unit=str(source_doc.get("unit") or "")),
                security_id=str(source_doc.get("security_id") or ""),
                subject=str(source_doc.get("subject") or source_doc.get("security_id") or ""),
                event_type=event_type,
                statement=title or content[:120],
                relation=ClaimRelation(relation),
                # 公告日期是"公布日"不是"发生日"——occurred_at 不冒充（F6 审查 P2：
                # 不发明事实；发生时间需提取器从正文结构化，确定性通道没有就留 None）
                occurred_at=None,
                published_at=published,
                value=raw_value,
                unit=str(source_doc.get("unit") or ""),
                citation_uri=str(source_doc.get("source") or ""),
                citation_hash=content_hash,
                # R2：正文即摘录（确定性转写可定位原文——无 content 的输入留空）
                quote_text=content[:500],
                # J0：转写了数值 → 声明数值事件核验范围（只转写已结构化字段，不发明；
                # 范围门仍要求谓词/时点/边界组件齐备才 FACT_CHECKED）
                verification_scope=(SCOPE_NUMERIC_EVENT if raw_value is not None else ""),
                extracted_by="deterministic",
            )
        except Exception as e:
            # 结构化失败的诚实降级：空列表（不硬凑、不假成功）
            logger.info(f"claim 结构化失败（降级为无主张）: {e}")
            return []
        return [claim]


class MockLLMExtractor(ClaimExtractor):
    """评测用 LLM 提取器替身（AI opt-in 测试）：按预设脚本产出 claim，
    记录调用次数供『同输入不重复付费』断言；自报 confidence 只进诊断字段。"""

    name = "mock_llm"

    def __init__(self, scripted: list[ClaimRecord]):
        super().__init__()
        self._scripted = scripted

    def _extract_impl(self, source_doc: dict) -> list[ClaimRecord]:
        return [c.model_copy(deep=True) for c in self._scripted]


def dedup_claims(claims: list[ClaimRecord]) -> tuple[list[ClaimRecord], int]:
    """事件谱系去重（两阶段，DESIGN ADR-F06）：
    第一阶段：content_fingerprint 相同（同一文档转载/重发）只保留一条；
    第二阶段：event_key 相同（跨来源同主体/同类型/同发生日/同归一值）只保留一条
    ——同一公告被转载十次、或被改写转载，仍是一份经济事件（不重复计票）。
    更正公告 revision 不同 → 指纹不同 → 第一阶段不吞；第二阶段若 event_key 相同
    （同日同值同主体）也只保留 revision 最高版本。值缺失的 claim 不参与第二阶段
    （值 None 不入键——防两笔未知金额订单被误并）。
    返回 (去重后列表, 去掉条数)。"""
    out: list[ClaimRecord] = []
    seen_fp: set = set()
    seen_key: dict = {}
    dropped = 0
    for c in claims:
        fp = c.content_fingerprint()
        if fp in seen_fp:
            dropped += 1
            continue
        seen_fp.add(fp)
        key = event_key(c)
        if c.value is None or c.revision != 1:
            # 值缺失：不参与聚类（防误并，F6 审查 P2）；
            # revision != 1：更正版本保留可审计（AN-03 语义——更正是版本不是转载）
            out.append(c)
            continue
        prev = seen_key.get(key)
        if prev is not None:
            # 同事件：保留 revision 更高（更正优先），否则保留先出现的（确定性）
            if c.revision > prev.revision:
                out[out.index(prev)] = c
            dropped += 1
            continue
        seen_key[key] = c
        out.append(c)
    return out, dropped
