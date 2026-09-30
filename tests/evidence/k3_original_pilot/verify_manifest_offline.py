# -*- coding: utf-8 -*-
"""K3 可携带清单离线复验入口（L3/iter5：换机器能核对；失败不伪补）。

对 evidence_manifest.json 每点位：
1. MISSING        PDF 文件不存在（不伪补——如实失败）
2. HASH_MISMATCH  本地 PDF sha256 ≠ 清单登记 hash（文件被换/损坏）
3. EXTRACT_FAILED 复提取异常（pypdf 版本差异/文件损坏）
4. VALUE_MISMATCH 复提取的负债合计/资产总计 与 清单 issuer_value 不符（容差 5e-5）
5. PASS           原件链可重建（hash→提取→算式全一致）
供应商值 recorded_only（离线不可重算——不复算、不冒充）。

用法: python verify_manifest_offline.py [--manifest 路径] [--pdf-root 仓库根]
退出码：全部 PASS=0；任一失败=1。
"""
import argparse
import hashlib
import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.stdout.reconfigure(encoding="utf-8")

DIR = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[3]

# 复用生产同一提取链路（LRN-20260925-016/L008：对照复验不手写近似逻辑）
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("k3_extract_reports", DIR / "extract_reports.py")
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
extract_one = _mod.extract_one

TOL = 5e-5


def main(manifest=None, pdf_root=None, results_path=None) -> dict:
    manifest = Path(manifest) if manifest else DIR / "evidence_manifest.json"
    root = Path(pdf_root) if pdf_root else REPO
    m = json.loads(manifest.read_text(encoding="utf-8"))
    out_points = []
    for p in m.get("points", []):
        if p.get("status") == "SELF_CHECK_FAILED":
            out_points.append({"key": p["key"], "status": "SELF_CHECK_FAILED",
                               "note": p.get("note")})
            continue
        pdf = root / p["pdf_path"]
        if not pdf.exists():
            out_points.append({"key": p["key"], "status": "MISSING",
                               "pdf_path": p["pdf_path"],
                               "note": "PDF 缺失——如实失败，不伪补（按清单 source_url 重新获取后重验）"})
            continue
        actual_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
        if actual_hash != p["file_hash"]:
            out_points.append({"key": p["key"], "status": "HASH_MISMATCH",
                               "expected": p["file_hash"], "actual": actual_hash,
                               "note": "本地文件与清单 hash 不符——文件被换/损坏，不伪补"})
            continue
        try:
            ex = extract_one(pdf)
        except Exception as e:
            out_points.append({"key": p["key"], "status": "EXTRACT_FAILED",
                               "error": f"{type(e).__name__}: {e}",
                               "note": "复提取失败——环境差异/文件问题，人工核对"})
            continue
        ratio = ex.get("liability_to_asset_issuer")
        if not ex.get("self_check_ok"):
            # P3：复提取自洽失败——明知不可重建不得报 PASS（失败不伪补）
            out_points.append({"key": p["key"], "status": "SELF_CHECK_FAILED",
                               "self_checks": ex.get("self_checks"),
                               "note": "复提取自洽校验未过——原件链不可重建，人工核对"})
            continue
        if ratio is None or abs(ratio - p["issuer_value"]) > TOL:
            out_points.append({"key": p["key"], "status": "VALUE_MISMATCH",
                               "manifest_value": p["issuer_value"],
                               "recomputed_value": ratio, "tolerance": TOL,
                               "note": "复算值与清单登记值不符——核对提取/清单"})
            continue
        out_points.append({"key": p["key"], "status": "PASS",
                           "file_hash": actual_hash,
                           "recomputed_value": ratio,
                           "supplier": "recorded_only（离线不可重算——不复算不冒充）"})
    passed = sum(1 for r in out_points if r["status"] == "PASS")
    failed = len(out_points) - passed
    try:
        manifest_display = os.path.relpath(manifest, REPO)
    except ValueError:
        manifest_display = str(manifest)
    summary = {"manifest": manifest_display, "total": len(out_points),
               "passed": passed, "failed": failed, "points": out_points}
    if results_path:
        Path(results_path).write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    for r in out_points:
        print(f"{r['key']}: {r['status']}" +
              (f" — {r.get('note', '')}" if r["status"] != "PASS" else ""), flush=True)
    print(f"离线复验：{passed}/{len(out_points)} PASS，{failed} FAIL", flush=True)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="K3 清单离线复验（失败不伪补）")
    ap.add_argument("--manifest", default=None, help="清单路径（缺省=同目录 evidence_manifest.json）")
    ap.add_argument("--pdf-root", default=None, help="PDF 相对路径根（缺省=仓库根）")
    ap.add_argument("--results", default=None, help="结果输出路径")
    a = ap.parse_args()
    s = main(manifest=a.manifest, pdf_root=a.pdf_root, results_path=a.results)
    sys.exit(0 if s["failed"] == 0 else 1)
