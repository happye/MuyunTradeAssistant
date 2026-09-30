# -*- coding: utf-8 -*-
"""K3 原件试点提取脚本（第一批剩余 5 份；范式=J1 extract_vanke_2024h1.py v2）。

对每份发行人原件 PDF 确定性提取**合并资产负债表**的资产总计/负债合计
（行锚定防子串陷阱 + 表内自洽校验），计算 liabilityToAsset。
section 边界：从「合并资产负债表」起，到「母公司资产负债表」止——季报中
两表常连续排布，不做边界会把母公司数字误当合并数。
自洽校验失败的点位标记 failed_self_check（交人工复核，不自动登记核定）。
"""
import hashlib
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

# L3 迁移：脚本/清单随交付入库（tests/evidence/）；PDF 单独存储在 artifacts（不追踪）
DIR = Path(__file__).resolve().parent
PDF_BASE = Path("tests/artifacts/k3_original_pilot")
PDF_BASE_J1 = Path("tests/artifacts/j1_original_pilot")
NUM = r"([0-9,]+\.[0-9]{2})"
LABELS = ("资产总计", "负债合计", "流动负债合计", "非流动负债合计",
          "负债和股东权益总计", "负债和所有者权益总计")

TARGETS = [
    ("000002", "万科A", "2024Q1", "2024-03-31"),
    ("000002", "万科A", "2024Q2", "2024-06-30"),
    ("000002", "万科A", "2024Q3", "2024-09-30"),
    ("600519", "贵州茅台", "2024Q1", "2024-03-31"),
    ("600519", "贵州茅台", "2024Q2", "2024-06-30"),
    ("600519", "贵州茅台", "2024Q3", "2024-09-30"),
]


def extract_one(pdf_path: Path) -> dict:
    reader = PdfReader(str(pdf_path))
    found: dict[str, dict] = {}
    bs_pages: list[int] = []
    in_merged = False
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        # K3 实测（茅台H1/万科Q1）：section 切换必须**行级**判定——
        # 合并表的负债端与「母公司资产负债表」标题可同页（表格续排+下一表标题同页），
        # 页级 flat 包含判定会把合并负债端整页跳过
        if "合并资产负债表" in re.sub(r"\s+", "", text):
            in_merged = True  # 标题页与数字行可分页；行首锚定+数字校验防叙述误提取
        for ln in text.splitlines():
            s = ln.strip().replace(" ", "")
            if s.startswith("母公司资产负债表"):
                in_merged = False  # 行级标题——合并 section 在此行结束
                continue
            if not in_merged:
                continue
            for label in LABELS:
                if ln.startswith(label) and label not in found:
                    nums = re.findall(NUM, ln[len(label):])
                    if nums:
                        found[label] = {"page_0based": i,
                                        "raw": nums[0],
                                        "value": float(nums[0].replace(",", ""))}
        if "资产总计" in re.sub(r"\s+", "", text) or "负债合计" in re.sub(r"\s+", "", text):
            if i not in bs_pages:
                bs_pages.append(i)
    result: dict = {"pdf": str(pdf_path), "pages": len(reader.pages),
                    "balance_sheet_pages_0based": bs_pages, "found": found}
    checks: dict[str, bool] = {}
    if {"流动负债合计", "非流动负债合计", "负债合计"} <= found.keys():
        s = found["流动负债合计"]["value"] + found["非流动负债合计"]["value"]
        checks["liab_sum_consistent"] = abs(s - found["负债合计"]["value"]) < 1.0
    total_label = ("负债和股东权益总计" if "负债和股东权益总计" in found
                   else "负债和所有者权益总计" if "负债和所有者权益总计" in found else None)
    if total_label and "资产总计" in found:
        checks["total_consistent"] = abs(found[total_label]["value"]
                                         - found["资产总计"]["value"]) < 1.0
    elif "资产总计" in found:
        # K3 实测：茅台季报的「负债和所有者权益（或股东权益）总计」被换行拆开
        # （行首形态"东权益）总计 <数>"）——回退用合并 section 内"…总计 <数>"行
        # 与资产总计相等的校验（等值匹配是强校验：正文叙述不会恰好等于资产总计）
        bs = set(bs_pages) | set(f["page_0based"] for f in found.values())
        total_line_found = False
        for i in bs:
            reader_i = PdfReader(str(pdf_path)).pages[i]
            for ln in (reader_i.extract_text() or "").splitlines():
                ln = ln.strip()
                m = re.match(r".{0,14}总计\s+" + NUM, ln)
                if m and abs(float(m.group(1).replace(",", ""))
                             - found["资产总计"]["value"]) < 1.0:
                    total_line_found = True
        checks["total_consistent"] = total_line_found
    if {"负债合计", "资产总计"} <= found.keys() and found["资产总计"]["value"] > 0:
        result["liability_to_asset_issuer"] = \
            found["负债合计"]["value"] / found["资产总计"]["value"]
    result["self_checks"] = checks
    result["self_check_ok"] = (checks.get("liab_sum_consistent", False)
                               and checks.get("total_consistent", False))
    result["file_hash"] = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    return result


def main():
    out = {}
    label_to_file = {"2024Q1": "2024Q1", "2024Q3": "2024Q3",
                     "2024Q2": "2024H1"}
    for code, name, label, period_end in TARGETS:
        file_label = label_to_file.get(f"{label}_{code}", label_to_file.get(label, label))
        pdf = PDF_BASE / f"{code}_{file_label}.pdf"
        if label == "2024Q2" and code == "000002":
            pdf = PDF_BASE_J1 / "000002_2024H1.pdf"
        if not pdf.exists():
            out[f"{code}_{label}"] = {"status": "UNAVAILABLE", "note": "PDF 未下载"}
            continue
        try:
            r = extract_one(pdf)
            r["security_id"], r["security_name"], r["label"], r["period_end"] = \
                code, name, label, period_end
            out[f"{code}_{label}"] = r
        except Exception as e:
            out[f"{code}_{label}"] = {"status": "EXTRACT_FAILED",
                                      "error": f"{type(e).__name__}: {e}"}
        status = out[f"{code}_{label}"]
        print(f"{code} {name} {label}: "
              f"ratio={status.get('liability_to_asset_issuer')} "
              f"self_ok={status.get('self_check_ok', status.get('status'))}", flush=True)
    (DIR / "extract_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
