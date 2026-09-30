"""L3 原件证据交付可重建回归测试（plan/fusion iteration5，DELIVERY_PLAN L3；K3 账本旧登记语义）

锁死语义（证据包可携带 + 登记语义可修复重放）：
1. verify_and_register 登记语义：供应商 raw/mapped **分列**（旧脚本把映射值写进
   baostock_raw 键——R11/L3 点名的键语义缺陷）；更正检索状态如实明示（不默认「首发版」）
2. 临时研究库重放：--store 指向临时库；旧记录存在且内容不同 → 显式报告
   EXISTING_DIVERGENT 且**不静默覆盖**（store 幂等语义 + 脚本漂移检测）
3. 可携带清单 evidence_manifest.json：URL/hash/证券/期间/字段/单位/合并范围/页/行/
   原值/公式/版本/更正检索状态齐备；供应商值标注 recorded_only（离线不可重算）
4. 离线复验入口 verify_manifest_offline.py：六点 PDF hash/重提取/算式复算；
   缺 PDF / hash 不符 / 数值不符如实 FAIL——失败不伪补

隔离：登记全部写临时 store；供应商值经 supplier-cache 注入且缓存覆盖全部
POINTS key（合成值标注 synthetic 仅测试用，不污染交付目录）——**零网络**，
不走真实 baostock（pytest 离线默认纪律）。
跑法：pytest tests/core/test_l3_evidence_pack.py -q
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
K3 = REPO / "tests" / "evidence" / "k3_original_pilot"  # L3：脚本/清单随交付入库（PDF 留 artifacts 单独存储）


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"k3_{name}", K3 / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def vr():
    return _load("verify_and_register")


@pytest.fixture(scope="module")
def ov():
    return _load("verify_manifest_offline")


@pytest.fixture()
def tmp_store(tmp_path):
    d = tmp_path / "research"
    d.mkdir()
    return d


def _supplier_cache(tmp_path, *, raw=72.707, mapped=0.72707, key="000002_2024Q1"):
    """合成供应商缓存（测试注入——非真实供应商数据，synthetic 标注）。
    覆盖全部 POINTS key（P1-2：任何点位不得落回实时拉取——零网络纪律）。"""
    p = tmp_path / "supplier_cache.json"
    all_keys = ["000002_2024Q1", "000002_2024Q3", "600519_2024Q1",
                "600519_2024Q2", "600519_2024Q3"]
    data = {"_note": "synthetic cache for replay test only"}
    for k in all_keys:
        if k == key:
            data[k] = {"baostock_raw": raw, "baostock_mapped": mapped,
                       "fetched_at": "2026-10-01T00:00:00+08:00",
                       "source": "synthetic"}
        else:
            data[k] = {"baostock_raw": 1.0, "baostock_mapped": 0.01,
                       "fetched_at": "2026-10-01T00:00:00+08:00",
                       "source": "synthetic-filler"}
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _run_register(vr, store_dir, cache_path, tmp_path):
    out = tmp_path / "results.json"
    vr.main(store=Path(store_dir), supplier_cache=Path(cache_path),
            results_path=out)
    return json.loads(out.read_text(encoding="utf-8"))


# ── 登记语义：raw/mapped 分列 + 更正状态如实 ─────────────────────

def test_registration_raw_and_mapped_separated(vr, tmp_store, tmp_path):
    """登记 entry 必须同时含 baostock_raw（供应商原始值）与 baostock_mapped
    （ISS-114 映射后 canonical）——旧脚本把映射值冒充 raw（L3 点名缺陷）。"""
    cache = _supplier_cache(tmp_path, raw=72.707, mapped=0.72707)
    results = _run_register(vr, tmp_store, cache, tmp_path)
    by_key = {r["key"]: r for r in results}
    assert "000002_2024Q1" in by_key
    entry = by_key["000002_2024Q1"]["entry"]
    assert entry["baostock_raw"] == pytest.approx(72.707), \
        "raw 键必须承载供应商原始值（不是映射值）"
    assert entry["baostock_mapped"] == pytest.approx(0.72707), \
        "mapped 键承载映射后 canonical 值"
    assert entry["baostock_raw"] != entry["baostock_mapped"]
    assert entry["mapping_id"] == "umd_iss114_liability_v1"
    assert entry["correction_search_status"] == "not_performed", \
        "更正公告检索未执行必须如实明示（不得默认「首发版」）"
    assert "首发版" not in (entry.get("revision_note") or ""), \
        "固定「首发版」注记=无证据的版本声明——必须替换为如实状态"


# ── 临时库重放：漂移检测 + 不静默覆盖 ───────────────────────────

def test_replay_drift_detected_not_overwritten(vr, tmp_store, tmp_path):
    """同点位重放且供应商值不同 → 显式 EXISTING_DIVERGENT；库中旧记录原样
    （幂等不覆盖——登记语义漂移必须人工核对，不能被重跑静默冲掉）。"""
    cache1 = _supplier_cache(tmp_path / "c1" if False else tmp_path,
                             raw=72.707, mapped=0.72707)
    r1 = _run_register(vr, tmp_store, cache1, tmp_path)
    assert r1[0]["status"] == "REGISTERED", f"首轮应正常登记: {r1[0]}"
    first = json.loads((Path(tmp_store) / "originals" /
                        f"{r1[0]['verification_id']}.json").read_text(encoding="utf-8"))
    cache2 = _supplier_cache(tmp_path, raw=72.999, mapped=0.72999)
    r2 = _run_register(vr, tmp_store, cache2, tmp_path)
    assert r2[0]["status"] == "EXISTING_DIVERGENT", \
        f"内容漂移重放必须显式报告: {r2[0]}"
    second_now = json.loads((Path(tmp_store) / "originals" /
                             f"{r1[0]['verification_id']}.json").read_text(encoding="utf-8"))
    assert second_now == first, "旧记录不得被静默覆盖"


def test_replay_idempotent_same_inputs(vr, tmp_store, tmp_path):
    """同输入重放 → 幂等（无漂移报告、登记一致）——重放不产生重复记录。"""
    cache = _supplier_cache(tmp_path, raw=72.707, mapped=0.72707)
    r1 = _run_register(vr, tmp_store, cache, tmp_path)
    r2 = _run_register(vr, tmp_store, cache, tmp_path)
    assert r2[0]["status"] == "REGISTERED_IDEMPOTENT", f"同输入重放=幂等: {r2[0]}"
    assert r2[0]["verification_id"] == r1[0]["verification_id"]


def test_supplier_cache_absent_value_honest(vr, tmp_store, tmp_path):
    """供应商值获取失败（缓存显式 null=记录的获取失败）→ 如实登记 pending
    （不伪造供应商值、不触网）。"""
    cache = _supplier_cache(tmp_path, raw=None, mapped=None)
    results = _run_register(vr, tmp_store, cache, tmp_path)
    by_key = {r["key"]: r for r in results}
    e = by_key["000002_2024Q1"]["entry"]
    assert e["baostock_raw"] is None and e["baostock_mapped"] is None
    assert e["verification_level"] == "cross_checked_pending_original"


# ── 可携带清单 + 离线复验 ───────────────────────────────────────

@pytest.fixture(scope="module")
def manifest_path(tmp_path_factory):
    """从真实交付产物构建清单（PDF 均在仓库内——离线可复验）。"""
    bm = _load("build_evidence_manifest")
    out = tmp_path_factory.mktemp("manifest") / "evidence_manifest.json"
    bm.main(results_path=out)
    return out


def test_manifest_portable_fields(manifest_path):
    """清单字段齐全（L3 目标）：URL/hash/证券/期间/字段/单位/合并范围/页/行/
    原值/公式/版本/更正检索状态；六点位。"""
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert m["schema"] == "k3_evidence_manifest_v1"
    assert len(m["points"]) == 6, f"六点位: {len(m['points'])}"
    required = ("security_id", "period_end", "field", "unit", "consolidation_scope",
                "pdf_path", "file_hash", "page_table", "raw_line_label",
                "issuer_value", "derived_formula", "source_url",
                "correction_search_status", "supplier", "verification_level")
    for p in m["points"]:
        missing = [k for k in required if k not in p]
        assert not missing, f"{p.get('key')} 缺字段: {missing}"
        assert p["supplier"].get("recorded_only") is True, \
            "供应商值离线不可重算——必须标注 recorded_only"
        assert not os.path.isabs(p["pdf_path"]), "清单必须可携带（相对路径）"


def test_offline_verifier_all_pass(manifest_path, ov):
    """六点离线复验：PDF 在/hash 合/重提取/算式复算全过。"""
    summary = ov.main(manifest=manifest_path, pdf_root=REPO,
                      results_path=None)
    assert summary["total"] == 6
    assert summary["failed"] == 0, f"离线复验失败点: {summary['points']}"
    assert summary["passed"] == 6


def test_delivered_manifest_itself_passes(ov):
    """守卫 P2-1：随交付的 evidence_manifest.json 本体必须过离线复验（本机 PDF 齐
    → 6/6 PASS）——手工篡改/损坏交付清单会被回归拦住。"""
    summary = ov.main(manifest=K3 / "evidence_manifest.json", pdf_root=REPO,
                      results_path=None)
    assert summary["total"] == 6 and summary["failed"] == 0 and summary["passed"] == 6


def test_offline_verifier_missing_pdf_fails_not_faked(tmp_path, manifest_path, ov):
    """缺 PDF → MISSING/FAIL 如实报告（失败不伪补），退出码非零。"""
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    m["points"][0]["pdf_path"] = "tests/artifacts/k3_original_pilot/__no_such__.pdf"
    broken = tmp_path / "broken_manifest.json"
    broken.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    summary = ov.main(manifest=broken, pdf_root=REPO, results_path=None)
    assert summary["failed"] >= 1
    bad = summary["points"][0]
    assert bad["status"] in ("MISSING", "HASH_MISMATCH", "EXTRACT_FAILED", "VALUE_MISMATCH")
    assert not bad.get("recomputed_value_fabricated", False)
