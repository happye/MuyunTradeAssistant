"""PlanV2 存储回归测试（plan/fusion，src/data/horizon_plans.py）

锁死语义：
1. 每股每周期一份（mid/long 正交并存，F5 硬约束）；save/get 往返；同 plan_id 重存
   revision 递增；extra 字段（facts_observed）往返保留
2. accept 激活：草稿→激活；无计划/两份未指定周期/写入被拒 三种失败原因区分
3. M5 指纹冲突：判据=加载态快照 vs 磁盘；外部修改 → 拒绝 + 重读磁盘（不互吃）
4. 损坏保护：坏 JSON → corrupted + 拒绝写（不覆盖原文件）
5. 解析失败的坏计划 → 按无计划处理（get 指定周期 None / get 全部剔除坏份）

纯临时文件测试，无网络。跑法：pytest tests/core/test_horizon_plans.py -q
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.core.decision_contract import Horizon
from src.core.decision_policy import POLICY_ID_LONG, POLICY_ID_MID, HorizonPlan
from src.data.horizon_plans import HorizonPlanStore


def _plan(code="601318", horizon="MID", accepted=None, plan_id="p2_t1", facts=None):
    return HorizonPlan(
        plan_id=plan_id, security_id=code, accepted_at=accepted,
        horizon=Horizon[horizon],
        policy_id=POLICY_ID_MID if horizon == "MID" else POLICY_ID_LONG,
        intent="锂电需求回暖驱动盈利兑现", facts_observed=facts or ["订单环比+30%"])


def test_save_get_roundtrip_revision_and_per_horizon_slots(tmp_path):
    store = HorizonPlanStore(tmp_path / "plans.json")
    assert store.get("601318", "MID") is None
    store.save(_plan())
    got = store.get("601318", "MID")
    assert got is not None and got.intent == _plan().intent and got.revision == 1
    # mid/long 正交并存（F5）：同股另一周期独立
    store.save(_plan(horizon="LONG", plan_id="p2_t2"))
    assert store.get("601318", "LONG") is not None
    assert store.get("601318", "MID") is not None
    # 同 plan_id 重存 → revision 递增
    p2 = _plan()
    p2.intent = "更新后的意图"
    store.save(p2)
    got2 = store.get("601318", "MID")
    assert got2.revision == 2 and got2.intent == "更新后的意图"
    assert got2.facts_observed == ["订单环比+30%"]  # extra 字段往返保留


def test_get_without_horizon_returns_both(tmp_path):
    store = HorizonPlanStore(tmp_path / "plans.json")
    store.save(_plan(horizon="MID", plan_id="p2_a"))
    store.save(_plan(horizon="LONG", plan_id="p2_b"))
    both = store.get("601318")
    assert set(both.keys()) == {"MID", "LONG"}


def test_accept_states_and_rm(tmp_path):
    store = HorizonPlanStore(tmp_path / "plans.json")
    assert store.accept("601318") == (False, "no_plan")
    store.save(_plan(horizon="MID", plan_id="p2_a"))
    store.save(_plan(horizon="LONG", plan_id="p2_b"))
    # 两份未指定周期 → ambiguous（CLI 提示指定）
    ok, msg = store.accept("601318")
    assert ok is False and msg.startswith("ambiguous:")
    ok, msg = store.accept("601318", "MID")
    assert ok is True and msg  # 激活时点
    assert store.get("601318", "MID").activated is True
    assert store.get("601318", "LONG").activated is False  # 只激活指定周期
    assert store.remove("601318", "MID") is True
    assert store.get("601318", "MID") is None
    assert store.remove("601318", "MID") is False  # 幂等


def test_fingerprint_conflict_rejects_stale_write(tmp_path):
    store = HorizonPlanStore(tmp_path / "plans.json")
    store.save(_plan())
    # 外部直接改文件（模拟另一实例）
    disk = json.loads((tmp_path / "plans.json").read_text(encoding="utf-8"))
    disk["plans"]["601318:MID"]["intent"] = "外部修改"
    (tmp_path / "plans.json").write_text(json.dumps(disk, ensure_ascii=False), encoding="utf-8")
    assert store.save(_plan(plan_id="p2_t1")) is False
    # 重读后可见外部修改（不互吃）
    assert store.get("601318", "MID").intent == "外部修改"


def test_corrupted_file_refuses_write(tmp_path):
    path = tmp_path / "plans.json"
    path.write_text("{broken json", encoding="utf-8")
    store = HorizonPlanStore(path)
    assert store.corrupted is True
    assert store.get("601318", "MID") is None  # 按空处理
    assert store.save(_plan()) is False  # 拒绝写——不覆盖原文件
    assert store.accept("601318") == (False, "no_plan")
    assert "{broken json" in path.read_text(encoding="utf-8")  # 原文件原样


def test_bad_plan_record_parses_to_none(tmp_path):
    path = tmp_path / "plans.json"
    path.write_text(json.dumps({"version": 1, "plans": {
        "601318:MID": {"plan_id": "x", "horizon": "NOT_A_HORIZON"},
        "000001:LONG": {"plan_id": "y", "horizon": "LONG", "policy_id": "fusion_long_v1",
                        "intent": "ok"}}}, ensure_ascii=False), encoding="utf-8")
    store = HorizonPlanStore(path)
    assert store.get("601318", "MID") is None  # 坏份按无计划处理
    both = store.get("000001")
    assert set(both.keys()) == {"LONG"}  # 好份照常
    assert store.get("000001", "LONG").intent == "ok"


def test_list_plans_sorted(tmp_path):
    store = HorizonPlanStore(tmp_path / "plans.json")
    store.save(_plan(code="600519", plan_id="p2_a"))
    store.save(_plan(code="000001", horizon="LONG", plan_id="p2_b"))
    assert [p.security_id for p in store.list_plans()] == ["000001", "600519"]
