"""M2 isolated storage/query contract; synthetic VALID is not a G3 proof."""
import builtins
import io
import json
import os
import socket
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.core.decision_contract import ThesisStatus
from src.core.decision_policy import HorizonPlan
from src.core.research import ThesisAssessment
from src.core.research_application import ResearchApplicationService
from src.core.research_service import ASSERTION_METHOD_VERSION, RESEARCH_SERVICE_VERSION
from src.data.horizon_plans import HorizonPlanStore
from src.data.research_store import AssessmentStore

CODE = "600519"


def service(root):
    return ResearchApplicationService(store=None,
        plans_store=HorizonPlanStore(root / "plans.json"),
        assessment_store=AssessmentStore(root / "research"),
        fetch_fn=lambda *a: pytest.fail("query fetched data"))


def seed(svc, horizon="MID", status="VALID", **overrides):
    now = datetime.now().astimezone()
    asm = ThesisAssessment(security_id=CODE, horizon=horizon,
        snapshot_id=f"snap_{horizon}", status=ThesisStatus(status),
        method_version=ASSERTION_METHOD_VERSION, evaluated_as_of=now - timedelta(minutes=1),
        unresolved_gaps=[] if status == "VALID" else ["需核对必需命题"])
    asm = asm.model_copy(update=overrides.pop("assessment", {}))
    aid = svc.assessment_store.save(asm)
    plan = HorizonPlan(plan_id=f"p2_{CODE}_{horizon}", security_id=CODE, horizon=horizon,
        policy_id=f"fusion_{horizon.lower()}_v1", intent="隔离查询样本",
        policy_version=RESEARCH_SERVICE_VERSION, assessment_id=aid, snapshot_id=asm.snapshot_id,
        **overrides)
    assert svc.plans_store.save(plan)
    assert svc.plans_store.accept(CODE, horizon)[0]
    return svc.plans_store.get(CODE, horizon)


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def query(svc, root, monkeypatch, as_of=None):
    before = tree(root)
    counters = {"write": 0, "network": 0, "ai": 0}
    def deny(kind):
        counters[kind] += 1
        pytest.fail(f"query attempted {kind}")
    original_open, original_io_open, original_os_open = builtins.open, io.open, os.open
    def guarded_open(original):
        def call(file, mode="r", *a, **kw):
            if any(c in mode for c in "wax+"):
                deny("write")
            return original(file, mode, *a, **kw)
        return call
    def guarded_os_open(path, flags, *a, **kw):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            deny("write")
        return original_os_open(path, flags, *a, **kw)
    with monkeypatch.context() as m:
        m.setattr(builtins, "open", guarded_open(original_open))
        m.setattr(io, "open", guarded_open(original_io_open))
        m.setattr(os, "open", guarded_os_open)
        for name in ("mkdir", "rename", "replace", "remove", "unlink"):
            m.setattr(os, name, lambda *a, **kw: deny("write"))
        m.setattr(socket.socket, "connect", lambda *a, **kw: deny("network"))
        m.setattr(socket, "getaddrinfo", lambda *a, **kw: deny("network"))
        import openai
        m.setattr(openai.OpenAI, "__init__", lambda *a, **kw: deny("ai"))
        m.setattr(svc, "run", lambda *a, **kw: pytest.fail("query ran research"))
        # Constructor as well as query must stay read-only.
        restarted = service(root)
        view = svc.get_daily_view(CODE, as_of=as_of or datetime.now().astimezone())
        second = restarted.get_daily_view(CODE, as_of=as_of or datetime.now().astimezone())
    assert tree(root) == before
    assert counters == {"write": 0, "network": 0, "ai": 0}
    assert second["horizons"] == view["horizons"]
    return view


def test_normal_accepted_and_repeated_query(tmp_path, monkeypatch):
    svc = service(tmp_path)
    plan = seed(svc)
    for _ in range(3):
        mid = query(svc, tmp_path, monkeypatch)["horizons"]["MID"]
        assert mid["accepted"] and mid["assessment_status"] == "VALID"
        assert (mid["plan_id"], mid["revision"], mid["content_hash"]) == (
            plan.plan_id, plan.revision, plan.content_hash())
        assert mid["accepted_ref"] == svc.plans_store.get_accepted_ref(CODE, "MID")


def test_empty_query_and_constructor_create_no_directory(tmp_path, monkeypatch):
    root = tmp_path / "absent"
    view = query(service(root), root, monkeypatch)
    assert view["store_status"] == "EMPTY"
    assert not root.exists()
    assert any(f"research {CODE}" in s for s in view["next_steps"])


def test_candidate_keeps_accepted_main(tmp_path, monkeypatch):
    svc = service(tmp_path)
    plan = seed(svc)
    cand = plan.model_copy(update={"plan_id": "p2_candidate", "intent": "新材料", "accepted_at": None})
    assert svc.plans_store.save_candidate(cand)
    mid = query(svc, tmp_path, monkeypatch)["horizons"]["MID"]
    assert mid["plan_id"] == plan.plan_id and mid["accepted"]
    assert mid["candidate_available"] and mid["candidate"]["plan_id"] == cand.plan_id
    assert not mid["candidate"]["accepted"]


@pytest.mark.parametrize("status", ["INVALID", "REVIEW_REQUIRED", "UNESTABLISHED"])
def test_assessment_status_is_consumed_not_rejudged(tmp_path, monkeypatch, status):
    svc = service(tmp_path)
    seed(svc, status=status)
    mid = query(svc, tmp_path, monkeypatch)["horizons"]["MID"]
    assert mid["assessment_status"] == status
    assert mid["assessment_gaps"] == ["需核对必需命题"]


@pytest.mark.parametrize("payload", ["{bad", "[]", '{"version":1,"plans":[]}',
    '{"version":1,"plans":{},"candidates":[]}', '{"version":1,"plans":{},"plans":{}}'])
def test_corrupt_store_is_not_empty(tmp_path, monkeypatch, payload):
    (tmp_path / "plans.json").write_text(payload, encoding="utf-8")
    view = query(service(tmp_path), tmp_path, monkeypatch)
    assert view["store_status"] == "CORRUPT"
    assert view["horizons"] == {}
    assert any("人工" in s for s in view["next_steps"])


def test_one_corrupt_slot_preserves_other_horizon(tmp_path, monkeypatch):
    svc = service(tmp_path)
    seed(svc, "LONG")
    path = tmp_path / "plans.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["plans"][f"{CODE}:MID"] = {"bad": "record"}
    path.write_text(json.dumps(data), encoding="utf-8")
    view = query(svc, tmp_path, monkeypatch)
    assert view["store_status"] == "CORRUPT"
    assert view["horizons"]["MID"]["gaps"]
    assert view["horizons"]["LONG"]["assessment_status"] == "VALID"


@pytest.mark.parametrize("change", [
    {"method_version": "OLD"}, {"security_id": "000001"}, {"horizon": "LONG"},
    {"snapshot_id": ""}, {"evaluated_as_of": None},
    {"evaluated_as_of": datetime.now().astimezone() + timedelta(days=1)}])
def test_assessment_qualification_gaps(tmp_path, monkeypatch, change):
    svc = service(tmp_path)
    seed(svc, assessment=change)
    mid = query(svc, tmp_path, monkeypatch)["horizons"]["MID"]
    assert mid["assessment_status"] == "REVIEW_REQUIRED"
    assert mid["assessment_gaps"]


def test_older_query_cutoff_rejects_evidence_future_to_query(tmp_path, monkeypatch):
    svc = service(tmp_path)
    seed(svc)
    mid = query(svc, tmp_path, monkeypatch,
        datetime.now().astimezone() - timedelta(days=2))["horizons"]["MID"]
    assert mid["assessment_status"] == "REVIEW_REQUIRED"
    assert any("晚于查询" in s for s in mid["assessment_gaps"])


def test_old_plan_generation_version_requires_review(tmp_path, monkeypatch):
    svc = service(tmp_path)
    plan = seed(svc)
    old = plan.model_copy(update={"policy_version": "OLD"})
    assert svc.plans_store.save(old)
    assert svc.plans_store.accept(CODE, "MID")[0]
    mid = query(svc, tmp_path, monkeypatch)["horizons"]["MID"]
    assert mid["accepted"] and mid["assessment_status"] == "REVIEW_REQUIRED"
    assert "已验证组合" in mid["version_note"]


def test_mid_long_and_restart(tmp_path, monkeypatch):
    svc = service(tmp_path)
    seed(svc, "MID")
    seed(svc, "LONG", status="INVALID")
    view = query(svc, tmp_path, monkeypatch)
    assert view["horizons"]["MID"]["accepted"]
    assert view["horizons"]["LONG"]["accepted"]
    assert view["horizons"]["LONG"]["assessment_status"] == "INVALID"


def test_unreadable_store_has_explicit_status(tmp_path, monkeypatch):
    svc = service(tmp_path)
    (tmp_path / "plans.json").mkdir()
    assert query(svc, tmp_path, monkeypatch)["store_status"] == "UNREADABLE"


@pytest.mark.parametrize("slot_map", ["plans", "candidates", "accepted_refs"])
def test_explicit_null_slot_is_corrupt_not_missing(tmp_path, monkeypatch, slot_map):
    svc = service(tmp_path)
    seed(svc, "MID")
    seed(svc, "LONG")
    path = tmp_path / "plans.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault(slot_map, {})[f"{CODE}:MID"] = None
    path.write_text(json.dumps(data), encoding="utf-8")
    view = query(svc, tmp_path, monkeypatch)
    assert view["store_status"] == "CORRUPT"
    mid = view["horizons"]["MID"]
    assert mid["gaps"] or mid["candidate"]["gaps"]
    assert not any("MID 无研究记录" in step for step in view["next_steps"])
    assert view["horizons"]["LONG"]["accepted"]
    assert view["horizons"]["LONG"]["assessment_status"] == "VALID"


def test_stored_next_check_is_visible_in_daily_steps(tmp_path, monkeypatch):
    svc = service(tmp_path)
    seed(svc, assessment={"next_checks": ["核对下期现金流公告"]})
    view = query(svc, tmp_path, monkeypatch)
    assert "MID 核验节点：核对下期现金流公告" in view["next_steps"]
