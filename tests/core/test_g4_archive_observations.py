"""G4 archive sidebar: existing research result and read-only local archive."""
import io
import hashlib
import json
from datetime import datetime, timezone

import pytest
from rich.console import Console

import src.cli.main as cli
from src.cli.main import _render_research_result
from src.core.research_application import ResearchApplicationService, ResearchRunResult
from src.data.horizon_plans import HorizonPlanStore
from src.data.research_store import AssessmentStore, ResearchStore

AS_OF = datetime(2026, 10, 16, tzinfo=timezone.utc)
SENTINEL = 987654321.123456


def make_app(tmp_path):
    return ResearchApplicationService(store=ResearchStore(tmp_path / "research"),
        plans_store=HorizonPlanStore(tmp_path / "plans.json"),
        assessment_store=AssessmentStore(tmp_path / "research"))


def seed(store, metric="CFOToNP", period="2023-12-31", *, raw_changes=None, index_changes=None):
    content = dict(security_id="600519", metric=metric, period_end=period,
        value=SENTINEL, unit="元" if metric == "netProfit" else "倍",
        period_kind="cumulative", published_at="2024-03-15",
        source_uri="synthetic://local/old-archive", source_version="fixture-v1")
    content.update(raw_changes or {})
    digest, _ = store.archive_raw(content, metadata={"queried_at": "2024-04-01T08:00:00+00:00"})
    row = dict(security_id="600519", metric=metric, period_end=period, raw_sha256=digest,
        source_version="fixture-v1", value=SENTINEL, unit=content["unit"],
        observed_at="2024-04-01T08:00:00+00:00")
    row.update(index_changes or {})
    store.append_observation(row)
    return digest, row


def tree_bytes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_cash_sidebar_visible_in_existing_result_renderer():
    result = ResearchRunResult(security_id="600519", as_of=AS_OF, run_id="old",
        financial_observations={"status": "NO_READABLE_OBSERVATIONS",
            "summary": "现金资料：本次无可读现金字段；仅资料观察，不改变研究评估"})
    output = io.StringIO()
    _render_research_result(Console(file=output, width=160), result)
    assert "现金资料：本次无可读现金字段" in output.getvalue()


def test_legacy_inventory_all_periods_restart_readonly_no_value(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    for period in ("2020-12-31", "2023-12-31", "2024-06-30"):
        for metric in ("CFOToNP", "netProfit", "roeAvg"):
            seed(app.store, metric, period)
    before = tree_bytes(tmp_path)
    calls = {"query": 0, "load": 0}
    query, load = app.store.query_observations, app.store.load_raw
    def counted_query(**kw):
        calls["query"] += 1
        return query(**kw)
    def counted_load(digest):
        calls["load"] += 1
        return load(digest)
    def forbidden(*args, **kwargs):
        pytest.fail("getter invoked a write/evaluation/fetch channel")
    monkeypatch.setattr(app.store, "query_observations", counted_query)
    monkeypatch.setattr(app.store, "load_raw", counted_load)
    for name in ("archive_raw", "append_observation", "save_snapshot_manifest"):
        monkeypatch.setattr(app.store, name, forbidden)
    app.fetch_fn = forbidden
    monkeypatch.setattr(app, "_save_draft", forbidden)
    monkeypatch.setattr(app.assessment_store, "save", forbidden)
    view = app.get_cash_observations("600519", as_of=AS_OF)
    assert calls == {"query": 1, "load": 9}
    assert view["inventory"]["counts"] == {"CFOToNP": 3, "netProfit": 3, "roeAvg": 3}
    assert view["inventory"]["report_periods"] == ["2020-12-31", "2023-12-31", "2024-06-30"]
    assert view["qualification"]["comparable_periods"] == 0
    assert view["qualification"]["industry_status"] == "UNKNOWN"
    assert view["cash_sustainability"] == "UNKNOWN" and not view["coverage_complete"]
    assert str(SENTINEL) not in json.dumps(view, ensure_ascii=False)
    assert "日期、单位为归档声明，待核" in view["summary"]
    assert all(r["metadata_missing"] for r in view["inventory"]["references"])
    assert tree_bytes(tmp_path) == before
    assert make_app(tmp_path).get_cash_observations("600519", as_of=AS_OF) == view
    rows = query(security_id="600519")
    monkeypatch.setattr(app.store, "query_observations", lambda **kw: list(reversed(rows)) + rows)
    assert app.get_cash_observations("600519", as_of=AS_OF) == view


@pytest.mark.parametrize("case,reason", [
    ("hash", "raw_content_hash_mismatch"), ("subject", "raw_index_security_id_mismatch"),
    ("period", "raw_index_period_end_mismatch"), ("future", "reporting_period_after_as_of"),
    ("missing", "raw_missing_or_unreadable"), ("digest", "invalid_raw_digest"),
    ("source", "raw_index_source_version_mismatch"), ("schema", "invalid_raw_schema_or_digest"),
    ("publication", "published_at_after_as_of"), ("no_subject", "raw_index_security_id_mismatch"),
])
def test_quarantines_invalid_archive_without_values(tmp_path, case, reason):
    app = make_app(tmp_path)
    raw_changes = {"subject": {"security_id": "000001"}, "no_subject": {"security_id": ""},
        "period": {"period_end": "2022-12-31"}, "future": {"period_end": "2027-12-31"},
        "source": {"source_version": "other"}, "publication": {"published_at": "2027-01-01"}}.get(case)
    index_changes = {"period_end": "2027-12-31"} if case == "future" else (
        {"raw_sha256": "../bad"} if case == "digest" else None)
    raw_id, _ = seed(app.store, raw_changes=raw_changes, index_changes=index_changes)
    path = app.store.raw_path(raw_id)
    if case == "missing":
        path.unlink()
    elif case in ("hash", "schema"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if case == "hash":
            payload["content"]["value"] = -123
        else:
            payload["schema_version"] = True
        path.write_text(json.dumps(payload), encoding="utf-8")
    view = app.get_cash_observations("600519", as_of=AS_OF)
    assert view["status"] in ("INCOMPLETE", "UNREADABLE")
    assert sum(view["inventory"]["counts"].values()) == 0
    assert reason in view["inventory"]["references"][0]["gaps"]
    assert str(SENTINEL) not in json.dumps(view)


def test_conflicting_versions_and_duplicate_raw_reads(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    _, row = seed(app.store)
    app.store.append_observation(row)
    seed(app.store, raw_changes={"value": SENTINEL + 7, "source_version": "fixture-v2"},
         index_changes={"source_version": "fixture-v2"})
    calls = []
    load = app.store.load_raw
    monkeypatch.setattr(app.store, "load_raw", lambda d: (calls.append(d), load(d))[1])
    view = app.get_cash_observations("600519", as_of=AS_OF)
    assert len(calls) == len(set(calls)) == 2
    assert view["inventory"]["counts"]["CFOToNP"] == 0
    assert all("conflicting_field_or_source_versions" in r["gaps"]
               for r in view["inventory"]["references"])


def test_repeated_archive_with_different_collection_time_is_one_field(tmp_path):
    app = make_app(tmp_path)
    _, row = seed(app.store)
    before = app.get_cash_observations("600519", as_of=AS_OF)
    app.store.append_observation({**row, "observed_at": "2025-04-01T08:00:00+00:00"})
    after = app.get_cash_observations("600519", as_of=AS_OF)
    assert after["inventory"]["counts"]["CFOToNP"] == 1
    assert len(after["inventory"]["references"]) == 1
    assert after["display_id"] == before["display_id"]
    assert after["inventory"]["references"][0]["collection_times"]["observed_at"] == [
        "2024-04-01T08:00:00+00:00", "2025-04-01T08:00:00+00:00"]


def test_capture_after_analysis_start_is_inventory_not_history(tmp_path):
    app = make_app(tmp_path)
    calls = []
    def capture(sec, year, quarter):
        calls.append((year, quarter))
        seed(app.store, period=f"{year}-{quarter * 3:02d}-" + {1: "31", 2: "30", 3: "30", 4: "31"}[quarter],
             raw_changes={"fetched_at": "2026-10-16T00:00:05+00:00"},
             index_changes={"observed_at": "2026-10-16T00:00:05+00:00"})
    app.fetch_fn = capture
    result = app.run("600519", as_of=AS_OF, capture=True)
    assert calls
    view = result.financial_observations
    assert view["inventory"]["counts"]["CFOToNP"] == 4
    assert view["qualification"]["comparable_periods"] == 0
    assert all("不证明历史可得性" in r["collection_note"] for r in view["inventory"]["references"])


def test_query_and_raw_exceptions_are_not_empty_success(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    seed(app.store)
    def broken(*args, **kwargs):
        raise OSError("synthetic unreadable")
    monkeypatch.setattr(app.store, "load_raw", broken)
    view = app.get_cash_observations("600519", as_of=AS_OF)
    assert view["status"] == "UNREADABLE" and "读取失败" in view["summary"]
    monkeypatch.setattr(app.store, "query_observations", broken)
    assert app.get_cash_observations("600519", as_of=AS_OF)["status"] == "UNREADABLE"


def test_run_lifecycle_and_accepted_slot_unchanged(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    seed(app.store)  # deliberately outside the existing four-quarter assessment window
    getter = app.get_cash_observations
    monkeypatch.setattr(app, "get_cash_observations", lambda *a, **kw: None)
    app.run("600519", as_of=AS_OF)
    assert app.plans_store.accept("600519", "MID")[0]
    assert app.plans_store.accept("600519", "LONG")[0]
    baseline = app.run("600519", as_of=AS_OF)
    accepted_bytes = app.plans_store.path.read_bytes()
    monkeypatch.setattr(app, "get_cash_observations", getter)
    result = app.run("600519", as_of=AS_OF)
    assert result.model_dump(exclude={"financial_observations"}) == baseline.model_dump(exclude={"financial_observations"})
    assert app.plans_store.path.read_bytes() == accepted_bytes
    assert result.financial_observations["display_snapshot_id"] != result.snapshot_id
    assert str(SENTINEL) not in json.dumps(result.financial_observations)
    def broken(*a, **kw):
        raise RuntimeError("sidebar-only failure")
    monkeypatch.setattr(app, "get_cash_observations", broken)
    failed = app.run("600519", as_of=AS_OF)
    assert failed.financial_observations["status"] == "UNREADABLE"
    assert failed.run_id == baseline.run_id


def test_real_research_command_text_json_and_legacy_result(tmp_path, monkeypatch, capsys):
    import src.data.horizon_plans as plans_module
    import src.data.research_store as store_module
    app = make_app(tmp_path)
    digest, _ = seed(app.store)
    bad_hash, _ = seed(app.store, "netProfit", "2022-12-31")
    payload = json.loads(app.store.raw_path(bad_hash).read_text(encoding="utf-8"))
    payload["content"]["value"] = -123
    app.store.raw_path(bad_hash).write_text(json.dumps(payload), encoding="utf-8")
    seed(app.store, "roeAvg", "2021-12-31", raw_changes={"security_id": "000001"})
    monkeypatch.setattr(store_module, "RESEARCH_DIR", app.store.dir)
    monkeypatch.setattr(plans_module, "PLANS_FILE", app.plans_store.path)
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=220))
    cli.research_command(["600519"])
    assert "现金资料：CFOToNP 1条" in output.getvalue()
    # Independent text failure: JSON had the raw reference but rendered text did not.
    assert digest[:12] in output.getvalue()
    assert "归档留样编号" in output.getvalue()
    assert "留样内容与哈希不符" in output.getvalue()
    assert "证券不一致或缺失" in output.getvalue()
    assert "其余1份" in output.getvalue() and "--json" in output.getvalue()
    assert str(SENTINEL) not in output.getvalue()
    cli.research_command(["600519", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["financial_observations"]["inventory"]["counts"]["CFOToNP"] == 1
    assert str(SENTINEL) not in json.dumps(data["financial_observations"])
    legacy = ResearchRunResult(security_id="600519", as_of=AS_OF, run_id="legacy")
    assert legacy.financial_observations is None
    _render_research_result(Console(file=io.StringIO()), legacy)


def test_declared_verified_data_and_industry_flag_cannot_grant_production_qualification(tmp_path):
    app = make_app(tmp_path)
    seed(app.store, raw_changes={"period_start": "2023-01-01", "period_basis": "YTD",
         "value_kind": "RATIO", "underlying_period_basis": "YTD",
         "metric_definition_version": "fixture-definition-v1", "semantic_status": "VERIFIED",
         "knowledge_basis": "AS_PUBLISHED_ARCHIVE", "available_at": "2024-03-16T00:00:00+00:00",
         "version_available_at": "2024-03-16T00:00:00+00:00", "industry_verified": True})
    view = app.get_cash_observations("600519", as_of=AS_OF)
    assert view["inventory"]["counts"]["CFOToNP"] == 1
    assert view["qualification"]["industry_status"] == "UNKNOWN"
    assert view["qualification"]["comparable_periods"] == 0
    assert str(SENTINEL) not in json.dumps(view)
