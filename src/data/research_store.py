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

logger = logging.getLogger(__name__)

RESEARCH_DIR = Path.home() / ".muyun" / "research"

RAW_SCHEMA_VERSION = 1
INVALIDATION_SCHEMA_VERSION = 1


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

    # ── 观测索引（按日 JSONL，引用原件不重复正文）──

    def append_observation(self, entry: dict, *, day: Optional[_date] = None) -> bool:
        """观测索引追加（security/metric/digest/时点等；调用方组织字段）。"""
        day = day or _date.today()
        record = {"observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                  **entry}
        return _atomic_write_jsonl_append(
            self.dir / "observations" / f"{day.isoformat()}.jsonl", record)

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
