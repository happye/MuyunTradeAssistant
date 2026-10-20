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

RESEARCH_APPLICATION_VERSION = "g3.research_application_v2"

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
    verified_claim_ids: list[str] = Field(
        default_factory=list,
        description="已核验（FACT_CHECKED）主张 ID——稳定证据引用，公开入口 --ref 可绑定"
                    "（L2/V4：用户能查看稳定证据ID；同输入重放不换引用）")
    source_count: int = 0
    source_uris: list[str] = Field(default_factory=list, description="来源文档 URI（J3 验收「来源跳转真实可达」）")
    account_version: str = Field(default="", description="账户账本内容版本（调用方传入——J3 服务输入契约）")
    financial_observations: Optional[dict] = None


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

    def get_cash_observations(self, security_id: str, *, as_of: datetime) -> dict:
        """Read existing archive only; never fetch, evaluate, write or infer industry."""
        from src.core.cash_material_intake import read_materials
        from src.core.cash_numeric_verification import read_proofs
        view = _cash_archive_view(self.store, security_id, as_of=as_of)
        context = {}
        materials = read_materials(self.store, str(security_id), as_of=as_of, _verified_context=context)
        view["cash_materials"] = materials
        proofs = read_proofs(self.store, str(security_id), as_of=as_of, _verified_context=context)
        view["cash_numeric_proofs"] = proofs
        from src.core.cash_source_qualification import read_source_checks
        source_checks = read_source_checks(self.store, str(security_id), as_of=as_of, _verified_context=context)
        view["cash_source_checks"] = source_checks
        if source_checks["source_checks"]:
            view["summary"] += "；留有官方字段核定历史记录，本次只读未重放"
        if source_checks["read_errors"]:
            view["summary"] += "；官方字段核定历史记录或依赖不完整，待重新取证"
        for proof in proofs["proofs"]:
            view["summary"] += "；" + proof["summary"] + "（" + proof["proof_id"] + "）"
        if proofs["read_errors"]:
            view["summary"] += "；本地数值核对记录读取不完整，待重新核对"
        if materials["receipts"]:
            view["summary"] += f"；本地现金材料回执{len(materials['receipts'])}条，均已归档待核"
            failures = sorted({r["extraction_explanation"] for r in materials["receipts"] if r.get("extraction_explanation")})
            if failures:
                view["summary"] += "；" + "；".join(failures)
        if materials["conflicts"]:
            view["summary"] += "；同期间字段存在冲突，两份材料均保留待核"
        if materials["read_errors"]:
            view["summary"] += "；现金材料读取不完整，待核对原件"
        return view

    def intake_cash_materials(self, security_id: str, manifest_path, *, as_of: datetime,
                              extractor=None) -> dict:
        """Separate intake branch; never invoke assessments, factors or drafts."""
        from src.core.cash_material_intake import intake
        return intake(self.store, security_id, manifest_path, as_of=as_of, extractor=extractor)

    def verify_cash_material(self, security_id: str, material_id: str, *, as_of: datetime,
                             extractor=None) -> dict:
        """Independent local arithmetic only; no assessments, facts or drafts."""
        from src.core.cash_numeric_verification import verify
        return verify(self.store, security_id, material_id, as_of=as_of, extractor=extractor)

    def check_cash_source(self, security_id: str, material_id: str, *, requested_as_of=None,
                          fetcher=None, extractor=None, industry_check=False, industry_fetcher=None) -> dict:
        """Explicit current source branch, independent of research run and its cutoff."""
        from src.core.cash_source_qualification import check_source
        return check_source(self.store, security_id, material_id, requested_as_of=requested_as_of,
                            fetcher=fetcher, extractor=extractor, industry_check=industry_check,
                            industry_fetcher=industry_fetcher)

    def get_daily_view(self, security_id: str, *, as_of: datetime) -> dict:
        """Consume local plan/assessment state without executing the research loop.

        No account, quote, archive, fetch or write channel is used. Candidates are
        independent display records and never replace the accepted main slot.
        """
        from src.core.decision_policy import HorizonPlan
        from src.core.research_service import RESEARCH_SERVICE_VERSION
        from src.core.shadow_diff import _load_verified_assessment

        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be an independent timezone-aware datetime")
        sid = str(security_id).strip()
        status, data, root_gaps = self.plans_store.read_snapshot()
        view = {"security_id": sid, "as_of": as_of.isoformat(),
                "store_status": status, "horizons": {}, "next_steps": list(root_gaps)}
        if status in ("CORRUPT", "UNREADABLE"):
            return view

        def describe(raw, horizon, ref=None, *, present=False, ref_present=False):
            entry = {"has_plan": present, "accepted": False,
                     "accepted_ref": ref, "assessment_status": "UNESTABLISHED",
                     "assessment_gaps": [], "gaps": []}
            if raw is None:
                if present:
                    entry["assessment_status"] = "REVIEW_REQUIRED"
                    entry["gaps"].append("计划槽存在但记录为空，待核对修复")
                if ref_present:
                    entry["gaps"].append("接受引用没有对应主计划")
                return entry
            try:
                plan = HorizonPlan.model_validate(raw)
                if plan.security_id != sid or plan.horizon.value != horizon:
                    raise ValueError("slot identity mismatch")
            except Exception:
                entry["assessment_status"] = "REVIEW_REQUIRED"
                entry["gaps"].append("计划记录损坏或证券/周期与槽位错配")
                return entry
            entry.update(plan_id=plan.plan_id, revision=plan.revision,
                         content_hash=plan.content_hash(), snapshot_id=plan.snapshot_id,
                         assessment_id=plan.assessment_id, activated=plan.activated)
            if isinstance(ref, dict):
                entry["accepted"] = (ref.get("plan_id") == plan.plan_id
                                     and ref.get("revision") == plan.revision
                                     and ref.get("content_hash") == plan.content_hash())
                if not entry["accepted"]:
                    entry["gaps"].append("接受引用与计划精确版本不一致")
            elif ref_present:
                entry["gaps"].append("接受引用记录损坏")
            asm = _load_verified_assessment(
                plan, horizon, sid, self.assessment_store,
                as_of=as_of, gaps=entry["assessment_gaps"])
            if asm is not None:
                entry["assessment_status"] = asm.status.value
                entry["assessment_gaps"].extend(asm.unresolved_gaps)
                entry["next_checks"] = list(asm.next_checks)
                entry["checkpoint_results"] = (
                    [r.model_dump(mode="json") for r in asm.checkpoint_results]
                    if asm.checkpoint_results is not None else None)
            else:
                entry["assessment_status"] = "REVIEW_REQUIRED"
            if plan.policy_version != RESEARCH_SERVICE_VERSION:
                entry["version_note"] = "生成版本非当期已验证组合"
                entry["assessment_status"] = "REVIEW_REQUIRED"
                entry["assessment_gaps"].append("生成版本非当期已验证组合，待复核")
            return entry

        for horizon in ("MID", "LONG"):
            key = self.plans_store._key(sid, horizon)
            plans, refs, candidates = (data.get(name, {}) for name in ("plans", "accepted_refs", "candidates"))
            raw, ref, cand_raw = plans.get(key), refs.get(key), candidates.get(key)
            entry = describe(raw, horizon, ref, present=key in plans, ref_present=key in refs)
            entry["candidate_available"] = key in candidates
            entry["candidate"] = describe(cand_raw, horizon, present=True) if key in candidates else None
            if entry["gaps"] or (entry["candidate"] and entry["candidate"]["gaps"]):
                view["store_status"] = "CORRUPT"
                view["next_steps"].append(f"{horizon} 记录缺口，请人工核对修复；另一周期仍独立展示")
            elif key not in plans and key not in candidates:
                view["next_steps"].append(
                    f"{horizon} 无研究记录：research {sid} --claims <主张文件> 建立研究")
            elif raw is not None and not entry["accepted"]:
                view["next_steps"].append(f"{horizon} 草稿未接受：plan2 accept {sid} {horizon.lower()}")
            if raw is not None and entry["assessment_status"] != "VALID":
                view["next_steps"].append(f"{horizon} 逻辑状态 {entry['assessment_status']}，重跑 research {sid} 复核")
            if key in candidates:
                view["next_steps"].append(f"{horizon} 存在候选草稿，未接受不替换主计划；plan2 diff/accept 显式处理")
            view["next_steps"].extend(f"{horizon} 核验节点：{check}" for check in entry.get("next_checks", []))
            view["horizons"][horizon] = entry
        return view

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

        try:
            financial_observations = self.get_cash_observations(security_id, as_of=as_of)
        except Exception:
            financial_observations = _cash_read_failure(security_id, as_of)

        return ResearchRunResult(
            security_id=security_id, as_of=as_of, run_id=bundle.run_id,
            snapshot_id=snap.snapshot_id,
            snapshot_dropped=dict(snap.drop_reasons),
            assessments={h: {"status": a["status"],
                             "gaps": list(a["unresolved_gaps"]),
                             "checkpoint_results": a.get("checkpoint_results")}
                         for h, a in bundle.assessments.items()},
            draft_plan_ids=draft_ids, draft_revisions=revisions, draft_kinds=draft_kinds,
            assessment_ids=assessment_ids,
            factors={fid: {"value": getattr(res, "value", None),
                           "status": getattr(res, "status", None)}
                     for fid, res in caps.items()},
            steps=steps, gaps=list(bundle.gaps), next_checks=list(bundle.next_checks),
            verified_claims=len(bundle.verified_claims),
            unverified_claims=bundle.unverified_claims,
            verified_claim_ids=sorted({c["claim"]["claim_id"]
                                       for c in bundle.verified_claims}),
            source_count=len(bundle.source_document_ids),
            source_uris=list(dict.fromkeys(
                list(bundle.source_document_ids) + claim_doc_uris)),
            account_version=str(account_version or ""),
            financial_observations=financial_observations,
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


CASH_DISPLAY_VERSION = "g4.archive_display.v1"
_CASH_METRICS = ("CFOToNP", "netProfit", "roeAvg")


def _cash_read_failure(security_id, as_of):
    return {"display_version": CASH_DISPLAY_VERSION, "security_id": str(security_id),
            "as_of": as_of.isoformat(), "status": "UNREADABLE", "incomplete": True,
            "coverage_complete": False, "cash_sustainability": "UNKNOWN",
            "gaps": ["现金资料读取失败，请核对归档索引与原件"],
            "summary": "现金资料：读取失败，资料不完整，待核对归档索引与原件；仅资料观察，不改变研究评估"}


def _cash_archive_view(store, security_id, *, as_of):
    """Separate inventory and qualification; no unqualified values cross this view."""
    import hashlib
    import json
    import re
    from collections import defaultdict
    from datetime import date
    from src.core.cash_observations import observe_cash
    from src.data.research_snapshot import EvidenceRecord, EvidenceSnapshot
    from src.data.research_store import RAW_SCHEMA_VERSION

    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be an independent timezone-aware datetime")
    sid = str(security_id).strip()

    def digest(value):
        raw = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def declared_date(value):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("invalid report date")
        return date.fromisoformat(value)

    def declared_time(value):
        # Day-only source dates remain day declarations, never manufactured instants.
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return declared_date(value), None
        if not isinstance(value, str):
            raise ValueError("missing source time")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError("source time has no timezone")
        return dt.date(), dt

    try:
        rows = store.query_observations(security_id=sid)  # exactly one index query
        if not isinstance(rows, list):
            raise ValueError("unreadable index result")
    except Exception:
        return _cash_read_failure(sid, as_of)

    references, records, raw_cache = {}, {}, {}
    signatures = defaultdict(set)
    groups = defaultdict(list)
    unreadable = False
    metadata_gaps = set()
    for row in rows:
        if not isinstance(row, dict):
            unreadable = True
            continue
        metric = row.get("metric")
        if metric not in _CASH_METRICS:
            continue
        raw_id = row.get("raw_sha256")
        # Deliberate allowlist: never copy index value/raw_value or raw payload extras.
        ref = {"metric": metric, "raw_sha256": raw_id if isinstance(raw_id, str) else "",
               "period_end": row.get("period_end") if isinstance(row.get("period_end"), str) else None,
               "source_version": row.get("source_version") if isinstance(row.get("source_version"), str) else "",
               "status": "QUARANTINED", "gaps": []}
        issues = ref["gaps"]
        content = None
        if row.get("security_id") != sid:
            issues.append("index_subject_mismatch")
        if not isinstance(raw_id, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", raw_id):
            issues.append("invalid_raw_digest")
        else:
            if raw_id not in raw_cache:
                try:
                    raw_cache[raw_id] = store.load_raw(raw_id)
                except Exception:
                    raw_cache[raw_id] = None
            payload = raw_cache[raw_id]
            if payload is None:
                issues.append("raw_missing_or_unreadable")
                unreadable = True
            elif (not isinstance(payload, dict) or type(payload.get("schema_version")) is not int
                  or payload.get("schema_version") != RAW_SCHEMA_VERSION
                  or payload.get("sha256") != raw_id or "content" not in payload
                  or not isinstance(payload.get("metadata"), dict)
                  or not isinstance(payload.get("archived_at"), str)):
                issues.append("invalid_raw_schema_or_digest")
            elif digest(payload["content"]) != raw_id:
                issues.append("raw_content_hash_mismatch")
            elif not isinstance(payload["content"], dict):
                issues.append("unsupported_raw_content")
            else:
                content = payload["content"]
                try:
                    _, archive_instant = declared_time(payload["archived_at"])
                    if archive_instant is None:
                        raise ValueError("archive timestamp must be an instant")
                except ValueError:
                    issues.append("invalid_raw_archive_timestamp")
                for key in ("security_id", "metric", "period_end", "source_version"):
                    if not content.get(key) or content.get(key) != row.get(key):
                        issues.append(f"raw_index_{key}_mismatch")
                if content.get("security_id") != sid:
                    issues.append("raw_subject_mismatch")
                # Metadata declarations, when present, cannot contradict the original.
                meta = payload["metadata"]
                for key, raw_key in (("stock_code", "security_id"), ("security_id", "security_id"),
                                     ("metric", "metric"), ("period_end", "period_end"),
                                     ("source_version", "source_version")):
                    if key in meta and meta[key] != content.get(raw_key):
                        issues.append(f"raw_metadata_{key}_mismatch")
                ref.update({key: content.get(key) for key in (
                    "unit", "period_start", "period_kind", "period_basis", "value_kind",
                    "underlying_period_basis", "metric_definition_version", "semantic_status",
                    "knowledge_basis", "published_at", "source_uri")
                    if isinstance(content.get(key), str)})
                ref["declarations"] = "日期、单位、来源均为归档声明，待核"
                try:
                    if declared_date(content.get("period_end")) > as_of.date():
                        issues.append("reporting_period_after_as_of")
                except ValueError:
                    issues.append("invalid_reporting_period")
                time_values = {"observed_at": row.get("observed_at"),
                               "queried_at": meta.get("queried_at")}
                time_values.update({key: content.get(key) for key in (
                    "published_at", "available_at", "first_seen_at", "version_available_at", "fetched_at")})
                parsed_times = {}
                collection_keys = {"queried_at", "observed_at", "fetched_at"}
                for key, value in time_values.items():
                    if value in (None, ""):
                        continue
                    try:
                        day, instant = declared_time(value)
                        after = day > as_of.date() or (instant is not None and instant > as_of)
                        if key in collection_keys:
                            ref.setdefault("collection_times", {})[key] = [value]
                            if after:
                                ref["collection_note"] = "采集晚于分析起点，仅为采集凭据，不证明历史可得性"
                        else:
                            ref[key] = value
                        if after and key not in collection_keys:
                            issues.append(f"{key}_after_as_of")
                        if instant is not None:
                            parsed_times[key] = instant
                        elif day == as_of.date():
                            metadata_gaps.add("来源同日时序未核定")
                    except ValueError:
                        issues.append(f"invalid_{key}")
                missing = []
                for key in ("period_start", "metric_definition_version", "source_uri", "period_basis", "value_kind"):
                    if not content.get(key) or content.get(key) == "UNKNOWN":
                        missing.append(key)
                if content.get("semantic_status") != "VERIFIED":
                    missing.append("VERIFIED_field_semantics")
                if not parsed_times.get("available_at"):
                    missing.append("trusted_available_at")
                ref["metadata_missing"] = sorted(missing)
                if missing:
                    metadata_gaps.add("期间起点、字段口径或单位语义尚待原件核定")
                if content.get("knowledge_basis") not in ("AS_PUBLISHED_ARCHIVE", "CONTEMPORANEOUS_CAPTURE"):
                    metadata_gaps.add("归档版本与历史可得性未证明，latest-only不构成历史证据")
                if not issues:
                    try:
                        data = {key: content[key] for key in EvidenceRecord.model_fields if key in content}
                        data.update(evidence_id="raw_" + raw_id, source_kind="financial",
                                    metric_or_claim=metric, source_document_hash=raw_id)
                        # No wall-clock reconstruction and no request-subject backfill.
                        for key in ("published_at", "available_at", "first_seen_at", "version_available_at", "fetched_at"):
                            data[key] = parsed_times.get(key)
                        if "period_start" in content:
                            data["period_start"] = content["period_start"]
                        rec = EvidenceRecord(**data)
                        records[raw_id] = rec
                        ref["status"] = "ARCHIVED_DECLARATION"
                        key = (metric, content["period_end"])
                        # All source/version disagreements stay visible; never last-winner.
                        signatures[key].add(digest((content, row.get("value"), row.get("unit"),
                                                    row.get("semantic_status"))))
                        groups[key].append(raw_id)
                    except Exception:
                        issues.append("invalid_evidence_metadata")
        ref["gaps"] = sorted(set(issues))
        references[digest(ref)] = ref

    for key, versions in signatures.items():
        if len(versions) > 1:
            for ref in references.values():
                if (ref["metric"], ref["period_end"]) == key:
                    ref["status"] = "QUARANTINED"
                    ref["gaps"] = sorted(set(ref["gaps"] + ["conflicting_field_or_source_versions"]))
            for raw_id in groups[key]:
                records.pop(raw_id, None)
    # Multiple collection events remain metadata of one archived field, not extra
    # financial observations. Union their times; input order never selects a winner.
    merged = {}
    for ref in sorted(references.values(), key=digest):
        identity = (ref["metric"], ref["period_end"], ref["source_version"], ref["raw_sha256"])
        if identity not in merged:
            merged[identity] = ref
            continue
        existing = merged[identity]
        for key, values in ref.get("collection_times", {}).items():
            times = existing.setdefault("collection_times", {}).setdefault(key, [])
            existing["collection_times"][key] = sorted(set(times + values))
        if ref.get("collection_note"):
            existing["collection_note"] = ref["collection_note"]
        existing["gaps"] = sorted(set(existing["gaps"] + ref["gaps"]))
        if ref["status"] == "QUARANTINED":
            existing["status"] = "QUARANTINED"
    refs = sorted(merged.values(), key=lambda r: (r["metric"], str(r["period_end"]), r["raw_sha256"], digest(r)))
    # Any rejected alias of a digest cannot leave a good alias in the display snapshot.
    rejected = {r["raw_sha256"] for r in refs if r["gaps"]}
    for ref in refs:
        if ref["raw_sha256"] in rejected and ref["status"] != "QUARANTINED":
            ref["status"] = "QUARANTINED"
            ref["gaps"].append("conflicting_index_alias")
    snap = EvidenceSnapshot.build(sid, as_of,
        [r for key, r in sorted(records.items()) if key not in rejected], strict=False)
    observations = observe_cash(snap, industry_premise=None, mode="current")
    counts = {m: sum(r["metric"] == m and r["status"] == "ARCHIVED_DECLARATION" for r in refs)
              for m in _CASH_METRICS}
    periods = sorted({r["period_end"] for r in refs if r["status"] == "ARCHIVED_DECLARATION"})
    quarantined = sum(r["status"] == "QUARANTINED" for r in refs)
    metadata_gaps.add("行业资格未核定，生产端没有行业核验器")
    status = "UNREADABLE" if unreadable else ("INCOMPLETE" if quarantined else
             "READABLE_ARCHIVE" if any(counts.values()) else "NO_READABLE_OBSERVATIONS")
    if refs:
        summary = (f"现金资料：CFOToNP {counts['CFOToNP']}条/净利润{counts['netProfit']}条/ROE {counts['roeAvg']}条，"
                   f"报告期 {','.join(periods) or '未核定'}（本次可读资料）；可比期0（行业/期间/口径待核）；"
                   f"隔离{quarantined}条；日期、单位为归档声明，待核；独立资料快照，与研究评估快照分别建立；")
        if unreadable:
            summary += "部分留样缺失或读取失败，资料不完整；"
        raw_refs = list(dict.fromkeys(r["raw_sha256"] for r in refs
            if re.fullmatch(r"[0-9a-fA-F]{64}", r["raw_sha256"])))
        if raw_refs:
            summary += "归档留样编号（SHA256前16位）：" + "、".join(r[:16] for r in raw_refs[:2]) + "；"
            if len(raw_refs) > 2:
                summary += f"其余{len(raw_refs) - 2}份及完整引用见 research --json；"
            else:
                summary += "完整引用见 research --json；"
        else:
            summary += "归档留样编号缺失或非法，待补核；"
        reason_groups = set()
        for reason in {reason for ref in refs for reason in ref["gaps"]}:
            if reason == "raw_content_hash_mismatch":
                label = "留样内容与哈希不符"
            elif "security_id" in reason or "subject" in reason or "stock_code" in reason:
                label = "证券不一致或缺失"
            elif reason == "raw_missing_or_unreadable":
                label = "留样缺失或读取失败"
            elif reason in {"invalid_raw_digest", "invalid_raw_schema_or_digest"}:
                label = "留样编号或归档格式无效"
            elif "conflicting" in reason:
                label = "同字段留样或来源版本冲突"
            elif "period" in reason:
                label = "报告期间不一致、缺失或无效"
            elif reason.endswith("_after_as_of"):
                label = "公开或有效时点晚于分析截止"
            elif reason.startswith("invalid_") and ("_at" in reason or "timestamp" in reason):
                label = "时间声明无效"
            elif reason.startswith("raw_index_") or reason.startswith("raw_metadata_"):
                label = "来源版本或字段身份不一致"
            else:
                label = "字段元数据未通过核对"
            reason_groups.add(label)
        if reason_groups:
            labels = sorted(reason_groups)
            summary += "隔离原因：" + "、".join(labels[:3]) + "；"
            if len(labels) > 3:
                summary += f"其余{len(labels) - 3}类原因见 research --json；"
        summary += "下一步核对留样对应财报的期间起点、现金字段口径及行业依据；"
    else:
        summary = "现金资料：本次无可读现金字段；" if not unreadable else "现金资料：读取失败，资料不完整；"
    summary += "仅资料观察，不改变研究评估"
    view = {"display_version": CASH_DISPLAY_VERSION, "security_id": sid, "as_of": as_of.isoformat(),
            "status": status, "coverage_complete": False, "coverage_scope": "本次可读索引",
            "incomplete": True, "inventory": {"counts": counts, "report_periods": periods,
                "references": refs, "quarantined_count": quarantined},
            "qualification": {"industry_status": "UNKNOWN", "comparable_periods": 0,
                "gaps": sorted(metadata_gaps), "algorithm_gaps": list(observations.gaps)},
            "cash_sustainability": "UNKNOWN", "observations_only": True,
            "display_snapshot_id": snap.snapshot_id,
            "snapshot_scope": "独立资料展示快照，与本次研究评估快照分别建立",
            "summary": summary}
    # Capturing the same original again does not change its display identity.
    identity_refs = [{key: value for key, value in ref.items()
                      if key not in {"collection_times", "collection_note"}} for ref in refs]
    view["display_id"] = digest((CASH_DISPLAY_VERSION, sid, as_of.isoformat(),
        snap.snapshot_id, status, identity_refs, sorted(metadata_gaps)))
    return view
