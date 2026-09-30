"""Read existing PDF/JSON artifacts only; no vendor calls or registry writes."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[3]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    extracted = json.loads((ROOT / "tests/artifacts/k3_original_pilot/extract_results.json").read_text(encoding="utf-8"))
    output = []
    for key, entry in extracted.items():
        pdf = ROOT / entry["pdf"]
        reader = PdfReader(pdf)
        checks = {}
        for label, found in entry["found"].items():
            page_text = re.sub(r"\s+", "", reader.pages[found["page_0based"]].extract_text())
            checks[label] = found["raw"] in page_text
        total = Decimal(entry["found"]["资产总计"]["raw"].replace(",", ""))
        liability = Decimal(entry["found"]["负债合计"]["raw"].replace(",", ""))
        ratio = liability / total
        output.append({"point": key, "file_exists": pdf.exists(),
                       "hash_matches": hashlib.sha256(pdf.read_bytes()).hexdigest() == entry["file_hash"],
                       "raw_numbers_at_recorded_pages": checks, "recalculated_ratio": str(ratio),
                       "ratio_matches": abs(float(ratio) - entry["liability_to_asset_issuer"]) < 1e-12})
    print(json.dumps({"scope": "Local byte identity, page text numbers and arithmetic only; no correction-announcement search, visual table audit, or vendor re-fetch",
                      "results": output}, ensure_ascii=False, indent=2))
    return int(any(not r["hash_matches"] or not r["ratio_matches"] or not all(r["raw_numbers_at_recorded_pages"].values()) for r in output))


if __name__ == "__main__":
    raise SystemExit(main())
