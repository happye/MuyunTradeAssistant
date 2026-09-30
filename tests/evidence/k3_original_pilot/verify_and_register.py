# -*- coding: utf-8 -*-
"""K3 原件试点对照+登记脚本（L3 重写：raw/mapped 分列 + 漂移检测 + 可注入重放）。

对每一点：发行人原件负债率（提取脚本产物，表内自洽通过）vs baostock 供应商值
交叉核对——供应商值 **raw 与 mapped 分列**（旧脚本把映射值写进 baostock_raw 键，
L3/iter5 点名的键语义缺陷；勘误见 88ddbc6）：
- baostock_raw    ← get_financial_quarterly 原始返回（未映射）
- baostock_mapped ← apply_unit_drift_mapping 显式应用后（与 research_application 同链路）
一致（差<5e-5）→ verified_against_original；不一致/缺值 → cross_checked_pending_original
如实登记，不硬登记核定。

L3 语义（iter5 DELIVERY_PLAN）：
- 版本/更正状态如实：correction_search_status=not_performed 明示（不默认「首发版」注记）
- 可注入重放：--store 指向临时研究库；--supplier-cache 供应商值缓存（离线重放），
  未给则实时拉取（真实网络）
- 旧记录不可静默覆盖：store 幂等（同 证券+报告期+文件hash 不覆盖）+ 脚本漂移检测——
  已有记录与本轮 entry 内容不同 → 显式 EXISTING_DIVERGENT（登记语义漂移交人工核对，
  参照 LRN-20261001-L008）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.stdout.reconfigure(encoding="utf-8")

from src.data.financial_data import apply_unit_drift_mapping, get_financial_quarterly
from src.data.research_store import ResearchStore

DIR = Path(__file__).resolve().parent
EXTRACT = json.loads((DIR / "extract_results.json").read_text(encoding="utf-8"))
DOWNLOAD = {e["code"] + "_" + e["label"]: e
            for e in json.loads((DIR / "download_log.json").read_text(encoding="utf-8"))}

# (extract_key, security_id, security_name, period_end, source_file_key, pdf_path)
POINTS = [
    ("000002_2024Q1", "000002", "万科A", "2024-03-31", "000002_2024Q1",
     "tests/artifacts/k3_original_pilot/000002_2024Q1.pdf"),
    ("000002_2024Q3", "000002", "万科A", "2024-09-30", "000002_2024Q3",
     "tests/artifacts/k3_original_pilot/000002_2024Q3.pdf"),
    ("600519_2024Q1", "600519", "贵州茅台", "2024-03-31", "600519_2024Q1",
     "tests/artifacts/k3_original_pilot/600519_2024Q1.pdf"),
    ("600519_2024Q2", "600519", "贵州茅台", "2024-06-30", "600519_2024H1",
     "tests/artifacts/k3_original_pilot/600519_2024H1.pdf"),
    ("600519_2024Q3", "600519", "贵州茅台", "2024-09-30", "600519_2024Q3",
     "tests/artifacts/k3_original_pilot/600519_2024Q3.pdf"),
]

QUARTER_OF = {"2024-03-31": (2024, 1), "2024-06-30": (2024, 2), "2024-09-30": (2024, 3)}

# 漂移比较字段（registered_at/verification_id 之外的全部语义字段）
_DRIFT_IGNORE = {"registered_at", "verification_id", "schema_version"}


def _supplier_values(key, sec, period_end, cache):
    """供应商值 (raw, mapped, pub)：有缓存用缓存（离线重放/复核同源），否则实时拉取。"""
    if cache is not None and key in cache:
        c = cache[key]
        return c.get("baostock_raw"), c.get("baostock_mapped"), c.get("published_at", "")
    year, quarter = QUARTER_OF[period_end]
    records = get_financial_quarterly(sec, year, quarter)
    raw = next((r["value"] for r in records if r.get("metric") == "liabilityToAsset"), None)
    pub = next((r.get("published_at") for r in records
                if r.get("metric") == "liabilityToAsset"), "")
    mapped = None
    if raw is not None:
        mapped_records = [apply_unit_drift_mapping(r) for r in records]
        mapped = next((r["value"] for r in mapped_records
                       if r.get("metric") == "liabilityToAsset"), None)
    return raw, mapped, pub


def _existing_record(store, sec, period_end, file_hash):
    rid = "orig_" + __import__("hashlib").sha256(
        f"{sec}:{period_end}:{file_hash}".encode("utf-8")).hexdigest()[:16]
    p = store.dir / "originals" / f"{rid}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            # 坏文件隔离（G14 同精神）：旧记录不可读时按无记录走，但不静默——留痕
            print(f"⚠ originals/{rid}.json 不可解析（{e}）——按无既有记录处理，"
                  f"请人工核对该文件", flush=True)
            return None
    return None


def main(store=None, supplier_cache=None, results_path=None):
    """登记五点（可注入：store=临时研究库目录；supplier_cache=离线缓存路径）。

    输出 results_path（缺省 DIR/verification_results.json）——每点 status:
    REGISTERED / REGISTERED_IDEMPOTENT / EXISTING_DIVERGENT / SKIPPED。"""
    store = ResearchStore(Path(store)) if store else ResearchStore()
    cache = None
    if supplier_cache is not None:
        cache = json.loads(Path(supplier_cache).read_text(encoding="utf-8"))
    results = []
    for key, sec, name, period_end, file_key, pdf_path in POINTS:
        ex = EXTRACT.get(key) or {}
        if not ex.get("self_check_ok") or "liability_to_asset_issuer" not in ex:
            results.append({"key": key, "status": "SKIPPED",
                            "reason": "提取未通过自洽校验——交人工复核，不自动登记"})
            continue
        issuer = ex["liability_to_asset_issuer"]
        found = ex["found"]
        bs_raw, bs_mapped, pub = _supplier_values(key, sec, period_end, cache)
        cmp_value = bs_mapped if bs_mapped is not None else bs_raw
        entry = {
            "security_id": sec, "security_name": name, "period_end": period_end,
            "field": "liabilityToAsset", "unit": "ratio（无量纲，0-1）",
            "consolidation_scope": "合并资产负债表",
            "file_hash": ex["file_hash"], "file_path": pdf_path,
            "file_pages": ex.get("pages"),
            "source_url": DOWNLOAD[file_key]["pdf_url"],
            "disclosure_proof": (f"巨潮资讯网 finalpage（{name}定期报告："
                                 f"{DOWNLOAD[file_key].get('title', '')}）"),
            # L3：更正检索状态如实——不默认「首发版」注记（无证据的版本声明）
            "correction_search_status": "not_performed",
            "revision_note": ("按下载时点所见版本登记（download_log 记录时点）；"
                              "更正公告检索未执行——本条为可修正项，更正检索后追加版本"),
            "page_table": (f"合并资产负债表 PDF 0基页 {ex.get('balance_sheet_pages_0based')}"
                           f"：资产总计@p{found['资产总计']['page_0based']}、"
                           f"负债合计@p{found['负债合计']['page_0based']}"),
            "raw_line_label": (f"资产总计={found['资产总计']['raw']} 元；"
                               f"负债合计={found['负债合计']['raw']} 元（单位：元，期末）"),
            "derived_formula": "liabilityToAsset = 负债合计 / 资产总计",
            "issuer_value": issuer,
            "issuer_value_rounded4": round(issuer, 4),
            # L3：raw/mapped 分列——旧脚本此处把映射值写进 baostock_raw（键语义缺陷）
            "baostock_raw": bs_raw,
            "baostock_mapped": bs_mapped,
            "supplier_published_at": pub,
            "mapping_id": "umd_iss114_liability_v1",
            "extraction_method": ("pypdf 6.19.0 确定性文本提取 + 行首锚定 + section 行级"
                                  "边界 + 表内自洽校验（脚本 tests/artifacts/"
                                  "k3_original_pilot/extract_reports.py；下载脚本 "
                                  "download_reports.py）"),
            "self_checks": {**ex.get("self_checks", {}),
                            "supplier_value_present": cmp_value is not None},
            "verification_level": "pending",
            "reviewer": "user(实施方提取+自洽校验；供应商对照自动化)",
        }
        if cmp_value is not None and abs(issuer - cmp_value) < 5e-5:
            entry["verification_level"] = "verified_against_original"
            entry["note"] = (f"K3 第一批点位：映射值与发行人原件一致（差<5e-5，4位舍入内；"
                             f"供应商公布日 {pub}）；点位置入 umd_iss114 已核定样本范围")
        else:
            entry["verification_level"] = "cross_checked_pending_original"
            entry["note"] = (f"K3 第一批点位：发行人值 {issuer:.6f} 与供应商值 {cmp_value} "
                             f"不一致或缺失——差异披露，不硬登记核定（人工复核项）")
        # L3：旧记录漂移检测——store 幂等不覆盖 + 脚本显式报告（不静默）。
        # 比较对象=已有记录 vs **本轮新 entry**（save 幂等返回旧记录，不能自比恒等）
        existing = _existing_record(store, sec, period_end, ex["file_hash"])
        rec = store.save_original_verification(entry)
        if existing is None:
            status = "REGISTERED"
        else:
            diff_keys = sorted(
                k for k in set(existing) | set(entry)
                if k not in _DRIFT_IGNORE and existing.get(k) != entry.get(k))
            status = "REGISTERED_IDEMPOTENT" if not diff_keys else "EXISTING_DIVERGENT"
            if diff_keys:
                entry["drift_fields"] = diff_keys
                print(f"⚠ {key}: 已有登记与本轮 entry 不同（字段 {diff_keys}）——"
                      f"store 幂等未覆盖旧记录，登记语义漂移交人工核对"
                      f"（参照 LRN-20261001-L008 清理后重登）", flush=True)
        results.append({"key": key, "status": status,
                        "verification_id": rec["verification_id"],
                        "level": rec.get("verification_level"),
                        "entry": entry,
                        "issuer": issuer, "baostock": cmp_value})
        print(f"{key}: {rec['verification_id']} level={rec.get('verification_level')} "
              f"issuer={issuer:.6f} bs={cmp_value} status={status}", flush=True)
    out = Path(results_path) if results_path else DIR / "verification_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="K3 原件试点对照+登记（L3 重写）")
    ap.add_argument("--store", default=None,
                    help="研究库目录（缺省=真实 ~/.muyun/research；重放/测试传临时目录）")
    ap.add_argument("--supplier-cache", default=None,
                    help="供应商值缓存 JSON（离线重放用；缺省=实时拉取 baostock）")
    ap.add_argument("--results", default=None, help="结果输出路径")
    a = ap.parse_args()
    main(store=a.store, supplier_cache=a.supplier_cache, results_path=a.results)
