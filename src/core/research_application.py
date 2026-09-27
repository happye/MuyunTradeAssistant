"""研究应用服务（plan/fusion iteration3 J3，DELIVERY_PLAN「把research接成可持续使用的研究链」）。

把 `l` 深分析与 `research` 专家入口共用的研究链落成一个可复用服务：
**归档读取/按需补采 → 提取核验 → 合格因子 → 评估 → 保存草稿**，最后由唯一
适配器生成视图（CLI 渲染 / --json）。

与 ResearchService（纯研究编排，无 IO）的分工：本模块是**应用层**——持 IO
（ResearchStore 归档、HorizonPlanStore 草稿、AssessmentStore 评估真值）与采集
调度（零重复抓取），纯策略继续无 IO（RESEARCH_LOOP §1 原文约束）。

步骤持久化（documents→verified→assessment→draft 四步）：
- documents 步：报告期粒度断点——已归档季度 has_quarter_evidence 跳过（零重复
  抓取，重启即恢复）；提取/评估/草稿为确定性重放（同输入同 run_id，无 AI 成本，
  重算即恢复）——run manifest 落 snapshot manifest（写一次）
- 因子输入由**合格快照派生**（strict=False live 资格视图——SUSPECT 已隔离、
  latest-only 如实保留）：J0b raw dict 旁路在应用层闭合（factor_bindings 绑定
  同一 snapshot_id/security/as_of）
- 草稿真实存入 HorizonPlanStore（K0c/A1 双槽）：同内容重跑幂等跳过；未接受计划
  原地修订（revision+1、accepted_at 置空）；**已接受计划+新材料 → 候选槽**
  （已接受版本与引用保持，plan2 accept 显式切换）；无计划 → 新 draft plan_id

chat/Web/TUI 当前未适配研究入口——显式「未支持」，不冒充（路由测试锁定）。
"""

import logging
from datetime import datetime
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.core.research_service import ResearchService, ResearchBundle

logger = logging.getLogger(__name__)

RESEARCH_APPLICATION_VERSION = "j3.research_application_v1"

# 需要人工/其他入口适配的消费者（J3 验收：显式标未支持——路由测试锁定）
UNSUPPORTED_ENTRYPOINTS = ("chat", "web", "tui")


class ResearchRunResult(BaseModel):
    """一次研究运行的回执（CLI 渲染与 --json 共用同一数据——跨入口一致）。"""
    model_config = ConfigDict(extra="allow")

    security_id: str
    as_of: datetime
    run_id: str
    snapshot_id: str = ""
    snapshot_dropped: dict = Field(default_factory=dict)
    assessments: dict = Field(default_factory=dict, description="horizon → {status, gaps}（与 bundle.assessments 同源）")
    draft_plan_ids: dict = Field(default_factory=dict, description="horizon → plan_id（已存 HorizonPlanStore）")
    draft_revisions: dict = Field(default_factory=dict)
    draft_kinds: dict = Field(default_factory=dict,
                              description="horizon → idempotent/new/updated/candidate"
                                          "（K0c/A1：同内容幂等/新材料入候选槽——回执可见）")
    assessment_ids: dict = Field(default_factory=dict, description="horizon → assessment_id（AssessmentStore）")
    factors: dict = Field(default_factory=dict, description="能力ID → {value, status}（绑定合格快照后）")
    steps: dict = Field(default_factory=dict, description="documents/verified/assessment/draft 步骤计数（断点续跑口径）")
    gaps: list[str] = Field(default_factory=list)
    next_checks: list[str] = Field(default_factory=list)
    verified_claims: int = 0
    unverified_claims: int = 0
    source_count: int = 0
    source_uris: list[str] = Field(default_factory=list, description="来源文档 URI（J3 验收「来源跳转真实可达」）")
    account_version: str = Field(default="", description="账户账本内容版本（调用方传入——J3 服务输入契约）")


class ResearchApplicationService:
    """研究应用服务（IO 编排；纯策略经 ResearchService）。

    - store：ResearchStore（归档/观测索引/manifest——测试传隔离目录）
    - plans_store：HorizonPlanStore（草稿持久化——测试传隔离路径）
    - assessment_store：AssessmentStore（评估唯一真值——J0b 草稿影子一致性前提）
    - fetch_fn(security_id, year, quarter) -> (records, raw_fin_dicts)：采集注入
      （缺省 None=不联网补采，只用归档；CLI --capture 注入真实网络采集）
    """

    def __init__(self, *, store, plans_store, assessment_store,
                 fetch_fn: Optional[Callable] = None, n_quarters: int = 4):
        self.store = store
        self.plans_store = plans_store
        self.assessment_store = assessment_store
        self.fetch_fn = fetch_fn
        self.n_quarters = n_quarters

    def run(self, security_id: str, *, as_of: datetime,
            horizons: tuple[str, ...] = ("MID", "LONG"),
            capture: bool = False, account_version: str = "",
            documents: Optional[list] = None,
            checkpoints: Optional[list] = None) -> ResearchRunResult:
        """K2a 公开研究闭环：documents（主张+核验池）与 checkpoints（检查点）
        是**公开入口的一等入参**——合法正例从公开解析器输入建立（不直调
        ResearchService 注入）。

        documents：list[dict]，每项 {"claim": {...字段...}, "document": {...原文...}}
            ——claim dict 构造 ClaimRecord，document dict 构造 SourceDocument
            （canonical_uri/body/security_ids/published_at——原文 URI/hash 可追溯，
            body 经 ResearchStore.archive_raw 留样）；直传 ClaimRecord/SourceDocument
            亦可（兼容服务层形态）。
        checkpoints：CheckpointCondition 列表（user_confirmed 必须由用户显式给出
            ——接受计划不自动替代风险确认，AI 不能自行确认）。
        """
        from src.core.claim_extraction import ClaimRecord, SourceDocument
        from src.data.financial_data import (
            apply_unit_drift_mapping, capture_quarterly_evidence, select_ended_quarters,
        )
        from src.data.research_snapshot import (
            EvidenceSnapshot, financial_record, screen_semantic_anomalies,
        )
        from src.core.factor_compute import (
            balance_risk_v1, cash_conversion_v1, roe_observed_v1,
        )

        security_id = str(security_id).strip()
        steps: dict = {"documents": {}, "verified": {}, "assessment": {}, "draft": {}}

        # ── K2a：公开主张通道——原文归档留样（URI/hash 可追溯）+ 形态适配 ──
        claim_bundles: list = []
        claim_doc_uris: list[str] = []
        for item in (documents or []):
            if item is None:
                continue
            if isinstance(item, dict) and "claim" in item:
                c_raw, d_raw = item.get("claim") or {}, item.get("document") or {}
                claim = c_raw if isinstance(c_raw, ClaimRecord) else ClaimRecord(**c_raw)
                doc = d_raw if isinstance(d_raw, SourceDocument) else SourceDocument(
                    canonical_uri=str(d_raw.get("canonical_uri") or ""),
                    content_hash=SourceDocument.body_hash(str(d_raw.get("body") or "")),
                    security_ids=[str(s) for s in (d_raw.get("security_ids") or [])],
                    published_at=d_raw.get("published_at"),
                    body=str(d_raw.get("body") or ""),
                    title=str(d_raw.get("title") or ""),
                    is_correction=bool(d_raw.get("is_correction", False)))
                claim.citation_uri = claim.citation_uri or doc.canonical_uri
                claim.citation_hash = claim.citation_hash or doc.content_hash
                # 原文留样（同 hash 幂等——归档是追溯链的落盘点）
                try:
                    if doc.body:
                        self.store.archive_raw(
                            {"kind": "research_document", "uri": doc.canonical_uri,
                             "body": doc.body},
                            metadata={"security_ids": doc.security_ids,
                                      "published_at": doc.published_at.isoformat()
                                      if doc.published_at else ""})
                except Exception as e:
                    logger.warning(f"研究原文留样失败（核验继续，追溯链缺该份）: {e}")
                if doc.canonical_uri and doc.canonical_uri not in claim_doc_uris:
                    claim_doc_uris.append(doc.canonical_uri)
                claim_bundles.append({"claim": claim, "documents": [doc]})
            elif isinstance(item, SourceDocument):
                claim_bundles.append(item)  # 仅来源文档（无主张）——服务层按归档处理
            else:
                logger.warning(f"documents 条目形态非法（跳过）: {type(item).__name__}")

        # ── Step 1 documents：归档读取 + 按需补采（报告期粒度断点）──
        records: list = []
        fetched, skipped, archived_missing = 0, 0, 0
        for y, q in select_ended_quarters(as_of, self.n_quarters):
            if self.store.has_quarter_evidence(security_id, y, q):
                skipped += 1  # 已归档——零重复抓取（重启跳过已成功步骤）
            elif capture and self.fetch_fn is not None:
                try:
                    self.fetch_fn(security_id, y, q)
                    fetched += 1
                except Exception as e:  # 网络失败登记不假装（单季失败不拖垮其余）
                    logger.warning(f"{y}Q{q} 采集失败（登记不替换）: {e}")
            # 统一从归档加载（补采成功与否以归档实况为准——不虚构已采集）
            recs_q = self._load_archived_records(security_id, y, q)
            if recs_q:
                records.extend(recs_q)
            else:
                archived_missing += 1
        records = screen_semantic_anomalies(records)
        steps["documents"] = {
            "quarters_selected": self.n_quarters, "quarters_archived_skipped": skipped,
            "quarters_fetched": fetched, "quarters_missing": archived_missing,
            "evidence_records": len(records),
            "claims_input": len(claim_bundles),  # K2a：公开通道导入的主张数（审查 P3-1）
            "resume": "已归档季度零重复抓取；提取/评估/草稿为确定性重放（同输入同 run_id）",
        }

        # ── Step 2 verified：合格快照派生因子（SUSPECT 已隔离——raw dict 旁路闭合）──
        snap = EvidenceSnapshot.build(security_id, as_of, records, strict=False)
        fin_inputs = [
            {"metric": r.metric_or_claim, "value": r.value, "unit": r.unit,
             "period_end": r.period_end,
             "published_at": r.published_at.isoformat() if r.published_at else "",
             "security_id": r.security_id}
            for r in snap.records
            if r.source_kind == "financial" and isinstance(r.value, (int, float))
        ]
        caps: dict = {}
        for fn in (cash_conversion_v1, roe_observed_v1, balance_risk_v1):
            res = fn(fin_inputs)
            caps[res.factor_id] = res
        bindings = {fid: {"snapshot_id": snap.snapshot_id, "security_id": security_id,
                          "as_of": as_of} for fid in caps}
        steps["verified"] = {
            "factor_inputs": len(fin_inputs),
            "suspect_isolated": len(snap.suspect_ids),
            "factors": {fid: getattr(res, "status", None) for fid, res in caps.items()},
            "factor_bindings": "snapshot_id/security/as_of 绑定（raw dict 旁路已闭合）",
        }

        # ── Step 3 assessment：评估唯一真值持久化（J0b 契约）──
        svc = ResearchService()
        bundle: ResearchBundle = svc.run(
            security_id, as_of=as_of, horizons=horizons,
            evidence_records=records, factor_capabilities=caps,
            factor_bindings=bindings, assessment_store=self.assessment_store,
            documents=claim_bundles or None, checkpoints=checkpoints,
            snapshot_builder=lambda sid, ao, recs, strict=True: snap)
        assessment_ids = {}
        for d in bundle.plan_drafts:
            aid = d.get("assessment_id") or ""
            if aid.startswith("asm_"):
                assessment_ids[d["horizon"]] = aid

        # ── Step 4 draft：草稿真实存入 HorizonPlanStore（可 plan2 找回/接受）──
        # K0c/A1 双槽：draft_kinds 向回执区分 idempotent/new/updated/candidate
        draft_ids, revisions, draft_kinds = {}, {}, {}
        for d in bundle.plan_drafts:
            plan, revision = self._save_draft(d, draft_kinds=draft_kinds)
            if plan is not None:
                draft_ids[d["horizon"]] = plan.plan_id
                revisions[d["horizon"]] = revision
        steps["draft"] = {"plan_ids": draft_ids, "revisions": revisions,
                          "kinds": draft_kinds,
                          "note": "同内容幂等跳过；已接受版本+新材料→候选槽（plan2 accept 切换）"}

        # run manifest（写一次——同 snapshot_id 幂等；断点/审计用）
        manifest = {
            "application_version": RESEARCH_APPLICATION_VERSION,
            "run_id": bundle.run_id, "as_of": as_of.isoformat(),
            "steps": steps,
            "assessment_ids": assessment_ids,
            "snapshot_dropped": dict(snap.drop_reasons),
        }
        try:
            self.store.save_snapshot_manifest(snap.snapshot_id, manifest)
        except Exception as e:
            logger.warning(f"run manifest 落盘失败（不影响研究结论）: {e}")

        return ResearchRunResult(
            security_id=security_id, as_of=as_of, run_id=bundle.run_id,
            snapshot_id=snap.snapshot_id,
            snapshot_dropped=dict(snap.drop_reasons),
            assessments={h: {"status": a["status"],
                             "gaps": list(a["unresolved_gaps"])}
                         for h, a in bundle.assessments.items()},
            draft_plan_ids=draft_ids, draft_revisions=revisions, draft_kinds=draft_kinds,
            assessment_ids=assessment_ids,
            factors={fid: {"value": getattr(res, "value", None),
                           "status": getattr(res, "status", None)}
                     for fid, res in caps.items()},
            steps=steps, gaps=list(bundle.gaps), next_checks=list(bundle.next_checks),
            verified_claims=len(bundle.verified_claims),
            unverified_claims=bundle.unverified_claims,
            source_count=len(bundle.source_document_ids),
            source_uris=list(dict.fromkeys(
                list(bundle.source_document_ids) + claim_doc_uris)),
            account_version=str(account_version or ""),
        )

    def _load_archived_records(self, security_id: str, year: int, quarter: int) -> list:
        """从归档重建证据记录（raw 留样 → 映射 → EvidenceRecord）——归档读不重复抓取。

        knowledge_basis=CONTEMPORANEOUS_CAPTURE 不可用（留样没有当时的 first_seen）——
        如实 latest-only（live 研究可用、strict 历史不入，与实时采集同口径）。"""
        from src.data.financial_data import apply_unit_drift_mapping
        from src.data.research_snapshot import financial_record
        obs = self.store.query_observations(security_id=security_id,
                                            period_end=_quarter_end(year, quarter))
        records: list = []
        for o in obs:
            payload = self.store.load_raw(o.get("raw_sha256") or "")
            if not payload:
                continue  # 留样丢失——观测索引残留按缺失处理（G14 不造假）
            fin = dict(payload.get("content") or {})
            if fin.get("metric") != o.get("metric"):
                continue
            fin = apply_unit_drift_mapping(fin)
            try:
                records.append(financial_record(security_id, fin))
            except Exception as e:
                logger.warning(f"归档证据重建失败（跳过该条）: {o.get('metric')}: {e}")
        return records

    # K0c/A1：草稿内容字段（同内容重跑幂等的比较面；不含 accepted_at/revision——
    # 它们是生命周期状态不是内容）
    _DRAFT_CONTENT_FIELDS = ("intent", "assessment_id", "snapshot_id", "policy_version",
                             "required_evidence_refs", "fact_evidence_refs",
                             "facts_observed", "review_triggers", "gaps",
                             "next_checks", "thesis_id")

    def _save_draft(self, draft: dict, *, draft_kinds: Optional[dict] = None):
        """草稿入 HorizonPlanStore（K0c/A1 双槽语义）：

        - 无现有计划 → 新草稿入主槽（计划生命周期起点，同旧口径）
        - 现有计划 + **同内容**重跑 → 幂等跳过：不写计划、不增 revision、
          不改变接受态（同输入重跑撤销接受=A1 主反例的根因）
        - 现有计划未接受 + 新内容 → 原地修订（revision 由 store.save 递增，
          accepted_at 置空——本来就是草稿，无接受态可保护）
        - 现有计划**已接受** + 新内容 → 只写**候选槽**（save_candidate）：已接受
          版本与 accepted_refs/主意图引用原样保留；用户显式 accept 才原子切换

        返回 (plan, revision)；保存失败 (None, 0)。draft_kinds 登记
        idempotent/new/updated/candidate（回执可见）。"""
        from src.core.decision_policy import HorizonPlan
        horizon = draft["horizon"]
        existing = self.plans_store.get(draft["security_id"], horizon)
        if existing is not None:
            updates = {f: draft[f] for f in self._DRAFT_CONTENT_FIELDS}
            same_content = all(getattr(existing, f, None) == v
                               for f, v in updates.items())
            if same_content:
                logger.info(f"研究内容与现有计划一致（{draft['security_id']} {horizon}）"
                            "——幂等跳过（不撤接受、revision 不变）")
                if draft_kinds is not None:
                    draft_kinds[horizon] = "idempotent"
                return existing, existing.revision
            if existing.accepted_at:
                # 双槽：已接受版本保持 active——新材料只进候选槽，等显式接受
                cand = existing.model_copy(update={
                    **updates, "accepted_at": None,
                    "supersedes_ref": existing.content_hash()})
                if self.plans_store.save_candidate(cand):
                    logger.info(
                        f"新材料产生候选草稿（{draft['security_id']} {horizon}）——"
                        "已接受版本保持 active；plan2 accept 显式切换")
                    if draft_kinds is not None:
                        draft_kinds[horizon] = "candidate"
                    return cand, existing.revision
                logger.warning(f"候选草稿保存被拒（指纹冲突/外部修改）: {cand.plan_id}")
                return None, 0
            plan = existing.model_copy(update={
                **updates, "accepted_at": None,  # 草稿修订不沿用旧接受（R3 契约）
                "supersedes_ref": existing.content_hash(),
                # revision 不在此改——store.save 对同 plan_id 统一 +1
            })
            kind = "updated"
        else:
            plan = HorizonPlan(**draft)
            kind = "new"
        if self.plans_store.save(plan):
            saved = self.plans_store.get(draft["security_id"], horizon)
            # store.save 对同 plan_id 统一 +1——回读落盘后的真实 revision
            #（J5 审查 P1-3：返回自增前的旧值会让 --json/CLI 报错版本号）
            if draft_kinds is not None:
                draft_kinds[horizon] = kind
            return (saved if saved is not None else plan), \
                (saved.revision if saved is not None else plan.revision)
        logger.warning(f"草稿保存被拒（指纹冲突/外部修改）: {plan.plan_id}")
        return None, 0


def _quarter_end(year: int, quarter: int) -> str:
    last_day = {1: "31", 2: "30", 3: "30", 4: "31"}[quarter]
    return f"{year:04d}-{quarter * 3:02d}-{last_day}"
