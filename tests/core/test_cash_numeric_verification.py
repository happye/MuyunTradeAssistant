"""Finite v40 cash numeric contract, isolated stores and explicit extraction seams."""
import hashlib
import json
import io
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from rich.console import Console

from src.core.research_application import ResearchApplicationService
from src.data.research_store import ResearchStore
from src.core import cash_material_intake as intake
from src.core import cash_numeric_verification as numeric
import src.cli.main as cli

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def test_public_verification_branch_exists_without_research_run(tmp_path):
    app = ResearchApplicationService(store=ResearchStore(tmp_path), plans_store=None, assessment_store=None)
    with pytest.raises(ValueError, match="material"):
        app.verify_cash_material("600519", "0" * 64, as_of=NOW)


def service(root):
    return ResearchApplicationService(store=ResearchStore(root), plans_store=None, assessment_store=None)


def envelope(metric, value, year=2024, code="000001"):
    bs = ("sh." if code.startswith("6") else "sz.") + code
    interface = f"baostock.query_{'profit' if metric == 'netProfit' else 'cash_flow'}_data(code={bs},year={year},quarter=1)"
    content = dict(metric=metric, value=str(value), unit="元" if metric == "netProfit" else "倍",
        period_end=f"{year}-03-31", source_version="baostock_financial_v1", source_uri=interface,
        published_at="2024-04-27")
    return dict(schema_version=1, sha256=intake._sha(json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode()),
        content=content, metadata=dict(stock_code=code, bs_code=bs, interface=interface, metric=metric,
        year=year, quarter=1, period_end=f"{year}-03-31", source_version="baostock_financial_v1"))


def synthetic_pages(code="000001", np="80.00", cfo="20.00"):
    return [dict(page=0, text=f"证券代码：{code}\n2024 年第一季度报告"),
        dict(page=1, text="合并利润表\n2024 年1至3 月\n单位：元 币种：人民币\n项目 2024 年第一季度 2023 年第一季度"),
        dict(page=2, text=f"五、净利润（净亏损以‘-’号填列） {np} {np}"),
        dict(page=3, text="合并现金流量表\n2024 年1至3 月\n单位：元 币种：人民币\n项目 2024 年第一季度 2023 年第一季度"),
        dict(page=4, text=f"经营活动产生的现金流量净\n额 {cfo} {cfo}")]


def setup_material(tmp_path, *, pages=None, np="80.00", ratio="0.250000", code="000001", published="2024-04-27", payload_changes=None):
    pages = synthetic_pages(code, np=np) if pages is None else pages
    pdf = tmp_path / "original.pdf"
    pdf.write_bytes(b"%PDF-1.7\nonly a test seam; actual worker is tested separately")
    paths = []
    for year in (2023, 2024):
        for metric, value in (("netProfit", np), ("CFOToNP", ratio)):
            obj = envelope(metric, value, year, code)
            if payload_changes:
                payload_changes(obj)
            path = tmp_path / f"{year}-{metric}.json"
            path.write_bytes(intake._encoded(obj))
            paths.append(path.name)
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(intake._encoded(dict(schema="cash_materials.v1", original=dict(path=pdf.name, published_at=published),
        supplier_raw_paths=paths, pages=[p["page"] for p in pages])))
    app = service(tmp_path / "store")
    receipt = app.intake_cash_materials(code, manifest, as_of=NOW,
        extractor=lambda *args: "\n".join(p["text"] for p in pages))
    return app, receipt, pages, manifest


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_other_security_real_storage_restart_idempotent_and_no_public_values(tmp_path, monkeypatch):
    app, receipt, pages, _ = setup_material(tmp_path)
    proof = app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=lambda *a: pages)
    assert proof["result"] == "LOCAL_NUMERIC_CONSISTENT"
    assert proof["replay_status"] == "EXTRACTED_THIS_CALL"
    before = tree(app.store.dir)
    again = app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=lambda *a: list(reversed(pages)))
    assert again["proof_id"] == proof["proof_id"] and tree(app.store.dir) == before
    def forbidden(*a, **k):
        raise AssertionError("readonly getter performed worker or write")
    monkeypatch.setattr(numeric, "_extract_pdf", forbidden)
    monkeypatch.setattr(ResearchStore, "save_cash_material_bytes", forbidden)
    loads = []
    original = ResearchStore.load_cash_material_bytes
    def count(self, kind, digest):
        loads.append((kind, digest))
        return original(self, kind, digest)
    monkeypatch.setattr(ResearchStore, "load_cash_material_bytes", count)
    read = service(app.store.dir).get_cash_observations("000001", as_of=NOW)
    assert read["cash_numeric_proofs"]["proofs"][0]["recorded_result"] == "LOCAL_NUMERIC_CONSISTENT"
    assert read["cash_numeric_proofs"]["proofs"][0]["replay_status"] == "NOT_REPLAYED_READONLY"
    assert len(loads) == len(set(loads)) and tree(app.store.dir) == before
    public = json.dumps(read, ensure_ascii=False)
    assert "80.00" not in public and "0.250000" not in public and "token" not in public
    assert "留有本地核对一致记录" in read["summary"]
    assert proof["comparable_periods"] == 0 and proof["cash_sustainability"] == "UNKNOWN"


@pytest.mark.parametrize("case", ["parent", "continuing", "summary", "parent_table", "cfo_subtotal", "investing", "columns", "noncontinuous", "duplicate", "wan", "foreign", "blank", "nonfinite", "wrong_identity", "conflicting_header"])
def test_unique_tables_rows_and_columns_fail_closed(tmp_path, case):
    pages = synthetic_pages()
    if case in ("parent", "continuing", "summary"):
        pages[2]["text"] = pages[2]["text"].replace("五、净利润", {"parent": "归属于母公司股东的净利润", "continuing": "1.持续经营净利润", "summary": "摘要净利润"}[case])
    if case == "parent_table":
        pages[1]["text"] = pages[1]["text"].replace("合并利润表", "母公司利润表")
    if case in ("cfo_subtotal", "investing"):
        pages[4]["text"] = "经营活动现金流入小计 20 20" if case == "cfo_subtotal" else "投资活动产生的现金流量净额 20 20"
    if case == "columns":
        pages[1]["text"] = pages[1]["text"].replace("2024 年第一季度 2023", "2023 年第一季度 2024")
    if case == "noncontinuous":
        pages[2]["page"] = 6
    if case == "duplicate":
        pages[2]["text"] += "\n" + pages[2]["text"]
    if case in ("wan", "foreign"):
        pages[1]["text"] = pages[1]["text"].replace("单位：元", "单位：万元") if case == "wan" else pages[1]["text"].replace("人民币", "美元")
    if case in ("blank", "nonfinite"):
        pages[2]["text"] = pages[2]["text"].replace("80.00", "—" if case == "blank" else "NaN")
    if case == "wrong_identity":
        pages[0]["text"] = pages[0]["text"].replace("000001", "600519")
    if case == "conflicting_header":
        pages[2]["text"] = "项目 2023 年第一季度 2024 年第一季度\n" + pages[2]["text"]
    app, receipt, pages, _ = setup_material(tmp_path, pages=pages)
    result = app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=lambda *a: pages)
    assert result["result"] == "PENDING_REVIEW"
    assert result["gaps"]


@pytest.mark.parametrize("np,cfo,supplier_np,ratio,expected", [
    ("80.00", "20.00", "80.01", "0.250000", "NUMERIC_MISMATCH"),
    ("80.00", "20.00", "80", "0.250001", "NUMERIC_MISMATCH"),
    ("80.00", "20.00", "80", "0.2500000", "LOCAL_NUMERIC_CONSISTENT"),
    ("80.00", "20.00", "80", "0.2500001", "PENDING_REVIEW"),
    ("80.00", "20.00", "80", "25%", "PENDING_REVIEW"),
    ("-80", "-20", "-80", "0.25", "LOCAL_NUMERIC_CONSISTENT"),
    ("80", "-20", "80", "-0.25", "LOCAL_NUMERIC_CONSISTENT"),
    ("0", "20", "0", "0", "PENDING_REVIEW"),
    ("2000000", "1", "2000000", "0.000001", "LOCAL_NUMERIC_CONSISTENT"),
    ("2000000", "-1", "2000000", "-0.000001", "LOCAL_NUMERIC_CONSISTENT"),
    ("80", "20", "8E+1", "2.5E-1", "LOCAL_NUMERIC_CONSISTENT"),
    ("80", "20", "1E+99999999", "0.25", "PENDING_REVIEW"),
])
def test_exact_decimal_and_half_up(tmp_path, np, cfo, supplier_np, ratio, expected):
    pages = synthetic_pages(np=np, cfo=cfo)
    app, receipt, pages, _ = setup_material(tmp_path, pages=pages, np=supplier_np, ratio=ratio)
    result = app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=lambda *a: pages)
    assert result["recorded_result"] == expected
    if supplier_np == "1E+99999999":
        context = {}
        intake.read_materials(app.store, "000001", as_of=NOW, _verified_context=context)
        assert all(s["value"] is None and s["numeric_status"] == "numeric_budget"
                   for s in context["suppliers"].values() if s["metric"] == "netProfit")
        assert len(intake._encoded(context)) < 12000
        assert numeric.GAPS["numeric_budget"] in result["gaps"]


def test_decimal_large_coefficients_do_not_normalize_or_round(tmp_path):
    np = "123456789012345678901234567890.12"
    pages = synthetic_pages(np=np, cfo=np)
    app, receipt, pages, _ = setup_material(tmp_path, pages=pages, np=np, ratio="1")
    assert app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=lambda *a: pages)["result"] == "LOCAL_NUMERIC_CONSISTENT"
    assert numeric._ratio(Decimal("0.0000005"), Decimal("1")) == Decimal("0.000001")


@pytest.mark.parametrize("case", ["identity", "exchange", "interface", "period", "metric", "unit", "metadata", "hash"])
def test_supplier_identity_binding_cannot_be_filled(tmp_path, case):
    def corrupt(obj):
        if case == "identity": obj["metadata"]["stock_code"] = "600519"
        if case == "exchange": obj["metadata"]["bs_code"] = "sh.000001"
        if case == "interface": obj["metadata"]["interface"] = "query_profit"
        if case == "period": obj["metadata"]["period_end"] = "2024-06-30"
        if case == "metric": obj["metadata"]["metric"] = "roeAvg"
        if case == "unit": obj["content"]["unit"] = "万元"
        if case == "metadata": obj.pop("metadata")
        if case == "hash": obj["sha256"] = "0" * 64
    app, receipt, pages, _ = setup_material(tmp_path, payload_changes=corrupt)
    result = app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=lambda *a: pages)
    assert result["result"] == "PENDING_REVIEW"


def test_time_gates_not_frozen_in_id_and_comparative_not_first_issue(tmp_path):
    app, receipt, pages, _ = setup_material(tmp_path)
    past = datetime(2024, 1, 1, tzinfo=timezone.utc)
    early = app.verify_cash_material("000001", receipt["material_id"], as_of=past, extractor=lambda *a: pages)
    late = app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=lambda *a: pages)
    assert early["proof_id"] == late["proof_id"]
    assert early["result"] == "PENDING_REVIEW" and late["result"] == "LOCAL_NUMERIC_CONSISTENT"
    assert early["recorded_result"] == "LOCAL_NUMERIC_CONSISTENT"
    same = app.get_cash_observations("000001", as_of=datetime(2024, 4, 27, tzinfo=timezone.utc))["cash_numeric_proofs"]["proofs"][0]
    assert same["result"] == "PENDING_REVIEW"
    assert same["source_authenticity"].startswith("PENDING") and same["comparable_periods"] == 0
    assert {r["role"] for r in late["locations"] if r["period_end"] == "2023-03-31"} == {"comparative"}


@pytest.mark.parametrize("kind", ["blobs_pdf", "blobs_raw", "proofs", "receipts", "proof_arithmetic", "proof_binding", "proof_location"])
def test_corrupted_or_missing_dependencies_never_show_consistency(tmp_path, kind):
    app, receipt, pages, _ = setup_material(tmp_path)
    proof = app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=lambda *a: pages)
    if kind.startswith("proof_"):
        obj = intake._json(app.store.load_cash_material_bytes("proofs", proof["reference"]))
        if kind == "proof_arithmetic": obj["rows"][0]["token"] = "999"
        if kind == "proof_binding": obj["dependencies"]["receipt_sha256"] = "0" * 64
        if kind == "proof_location": obj["rows"][0]["page"] = -1
        obj["proof_id"] = numeric._identity(obj)
        app.store.save_cash_material_bytes("proofs", intake._encoded(obj))
        (app.store.dir / "cash_materials/proofs" / proof["reference"]).unlink()
    else:
        rec = intake._json(app.store.load_cash_material_bytes("receipts", receipt["storage_reference"]))
        namespace = kind.split("_")[0]
        reference = (rec["pdf_sha256"] if kind == "blobs_pdf" else rec["suppliers"][0]["blob_sha256"]
                     if kind == "blobs_raw" else proof["reference"] if kind == "proofs" else receipt["storage_reference"])
        (app.store.dir / "cash_materials" / namespace / reference).write_bytes(b"damaged")
    read = service(app.store.dir).get_cash_observations("000001", as_of=NOW)
    assert not read["cash_numeric_proofs"]["proofs"]
    assert read["cash_numeric_proofs"]["read_errors"]


def test_half_write_does_not_claim_completion(tmp_path, monkeypatch):
    app, receipt, pages, _ = setup_material(tmp_path)
    original = ResearchStore.save_cash_material_bytes
    def fail(self, kind, content):
        if kind == "proofs":
            raise OSError("test write failure")
        return original(self, kind, content)
    monkeypatch.setattr(ResearchStore, "save_cash_material_bytes", fail)
    with pytest.raises(OSError):
        app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=lambda *a: pages)
    assert not app.store.list_cash_numeric_proofs()


def test_real_fixture_pdf_pages_and_four_complete_supplier_payloads(tmp_path):
    """Real PDF parser input; this in-process seam does not prove subprocess execution."""
    from pypdf import PdfReader
    pdf = Path("tests/artifacts/k3_original_pilot/600519_2024Q1.pdf")
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == "b706e237e25ff54a0bde7070a13b019a5e1eec7f412eb9d0baf33e750a7c32e2"
    reader = PdfReader(pdf)
    pages = [dict(page=p, text=reader.pages[p].extract_text()) for p in (0, 7, 8, 9, 10)]
    sources = []
    for path in Path("tests/artifacts/probe_fin_semantics_raw/raw").glob("*.json"):
        obj = json.loads(path.read_text(encoding="utf-8"))
        content, meta = obj["content"], obj["metadata"]
        if content.get("metric") in ("netProfit", "CFOToNP") and content.get("period_end") in ("2023-03-31", "2024-03-31") and meta.get("stock_code") == "600519":
            sources.append(str(path.resolve()))
    assert len(sources) == 4
    manifest = tmp_path / "real.json"
    manifest.write_bytes(intake._encoded(dict(schema="cash_materials.v1", original=dict(path=str(pdf.resolve()), published_at="2024-04-27"),
        supplier_raw_paths=sources, pages=[0, 7, 8, 9, 10])))
    app = service(tmp_path / "store")
    receipt = app.intake_cash_materials("600519", manifest, as_of=NOW, extractor=lambda *a: "\n".join(p["text"] for p in pages))
    proof = app.verify_cash_material("600519", receipt["material_id"], as_of=NOW, extractor=lambda *a: pages)
    assert proof["result"] == "LOCAL_NUMERIC_CONSISTENT"
    assert {(r["table"], r["page"], r["header_page"]) for r in proof["locations"]} == {("NP", 8, 7), ("CFO", 10, 9)}
    assert service(app.store.dir).get_cash_observations("600519", as_of=NOW)["cash_numeric_proofs"]["proofs"][0]["recorded_result"] == "LOCAL_NUMERIC_CONSISTENT"


def test_public_repl_cli_text_json_and_mixed_flags(tmp_path, monkeypatch, capsys):
    import start
    import src.data.research_store as stores
    app, receipt, pages, _ = setup_material(tmp_path)
    monkeypatch.setattr(stores, "RESEARCH_DIR", app.store.dir)
    monkeypatch.setattr(numeric, "_extract_pdf", lambda *a: pages)
    monkeypatch.setattr(ResearchApplicationService, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("research ran")))
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=200))
    mode, args = start.parse_input(f"research 000001 --cash-verify {receipt['material_id']} --json")
    start.run_cli(mode, args)
    result = json.loads(capsys.readouterr().out)
    assert result["result"] == "LOCAL_NUMERIC_CONSISTENT"
    cli.research_command(["000001", "--cash-verify", receipt["material_id"]])
    assert "本次本地数值核对完成" in output.getvalue()
    for flag in ("--capture", "--cash-materials", "--claims", "--checkpoint", "--confirm-risk"):
        cli.research_command(["000001", "--cash-verify", receipt["material_id"], flag, "--json"])
        assert json.loads(capsys.readouterr().out)["error"] == "mixed_research_flags"


def test_material_isolation_algorithm_version_and_legacy_state_unchanged(tmp_path, monkeypatch):
    app, receipt, pages, manifest = setup_material(tmp_path)
    legacy = app.store.dir / "originals/legacy"
    legacy.parent.mkdir()
    legacy.write_bytes(b"legacy original state")
    accepted = app.store.dir / "accepted_refs.json"
    accepted.write_bytes(b'{"retained":"accepted"}')
    before = {legacy: legacy.read_bytes(), accepted: accepted.read_bytes()}
    first = app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=lambda *a: pages)
    obj = json.loads(manifest.read_bytes())
    obj["original"]["source_uri"] = "https://example.invalid/second-declaration"
    manifest.write_bytes(intake._encoded(obj))
    other = app.intake_cash_materials("000001", manifest, as_of=NOW, extractor=lambda *a: "\n".join(p["text"] for p in pages))
    second = app.verify_cash_material("000001", other["material_id"], as_of=NOW, extractor=lambda *a: pages)
    assert first["material_id"] != second["material_id"] and first["proof_id"] != second["proof_id"]
    monkeypatch.setattr(numeric, "CALCULATION_VERSION", "decimal_ratio_half_up_6.test_next")
    new = app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=lambda *a: pages)
    assert new["proof_id"] != first["proof_id"]
    assert len(app.store.list_cash_numeric_proofs()) == 3
    assert all(p.read_bytes() == value for p, value in before.items())
    assert set(p.name for p in app.store.dir.iterdir()) == {"cash_materials", "originals", "accepted_refs.json"}


def test_timeout_is_pending_and_record_can_be_read_without_extraction(tmp_path):
    app, receipt, pages, _ = setup_material(tmp_path)
    def timeout(*a):
        raise TimeoutError("sensitive arbitrary contents")
    result = app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=timeout)
    assert result["recorded_result"] == "PENDING_REVIEW"
    assert result["arithmetic_check"] == "NOT_AVAILABLE"
    assert "sensitive" not in json.dumps(result)
    read = app.get_cash_observations("000001", as_of=NOW)["cash_numeric_proofs"]
    assert read["proofs"][0]["recorded_result"] == "PENDING_REVIEW" and not read["read_errors"]


def test_supplier_conflict_and_absence_remain_pending(tmp_path):
    app, receipt, pages, manifest = setup_material(tmp_path)
    obj = json.loads(manifest.read_bytes())
    # Replace one other metric with an independently bound, conflicting NP payload.
    conflict = tmp_path / "conflict.json"
    conflict.write_bytes(intake._encoded(envelope("netProfit", "80.01", 2024)))
    obj["supplier_raw_paths"][0] = conflict.name
    manifest.write_bytes(intake._encoded(obj))
    receipt = app.intake_cash_materials("000001", manifest, as_of=NOW, extractor=lambda *a: "\n".join(p["text"] for p in pages))
    proof = app.verify_cash_material("000001", receipt["material_id"], as_of=NOW, extractor=lambda *a: pages)
    assert proof["result"] == "PENDING_REVIEW"
    assert "同期间同字段存在多个不同留样值" in proof["gaps"]
    assert "缺少同期间净利润或现金净额比率留样" in proof["gaps"]


def test_concurrent_first_proofs_merge_on_read_without_overwrite(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from itertools import count
    from time import sleep
    app, receipt, pages, _ = setup_material(tmp_path)
    barrier = Barrier(2)
    order = count()
    original = ResearchStore.list_cash_numeric_proofs
    def synchronized(self):
        refs = original(self)
        ordinal = next(order)
        barrier.wait(timeout=10)
        sleep(0.025 * ordinal)  # Force different recorded_at values after both empty reads.
        return refs
    monkeypatch.setattr(ResearchStore, "list_cash_numeric_proofs", synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: app.verify_cash_material("000001", receipt["material_id"],
            as_of=NOW, extractor=lambda *a: pages), range(2)))
    monkeypatch.setattr(ResearchStore, "list_cash_numeric_proofs", original)
    assert results[0]["proof_id"] == results[1]["proof_id"]
    before = tree(app.store.dir)
    read = service(app.store.dir).get_cash_observations("000001", as_of=NOW)["cash_numeric_proofs"]
    assert len(read["proofs"]) == 1
    assert set(read["proofs"][0]["storage_references"]) == {r["reference"] for r in results}
    assert tree(app.store.dir) == before
