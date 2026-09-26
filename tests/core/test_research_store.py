"""研究数据最小存储回归测试（plan/fusion iteration2 R1，DATA_TRUST §5）

锁死语义：
1. raw 归档写一次幂等：同内容重复归档不覆盖原文件（原始留样不可篡改）
2. 快照清单写一次：同 snapshot_id 幂等跳过
3. 纠错账本：追加式 + 按证券/指标/状态检索；坏行隔离不崩（G14）
4. 测试隔离：research_dir 显式传入，绝不读写真实 HOME

纯内存+临时目录测试，零网络。跑法：pytest tests/core/test_research_store.py -q
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.data.research_store import ResearchStore


def test_archive_raw_write_once_idempotent(tmp_path):
    store = ResearchStore(tmp_path / "research")
    digest1, created1 = store.archive_raw({"foo": "bar", "n": 1}, metadata={"src": "baostock"})
    assert created1 is True and len(digest1) == 64
    path = store.raw_path(digest1)
    assert path.exists()
    original = path.read_text(encoding="utf-8")
    # 同内容重复归档：幂等（created=False），原文件不被触碰
    digest2, created2 = store.archive_raw({"foo": "bar", "n": 1})
    assert digest2 == digest1 and created2 is False
    assert path.read_text(encoding="utf-8") == original
    # 不同内容：新文件
    digest3, created3 = store.archive_raw({"foo": "bar", "n": 2})
    assert digest3 != digest1 and created3 is True


def test_snapshot_manifest_write_once(tmp_path):
    store = ResearchStore(tmp_path / "research")
    assert store.save_snapshot_manifest("snap-1", {"as_of": "2026-09-26"}) is True
    # 同 id 幂等跳过（不覆盖）
    assert store.save_snapshot_manifest("snap-1", {"as_of": "2099-01-01"}) is False
    saved = json.loads((tmp_path / "research" / "snapshots" / "snap-1.json")
                       .read_text(encoding="utf-8"))
    assert saved["as_of"] == "2026-09-26", "首次内容保留——快照清单不可被改写"


def test_invalidation_ledger_append_and_filter(tmp_path):
    store = ResearchStore(tmp_path / "research")
    inv1 = store.append_invalidation(cause_kind="unit_drift", security_id="000002",
                                     metric="liabilityToAsset", period_end="2023-12-31",
                                     description="量级漂移待核",
                                     affected_refs=[{"kind": "evidence", "id": "ev-1"}])
    inv2 = store.append_invalidation(cause_kind="manual_correction", security_id="600519",
                                     metric="netProfit", description="人工更正")
    assert inv1["status"] == "open" and inv1["invalidation_id"] != inv2["invalidation_id"]
    assert len(store.list_invalidations()) == 2
    assert [r["metric"] for r in store.list_invalidations(security_id="000002")] == ["liabilityToAsset"]
    assert [r["cause_kind"] for r in store.list_invalidations(metric="netProfit")] == ["manual_correction"]
    assert store.list_invalidations(status="resolved") == []


def test_invalidation_ledger_bad_line_isolated(tmp_path):
    store = ResearchStore(tmp_path / "research")
    store.append_invalidation(cause_kind="unit_drift", metric="x", description="ok")
    path = tmp_path / "research" / "invalidations.jsonl"
    path.write_text("{broken json\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    assert len(store.list_invalidations()) == 1, "坏行隔离（G14）"


def test_store_never_touches_portfolio_or_real_home(tmp_path, monkeypatch):
    """存储边界：只写 research_dir 内；纠错登记不触碰持仓文件（旧已确认成交保持事实）。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    store = ResearchStore(tmp_path / "research")
    store.archive_raw("hello")
    store.append_invalidation(cause_kind="unit_drift", metric="m", description="d")
    wrote = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert all(str(p).startswith(str(tmp_path / "research")) for p in wrote), "只写 research 目录"
    assert not list(home.rglob("*")), "未触碰 HOME"
