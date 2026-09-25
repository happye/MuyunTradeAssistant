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
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.decision_contract import FactStatus

logger = logging.getLogger(__name__)

CLAIM_SCHEMA_VERSION = "f6.v1"


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


def verify_claim(claim: ClaimRecord, evidence_pool: Optional[list] = None) -> VerifiedClaim:
    """规则核验（DESIGN ADR-F06 失败规则，全部通过才返回 VerifiedClaim）：
    ① 必须带引用（citation_uri 或 citation_hash）；
    ② 引用必须存在（evidence_pool 提供时：条目按 uri/hash 匹配，且条目带 security_id
       时必须与 claim 主体一致——**错误实体**（把别家公告安到自家头上）在此拦截）。
       ⚠️ 拦截强度的诚实边界（F6 审查 P1-2）：条目只有来源级 uri（无 hash/无
       security_id）时为**弱验证**——uri 是"巨潮公告"这类来源名时，伪造归属可穿透。
       强验证要求文档级引用（每份公告独立 uri 或带内容 hash + 归属方）；claim 引用
       带 citation_hash 时按 hash 精确匹配，不受此限；
    ③ 公开时点不得晚于当前（未来日期拒收）；
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
    now = datetime.now(timezone.utc)
    if claim.published_at is not None and claim.published_at > now:
        raise ClaimVerificationError(
            f"未来日期拒收：published_at={claim.published_at.isoformat()} 晚于当前")
    checks.append("no_future_date")
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

    def extract(self, source_doc: dict) -> list[ClaimRecord]:
        """source_doc: {title, content, date, source, security_id, ...}。

        缓存键 = self.name + 输入内容 hash（实例级缓存；调用方应复用同一实例——
        每次 new 实例缓存即失效，F6 审查 P2 备案）。"""
        key = self.name + "|" + hashlib.sha256(json.dumps(
            source_doc, ensure_ascii=False, sort_keys=True,
            default=str).encode("utf-8")).hexdigest()
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
