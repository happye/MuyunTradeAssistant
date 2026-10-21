"""ISS-123 残余：测试不得写入仓库 分析报告/analysis（evidence._REPORT_DIR 隔离）。

红→绿约定（对齐 BUG-IDX）：
- 红：把 _REPORT_DIR 指回仓库生产目录再 record_evidence → 生产目录出现新卡
- 绿：session 隔离生效时，record_evidence 只写临时目录；生产目录文件名集合不变
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.cli import evidence as ev
from src.data.models import DecisionResult, MarketState, SignalType, StockData, SkillSignal

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_REPORT_DIR = _REPO_ROOT / "分析报告" / "analysis"


def _mk_result(code="600519", name="隔离探针股", price=100.0):
    stock = StockData(
        stock_code=code, stock_name=name, price=price,
        change_pct=0.0, open=price, high=price, low=price, volume=1000,
    )
    signals = [
        SkillSignal(skill_name="s1", skill_alias="趋势", signal=SignalType.BUY,
                    confidence=0.8, reason=[]),
    ]
    return DecisionResult(
        stock=stock, state=MarketState.RISK_ON, decision=SignalType.HOLD,
        score=0.55, signals=signals, reason=["隔离探针"], warnings=[],
    )


def _mk_strategy():
    return SimpleNamespace(
        position_action=SimpleNamespace(value="HOLD_POSITION"),
        sell_path=None,
        top_signal=None,
    )


def test_session_isolates_report_dir_away_from_repo():
    """session 隔离：模块 _REPORT_DIR 不得仍指向仓库生产目录。"""
    real = _REAL_REPORT_DIR.resolve()
    current = Path(ev._REPORT_DIR).resolve()
    assert current != real, (
        f"evidence._REPORT_DIR 仍指向生产目录 {real}；"
        "conftest._isolate_evidence_report_dir 未生效"
    )


def test_record_evidence_under_isolation_skips_repo_dir(tmp_path, monkeypatch):
    """绿：隔离下写卡不碰仓库；本测再把目录指到 tmp_path 便于断言文件落点。"""
    before = set(_REAL_REPORT_DIR.iterdir()) if _REAL_REPORT_DIR.is_dir() else set()
    target = tmp_path / "cards"
    monkeypatch.setattr(ev, "_REPORT_DIR", target)
    monkeypatch.setattr(ev, "_EVIDENCE_FILE", tmp_path / "evidence.jsonl")
    monkeypatch.setattr(ev, "_STATE_DIR", tmp_path)

    rec = ev.record_evidence(_mk_result(), _mk_strategy(), source="test-iso")
    assert rec is not None
    written = list(target.glob("*.md"))
    assert len(written) == 1
    assert written[0].is_file()
    after = set(_REAL_REPORT_DIR.iterdir()) if _REAL_REPORT_DIR.is_dir() else set()
    assert after == before


def test_red_without_isolation_would_write_repo_dir(monkeypatch, tmp_path):
    """红（受控演示）：临时把 _REPORT_DIR 指回生产目录 → 出现新文件；测末清理。

    不依赖 session 守卫（本文件写入会在单测内删除）；证明污染根因是模块常量
    锚仓库根，而非调用方故意写盘。
    """
    _REAL_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    marker = f"ISO-RED-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    monkeypatch.setattr(ev, "_REPORT_DIR", _REAL_REPORT_DIR)
    monkeypatch.setattr(ev, "_EVIDENCE_FILE", tmp_path / "evidence.jsonl")
    monkeypatch.setattr(ev, "_STATE_DIR", tmp_path)

    before = set(os.listdir(_REAL_REPORT_DIR))
    rec = ev.record_evidence(
        _mk_result(name=marker), _mk_strategy(), source="test-iso-red",
    )
    assert rec is not None
    after = set(os.listdir(_REAL_REPORT_DIR))
    added = after - before
    assert added, "未隔离时应向仓库分析报告/analysis 新增文件（红灯前提）"
    for name in added:
        try:
            (_REAL_REPORT_DIR / name).unlink()
        except OSError:
            pass
    assert set(os.listdir(_REAL_REPORT_DIR)) == before


def test_report_dir_names_helper_detects_added_file(tmp_path, monkeypatch):
    """守卫用的文件名集合：新增文件可被差分检出（不依赖全量 sha）。"""
    import tests.conftest as cf

    fake = tmp_path / "analysis"
    fake.mkdir()
    monkeypatch.setattr(cf, "_REAL_REPORT_DIR", str(fake))
    before = cf._report_dir_names()
    assert before == set()
    (fake / "probe.md").write_text("x", encoding="utf-8")
    after = cf._report_dir_names()
    assert after - before == {"probe.md"}
