"""研究服务（plan/fusion iteration2 R3，RESEARCH_LOOP §1/§2）：单服务编排研究链。

产品变化（RESEARCH_LOOP §1）：输入代码后系统自动形成有来源的研究草稿——用户选择
意图和风险约束，不再靠手写 --facts 解锁资格；数据不足时给出**具体缺口**而不是让
用户编事实。

链路：snapshot → extract → verify → factor → assess → draft；**bundle 级缓存幂等**
（同输入重跑同 run_id 直接复用——分析重试幂等；步骤级缓存与失败恢复待 R8 增量任务
账本接线，本版如实不声称）。同一股票两个周期共享抓取与提取；命题评估、计划边界
分别按周期计算（共享事实、分别评估——架构师裁决③）。

边界（RESEARCH_LOOP §1 原文约束）：
- 纯研究模块：不 print、不读用户 HOME、不建 AI client——IO 与依赖**注入**
- ResearchBundle 只含研究结论，**不是第二个 DecisionPacket**：不定义最终买卖动作、
  仓位和执行状态（中间意图与最终执行资格区分，R3 验收7）
- 命题评估唯一入口 = research.assess_thesis_by_assertions（shadow/CLI 不自写判断）
- 因子要求按**能力ID**匹配（R4：非空数字不自动满足能力）

documents 输入契约：list[dict]，每项 {"claim": ClaimRecord, "documents": [SourceDocument|旧池条目]}
——待核验主张及其核验池；顶层裸 SourceDocument 条目按「仅来源文档（无主张）」归档，
进 source_document_ids 不参与核验。

离线纯净：模块 import 与纯函数路径零网络零 AI；真实数据经注入的 provider 进入。
"""

import hashlib
import json
import logging
from datetime import datetime
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.core.claim_extraction import (
    SourceDocument,
    VerificationLevel,
    verify_claim_tiered,
)
from src.core.decision_contract import ResearchStatus, ThesisStatus
from src.core.research import (
    AssertionEvaluation,
    ThesisAssertion,
    ThesisAssessment,
    assess_thesis_by_assertions,
    evaluate_assertion,
)

logger = logging.getLogger(__name__)

RESEARCH_SERVICE_VERSION = "r3.research_service_v1"
ASSERTION_METHOD_VERSION = "r3.assertion_v1"


# ── 周期最小命题模板（RESEARCH_LOOP §2；evidence_requirements 引用 R4 能力ID）──

_MID_TEMPLATE = [
    ("real_exposure", "公司哪个业务真实受益、暴露依据可查证（仅概念标签不达标）",
     "required", []),
    ("change_to_profit", "需求/价格/订单/供给变化如何进入收入或利润、当前处于预期/签约/交付/收入确认哪个阶段",
     "required", []),
    ("window_and_refutation", "何时验证、什么事实出现会推翻、到期未兑现如何处理",
     "required", []),
    ("price_context", "趋势/相对行业表现/流动性/已反映预期的可解释代理（不能自动证明经营命题）",
     "supporting", ["relative_return_v2", "trading_capacity_v1"]),
]

_LONG_TEMPLATE = [
    ("cash_sustainability", "盈利与现金创造的多期持续性（解释资本开支/营运资本/一次性项目）",
     "required", ["cash_conversion_v1", "roe_observed_v1"]),
    ("capital_constraint", "资本与资产负债约束：回报来源、杠杆、融资/稀释、偿债风险",
     "required", ["balance_risk_v1"]),
    ("moat", "经营优势有可查证经营事实支持（品牌形容词/技术走势不替代）",
     "required", []),
    ("valuation_assumption", "当前价隐含什么盈利/回报假设；悲观/基准/乐观情景由哪些数值决定",
     "required", ["valuation_range_v1"]),  # R4：区间能力当前不可计算——LONG 诚实带缺口
    ("longterm_invalidation", "长期失效条件与复核节奏（普通日线回撤不单独推翻企业逻辑）",
     "required", []),
]


def _default_assertions(security_id: str, horizon: str, thesis_version: str) -> list[ThesisAssertion]:
    template = _MID_TEMPLATE if horizon == "MID" else _LONG_TEMPLATE
    out = []
    for ptype, desc, importance, reqs in template:
        out.append(ThesisAssertion(
            security_id=security_id, horizon=horizon, thesis_version=thesis_version,
            proposition_type=ptype, description=desc, importance=importance,
            evidence_requirements=list(reqs)))
    return out


class ResearchBundle(BaseModel):
    """一次研究运行的产物（研究结论包——不是 DecisionPacket：无动作/仓位/执行资格）。"""
    model_config = ConfigDict(extra="allow")

    run_id: str = Field(description="运行指纹（输入内容+实现版本 hash——同输入重跑同 run_id，幂等）")
    security_id: str
    as_of: datetime
    horizons: list[str]
    snapshot_id: str = Field(default="", description="证据快照 id（可复现输入）")
    snapshot_dropped: dict = Field(default_factory=dict, description="快照拒收原因摘要（latest-only 等）")
    source_document_ids: list[str] = Field(default_factory=list)
    verified_claims: list[dict] = Field(default_factory=list, description="FACT_CHECKED 级主张（含引用/摘录）")
    unverified_claims: int = Field(default=0, description="未达 FACT_CHECKED 的主张数（入分母）")
    factors: dict = Field(default_factory=dict, description="能力ID → FactorResult 摘要")
    assessments: dict = Field(default_factory=dict, description="horizon → ThesisAssessment")
    plan_drafts: list[dict] = Field(default_factory=list, description="各周期草稿（HorizonPlan dump——未激活）")
    gaps: list[str] = Field(default_factory=list, description="具体缺口（人话）")
    next_checks: list[str] = Field(default_factory=list)
    cost_summary: dict = Field(default_factory=dict, description="步骤调用/缓存命中计数")


def _content_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()[:16]


class ResearchService:
    """研究链编排（依赖注入；步骤缓存幂等）。"""

    def __init__(self):
        self._step_cache: dict[str, object] = {}
        self.stats = {"bundle_runs": 0, "bundle_cache_hits": 0}

    def run(self, security_id: str, *, as_of: datetime,
            horizons: tuple[str, ...] = ("MID", "LONG"),
            evidence_records: list, documents: Optional[list] = None,
            factor_capabilities: Optional[dict] = None,
            snapshot_builder: Optional[Callable] = None) -> ResearchBundle:
        """一次单股研究（同步、确定性）。

        evidence_records：EvidenceRecord 列表（caller 从 research_store/采集管线取得）；
        documents：list[dict]，每项 {"claim": ClaimRecord, "documents": [SourceDocument|旧池条目]}
            ——待核验主张及其核验池；顶层裸 SourceDocument 按「仅来源文档」归档；
        factor_capabilities：{能力ID: FactorResult}（R4 计算函数产出——服务不自行算因子）；
        snapshot_builder：默认 EvidenceSnapshot.build（strict——注入便于测试）。
        """
        if getattr(as_of, "tzinfo", None) is None:
            raise ValueError("as_of 必须带时区（naive 拒收）")
        factor_capabilities = factor_capabilities or {}
        def _pool_hash(p):
            """核验池条目指纹（SourceDocument 用其 content_hash 字段；dict 直接哈希）。"""
            if isinstance(p, SourceDocument):
                return p.content_hash
            return _content_hash(p)

        run_key = _content_hash({
            "v": RESEARCH_SERVICE_VERSION, "security_id": security_id,
            "as_of": as_of.isoformat(), "horizons": list(horizons),
            "records": [r.content_fingerprint() for r in evidence_records],
            "documents": [_content_hash(d.model_dump(mode="json"))
                          if isinstance(d, SourceDocument)
                          else _content_hash({"c": d["claim"].content_fingerprint(),
                                              "pool": [_pool_hash(p)
                                                       for p in (d.get("documents") or [])]})
                          for d in (documents or [])],
            "factors": {k: _content_hash({"id": k, "v": getattr(v, "value", None),
                                          "s": getattr(v, "status", None)})
                        for k, v in factor_capabilities.items()},
        })
        run_id = f"run_{run_key}"
        cached = self._step_cache.get(f"bundle:{run_key}")
        if cached is not None:
            self.stats["bundle_cache_hits"] += 1
            return cached  # 分析重试幂等：同输入直接返回同 bundle
        self.stats["bundle_runs"] += 1

        # ① snapshot（strict PIT 资格——R1 统一资格算法）
        if snapshot_builder is None:
            from src.data.research_snapshot import EvidenceSnapshot
            snapshot_builder = EvidenceSnapshot.build
        snap = snapshot_builder(security_id, as_of, list(evidence_records), strict=True)

        # ② extract + ③ verify（claim 分级核验——R2 cv2）
        # 顶层裸 SourceDocument = 仅来源文档（无主张）——归档 id，不参与核验
        source_doc_ids = [d.canonical_uri for d in (documents or [])
                          if isinstance(d, SourceDocument)]
        verified, unverified_count = [], 0
        for d in (documents or []):
            if isinstance(d, SourceDocument):
                continue
            res = verify_claim_tiered(
                d["claim"], d.get("documents") or [], as_of=as_of)
            for p in (d.get("documents") or []):
                if isinstance(p, SourceDocument) and p.canonical_uri not in source_doc_ids:
                    source_doc_ids.append(p.canonical_uri)
            if res.level is VerificationLevel.FACT_CHECKED:
                verified.append({"claim": res.claim.model_dump(mode="json"),
                                 "quote_text": res.claim.quote_text,
                                 "citation_uri": res.claim.citation_uri})
            else:
                unverified_count += 1

        # ④ assess（命题级评估——共享事实、分别评估；next_checks 按周期隔离——
        # 监督员 P1：共享可变列表会让 MID 待办串进 LONG 评估）
        verified_ids = {c["claim"]["claim_id"] for c in verified}
        assessments: dict[str, ThesisAssessment] = {}
        gaps: list[str] = []
        next_checks: list[str] = []
        for horizon in horizons:
            horizon_checks: list[str] = []
            assertions = _default_assertions(security_id, horizon, run_id)
            evaluated: list[ThesisAssertion] = []
            for a in assertions:
                # 共享事实：已核验主张按 event_type 挂到两周期的相关命题（各自独立求值）
                supporting = [c["claim"]["claim_id"] for c in verified
                              if _claim_supports(c["claim"], a.proposition_type)]
                a2 = a.model_copy(update={"supporting_evidence_ids": supporting}) if supporting else a
                evaluated.append(evaluate_assertion(
                    a2, available_capabilities=factor_capabilities,
                    verified_evidence_ids=verified_ids, as_of=as_of))
            missing_reqs = sorted({req for a in evaluated
                                   for req in a.evidence_requirements
                                   if a.evaluation is not AssertionEvaluation.TRUE
                                   and req not in factor_capabilities})
            for req in missing_reqs:
                gaps.append(f"[{horizon}] 缺能力/证据: {req}（命题无法建立——不强行 VALID）")
            if not verified:
                horizon_checks.append(f"[{horizon}] 待核验事实——自动研究需来源文档或人工通道")
            unresolved = [a.description for a in evaluated
                          if a.importance == "required"
                          and a.evaluation is not AssertionEvaluation.TRUE]
            status = assess_thesis_by_assertions(
                evaluated, invalidation_value=None, verified_counter_evidence=False)
            assessments[horizon] = ThesisAssessment(
                thesis_id=f"thesis_{security_id}_{horizon}", horizon=horizon,
                snapshot_id=snap.snapshot_id, status=status,
                required_assertions=[a for a in evaluated if a.importance == "required"],
                supporting_assertions=[a for a in evaluated if a.importance == "supporting"],
                unresolved_gaps=unresolved,
                material_counter_evidence=[],
                next_checks=list(horizon_checks),
                method_version=ASSERTION_METHOD_VERSION,
                evaluated_as_of=as_of)
            next_checks.extend(horizon_checks)

        # ⑤ draft（系统草稿——未激活；不带 --facts 也能生成）
        drafts = []
        for horizon in horizons:
            asm = assessments[horizon]
            plan = _draft_plan(security_id, horizon, run_id, asm, verified, snap)
            drafts.append(plan)

        bundle = ResearchBundle(
            run_id=run_id, security_id=security_id, as_of=as_of,
            horizons=list(horizons), snapshot_id=snap.snapshot_id,
            snapshot_dropped=dict(snap.drop_reasons),
            source_document_ids=source_doc_ids,
            verified_claims=verified, unverified_claims=unverified_count,
            factors={k: {"value": getattr(v, "value", None), "status": getattr(v, "status", None),
                         "note": getattr(v, "note", "")}
                     for k, v in factor_capabilities.items()},
            assessments={h: a.model_dump(mode="json") for h, a in assessments.items()},
            plan_drafts=drafts, gaps=gaps, next_checks=next_checks,
            cost_summary={"bundle_runs": self.stats["bundle_runs"],
                          "bundle_cache_hits": self.stats["bundle_cache_hits"],
                          "cache_note": "bundle 级幂等缓存；步骤级增量缓存随 R8 接线",
                          "claims_verified": len(verified),
                          "claims_unverified": unverified_count})
        self._step_cache[f"bundle:{run_key}"] = bundle
        return bundle


def _claim_supports(claim: dict, proposition_type: str) -> bool:
    """已核验主张与命题的**确定性**关联（不解释语义——按事件类型粗分类，
    精细关联属研究推论，由 R8 界面/R7 评测口径细化）。"""
    et = claim.get("event_type") or ""
    if proposition_type in ("real_exposure", "change_to_profit", "moat"):
        return et in ("order", "exposure", "earnings", "forecast")
    if proposition_type == "cash_sustainability":
        return et in ("earnings", "forecast")
    return False


def _draft_plan(security_id: str, horizon: str, run_id: str,
                asm: ThesisAssessment, verified: list[dict], snap) -> dict:
    """系统草稿（HorizonPlan 兼容 dict——未激活；用户选择意图后才 accept）。
    facts_observed=已核验主张 statement；fact_evidence_refs=statement→claim_id
    （R0 资格门的正式数据来源——无核验引用的文本不作为逻辑成立依据）。"""
    from src.core.decision_policy import POLICY_ID_LONG, POLICY_ID_MID
    facts = []
    refs: dict[str, list[str]] = {}
    for c in verified:
        st = c["claim"]["statement"]
        if st not in refs:
            facts.append(st)
            refs[st] = [c["claim"]["claim_id"]]
        else:
            refs[st].append(c["claim"]["claim_id"])
    gap_lines = [f"[{horizon}] {g}" for g in asm.unresolved_gaps]
    return {
        "plan_id": f"draft_{security_id}_{horizon}_{run_id}",
        "security_id": security_id,
        "accepted_at": None,  # 系统草稿永远未激活——用户选择意图后 accept
        "horizon": horizon,
        "policy_id": POLICY_ID_MID if horizon == "MID" else POLICY_ID_LONG,
        "intent": f"[系统草稿 {asm.status.value}] 命题评估见 assessment；缺口 {len(asm.unresolved_gaps)} 项",
        "thesis_id": asm.thesis_id,
        "assessment_id": f"{asm.horizon}:{asm.snapshot_id}",
        "snapshot_id": asm.snapshot_id,
        "policy_version": RESEARCH_SERVICE_VERSION,
        "supersedes_ref": None,
        "required_evidence_refs": sorted({req for a in asm.required_assertions
                                          for req in a.evidence_requirements}),
        "facts_observed": facts,           # extra 字段（F5 起影子消费；R3 起带 refs）
        "fact_evidence_refs": refs,        # 正式化：statement → claim_id（R0 资格门数据源）
        "review_triggers": [f"研究缺口复核: {g}" for g in gap_lines[:3]],
        "gaps": gap_lines,
        "next_checks": asm.next_checks,
    }
