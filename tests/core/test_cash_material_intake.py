"""Bounded cash material intake through public research entrypoints."""
import io
import json
import hashlib
from datetime import datetime, timezone

import pytest
from rich.console import Console

import src.cli.main as cli
from src.data.research_store import ResearchStore
from src.core.research_application import ResearchApplicationService
import src.core.cash_material_intake as material

NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)
TEXT = "证券代码：600519 贵州茅台 2024 年第一季度报告 合并 人民币 净利润 经营活动产生的现金流量净额 987654321.123456"


def supplier(value=987654321.123456, **changes):
    interface = "baostock.query_profit_data(code=sh.600519,year=2024,quarter=1)"
    content = dict(metric="netProfit", value=value, unit="元", period_end="2024-03-31",
        source_version="baostock_financial_v1", source_uri=interface, published_at="2024-04-27")
    content.update(changes)
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return dict(schema_version=1, sha256=digest, content=content, metadata=dict(stock_code="600519",
        bs_code="sh.600519", interface=interface, year=2024, quarter=1, metric="netProfit",
        period_end="2024-03-31", source_version="baostock_financial_v1"))


def fixture_files(tmp_path, payload=None, declaration=None):
    (tmp_path / "original.pdf").write_bytes(b"%PDF-1.7\nsynthetic bounded fixture")
    (tmp_path / "supplier.json").write_text(json.dumps(payload or supplier()), encoding="utf-8")
    manifest = dict(schema="cash_materials.v1", original={"path": "original.pdf", **(declaration or {})},
        supplier_raw_paths=["supplier.json"], pages=[0, 7, 8, 9, 10])
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def app(store):
    return ResearchApplicationService(store=store, plans_store=None, assessment_store=None)


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_public_missing_manifest_does_not_run_research(tmp_path, monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output))
    monkeypatch.setattr(ResearchApplicationService, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("research ran")))
    cli.research_command(["600519", "--cash-materials", str(tmp_path / "absent.json")])
    assert "现金材料导入失败" in output.getvalue()


def test_public_mixed_flags_rejected_before_store(tmp_path, monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output))
    cli.research_command(["600519", "--cash-materials", str(tmp_path / "a.json"), "--capture"])
    assert "不能混用" in output.getvalue()


def test_public_repl_intake_restart_normal_research_text_json(tmp_path, monkeypatch, capsys):
    import start
    import src.data.research_store as stores
    import src.data.horizon_plans as plans
    manifest = fixture_files(tmp_path)
    root = tmp_path / "research"
    monkeypatch.setattr(stores, "RESEARCH_DIR", root)
    monkeypatch.setattr(plans, "PLANS_FILE", tmp_path / "plans.json")
    monkeypatch.setattr(material, "_extract_pdf", lambda data, pages: TEXT)
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=220))
    mode, args = start.parse_input(f'research 600519 --cash-materials "{manifest}" --json')
    start.run_cli(mode, args)
    result = json.loads(capsys.readouterr().out)
    assert result["document_identity_check"] == "MATCHED_LOCAL_TEXT"
    assert result["report_periods"] == ["2024-03-31"]
    assert result["field_consistency"].startswith("PENDING")
    assert result["supplier_items"][0]["status"] == "LOCAL_PAYLOAD_CHECKED"
    assert "987654321" not in json.dumps(result)
    assert {p.name for p in root.iterdir()} == {"cash_materials"}
    before = tree(root)
    again = app(ResearchStore(root)).intake_cash_materials("600519", manifest, as_of=NOW)
    assert again["receipt_id"] == result["receipt_id"]
    assert tree(root) == before
    view = app(ResearchStore(root)).get_cash_observations("600519", as_of=NOW)
    assert view["cash_materials"]["receipts"][0]["receipt_id"] == result["receipt_id"]
    assert tree(root) == before
    cli.research_command(["600519"])
    assert "本地现金材料回执1条，均已归档待核" in output.getvalue()
    cli.research_command(["600519", "--json"])
    ordinary = json.loads(capsys.readouterr().out)
    assert ordinary["financial_observations"]["cash_materials"]["receipts"][0]["receipt_id"] == result["receipt_id"]
    assert "987654321" not in json.dumps(ordinary) + output.getvalue()


@pytest.mark.parametrize("case", ["hash", "wrong_stock", "missing_identity", "exchange", "period", "source", "unit", "bool", "duplicate"])
def test_bad_supplier_isolated_without_losing_other_originals(tmp_path, case):
    payload = supplier()
    if case == "hash":
        payload["sha256"] = "0" * 64
    if case == "wrong_stock":
        payload["metadata"]["stock_code"] = "000001"
    if case == "missing_identity":
        del payload["metadata"]["bs_code"]
    if case == "exchange":
        payload["metadata"]["bs_code"] = "sz.600519"
    if case == "period":
        payload["metadata"]["period_end"] = "2023-03-31"
    if case == "source":
        payload["metadata"]["source_version"] = "other"
    if case == "unit":
        payload = supplier(unit="万元")
    if case == "bool":
        payload = supplier(value=True)
    manifest = fixture_files(tmp_path, payload)
    if case == "duplicate":
        (tmp_path / "supplier.json").write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    good = tmp_path / "good.json"
    good.write_text(json.dumps(supplier()), encoding="utf-8")
    content = json.loads(manifest.read_text())
    content["supplier_raw_paths"].append("good.json")
    manifest.write_text(json.dumps(content), encoding="utf-8")
    result = app(ResearchStore(tmp_path / "store")).intake_cash_materials("600519", manifest, as_of=NOW, extractor=lambda *a: TEXT)
    assert [p["status"] for p in result["supplier_items"]] == ["REJECTED_PENDING_REVIEW", "LOCAL_PAYLOAD_CHECKED"]
    assert "987654321" not in json.dumps(result)


@pytest.mark.parametrize("text,status", [("证券代码：000001 2024 年第一季度报告", "PENDING_DOCUMENT_IDENTITY"),
    ("证券代码：600519 2024 年第一季度报告 2023 年第一季度报告", "PENDING_DOCUMENT_IDENTITY"),
    ("扫描件", "PENDING_DOCUMENT_IDENTITY"), (TEXT.replace("合并", "归属于母公司"), "MATCHED_LOCAL_TEXT")])
def test_ambiguous_or_parent_only_document_never_qualifies(tmp_path, text, status):
    manifest = fixture_files(tmp_path)
    result = app(ResearchStore(tmp_path / "store")).intake_cash_materials("600519", manifest, as_of=NOW, extractor=lambda *a: text)
    assert result["document_identity_check"] == status
    assert result["field_consistency"] == "PENDING_UNIQUE_TABLE_DEFINITION"
    assert result["roe_definition"] == "PENDING" and result["industry"] is None
    assert result["comparable_periods"] == 0 and result["cash_sustainability"] == "UNKNOWN"


@pytest.mark.parametrize("value,status", [("2026-10-17", "DECLARED_FUTURE"), ("2026-10-16", "DECLARED_SAME_DAY_TIME_UNKNOWN"),
    ("2024-04-27T16:00:00+08:00", "DECLARED_ONLY")])
def test_publication_only_a_declaration(tmp_path, value, status):
    manifest = fixture_files(tmp_path, declaration={"published_at": value, "source_uri": "https://example.invalid/declared"})
    result = app(ResearchStore(tmp_path / "store")).intake_cash_materials("600519", manifest, as_of=NOW, extractor=lambda *a: TEXT)
    assert result["pit_status"] == status
    assert result["source_authenticity"].startswith("PENDING")


@pytest.mark.parametrize("change", ["date", "manifest_size", "pdf_size", "supplier_size", "pages", "duplicate", "values"])
def test_bad_manifest_or_resource_no_receipt(tmp_path, change):
    manifest = fixture_files(tmp_path)
    content = json.loads(manifest.read_text())
    if change == "date":
        content["original"]["published_at"] = "2024-02-30"
    if change == "pages":
        content["pages"] = [True]
    if change == "values":
        content["values"] = {"VERIFIED": 123}
    manifest.write_text(json.dumps(content), encoding="utf-8")
    if change == "manifest_size":
        manifest.write_bytes(b" " * 65537)
    if change == "duplicate":
        manifest.write_bytes(b'{"schema":"cash_materials.v1","schema":"cash_materials.v1"}')
    if change == "pdf_size":
        (tmp_path / "original.pdf").write_bytes(b"%PDF-" + b" " * material.PDF_LIMIT)
    if change == "supplier_size":
        (tmp_path / "supplier.json").write_bytes(b" " * (material.RAW_LIMIT + 1))
    store = ResearchStore(tmp_path / "store")
    with pytest.raises((ValueError, UnicodeError)):
        app(store).intake_cash_materials("600519", manifest, as_of=NOW, extractor=lambda *a: TEXT)
    assert not store.list_cash_material_receipts()


def test_extraction_timeout_still_preserves_explicit_pending_receipt(tmp_path):
    manifest = fixture_files(tmp_path)
    def timeout(*args):
        raise TimeoutError("sensitive contents must not leak")
    result = app(ResearchStore(tmp_path / "store")).intake_cash_materials("600519", manifest, as_of=NOW, extractor=timeout)
    assert result["document_identity_check"] == "PENDING_PDF_EXTRACTION"
    assert "sensitive" not in json.dumps(result)


def test_receipt_last_partial_write_and_corrupt_existing_never_overwrite(tmp_path, monkeypatch):
    manifest = fixture_files(tmp_path)
    store = ResearchStore(tmp_path / "store")
    real = store.save_cash_material_bytes
    def fail(kind, data):
        if kind == "blobs" and not data.startswith(b"%PDF-"):
            raise OSError("partial failure")
        return real(kind, data)
    monkeypatch.setattr(store, "save_cash_material_bytes", fail)
    with pytest.raises(material.CashMaterialError):
        app(store).intake_cash_materials("600519", manifest, as_of=NOW, extractor=lambda *a: TEXT)
    assert not store.list_cash_material_receipts()
    assert material.read_materials(store, "600519", as_of=NOW)["receipts"] == []
    digest = real("blobs", b"original")
    path = store.dir / "cash_materials" / "blobs" / digest
    path.write_bytes(b"damaged")
    with pytest.raises(ValueError):
        real("blobs", b"original")
    assert path.read_bytes() == b"damaged"


def test_restart_conflict_keeps_both_no_values_and_bad_blob_read_once(tmp_path, monkeypatch):
    manifest = fixture_files(tmp_path)
    store = ResearchStore(tmp_path / "store")
    first = app(store).intake_cash_materials("600519", manifest, as_of=NOW, extractor=lambda *a: TEXT)
    (tmp_path / "supplier.json").write_text(json.dumps(supplier(value=123456789)), encoding="utf-8")
    second = app(store).intake_cash_materials("600519", manifest, as_of=NOW, extractor=lambda *a: TEXT)
    assert first["receipt_id"] != second["receipt_id"]
    view = material.read_materials(ResearchStore(store.dir), "600519", as_of=NOW)
    assert len(view["receipts"]) == 2
    assert view["conflicts"] == [{"period_end": "2024-03-31", "metric": "netProfit", "status": "CONFLICT_PENDING_REVIEW"}]
    assert "987654321" not in json.dumps(view) and "123456789" not in json.dumps(view)
    pdf_digest = hashlib.sha256((tmp_path / "original.pdf").read_bytes()).hexdigest()
    (store.dir / "cash_materials" / "blobs" / pdf_digest).write_bytes(b"corrupt")
    calls = []
    real = store.load_cash_material_bytes
    def counted(kind, digest):
        calls.append((kind, digest))
        return real(kind, digest)
    monkeypatch.setattr(store, "load_cash_material_bytes", counted)
    before = tree(store.dir)
    view = material.read_materials(store, "600519", as_of=NOW)
    assert not view["receipts"] and view["read_errors"]
    assert calls.count(("blobs", pdf_digest)) == 1
    assert tree(store.dir) == before


@pytest.mark.parametrize("quote", ['"', "'"])
def test_repl_preserves_windows_backslashes_and_continuous_spaces(quote):
    import start
    path = r"C:\Local Files\cash  material\input.json"
    mode, args = start.parse_input(f"research 600519 --cash-materials {quote}{path}{quote} --json")
    assert mode == "research"
    assert args["rest"] == ["600519", "--cash-materials", path, "--json"]


def test_repl_unclosed_quote_error_no_writes(tmp_path, monkeypatch):
    import start
    import src.data.research_store as stores
    monkeypatch.setattr(stores, "RESEARCH_DIR", tmp_path / "store")
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output))
    mode, args = start.parse_input('research 600519 --cash-materials "missing manifest.json')
    start.run_cli(mode, args)
    assert "现金材料导入失败" in output.getvalue()
    assert not (tmp_path / "store").exists()


def test_pdf_only_json_failure_and_accepted_bytes_untouched(tmp_path, monkeypatch, capsys):
    import src.data.research_store as stores
    manifest = fixture_files(tmp_path)
    content = json.loads(manifest.read_text())
    del content["supplier_raw_paths"]
    manifest.write_text(json.dumps(content), encoding="utf-8")
    store = ResearchStore(tmp_path / "store")
    store.dir.mkdir()
    accepted = store.dir / "existing_assessment.json"
    accepted.write_bytes(b'"accepted immutable sentinel"')
    before = accepted.read_bytes()
    result = app(store).intake_cash_materials("600519", manifest, as_of=NOW, extractor=lambda *a: TEXT)
    assert not result["supplier_items"] and "供应商留样未提供" in result["summary"]
    assert accepted.read_bytes() == before
    monkeypatch.setattr(stores, "RESEARCH_DIR", store.dir)
    cli.research_command(["600519", "--cash-materials", str(tmp_path / "missing.json"), "--json"])
    error = json.loads(capsys.readouterr().out)
    assert error["error"] == "source_file_missing" and error["intake_status"] == "FAILED"


def test_read_rechecks_declared_time_and_ignores_forged_public_fields(tmp_path):
    manifest = fixture_files(tmp_path, declaration={"published_at": "2026-10-16"})
    store = ResearchStore(tmp_path / "store")
    result = app(store).intake_cash_materials("600519", manifest, as_of=NOW, extractor=lambda *a: TEXT)
    earlier = datetime(2026, 10, 15, tzinfo=timezone.utc)
    view = material.read_materials(store, "600519", as_of=earlier)
    assert view["receipts"][0]["pit_status"] == "DECLARED_FUTURE"
    raw = store.load_cash_material_bytes("receipts", result["storage_reference"])
    receipt = json.loads(raw)
    receipt["document_identity_check"] = "SENSITIVE_SENTINEL_987654321"
    receipt["material_id"] = material._material_id(receipt)
    store.save_cash_material_bytes("receipts", material._encoded(receipt))
    after = material.read_materials(store, "600519", as_of=NOW)
    assert len(after["receipts"]) == 1 and after["read_errors"]
    assert "SENSITIVE_SENTINEL" not in json.dumps(after)


def test_parallel_first_write_does_not_replace_and_no_temporary_left(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = ResearchStore(tmp_path / "store")
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: store.save_cash_material_bytes("blobs", b"same-original"), range(2)))
    assert ids[0] == ids[1]
    assert store.load_cash_material_bytes("blobs", ids[0]) == b"same-original"
    assert len(list((store.dir / "cash_materials" / "blobs").iterdir())) == 1


def test_roe_real_unit_and_source_alias_still_pending(tmp_path):
    payload = supplier(unit="倍", metric="roeAvg")
    payload["metadata"]["metric"] = "roeAvg"
    payload["metadata"]["stock_code"] = "600519.SH"
    manifest = fixture_files(tmp_path, payload)
    result = app(ResearchStore(tmp_path / "store")).intake_cash_materials("600519", manifest, as_of=NOW, extractor=lambda *a: TEXT)
    assert result["supplier_items"][0]["status"] == "LOCAL_PAYLOAD_CHECKED"
    assert result["roe_definition"] == "PENDING"


def test_supplier_fingerprint_never_rounds_decimal_digits():
    left = material._supplier(json.dumps(supplier(value="12345678901234567890123456789.1")).encode(), "600519")
    right = material._supplier(json.dumps(supplier(value="12345678901234567890123456789.2")).encode(), "600519")
    equivalent = material._supplier(json.dumps(supplier(value="12345678901234567890123456789.10")).encode(), "600519")
    assert left["value_fingerprint"] != right["value_fingerprint"]
    assert left["value_fingerprint"] == equivalent["value_fingerprint"]


def test_supplier_time_gate_replayed_without_changing_material_identity(tmp_path):
    manifest = fixture_files(tmp_path)
    store = ResearchStore(tmp_path / "store")
    earlier = datetime(2023, 10, 16, tzinfo=timezone.utc)
    first = app(store).intake_cash_materials("600519", manifest, as_of=earlier, extractor=lambda *a: TEXT)
    assert first["supplier_items"][0]["reason"] == "supplier_future_period"
    later = app(store).intake_cash_materials("600519", manifest, as_of=NOW, extractor=lambda *a: TEXT)
    assert first["material_id"] == later["material_id"]
    assert later["supplier_items"][0]["status"] == "LOCAL_PAYLOAD_CHECKED"
    replay = material.read_materials(store, "600519", as_of=earlier)
    assert replay["receipts"][0]["supplier_items"][0]["reason"] == "supplier_future_period"
    assert len(store.list_cash_material_receipts()) == 1


def test_collision_verification_does_not_use_unbounded_read_bytes(tmp_path, monkeypatch):
    from pathlib import Path
    store = ResearchStore(tmp_path / "store")
    digest = store.save_cash_material_bytes("blobs", b"original")
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("unbounded collision read"))
    assert store.save_cash_material_bytes("blobs", b"original") == digest


@pytest.mark.parametrize("reason", ["timeout", "missing_dependency", "encrypted", "page_or_size_limit", "scanned_or_no_text", "malformed"])
def test_extraction_failure_reasons_are_finite_and_replayed(tmp_path, reason):
    manifest = fixture_files(tmp_path)
    store = ResearchStore(tmp_path / "store")
    def failed(*args):
        raise material.CashMaterialError(reason)
    result = app(store).intake_cash_materials("600519", manifest, as_of=NOW, extractor=failed)
    assert result["extraction_failure"] == reason
    replay = material.read_materials(store, "600519", as_of=NOW)
    assert replay["receipts"][0]["extraction_failure"] == reason
    assert result["extraction_explanation"] in result["summary"]
