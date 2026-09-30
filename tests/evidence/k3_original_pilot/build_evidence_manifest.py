# -*- coding: utf-8 -*-
"""K3 可携带原件证据清单构建（L3/iter5：机器可读清单随交付获得）。

六点位（含 J1 批次下载的万科 2024H1）字段：URL、hash、证券/期间/字段/单位/
合并范围、页/行、原值/映射值/公式、版本与更正检索状态——换机器可核对。
供应商值标注 recorded_only（离线不可重算）；无法溯源的 URL 如实 null（不编造）。
离线复验入口：verify_manifest_offline.py（同目录）。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.stdout.reconfigure(encoding="utf-8")

DIR = Path(__file__).resolve().parent
EXTRACT = json.loads((DIR / "extract_results.json").read_text(encoding="utf-8"))
DOWNLOAD = {e["code"] + "_" + e["label"]: e
            for e in json.loads((DIR / "download_log.json").read_text(encoding="utf-8"))}

# 六点位（000002 2024H1 = J1 批次下载，PDF 在 j1_original_pilot——相对路径随清单携带）
POINTS = [
    ("000002_2024Q1", "000002", "万科A", "2024-03-31", "000002_2024Q1",
     "tests/artifacts/k3_original_pilot/000002_2024Q1.pdf", "2024Q1"),
    ("000002_2024Q2", "000002", "万科A", "2024-06-30", "000002_2024H1",
     "tests/artifacts/j1_original_pilot/000002_2024H1.pdf", "2024Q2"),
    ("000002_2024Q3", "000002", "万科A", "2024-09-30", "000002_2024Q3",
     "tests/artifacts/k3_original_pilot/000002_2024Q3.pdf", "2024Q3"),
    ("600519_2024Q1", "600519", "贵州茅台", "2024-03-31", "600519_2024Q1",
     "tests/artifacts/k3_original_pilot/600519_2024Q1.pdf", "2024Q1"),
    ("600519_2024Q2", "600519", "贵州茅台", "2024-06-30", "600519_2024H1",
     "tests/artifacts/k3_original_pilot/600519_2024H1.pdf", "2024Q2"),
    ("600519_2024Q3", "600519", "贵州茅台", "2024-09-30", "600519_2024Q3",
     "tests/artifacts/k3_original_pilot/600519_2024Q3.pdf", "2024Q3"),
]

# 首批登记的对照结果（verification_results.json——旧脚本仅记录 mapped 值为
# "baostock" 键；raw 未记录属旧键语义缺陷，如实标注，重跑新脚本补记）
PRIOR_RESULTS = {r["key"]: r for r in json.loads(
    (DIR / "verification_results.json").read_text(encoding="utf-8"))
    if isinstance(r, dict) and "key" in r}


def main(results_path=None):
    points = []
    for key, sec, name, period_end, file_key, pdf_path, label in POINTS:
        ex = EXTRACT.get(key) or {}
        if not ex.get("self_check_ok"):
            points.append({"key": key, "status": "SELF_CHECK_FAILED",
                           "note": "提取自洽未过——交人工复核，不入清单正例"})
            continue
        found = ex.get("found") or {}
        issuer_value = ex.get("liability_to_asset_issuer")
        if issuer_value is None or "资产总计" not in found:
            points.append({"key": key, "status": "INCOMPLETE_EXTRACT",
                           "note": "提取产物缺关键值——清单不登记不完整点位"})
            continue
        dl = DOWNLOAD.get(file_key) or {}
        prior = PRIOR_RESULTS.get(key) or {}
        points.append({
            "key": key, "security_id": sec, "security_name": name,
            "period_end": period_end, "field": "liabilityToAsset",
            "unit": "ratio（无量纲，0-1）", "consolidation_scope": "合并资产负债表",
            "pdf_path": pdf_path, "file_hash": ex["file_hash"],
            "file_pages": ex.get("pages"),
            "balance_sheet_pages_0based": ex.get("balance_sheet_pages_0based"),
            "page_table": (f"资产总计@p{found['资产总计']['page_0based']}、"
                           f"负债合计@p{found['负债合计']['page_0based']}"),
            "raw_line_label": (f"资产总计={found['资产总计']['raw']} 元；"
                               f"负债合计={found['负债合计']['raw']} 元（单位：元，期末）"),
            "issuer_value": issuer_value,
            "issuer_value_rounded4": round(issuer_value, 4),
            "derived_formula": "liabilityToAsset = 负债合计 / 资产总计",
            "source_url": dl.get("pdf_url"),
            "source_url_status": ("recorded" if dl.get("pdf_url")
                                  else "not_recorded（该批次下载未记录 URL——缺项明示，不编造）"),
            "disclosure_proof": (f"巨潮资讯网 finalpage（{name}定期报告："
                                 f"{dl.get('title', '')}）" if dl else
                                 "J1 批次下载（万科创世纪 2024 半年度报告）——URL 未随批次记录"),
            "supplier": {
                # 首批运行只记录了映射值（旧键语义）；raw 待新脚本重跑补记——如实缺项
                "mapped_recorded": prior.get("baostock"),
                "raw_recorded": None,
                "raw_note": ("首批脚本未记录 raw（键语义缺陷，勘误 88ddbc6）——"
                             "重跑 verify_and_register.py 以新语义补记")
                if prior else "本点位首批未登记（verification_results 无记录）——登记后补记",
                "mapping_id": "umd_iss114_liability_v1",
                "recorded_only": True,
                "note": "供应商值离线不可重算（记录性数据）——原件链可离线复验",
            },
            "correction_search_status": "not_performed",
            "revision_note": "按下载时点所见版本登记；更正公告检索未执行——可修正项",
            "verification_level": prior.get("level", "unregistered"),
            "extraction_method": ("pypdf 6.19.0 确定性文本提取（extract_reports.py/"
                                  "extract_one——离线复验入口复用同一函数）"),
        })
    manifest = {
        "schema": "k3_evidence_manifest_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "K3 原件试点六点位（liabilityToAsset 波次A）——原件数值核定与 PIT 可得性分别出状态；核定不自动取得历史 PIT 资格",
        "pit_availability": "not_assessed（原件核定 ≠ 严格历史资格——映射升级另按版本契约审批）",
        "tools": ["extract_reports.py", "verify_and_register.py",
                  "verify_manifest_offline.py", "build_evidence_manifest.py"],
        "points": points,
    }
    out = Path(results_path) if results_path else DIR / "evidence_manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"清单已写 {out}（{len(points)} 点）", flush=True)
    return manifest


if __name__ == "__main__":
    main()
