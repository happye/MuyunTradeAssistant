"""Bounded local cash-material intake. No research qualifications are granted."""
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path

VERSION = "cash_materials.v1"
PDF_LIMIT = 20 * 1024 * 1024
RAW_LIMIT = 4 * 1024 * 1024
EXTRACTION_FAILURES = {
    "timeout": "PDF提取超时，待缩小页码范围后核对",
    "missing_dependency": "PDF提取依赖缺失，待修复本地依赖",
    "encrypted": "PDF已加密，待提供可读取原件",
    "page_or_size_limit": "PDF页码或资源超限，待核对页码与文件大小",
    "scanned_or_no_text": "PDF未提取到文本，可能是扫描件，待人工核对",
    "malformed": "PDF格式或提取结果无效，待核对原件",
    "unavailable": "PDF提取暂不可用，待核对本地运行环境",
}


class CashMaterialError(ValueError):
    """Finite public error category; never include file contents."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = value
        return out
    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON number")))


def _read(path, limit):
    import os
    import stat
    with Path(path).open("rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("material must be regular file")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("material resource limit")
    return data


def _sid(value):
    from src.data.financial_data import _to_baostock_code, _response_security_id
    canonical = _to_baostock_code(str(value or ""))
    sid = _response_security_id(value, canonical)
    if sid is None:
        raise ValueError("supplier_identity_invalid")
    return sid


def _publication(value, as_of):
    if value is None:
        return "DECLARATION_MISSING"
    if not isinstance(value, str):
        raise ValueError("invalid publication declaration")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        parsed = date.fromisoformat(value)
        return "DECLARED_FUTURE" if parsed > as_of.date() else "DECLARED_SAME_DAY_TIME_UNKNOWN" if parsed == as_of.date() else "DECLARED_ONLY"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[.]\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
        raise ValueError("publication requires full calendar date or aware timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return "DECLARED_FUTURE" if parsed > as_of else "DECLARED_ONLY"


def _supplier(data, requested):
    """Validate independent payload identity; do not fill identity from the request."""
    from decimal import Decimal, InvalidOperation
    envelope = _json(data)
    if not isinstance(envelope, dict) or type(envelope.get("schema_version")) is not int or envelope.get("schema_version") != 1:
        raise ValueError("supplier_schema")
    content, meta = envelope.get("content"), envelope.get("metadata")
    if not isinstance(content, dict) or not isinstance(meta, dict):
        raise ValueError("supplier_envelope")
    legacy = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    if envelope.get("sha256") != _sha(legacy):
        raise ValueError("supplier_content_hash")
    interface = meta.get("interface")
    match = re.fullmatch(r"baostock.query_(profit|cash_flow)_data\(code=((?:sh|sz|bj)[.][0-9]{6}),year=(\d{4}),quarter=1\)", str(interface))
    if not match:
        raise ValueError("supplier_interface_or_period")
    identities = [_sid(match[2])]
    for obj, keys in ((content, ("security_id", "stock_code", "bs_code")), (meta, ("stock_code", "bs_code"))):
        for key in keys:
            if key in obj:
                identities.append(_sid(obj[key]))
    if "security_id" not in content and not all(k in meta for k in ("stock_code", "bs_code")):
        raise ValueError("supplier_identity_missing")
    if len(set(identities)) != 1 or identities[0] != requested:
        raise ValueError("supplier_identity_mismatch")
    period = match[3] + "-03-31"
    date.fromisoformat(period)
    metric = content.get("metric")
    allowed = {"netProfit": ("profit", "元"), "CFOToNP": ("cash_flow", "倍"), "roeAvg": ("profit", "倍")}
    if metric not in allowed or match[1] != allowed[metric][0]:
        raise ValueError("supplier_field_interface")
    if any(obj.get("period_end") != period for obj in (content, meta)) or type(meta.get("year")) is not int or type(meta.get("quarter")) is not int or meta.get("year") != int(match[3]) or meta.get("quarter") != 1:
        raise ValueError("supplier_period_mismatch")
    if any(obj.get("source_version") != "baostock_financial_v1" for obj in (content, meta)) or content.get("unit") != allowed[metric][1]:
        raise ValueError("supplier_version_or_unit")
    if meta.get("metric") != metric or content.get("source_uri") != interface:
        raise ValueError("supplier_field_binding")
    publication = content.get("published_at")
    if publication is not None:
        from datetime import timezone
        _publication(publication, datetime(9999, 1, 1, tzinfo=timezone.utc))
    if "published_at" in meta and meta["published_at"] != publication:
        raise ValueError("supplier_publication_mismatch")
    value = content.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("supplier_number")
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("supplier_number")
    except InvalidOperation:
        raise ValueError("supplier_number") from None
    sign, digits, exponent = number.as_tuple()
    digits = list(digits)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    canonical_number = [sign, digits, exponent] if digits else [0, [], 0]
    return {"security_id": identities[0], "period_end": period, "metric": metric,
            "content_sha256": envelope["sha256"], "published_at": publication,
            "value_fingerprint": _sha(_encoded(canonical_number))}


def _extract_pdf(data, pages):
    """One killable fixed worker; no shell, configuration or product imports."""
    import base64
    import subprocess
    import sys
    request = _encoded({"pdf": base64.b64encode(data).decode("ascii"), "pages": pages})
    try:
        completed = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--pdf-worker"],
            input=request, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=True)
    except subprocess.TimeoutExpired:
        raise CashMaterialError("timeout") from None
    except subprocess.CalledProcessError:
        raise CashMaterialError("malformed") from None
    result = _json(completed.stdout)
    if isinstance(result, dict) and result.get("error") in EXTRACTION_FAILURES:
        raise CashMaterialError(result["error"])
    if not isinstance(result, dict) or not isinstance(result.get("text"), str) or len(result["text"]) > 400000:
        raise ValueError("pdf extraction invalid")
    return result["text"]


def _document(text, requested):
    codes = set(re.findall(r"(?:证券|股票)代码\s*[:：]?\s*([0-9]{6})(?![0-9])", text))
    years = set(re.findall(r"(20[0-9]{2})\s*年\s*第[一1]季度报告", text))
    identity = "MATCHED_LOCAL_TEXT" if codes == {requested} and len(years) == 1 else "PENDING_DOCUMENT_IDENTITY"
    return {"document_identity_check": identity,
            "report_periods": [next(iter(years)) + "-03-31"] if identity == "MATCHED_LOCAL_TEXT" else [],
            "table_context": "PENDING_UNIQUE_COLUMNS_AND_ROWS" if all(term in text for term in ("合并", "人民币", "净利润", "经营活动产生的现金流量净额")) else "PENDING_TABLE_CONTEXT"}


def intake(store, security_id, manifest_path, *, as_of, extractor=None):
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ValueError("aware as_of required")
    sid = _sid(security_id)
    manifest_path = Path(manifest_path)
    manifest = _json(_read(manifest_path, 65536))
    if not isinstance(manifest, dict) or set(manifest) - {"schema", "original", "supplier_raw_paths", "pages"} or manifest.get("schema") != VERSION:
        raise ValueError("invalid cash material manifest")
    original = manifest.get("original")
    paths = manifest.get("supplier_raw_paths", [])
    pages = manifest.get("pages")
    if not isinstance(original, dict) or set(original) - {"path", "source_uri", "published_at"} or not isinstance(original.get("path"), str):
        raise ValueError("original PDF required")
    if not isinstance(paths, list) or len(paths) > 4 or not all(isinstance(p, str) and p for p in paths):
        raise ValueError("zero to four supplier payloads required")
    if pages is not None and (not isinstance(pages, list) or not 1 <= len(pages) <= 6 or any(type(p) is not int or p < 0 or p >= 40 for p in pages) or len(set(pages)) != len(pages)):
        raise ValueError("invalid PDF pages")
    if original.get("source_uri") is not None and (not isinstance(original["source_uri"], str) or len(original["source_uri"]) > 2048):
        raise ValueError("invalid source declaration")
    pit = _publication(original.get("published_at"), as_of)
    pdf = _read(manifest_path.parent / original["path"], PDF_LIMIT)
    if not pdf.startswith(b"%PDF-"):
        raise ValueError("invalid PDF header")
    raw_files = [_read(manifest_path.parent / p, RAW_LIMIT) for p in paths]
    if sum(map(len, raw_files)) > RAW_LIMIT:
        raise ValueError("supplier total resource limit")
    # Extraction failures do not invent document identity or values.
    try:
        text = (extractor or _extract_pdf)(pdf, pages)
        if not isinstance(text, str):
            raise CashMaterialError("malformed")
        if not text.strip():
            raise CashMaterialError("scanned_or_no_text")
        document = _document(text, sid)
        extraction = {"version": "cash_pdf_text.v1", "text_sha256": _sha(text.encode("utf-8")),
                      "pdf_sha256": _sha(pdf), "selected_pages": pages, "failure_reason": None}
    except Exception as error:
        if isinstance(error, CashMaterialError) and str(error) in EXTRACTION_FAILURES:
            failure = str(error)
        elif isinstance(error, TimeoutError):
            failure = "timeout"
        elif isinstance(error, ModuleNotFoundError):
            failure = "missing_dependency"
        elif isinstance(error, (ValueError, TypeError)):
            failure = "malformed"
        else:
            failure = "unavailable"
        document = {"document_identity_check": "PENDING_PDF_EXTRACTION", "report_periods": [], "table_context": "PENDING_EXTRACTION_OR_RESOURCE_LIMIT"}
        extraction = {"version": "cash_pdf_text.v1", "text_sha256": None, "pdf_sha256": _sha(pdf), "selected_pages": pages, "failure_reason": failure}
    suppliers = []
    for raw in raw_files:
        descriptor = {"blob_sha256": _sha(raw)}
        try:
            checked = _supplier(raw, sid)
            descriptor.update(checked, status="LOCAL_PAYLOAD_CHECKED")
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            reason = str(error)
            descriptor.update(status="REJECTED_PENDING_REVIEW", reason=reason if reason in REASONS else "supplier_invalid_format")
        suppliers.append(descriptor)
    receipt = {"schema": VERSION, "security_id": sid, "pdf_sha256": _sha(pdf),
        "declarations": original, "pit_status": pit, "pages": pages, "extraction": extraction, **document,
        "suppliers": suppliers, "field_consistency": "PENDING_UNIQUE_TABLE_DEFINITION",
        "source_authenticity": "PENDING_INDEPENDENT_SOURCE_VERIFICATION", "roe_definition": "PENDING"}
    receipt["material_id"] = _material_id(receipt)
    # Reimport returns the first existing immutable record with the same material identity.
    existing = read_materials(store, sid, as_of=as_of)
    for view in existing["receipts"]:
        if view["receipt_id"] == receipt["material_id"]:
            return view
    receipt["recorded_at"] = datetime.now().astimezone().isoformat(timespec="microseconds")
    # Every supplied original is preserved; only a last committed receipt makes it discoverable.
    try:
        for blob in [pdf, *raw_files]:
            store.save_cash_material_bytes("blobs", blob)
        receipt_id = store.save_cash_material_bytes("receipts", _encoded(receipt))
    except (OSError, ValueError):
        raise CashMaterialError("storage_failure") from None
    return _public(receipt, receipt_id, as_of=as_of)


REASONS = {name: text for name, text in (
    ("supplier_schema", "供应商格式版本无效"), ("supplier_envelope", "供应商完整封装缺失"),
    ("supplier_content_hash", "留样内容与声明哈希不符"), ("supplier_interface_or_period", "接口或季度不在本批范围"),
    ("supplier_identity_missing", "缺独立证券身份"), ("supplier_identity_invalid", "证券或交易所无效"),
    ("supplier_identity_mismatch", "证券身份不一致"), ("supplier_field_interface", "字段与接口不一致"),
    ("supplier_period_mismatch", "实际报告期不一致"), ("supplier_version_or_unit", "来源版本或单位待核"),
    ("supplier_field_binding", "字段与来源绑定不一致"), ("supplier_publication_mismatch", "公开日期声明不一致"),
    ("supplier_number", "数值格式无效"), ("supplier_future_period", "报告期晚于分析时点"),
    ("supplier_future_publication", "声明公开日晚于分析时点，历史资格待核"),
    ("supplier_invalid_format", "格式、重复键或日期声明无效"))}


def _material_id(receipt):
    return _sha(_encoded({k: v for k, v in receipt.items() if k not in ("recorded_at", "material_id", "pit_status")}))


def _public(receipt, receipt_id, *, as_of):
    # Public output never trusts free text from disk, even a newly addressed receipt.
    if (receipt.get("schema") != VERSION or _sid(receipt.get("security_id")) != receipt.get("security_id")
            or receipt.get("document_identity_check") not in ("MATCHED_LOCAL_TEXT", "PENDING_DOCUMENT_IDENTITY", "PENDING_PDF_EXTRACTION")
            or receipt.get("pit_status") not in ("DECLARATION_MISSING", "DECLARED_FUTURE", "DECLARED_SAME_DAY_TIME_UNKNOWN", "DECLARED_ONLY")
            or receipt.get("field_consistency") != "PENDING_UNIQUE_TABLE_DEFINITION"
            or receipt.get("source_authenticity") != "PENDING_INDEPENDENT_SOURCE_VERIFICATION"
            or not isinstance(receipt.get("report_periods"), list)
            or any(not isinstance(p, str) or not re.fullmatch(r"20[0-9]{2}-03-31", p) for p in receipt["report_periods"])
            or len(receipt["report_periods"]) > 1
            or not isinstance(receipt.get("suppliers"), list) or len(receipt["suppliers"]) > 4
            or any(s.get("status") not in ("LOCAL_PAYLOAD_CHECKED", "REJECTED_PENDING_REVIEW") or not re.fullmatch(r"[0-9a-f]{64}", str(s.get("blob_sha256"))) or (s["status"] == "REJECTED_PENDING_REVIEW" and s.get("reason") not in REASONS) for s in receipt["suppliers"])):
        raise ValueError("invalid receipt output fields")
    if receipt.get("material_id") != _material_id(receipt):
        raise ValueError("receipt material identity mismatch")
    recorded = datetime.fromisoformat(receipt["recorded_at"])
    if recorded.tzinfo is None:
        raise ValueError("receipt recorded time invalid")
    extraction = receipt["extraction"]
    if extraction.get("version") != "cash_pdf_text.v1" or extraction.get("pdf_sha256") != receipt["pdf_sha256"] or extraction.get("selected_pages") != receipt["pages"] or (receipt["document_identity_check"] != "PENDING_PDF_EXTRACTION" and not re.fullmatch(r"[0-9a-f]{64}", str(extraction.get("text_sha256")))):
        raise ValueError("receipt extraction binding invalid")
    failure = extraction.get("failure_reason")
    if (receipt["document_identity_check"] == "PENDING_PDF_EXTRACTION" and failure not in EXTRACTION_FAILURES) or (receipt["document_identity_check"] != "PENDING_PDF_EXTRACTION" and failure is not None):
        raise ValueError("receipt extraction failure invalid")
    supplier_items = []
    for supplier in receipt["suppliers"]:
        item = {"reference": supplier["blob_sha256"], "status": supplier["status"]}
        if "reason" in supplier:
            item.update(reason=supplier["reason"], explanation=REASONS[supplier["reason"]])
        else:
            publication_status = _publication(supplier.get("published_at"), as_of)
            item["publication_status"] = publication_status
            if date.fromisoformat(supplier["period_end"]) > as_of.date():
                item.update(status="REJECTED_PENDING_REVIEW", reason="supplier_future_period",
                            explanation=REASONS["supplier_future_period"])
            elif publication_status == "DECLARED_FUTURE":
                item.update(status="REJECTED_PENDING_REVIEW", reason="supplier_future_publication",
                            explanation=REASONS["supplier_future_publication"])
        supplier_items.append(item)
    return {"receipt_id": receipt["material_id"], "material_id": receipt["material_id"], "storage_reference": receipt_id, "collection_times": [recorded.isoformat()], "security_id": receipt["security_id"], "intake_status": "ARCHIVED_PENDING_REVIEW",
        "document_identity_check": receipt["document_identity_check"], "report_periods": receipt["report_periods"],
        "field_consistency": receipt["field_consistency"], "source_authenticity": receipt["source_authenticity"],
        "pit_status": _publication(receipt["declarations"].get("published_at"), as_of), "roe_definition": "PENDING", "industry": None,
        "comparable_periods": 0, "cash_sustainability": "UNKNOWN",
        "supplier_items": supplier_items,
        "extraction_failure": failure, "extraction_explanation": EXTRACTION_FAILURES.get(failure),
        "summary": "现金材料已归档；原件表格唯一列、字段定义与来源真实性待核；未授予数值或历史资格" + ("；供应商留样未提供，字段待核" if not supplier_items else "") + ("；" + EXTRACTION_FAILURES[failure] if failure else "")}


def read_materials(store, security_id, *, as_of, _verified_context=None):
    views, reasons, cache, values = [], [], {}, {}
    try:
        references = store.list_cash_material_receipts()
    except Exception:
        references = []
        reasons.append("receipt_directory_unreadable")
    for reference in references:
        try:
            receipt = _json(store.load_cash_material_bytes("receipts", reference))
            if receipt.get("schema") != VERSION:
                raise ValueError("receipt schema")
            if receipt.get("security_id") != security_id:
                continue
            view = _public(receipt, reference, as_of=as_of)
            for digest in [receipt["pdf_sha256"], *(s["blob_sha256"] for s in receipt["suppliers"])]:
                if digest not in cache:
                    try:
                        raw = store.load_cash_material_bytes("blobs", digest)
                        if digest == receipt["pdf_sha256"]:
                            if not raw.startswith(b"%PDF-"):
                                raise ValueError("PDF header damaged")
                            cache[digest] = True
                        else:
                            try:
                                cache[digest] = _supplier(raw, security_id)
                                if _verified_context is not None:
                                    # Compact, request-local inputs only. No PDF bytes are retained.
                                    from decimal import Decimal
                                    from src.core.cash_numeric_verification import _compact_supplier_number
                                    exact_value = json.loads(raw.decode("utf-8"), parse_float=Decimal,
                                                             parse_int=Decimal)["content"]["value"]
                                    _verified_context.setdefault("suppliers", {})[digest] = {
                                        **cache[digest], **_compact_supplier_number(exact_value)}
                            except (ValueError, TypeError, KeyError, OverflowError):
                                cache[digest] = False
                    except Exception:
                        cache[digest] = None
                if cache[digest] is None:
                    raise ValueError("unreadable blob")
            for supplier in receipt["suppliers"]:
                if supplier["status"] == "LOCAL_PAYLOAD_CHECKED":
                    actual = cache[supplier["blob_sha256"]]
                    if not isinstance(actual, dict):
                        raise ValueError("supplier descriptor unreadable")
                    if any(actual[k] != supplier[k] for k in actual):
                        raise ValueError("supplier receipt mismatch")
                    if date.fromisoformat(actual["period_end"]) <= as_of.date() and _publication(actual.get("published_at"), as_of) != "DECLARED_FUTURE":
                        key = (actual["period_end"], actual["metric"])
                        values.setdefault(key, set()).add(actual["value_fingerprint"])
            if _verified_context is not None:
                _verified_context.setdefault("receipts", {})[reference] = receipt
            duplicate = next((v for v in views if v["receipt_id"] == view["receipt_id"]), None)
            if duplicate is None:
                views.append(view)
            else:
                duplicate["collection_times"] = sorted(set(duplicate["collection_times"] + view["collection_times"]))
                duplicate["storage_references"] = sorted(set(duplicate.get("storage_references", [duplicate["storage_reference"]]) + [view["storage_reference"]]))
        except Exception:
            reasons.append("receipt_or_blob_missing_damaged_or_invalid")
    conflicts = [{"period_end": p, "metric": m, "status": "CONFLICT_PENDING_REVIEW"}
                 for (p, m), variants in sorted(values.items()) if len(variants) > 1]
    return {"receipts": views, "incomplete": True, "coverage_complete": False,
            "read_errors": sorted(set(reasons)), "conflicts": conflicts,
            "industry": None, "comparable_periods": 0, "cash_sustainability": "UNKNOWN"}


def _pdf_worker():
    import base64
    import io
    import os
    import sys
    sys.dont_write_bytecode = True
    def deny(event, args):
        if event in ("socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            raise RuntimeError("PDF worker external action prohibited")
        if event == "open":
            mode, flags = args[1:3]
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
                raise RuntimeError("PDF worker writes prohibited")
        if event in ("os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.replace", "os.link", "os.symlink"):
            raise RuntimeError("PDF worker writes prohibited")
    sys.addaudithook(deny)
    request_bytes = sys.stdin.buffer.read(28 * 1024 * 1024 + 1)
    if len(request_bytes) > 28 * 1024 * 1024:
        raise CashMaterialError("page_or_size_limit")
    request = _json(request_bytes)
    data = base64.b64decode(request["pdf"], validate=True)
    if len(data) > PDF_LIMIT:
        raise CashMaterialError("page_or_size_limit")
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError:
        raise CashMaterialError("missing_dependency") from None
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise CashMaterialError("encrypted")
    if len(reader.pages) > 40:
        raise CashMaterialError("page_or_size_limit")
    pages = request.get("pages")
    if pages is None:
        pages = list(range(min(6, len(reader.pages))))
    if len(pages) > 6 or any(type(p) is not int or not 0 <= p < len(reader.pages) for p in pages):
        raise CashMaterialError("page_or_size_limit")
    text = "\n".join(reader.pages[p].extract_text() or "" for p in pages)
    if len(text) > 400000:
        raise CashMaterialError("page_or_size_limit")
    if not text.strip():
        raise CashMaterialError("scanned_or_no_text")
    sys.stdout.buffer.write(_encoded({"text": text}))


if __name__ == "__main__":
    import sys
    if sys.argv[1:] != ["--pdf-worker"]:
        raise SystemExit(2)
    try:
        _pdf_worker()
    except CashMaterialError as error:
        sys.stdout.buffer.write(_encoded({"error": str(error) if str(error) in EXTRACTION_FAILURES else "malformed"}))
    except Exception:
        sys.stdout.buffer.write(_encoded({"error": "malformed"}))
