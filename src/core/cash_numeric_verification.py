"""Bounded Q1 consolidated-table arithmetic, separate from research qualification."""
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path

VERSION = "cash_numeric_proof.v1"
PARSER_VERSION = "cash_q1_table.v1"
PARTIAL_PARSER_VERSION = "cash_q1_table_partial.v2"
CALCULATION_VERSION = "decimal_ratio_half_up_6.v1"
PDF_LIMIT = 20 * 1024 * 1024
GAPS = {
    "document_identity": "文内证券或季度报告版本不能独立唯一确认",
    "table_context": "合并表、人民币元、季度列或连续页表头不能唯一确认",
    "target_row": "合并净利润总行或经营现金净额行不能唯一定位",
    "supplier_missing": "缺少同期间净利润或现金净额比率留样",
    "supplier_rejected": "供应商身份、接口、期间、单位或完整性待核",
    "supplier_conflict": "同期间同字段存在多个不同留样值",
    "supplier_precision": "供应商比率超过六位有效小数，舍入规范待核",
    "numeric_budget": "数值词元、有效位或指数超过本地核对资源界限，待核",
    "zero_denominator": "合并净利润为零，不能计算现金净额比率",
    "numeric_mismatch": "原件与供应商数值不一致，待独立复核",
    "extraction": "PDF提取失败或资源受限，待核对原件及页码",
}
RESULTS = {"LOCAL_NUMERIC_CONSISTENT", "NUMERIC_MISMATCH", "PENDING_REVIEW"}


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _number(token):
    if not isinstance(token, str) or len(token) > 200 or not re.fullmatch(r"-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:[.][0-9]+)?", token):
        raise ValueError("numeric token invalid")
    value = Decimal(token.replace(",", ""))
    if not value.is_finite():
        raise ValueError("nonfinite numeric token")
    return value


def _fractional_places(number):
    _, digits, exponent = number.as_tuple()
    digits = list(digits)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return max(0, -exponent) if digits else 0


def _supplier_number(token):
    if not isinstance(token, str) or len(token) > 200:
        raise ValueError("numeric_budget")
    value = Decimal(token)
    if not value.is_finite() or len(value.as_tuple().digits) > 200 or abs(value.as_tuple().exponent) > 400:
        raise ValueError("numeric_budget")
    return value


def _compact_supplier_number(value):
    """Bound the private context before retaining any token; never expand exponents."""
    if isinstance(value, Decimal):
        parts = value.as_tuple()
        if not value.is_finite() or len(parts.digits) > 200 or abs(parts.exponent) > 400:
            return {"value": None, "numeric_status": "numeric_budget"}
    if isinstance(value, str) and len(value) > 200:
        return {"value": None, "numeric_status": "numeric_budget"}
    token = str(value)
    try:
        _supplier_number(token)
    except (ValueError, InvalidOperation):
        return {"value": None, "numeric_status": "numeric_budget"}
    return {"value": token}


def _ratio(cfo, np):
    if not np:
        raise ValueError("zero_denominator")
    with localcontext() as ctx:
        # Enough precision for both full coefficients and six decimal half cases.
        ctx.prec = max(50, len(cfo.as_tuple().digits) + len(np.as_tuple().digits)
                       + abs(cfo.as_tuple().exponent) + abs(np.as_tuple().exponent) + 20)
        return (cfo / np).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _parse(pages, security_id, *, partial=False):
    """Only two explicit Q1 consolidated tables; never use supplier values to pick rows."""
    if (not isinstance(pages, list) or not 1 <= len(pages) <= 6
            or any(not isinstance(p, dict) or set(p) != {"page", "text"}
                   or type(p["page"]) is not int or not 0 <= p["page"] < 40
                   or not isinstance(p["text"], str) for p in pages)
            or len({p["page"] for p in pages}) != len(pages)
            or sum(len(p["text"]) for p in pages) > 400000):
        raise ValueError("invalid bounded page extraction")
    pages = sorted(pages, key=lambda p: p["page"])
    full = "\n".join(p["text"] for p in pages)
    codes = set(re.findall(r"(?:证券|股票)代码\s*[:：]?\s*([0-9]{6})(?![0-9])", full))
    years = set(re.findall(r"(20[0-9]{2})\s*年\s*第[一1]季度报告", full))
    if codes != {security_id} or len(years) != 1:
        return [], ["document_identity"], None
    year = int(next(iter(years)))
    rows, headers = [], []
    context, previous = None, None
    for page in pages:
        if previous is None or page["page"] != previous + 1:
            context = None
        previous = page["page"]
        lines = page["text"].splitlines()
        for line_no, raw_line in enumerate(lines):
            line = re.sub(r"\s+", "", raw_line)
            if line in ("合并利润表", "合并现金流量表"):
                kind = "NP" if line == "合并利润表" else "CFO"
                block = "".join(re.sub(r"\s+", "", l) for l in lines[line_no + 1:line_no + 7])
                columns = re.search(r"项目(20[0-9]{2})年第[一1]季度(20[0-9]{2})年第[一1]季度", block)
                valid = (re.search(str(year) + r"年1[至—–-]3月", block)
                         and re.search(r"单位[:：]元", block)
                         and re.search(r"币种[:：]人民币", block)
                         and "万元" not in block and "外币" not in block
                         and columns and [int(columns[1]), int(columns[2])] == [year, year - 1])
                context = {"table": kind, "header_page": page["page"], "header_line": line_no,
                           "years": [year, year - 1]} if valid else None
                headers.append(kind if valid else kind + ":INVALID")
                continue
            if (re.search(r"(?:母公司|合并).*(?:利润表|现金流量表|资产负债表)", line)
                    or "公司负责人" in line):
                context = None
                continue
            if context is None:
                continue
            if ((line.startswith("单位") and not re.search(r"单位[:：]元(?:币种|$)", line))
                    or ("币种" in line and not re.search(r"币种[:：]人民币", line))
                    or (line.startswith("项目") and re.findall(r"(20[0-9]{2})年第[一1]季度", line) != [str(year), str(year - 1)])):
                headers.append(context["table"] + ":INVALID")
                context = None
                continue
            label = line
            end_line = line_no
            if context["table"] == "CFO" and line == "经营活动产生的现金流量净" and line_no + 1 < len(lines):
                label += re.sub(r"\s+", "", lines[line_no + 1])
                end_line += 1
            # Remove only the explicit total-row annotation, never parent or continuing-profit labels.
            if context["table"] == "NP":
                match = re.fullmatch(r"(?:[一二三四五六七八九十]+[、.])?净利润(?:[（(]净亏损以.*?填列[）)])?(.+)", label)
            else:
                match = re.fullmatch(r"经营活动产生的现金流量净额(.+)", label)
            if not match:
                continue
            # Retain whitespace between the two column tokens for unambiguous boundaries.
            original = raw_line if end_line == line_no else raw_line + lines[end_line]
            if context["table"] == "NP":
                suffix = re.sub(r"^\s*(?:[一二三四五六七八九十]+[、.]\s*)?净利润(?:[（(]净亏损以.*?填列[）)])?", "", original)
            else:
                suffix = re.sub(r"^\s*经营活动产生的现金流量净\s*额", "", original)
            tokens = suffix.split()
            if len(tokens) != 2:
                continue
            try:
                for token in tokens:
                    _number(token)
            except ValueError:
                continue
            for column, token in enumerate(tokens):
                rows.append({"table": context["table"], "period_start": f"{context['years'][column]}-01-01",
                    "period_end": f"{context['years'][column]}-03-31", "role": "current" if column == 0 else "comparative",
                    "column": column, "page": page["page"], "line": line_no, "end_line": end_line,
                    "header_page": context["header_page"], "header_line": context["header_line"], "token": token})
    if partial:
        accepted, gaps = [], []
        for kind in ("NP", "CFO"):
            subset = [r for r in rows if r["table"] == kind]
            if headers.count(kind) != 1 or kind + ":INVALID" in headers:
                gaps.append(kind + ":table_context")
            elif len(subset) != 2 or len({r["role"] for r in subset}) != 2:
                gaps.append(kind + ":target_row")
            else:
                accepted.extend(subset)
        return sorted(accepted, key=lambda r: (r["period_end"], r["table"])), gaps, year
    if sorted(headers) != ["CFO", "NP"]:
        return [], ["table_context"], year
    keys = [(r["table"], r["role"]) for r in rows]
    if len(rows) != 4 or len(set(keys)) != 4:
        return [], ["target_row"], year
    return sorted(rows, key=lambda r: (r["period_end"], r["table"])), [], year


def _dependencies(receipt_reference, receipt, context):
    suppliers = []
    for s in receipt["suppliers"]:
        actual = context.get("suppliers", {}).get(s["blob_sha256"])
        suppliers.append({"blob_sha256": s["blob_sha256"], "status": s["status"],
            "descriptor": {k: v for k, v in (actual or {}).items() if k != "value"}})
    return {"receipt_sha256": receipt_reference, "pdf_sha256": receipt["pdf_sha256"],
            "suppliers": sorted(suppliers, key=lambda s: s["blob_sha256"])}


def _calculate(rows, receipt, context):
    gaps, checks = [], []
    if any(s["status"] != "LOCAL_PAYLOAD_CHECKED" for s in receipt["suppliers"]):
        gaps.append("supplier_rejected")
    if not rows:
        return checks, gaps
    for period in sorted({r["period_end"] for r in rows}):
        group = {r["table"]: r for r in rows if r["period_end"] == period}
        np, cfo = (_number(group[k]["token"]) for k in ("NP", "CFO"))
        ratio = _ratio(cfo, np) if np else None
        if ratio is None:
            gaps.append("zero_denominator")
        for metric, expected in (("netProfit", np), ("CFOToNP", ratio)):
            candidates = [context["suppliers"][s["blob_sha256"]] for s in receipt["suppliers"]
                if s["status"] == "LOCAL_PAYLOAD_CHECKED" and s["blob_sha256"] in context.get("suppliers", {})
                and context["suppliers"][s["blob_sha256"]]["period_end"] == period
                and context["suppliers"][s["blob_sha256"]]["metric"] == metric]
            status = "MATCHED"
            if not candidates:
                status = "supplier_missing"
            elif expected is None:
                status = "zero_denominator"
            else:
                try:
                    value = _supplier_number(candidates[0]["value"])
                    if len({_supplier_number(c["value"]) for c in candidates}) != 1:
                        status = "supplier_conflict"
                    elif metric == "CFOToNP" and _fractional_places(value) > 6:
                        status = "supplier_precision"
                    elif value != expected:
                        status = "numeric_mismatch"
                except (ValueError, InvalidOperation):
                    status = "numeric_budget"
            if status != "MATCHED":
                gaps.append(status)
            checks.append({"period_end": period, "metric": metric, "status": status})
    return checks, sorted(set(gaps))


def _result(gaps):
    return "NUMERIC_MISMATCH" if "numeric_mismatch" in gaps else "PENDING_REVIEW" if gaps else "LOCAL_NUMERIC_CONSISTENT"


def _identity(record):
    return _sha(_encoded({k: v for k, v in record.items() if k not in ("proof_id", "recorded_at")}))


def _public(record, reference, receipt, as_of, *, replay):
    from src.core.cash_material_intake import _publication
    time_gaps = []
    if any(date.fromisoformat(r["period_end"]) > as_of.date() for r in record["rows"]):
        time_gaps.append("报告期晚于本次分析时点，资格待核")
    publications = [receipt["declarations"].get("published_at")]
    publications += [s.get("published_at") for s in receipt["suppliers"] if s["status"] == "LOCAL_PAYLOAD_CHECKED"]
    statuses = sorted({_publication(p, as_of) for p in publications})
    if "DECLARED_FUTURE" in statuses:
        time_gaps.append("声明公开日晚于本次分析时点，历史资格待核")
    if "DECLARED_SAME_DAY_TIME_UNKNOWN" in statuses:
        time_gaps.append("声明公开日与分析同日但时序未知，历史资格待核")
    if "DECLARATION_MISSING" in statuses:
        time_gaps.append("公开日声明缺失，历史资格待核")
    locations = [{k: r[k] for k in ("table", "period_start", "period_end", "role", "column", "page", "line", "end_line", "header_page", "header_line")} for r in record["rows"]]
    return {"schema": VERSION, "proof_id": record["proof_id"], "reference": reference,
        "storage_references": [reference], "collection_times": [record["recorded_at"]],
        "material_id": record["material_id"], "security_id": record["security_id"],
        "parser_version": record["parser_version"], "calculation_version": record["calculation_version"],
        "recorded_result": record["result"], "result": "PENDING_REVIEW" if time_gaps else record["result"],
        "dependency_integrity": "CHECKED", "arithmetic_check": "CHECKED" if record["rows"] else "NOT_AVAILABLE", "replay_status": replay,
        "locations": locations, "gaps": [GAPS[g] for g in record["gaps"]] + time_gaps,
        "source_authenticity": "PENDING_INDEPENDENT_SOURCE_VERIFICATION", "pit_status": statuses,
        "roe_definition": "PENDING", "industry": None, "comparable_periods": 0, "cash_sustainability": "UNKNOWN",
        "summary": ("留有本地核对一致记录" if replay == "NOT_REPLAYED_READONLY" and record["result"] == "LOCAL_NUMERIC_CONSISTENT"
                    else "本次本地数值核对完成" if replay == "EXTRACTED_THIS_CALL" else "留有本地核对待复核记录")
                    + "；来源真实性、历史可得与长期资格仍待核"}


def verify(store, security_id, material_id, *, as_of, extractor=None):
    from src.core.cash_material_intake import read_materials, _sid
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ValueError("aware as_of required")
    sid = _sid(security_id)
    context = {}
    read_materials(store, sid, as_of=as_of, _verified_context=context)
    choices = sorted((ref, r) for ref, r in context.get("receipts", {}).items() if r["material_id"] == material_id)
    if not choices:
        raise ValueError("material missing or dependencies invalid")
    reference, receipt = choices[0]
    pdf = store.load_cash_material_bytes("blobs", receipt["pdf_sha256"])
    try:
        pages = (extractor or _extract_pdf)(pdf, receipt["pages"])
        rows, parse_gaps, year = _parse(pages, sid)
    except Exception:
        rows, parse_gaps, year = [], ["extraction"], None
    checks, gaps = _calculate(rows, receipt, context)
    gaps = sorted(set(gaps + parse_gaps))
    record = {"schema": VERSION, "parser_version": PARSER_VERSION, "calculation_version": CALCULATION_VERSION,
        "material_id": material_id, "security_id": sid, "report_year": year,
        "dependencies": _dependencies(reference, receipt, context), "rows": rows,
        "checks": checks, "gaps": gaps, "result": _result(gaps)}
    record["proof_id"] = _identity(record)
    # Replays use the original timestamp and bytes; no second record for the same proof.
    for ref in store.list_cash_numeric_proofs():
        try:
            from src.core.cash_material_intake import _json
            existing = _json(store.load_cash_material_bytes("proofs", ref))
            if existing.get("proof_id") == record["proof_id"] and _identity(existing) == record["proof_id"]:
                return _public(existing, ref, receipt, as_of, replay="EXTRACTED_THIS_CALL")
        except (OSError, ValueError, TypeError):
            continue
    record["recorded_at"] = datetime.now().astimezone().isoformat(timespec="microseconds")
    ref = store.save_cash_material_bytes("proofs", _encoded(record))
    return _public(record, ref, receipt, as_of, replay="EXTRACTED_THIS_CALL")


def _validate(record, security_id, context):
    if (not isinstance(record, dict) or record.get("schema") != VERSION
            or record.get("parser_version") != PARSER_VERSION or record.get("calculation_version") != CALCULATION_VERSION
            or record.get("security_id") != security_id or record.get("proof_id") != _identity(record)
            or record.get("result") not in RESULTS):
        raise ValueError("proof identity or version invalid")
    recorded = datetime.fromisoformat(record["recorded_at"])
    if recorded.tzinfo is None:
        raise ValueError("proof timestamp invalid")
    receipt_ref = record["dependencies"]["receipt_sha256"]
    receipt = context.get("receipts", {}).get(receipt_ref)
    if receipt is None or receipt["material_id"] != record["material_id"] or _dependencies(receipt_ref, receipt, context) != record["dependencies"]:
        raise ValueError("proof dependencies invalid")
    rows = record["rows"]
    if not isinstance(rows, list) or len(rows) not in (0, 4):
        raise ValueError("proof rows invalid")
    for row in rows:
        if set(row) != {"table", "period_start", "period_end", "role", "column", "page", "line", "end_line", "header_page", "header_line", "token"}:
            raise ValueError("proof row fields invalid")
        if (row["table"] not in ("NP", "CFO") or row["role"] not in ("current", "comparative")
                or type(record["report_year"]) is not int or not 2000 <= record["report_year"] <= 2099
                or type(row["column"]) is not int or row["column"] != (0 if row["role"] == "current" else 1)
                or row["period_start"] != f"{record['report_year'] - row['column']}-01-01"
                or row["period_end"] != f"{record['report_year'] - row['column']}-03-31"
                or any(type(row[k]) is not int or not 0 <= row[k] < 40 for k in ("page", "header_page"))
                or row["page"] < row["header_page"]
                or any(type(row[k]) is not int or not 0 <= row[k] <= 400000 for k in ("line", "end_line", "header_line"))
                or not row["line"] <= row["end_line"] <= row["line"] + 1):
            raise ValueError("proof row binding invalid")
        _number(row["token"])
        selected = receipt["pages"] if receipt["pages"] is not None else list(range(6))
        if (any(p not in selected for p in range(row["header_page"], row["page"] + 1))
                or (row["page"] == row["header_page"] and row["line"] <= row["header_line"])):
            raise ValueError("proof page continuity invalid")
    if rows and len({(r["table"], r["role"]) for r in rows}) != 4:
        raise ValueError("proof duplicate rows")
    checks, arithmetic_gaps = _calculate(rows, receipt, context)
    gaps = record["gaps"]
    if (not isinstance(gaps, list) or any(g not in GAPS for g in gaps) or gaps != sorted(set(gaps))
            or record["checks"] != checks or record["result"] != _result(gaps)
            or sorted(g for g in gaps if g not in ("document_identity", "table_context", "target_row", "extraction")) != arithmetic_gaps
            or (bool(rows) == bool(set(gaps) & {"document_identity", "table_context", "target_row", "extraction"}))):
        raise ValueError("proof arithmetic invalid")
    return receipt


def read_proofs(store, security_id, *, as_of, _verified_context=None):
    from src.core.cash_material_intake import read_materials, _json, _sid
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ValueError("aware as_of required")
    sid = _sid(security_id)
    context = _verified_context
    if context is None:
        context = {}
        read_materials(store, sid, as_of=as_of, _verified_context=context)
    views, errors = [], []
    try:
        refs = store.list_cash_numeric_proofs()
    except OSError:
        refs = []
        errors.append("本地核对记录目录不可读")
    for ref in refs:
        try:
            record = _json(store.load_cash_material_bytes("proofs", ref))
            if record.get("security_id") != sid:
                continue
            receipt = _validate(record, sid, context)
            view = _public(record, ref, receipt, as_of, replay="NOT_REPLAYED_READONLY")
            duplicate = next((v for v in views if v["proof_id"] == view["proof_id"]), None)
            if duplicate is None:
                views.append(view)
            else:
                duplicate["storage_references"] = sorted(set(duplicate["storage_references"] + [ref]))
                duplicate["collection_times"] = sorted(set(duplicate["collection_times"] + view["collection_times"]))
        except Exception:
            errors.append("本地核对记录或依赖缺失、损坏或算术不一致，待重新核对")
    return {"proofs": views, "read_errors": sorted(set(errors)), "comparable_periods": 0, "cash_sustainability": "UNKNOWN"}


def _extract_pdf(data, pages):
    import base64
    import subprocess
    import sys
    from src.core.cash_material_intake import _json
    completed = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--pdf-worker"],
        input=_encoded({"pdf": base64.b64encode(data).decode("ascii"), "pages": pages}),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=True)
    result = _json(completed.stdout)
    if set(result) != {"pages"}:
        raise ValueError("bounded extraction unavailable")
    return result["pages"]


def _pdf_worker():
    import base64
    import io
    import os
    import sys
    sys.dont_write_bytecode = True
    def deny(event, args):
        if event in ("socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
            raise RuntimeError("worker external action prohibited")
        if event == "open":
            mode, flags = args[1:3]
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
                raise RuntimeError("worker writes prohibited")
        if event in ("os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.replace", "os.link", "os.symlink"):
            raise RuntimeError("worker writes prohibited")
    sys.addaudithook(deny)
    request = sys.stdin.buffer.read(28 * 1024 * 1024 + 1)
    if len(request) > 28 * 1024 * 1024:
        raise ValueError("worker input limit")
    request = json.loads(request.decode("utf-8"))
    data = base64.b64decode(request["pdf"], validate=True)
    if len(data) > PDF_LIMIT or not data.startswith(b"%PDF-"):
        raise ValueError("worker PDF limit")
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or len(reader.pages) > 40:
        raise ValueError("worker page limit")
    selected = request["pages"]
    if selected is None:
        selected = list(range(min(6, len(reader.pages))))
    if (not isinstance(selected, list) or not 1 <= len(selected) <= 6 or len(set(selected)) != len(selected)
            or any(type(p) is not int or not 0 <= p < len(reader.pages) for p in selected)):
        raise ValueError("worker selected page limit")
    pages = [{"page": p, "text": reader.pages[p].extract_text() or ""} for p in sorted(selected)]
    if sum(len(p["text"]) for p in pages) > 400000 or not any(p["text"].strip() for p in pages):
        raise ValueError("worker text limit")
    sys.stdout.buffer.write(_encoded({"pages": pages}))


if __name__ == "__main__":
    import sys
    if sys.argv[1:] != ["--pdf-worker"]:
        raise SystemExit(2)
    try:
        _pdf_worker()
    except Exception:
        sys.stdout.buffer.write(_encoded({"error": "bounded_extraction_unavailable"}))
