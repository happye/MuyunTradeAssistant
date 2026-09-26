"""PlanV2 持久化存储（plan/fusion F5/F7 登记项：HorizonPlan 侧挂对象落盘 + 用户确认流）。

设计（与 proposals.json 同款纪律）：
- ~/.muyun/horizon_plans.json：{version, plans: {"<code>:<HORIZON>": HorizonPlan}}——
  **每股每周期一份**（mid/long 正交，F5 硬约束：同股两套计划并存）；revision 递增；
  历史不删（ADR-F09）
- M5 同款指纹冲突拒绝：判据是**加载态快照** vs 磁盘（保存前内存已含本次修改，
  对比内存态永远失配——2026-09-26 审查修正）；写入被拒后 self._data 置 None 强制重读
- accepted_at None=草稿（只 REVIEW 不出行动，决策表行0 激活门）；用户 accept 后激活
- 同周期重立计划 = 替换（CLI 端对替换已激活计划有明示提醒）
- 不改 TradePlan 任何字段、不写 portfolio.yaml（侧挂设计，F5 硬约束）

本模块不做裁决（纯存取 + 校验）；决策语义在 decision_policy.evaluate_horizon。
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import ValidationError  # noqa: F401 —— get() 解析失败分类留档用


logger = logging.getLogger(__name__)

PLANS_FILE = Path.home() / ".muyun" / "horizon_plans.json"
PLANS_VERSION = 1


def _fingerprint(data: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()[:16]


class HorizonPlanStore:
    """HorizonPlan 存取（每股一份最新计划；M5 指纹冲突拒绝）。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else PLANS_FILE
        self._data: Optional[dict] = None
        self._loaded_fingerprint: Optional[str] = None  # 加载态指纹（M5 冲突判据）
        self._corrupted = False

    def _load(self) -> dict:
        if self._data is not None:
            return self._data
        self._loaded_fingerprint = None
        if not self.path.exists():
            self._data = {"version": PLANS_VERSION, "plans": {}}
            self._loaded_fingerprint = _fingerprint(self._data)
            return self._data
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("plans"), dict):
                raise ValueError("根结构不是 {version, plans}")
            data.setdefault("version", PLANS_VERSION)
            self._data = data
            self._loaded_fingerprint = _fingerprint(data)
        except Exception as e:
            # 损坏保护（G14/F7 P2-11 同款）：不覆盖原文件，显式标记让调用方提示
            logger.warning(f"horizon_plans.json 读取失败（损坏保护生效，本次按空处理"
                           f"且不会覆盖原文件）: {e}")
            self._corrupted = True
            self._data = {"version": PLANS_VERSION, "plans": {}}
        return self._data

    def _save(self, data: dict) -> bool:
        """原子替换写；写入前对比「加载态指纹」与磁盘（M5）——外部改动 → 拒绝。
        注意判据是加载时快照，不是本函数入参（入参已含本次修改，对比它永远失配）。"""
        if self._corrupted:
            # 损坏保护承诺：不覆盖原文件（docstring/告警口径一致）
            logger.warning("horizon_plans.json 此前读取失败（损坏保护生效）——"
                           "写入拒绝，请手工检查该文件后重试")
            return False
        if self.path.exists() and self._loaded_fingerprint is not None:
            try:
                disk = json.loads(self.path.read_text(encoding="utf-8"))
                if _fingerprint(disk) != self._loaded_fingerprint:
                    logger.warning("horizon_plans.json 被外部修改（指纹冲突）——本次写入拒绝，"
                                   "请重试（将合并最新磁盘内容）")
                    self._data = None  # 下次访问重读磁盘
                    return False
            except Exception:
                pass  # 磁盘读不出=按无外部改动处理（首次保存/损坏态）
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(str(tmp), str(self.path))
        self._loaded_fingerprint = _fingerprint(data)
        return True

    @property
    def corrupted(self) -> bool:
        self._load()  # 损坏与否在首次加载时才知道（惰性）
        return self._corrupted

    @staticmethod
    def _key(security_id: str, horizon: str) -> str:
        """槽位键：每股每周期一份（mid/long 正交并存）。"""
        return f"{security_id}:{str(horizon).upper()}"

    def _parse(self, raw):
        if not raw:
            return None
        try:
            from src.core.decision_policy import HorizonPlan
            return HorizonPlan.model_validate(raw)
        except Exception as e:
            logger.warning(f"PlanV2 解析失败，该份计划按无计划处理: {e}")
            return None

    def get(self, security_id: str, horizon: Optional[str] = None):
        """取计划。指定 horizon → 该周期那份；未指定 → 该股全部（{horizon: plan}）。"""
        plans = self._load()["plans"]
        sid = str(security_id)
        if horizon is not None:
            return self._parse(plans.get(self._key(sid, horizon)))
        out = {}
        for key, raw in plans.items():
            if key.split(":")[0] == sid:
                p = self._parse(raw)
                if p is not None:
                    out[p.horizon.value] = p
        return out

    def save(self, plan) -> bool:
        """新建/更新计划（同 plan_id 重存 revision 递增；不同 plan_id=重立替换）。

        候选副本模式：修改先落在副本上，写入成功才提交内存态——失败（指纹冲突/
        损坏拒绝）不污染内存（否则被拒的修改会"阴魂不散"地影响后续读判定）。"""
        from src.core.decision_policy import HorizonPlan
        if not isinstance(plan, HorizonPlan):
            raise TypeError("save 需要 HorizonPlan 实例")
        data = self._load()
        key = self._key(plan.security_id, plan.horizon.value)
        old = data["plans"].get(key)
        if old and old.get("plan_id") == plan.plan_id:
            plan = plan.model_copy(update={"revision": int(old.get("revision") or 1) + 1})
        candidate = {"version": data.get("version", PLANS_VERSION),
                     "plans": dict(data["plans"])}
        candidate["plans"][key] = plan.model_dump(mode="json")
        if self._save(candidate):
            self._data = candidate
            return True
        return False

    def accept(self, security_id: str, horizon: Optional[str] = None) -> tuple:
        """用户确认激活。返回 (ok, msg)：ok=False 时 msg 说明原因（无计划/多份需指定/
        写入被拒）——CLI 端不猜原因。"""
        plans = self._load()["plans"]
        sid = str(security_id)
        keys = [k for k in plans if k.split(":")[0] == sid]
        if horizon is not None:
            keys = [k for k in keys if k.endswith(f":{str(horizon).upper()}")]
        if not keys:
            return False, "no_plan"
        if len(keys) > 1:
            return False, "ambiguous:" + ",".join(k.split(":")[1] for k in keys)
        plan = self._parse(plans[keys[0]])
        if plan is None:
            return False, "no_plan"
        plan.accepted_at = datetime.now().isoformat(timespec="seconds")
        ok = self.save(plan)
        return (True, plan.accepted_at) if ok else (False, "write_rejected")

    def remove(self, security_id: str, horizon: Optional[str] = None) -> bool:
        data = self._load()
        sid = str(security_id)
        keys = [k for k in data["plans"] if k.split(":")[0] == sid
                and (horizon is None or k.endswith(f":{str(horizon).upper()}"))]
        if not keys:
            return False
        candidate = {"version": data.get("version", PLANS_VERSION),
                     "plans": {k: v for k, v in data["plans"].items() if k not in keys}}
        if self._save(candidate):
            self._data = candidate
            return True
        return False

    def list_plans(self) -> list:
        """全部计划（HorizonPlan 列表，按槽位键排序）。"""
        plans = []
        for key in sorted(self._load()["plans"]):
            p = self._parse(self._load()["plans"][key])
            if p is not None:
                plans.append(p)
        return plans
