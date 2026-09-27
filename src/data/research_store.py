"""研究数据最小存储（plan/fusion iteration2 R1，DATA_TRUST §5）。

复用现有 JSON/JSONL + 原子写纪律，首版不引入消息队列/图数据库/分布式工作流：

    <research_dir>/raw/<sha256>.json                 原始响应/正文与采集元数据；写入一次不可覆盖
    <research_dir>/observations/<YYYY-MM-DD>.jsonl   观测索引；引用原件 hash 而非重复大正文
    <research_dir>/snapshots/<snapshot_id>.json      截止时点清单、版本、拒收原因；写一次
    <research_dir>/invalidations.jsonl               数据纠错/源撤销与受影响对象（追加式）

默认目录 ~/.muyun/research/；**测试必须隔离**（构造时传 research_dir，绝不读写真实 HOME）。
写入 crash-safe：先写 .tmp 再 os.replace；raw/snapshots 写一次幂等（同 hash 重复归档
不覆盖原文件——原始响应留样不可篡改，DATA_TRUST §2）。

纠错传播边界：本模块只登记 invalidations 并提供按 metric/security 的检索；
因子/计划/实验的反向影响清单由各层把 affected_refs 追加进来（R3/R4 接线）。
**绝不写 portfolio/持仓**——「已确认成交保持事实」由调用边界保证，本模块无该能力。
"""

import hashlib
import json
import logging
import os
from datetime import date as _date
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

RESEARCH_DIR = Path.home() / ".muyun" / "research"

RAW_SCHEMA_VERSION = 1
INVALIDATION_SCHEMA_VERSION = 1
ASSESSMENT_SCHEMA_VERSION = 1


def _atomic_write_jsonl_append(path: Path, record: dict) -> bool:
    """追加一条 JSONL（行级原子：单行 write；坏行由读侧隔离——G14 纪律）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return True


def _atomic_write_json(path: Path, payload: dict, *, overwrite: bool = False) -> bool:
    """原子替换写；overwrite=False 时已存在即拒绝（写一次语义）。
    tmp 名带进程+随机后缀——并发写同一路径不共享临时文件（审查 P2 防交错 replace）。"""
    if path.exists() and not overwrite:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    import uuid
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8")
    os.replace(str(tmp), str(path))
    return True


class ResearchStore:
    """研究数据存储（raw 归档 / 观测索引 / 快照清单 / 纠错账本）。"""

    def __init__(self, research_dir: Optional[Path] = None):
        self.dir = Path(research_dir) if research_dir else RESEARCH_DIR

    # ── raw 原始留样（写一次不可覆盖，DATA_TRUST §2 链路1）──

    def archive_raw(self, content, metadata: Optional[dict] = None) -> tuple[str, bool]:
        """原始响应/正文归档。返回 (sha256, created)；同内容重复归档幂等不覆盖。

        content：任意可 JSON 序列化对象（dict/str/list）；文件名=内容 sha256
        （与 document_version_id/source_document_hash 同算法，可直接互指）。
        """
        if isinstance(content, str):
            raw = content.encode("utf-8")
        else:
            raw = json.dumps(content, ensure_ascii=False, sort_keys=True,
                             default=str).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        path = self.dir / "raw" / f"{digest}.json"
        payload = {
            "schema_version": RAW_SCHEMA_VERSION,
            "sha256": digest,
            "archived_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "content": content,
            "metadata": dict(metadata or {}),
        }
        created = _atomic_write_json(path, payload, overwrite=False)
        if not created and not path.exists():
            logger.warning(f"原始留样写入失败: {path}")
        return digest, created

    def raw_path(self, digest: str) -> Path:
        return self.dir / "raw" / f"{digest}.json"

    def load_raw(self, digest: str) -> Optional[dict]:
        """按 hash 离线读回原始留样（J1b：相同归档离线可读，不重复抓取）。

        非法 id / 不存在 → None；坏文件隔离告警不崩（G14）。"""
        d = str(digest or "").strip()
        if not d or any(seg in d for seg in ("/", "\\", "..")):
            return None  # 路径注入防御
        path = self.raw_path(d)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"原始留样读取失败（按缺失处理）: {d}: {e}")
            return None

    # ── 观测索引（按日 JSONL，引用原件不重复正文）──

    def append_observation(self, entry: dict, *, day: Optional[_date] = None) -> bool:
        """观测索引追加（security/metric/digest/时点等；调用方组织字段）。"""
        day = day or _date.today()
        record = {"observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                  **entry}
        return _atomic_write_jsonl_append(
            self.dir / "observations" / f"{day.isoformat()}.jsonl", record)

    def query_observations(self, *, security_id: Optional[str] = None,
                           metric: Optional[str] = None,
                           period_end: Optional[str] = None,
                           source_version: Optional[str] = None) -> list[dict]:
        """观测索引查询（J1b 原件索引：按证券/指标/报告期/源版本检索；坏行隔离）。

        证据 hash 经条目 raw_sha256 与 load_raw 互指——query 可复现、原件可读回。"""
        out: list[dict] = []
        obs_dir = self.dir / "observations"
        if not obs_dir.exists():
            return out
        for p in sorted(obs_dir.glob("*.jsonl")):
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 坏行隔离（G14）
                if security_id is not None and r.get("security_id") != security_id:
                    continue
                if metric is not None and r.get("metric") != metric:
                    continue
                if period_end is not None and r.get("period_end") != period_end:
                    continue
                if source_version is not None and r.get("source_version") != source_version:
                    continue
                out.append(r)
        return out

    def has_quarter_evidence(self, security_id: str, year: int, quarter: int) -> bool:
        """该季财务证据是否已归档（J1b 零重复抓取判据——按报告期匹配观测索引）。"""
        last_day = {1: "31", 2: "30", 3: "30", 4: "31"}[quarter]
        period_end = f"{year:04d}-{quarter * 3:02d}-{last_day}"
        return bool(self.query_observations(security_id=str(security_id),
                                            period_end=period_end))

    # ── 发行人原件核对记录（J1b，DATA_DECISION §4 证据条目）──

    def save_original_verification(self, entry: dict) -> dict:
        """发行人原件核对记录（追加式 originals/ 索引；同 证券+报告期+文件hash 幂等）。

        必需字段：security_id/period_end/file_hash（其余按 DATA_DECISION §4 证据条目
        自由登记：合并范围/披露证明/页表定位/原始行值/派生公式/提取方式/复核人）。
        **双供应商一致不自动升核定**——verification_level 只能由人工依据发行人原件
        显式设置；本方法不推断、不覆盖旧记录。"""
        required = ("security_id", "period_end", "file_hash")
        missing = [k for k in required if not str(entry.get(k) or "").strip()]
        if missing:
            raise ValueError(f"原件核对记录缺必需字段: {missing}")
        key = f"{entry['security_id']}:{entry['period_end']}:{entry['file_hash']}"
        rid = "orig_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        record = {
            "schema_version": RAW_SCHEMA_VERSION,
            "verification_id": rid,
            "registered_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **entry,
        }
        path = self.dir / "originals" / f"{rid}.json"
        created = _atomic_write_json(path, record, overwrite=False)
        if not created and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))  # 幂等返回原记录
        return record

    def list_original_verifications(self, *, security_id: Optional[str] = None,
                                    period_end: Optional[str] = None) -> list[dict]:
        """按证券/报告期检索原件核对记录（坏文件隔离；无记录=该范围未核定）。"""
        out: list[dict] = []
        orig_dir = self.dir / "originals"
        if not orig_dir.exists():
            return out
        for p in sorted(orig_dir.glob("*.json")):
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if security_id is not None and r.get("security_id") != security_id:
                continue
            if period_end is not None and r.get("period_end") != period_end:
                continue
            out.append(r)
        return out

    # ── 快照清单（写一次；同 snapshot_id 幂等）──

    def save_snapshot_manifest(self, snapshot_id: str, manifest: dict) -> bool:
        """快照清单落盘（截止时点、版本、拒收原因）；同 id 已存在则幂等跳过。"""
        path = self.dir / "snapshots" / f"{snapshot_id}.json"
        if path.exists():
            return False
        return _atomic_write_json(path, {"snapshot_id": snapshot_id, **manifest},
                                  overwrite=False)

    # ── 纠错/撤销账本（追加式 + 检索；DATA_TRUST §2 链路6）──

    def append_invalidation(self, *, cause_kind: str, security_id: str = "",
                            metric: str = "", period_end: str = "",
                            description: str = "",
                            affected_refs: Optional[list[dict]] = None) -> dict:
        """登记一次数据纠错/源撤销。

        cause_kind：unit_drift / source_retraction / restatement / manual_correction…
        affected_refs：[{"kind": "evidence|factor|assessment|plan|proposal|experiment",
                        "id": ...}]——反向影响清单由各层登记（R3/R4 接线后齐全）。
        返回含 invalidation_id 的完整记录（不自动改写任何受影响对象——撤销资格/
        重算派生由调用方执行；已确认成交保持事实，本模块无权触碰）。
        """
        record = {
            "schema_version": INVALIDATION_SCHEMA_VERSION,
            "invalidation_id": (f"inv_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                                f"_{hashlib.sha256(description.encode('utf-8')).hexdigest()[:8]}"),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "cause_kind": cause_kind,
            "security_id": str(security_id),
            "metric": metric,
            "period_end": period_end,
            "description": description,
            "affected_refs": list(affected_refs or []),
            "status": "open",
        }
        _atomic_write_jsonl_append(self.dir / "invalidations.jsonl", record)
        return record

    def list_invalidations(self, *, security_id: Optional[str] = None,
                           metric: Optional[str] = None,
                           status: Optional[str] = None) -> list[dict]:
        """按证券/指标/状态检索纠错记录（坏行隔离不崩——G14）。"""
        path = self.dir / "invalidations.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if security_id is not None and r.get("security_id") != security_id:
                continue
            if metric is not None and r.get("metric") != metric:
                continue
            if status is not None and r.get("status") != status:
                continue
            out.append(r)
        return out


# ──────────────── J0b：周期研究评估唯一真值存储 ────────────────

class AssessmentStore:
    """ThesisAssessment 不可变存储（DELIVERY_PLAN J0b：一个 Assessment 贯穿研究、
    草稿、影子——shadow 消费同一 status，不再用旧 facts+refs 简化判断重判）。

    - 内容寻址：assessment_id = "asm_" + 评估规范内容 sha256[:16]——同输入幂等、
      内容变即新 id，已存评估**不可变**（写一次语义，不覆盖）
    - 落盘 <research_dir>/assessments/<assessment_id>.json；测试必须传 research_dir
      隔离，绝不读写真实 HOME
    """

    def __init__(self, research_dir: Optional[Path] = None):
        self.dir = Path(research_dir) if research_dir else RESEARCH_DIR

    @staticmethod
    def assessment_id_of(assessment) -> str:
        """评估内容指纹（DELIVERY_PLAN J0 存储合同：security/horizon/snapshot/
        method_version/status/required_assertions/invalidation_results + supporting/
        next_checks——消费合同字段全覆盖；extra="allow" 附加字段不进 id，新增
        消费字段时必须同步本 payload，否则同 id 不同内容会静默保留首写）。"""
        status = getattr(assessment.status, "value", assessment.status)
        payload = {
            "security_id": assessment.security_id,
            "horizon": assessment.horizon,
            "snapshot_id": assessment.snapshot_id,
            "method_version": assessment.method_version,
            "status": status,
            "required_assertions": [a.model_dump(mode="json")
                                    for a in assessment.required_assertions],
            "supporting_assertions": [a.model_dump(mode="json")
                                      for a in assessment.supporting_assertions],
            "invalidation_results": assessment.invalidation_results,
            "next_checks": list(assessment.next_checks),
            "evaluated_as_of": (assessment.evaluated_as_of.isoformat()
                                if assessment.evaluated_as_of else ""),
        }
        return "asm_" + hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str)
            .encode("utf-8")).hexdigest()[:16]

    def save(self, assessment) -> str:
        """持久化评估（幂等——同内容重复保存不覆盖原文件）。返回 assessment_id。"""
        aid = self.assessment_id_of(assessment)
        path = self.dir / "assessments" / f"{aid}.json"
        payload = {
            "schema_version": ASSESSMENT_SCHEMA_VERSION,
            "assessment_id": aid,
            "content_hash": aid[len("asm_"):],
            "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "assessment": assessment.model_dump(mode="json"),
        }
        created = _atomic_write_json(path, payload, overwrite=False)
        if not created and not path.exists():
            logger.warning(f"评估写入失败: {path}")
        return aid

    def load(self, assessment_id: str):
        """按 id 加载评估；不存在/坏文件返回 None（坏文件隔离告警，不崩）。"""
        aid = str(assessment_id or "").strip()
        if not aid or any(seg in aid for seg in ("/", "\\", "..")):
            return None  # 非法 id（路径注入防御）按缺失处理
        path = self.dir / "assessments" / f"{aid}.json"
        if not path.exists():
            return None
        try:
            from src.core.research import ThesisAssessment
            data = json.loads(path.read_text(encoding="utf-8"))
            return ThesisAssessment.model_validate(data["assessment"])
        except Exception as e:
            logger.warning(f"评估加载失败（按缺失处理）: {aid}: {e}")
            return None


# ──────────────── R4：行业成员双时间轴（DATA_TRUST §5）────────────────

class IndustryMembership(BaseModel):
    """行业成员记录（双时间轴——经济归属生效期 × 本系统知晓期）。

    - effective_from/effective_to：**经济归属**生效区间（公司真实行业归属期间）
    - known_from/known_to：**本系统何时知道**该版本（当前值接口只有 known_from=
      first_seen 起的前瞻能力——不可回填到上市日）
    strict 历史资格（R4 验收2 后半）：as_of 时点的行业归属要求
    known_from ≤ as_of ≤ known_to 且 effective_from ≤ as_of ≤ effective_to——
    只有当前查询值（known_from=今天）时，任何历史 as_of 都查不到 → 不宣称 strict。"""

    security_id: str
    industry_id: str
    source_version: str = ""
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    known_from: str = Field(default="", description="本系统知晓该版本的起始时点（ISO；不可回拨）")
    known_to: Optional[str] = None
    note: str = ""

    def strict_eligible_at(self, as_of: str) -> bool:
        """该成员记录能否支撑 as_of 的**严格历史**行业归属判定。"""
        if not self.known_from:
            return False
        if self.known_from > as_of:
            return False  # 截止时本系统还不知道该版本——不能宣称当时已知
        if self.known_to and as_of > self.known_to:
            return False
        if self.effective_from and as_of < self.effective_from:
            return False
        if self.effective_to and as_of > self.effective_to:
            return False
        return True


def industry_membership_from_current(security_id: str, industry_id: str, *,
                                     known_from: str, source_version: str = "",
                                     note: str = "") -> IndustryMembership:
    """「当前查询值」构造成员记录——effective_from 未知（不回填），known_from=first_seen：
    只支持 known_from 之后的决策，任何历史 as_of 均不 strict（DATA_TRUST §5 原文）。"""
    return IndustryMembership(
        security_id=security_id, industry_id=industry_id,
        source_version=source_version, known_from=known_from, note=note or "当前值接口——"
        "经济归属生效期未知，不回填历史（strict 历史行业归属不可宣称）")
