"""R15 branch-intersection probes; run only through guarded review_test_runner."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests/core"))
import test_o0_exceptional_account as o0
import src.chat.tools as chat


def emit(case, **values):
    print("R15_RESULT " + json.dumps(dict(case=case, **values), ensure_ascii=False, default=str))


def test_partial_ledger_zero_quantity_projection_is_not_flat(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position(o0.CODE, entry_price=10, ratio=0.0)
    pm._data["positions"][o0.CODE]["quantity_fact"] = {
        "quantity": 0, "as_of": "2026-09-19", "avg_cost": 0.0}
    o0.account_module.DEFAULT_LEDGER_PATH.write_text('{"truncated_event"\n', encoding="utf-8")
    before = o0.account_module.DEFAULT_LEDGER_PATH.read_bytes()
    facts = pm.request_account_facts()
    assert facts._ledger_partial
    ctx = facts.context_for(o0.CODE)
    emit("Z1_partial_zero_projection", state=ctx.position_state,
         weight=ctx.confirmed_weight, reason=ctx.weight_reason)
    assert o0.account_module.DEFAULT_LEDGER_PATH.read_bytes() == before
    assert ctx.position_state == "UNKNOWN" and ctx.confirmed_weight is None


def test_projection_list_failure_sets_incomplete(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    # Valid YAML with an invalid record shape: the file-level syntax guard does
    # not mark it corrupted, while the real PositionRecord decoder fails.
    Path(o0.portfolio_module.DEFAULT_PORTFOLIO_PATH).write_text(
        "positions:\n  '600519': 42\n", encoding="utf-8")
    pm = o0.PortfolioManager()
    assert not pm._corrupted
    facts = pm.request_account_facts()
    entries, incomplete = facts.holding_entries()
    output = o0._repl("la")
    emit("Z2_projection_list_failure", count=len(entries), incomplete=incomplete, output=output)
    assert incomplete is True and "当前无持仓记录" not in output


def test_chat_mixed_holdings_not_truncated_to_projection(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    pm.add_position("000001", stock_name="projection only", entry_price=5, ratio=.1)
    o0._seed_ledger_only(quantity=100)
    monkeypatch.setattr(chat, "_portfolio_manager", pm)
    output = chat.get_portfolio()
    emit("Z3_chat_mixed_collection", output=output)
    assert "000001" in output and o0.CODE in output


def test_ledger_holding_output_does_not_claim_flat(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    o0._seed_ledger_only(quantity=100)
    monkeypatch.setattr(o0.akshare_module, "get_stock_data", o0._stub_market)
    output = o0._repl("la")
    emit("Z3_ledger_holding_output", says_flat="将从FLAT状态开始分析" in output,
         says_ledger_held="账本有仓 100 股" in output)
    assert "将从FLAT状态开始分析" not in output
    assert "账本有仓 100 股" in output


def test_complete_empty_and_legacy_ratio_stay_distinct(tmp_path, monkeypatch):
    o0._isolate(tmp_path, monkeypatch)
    pm = o0.PortfolioManager()
    empty = pm.request_account_facts().context_for(o0.CODE)
    pm.add_position(o0.CODE, entry_price=10, ratio=.1)
    legacy = pm.request_account_facts().context_for(o0.CODE)
    emit("compat_no_ledger", empty=empty.position_state,
         legacy=legacy.position_state, weight=legacy.confirmed_weight)
    assert empty.position_state == "NONE"
    assert legacy.position_state == "HELD" and legacy.confirmed_weight == .1


@pytest.mark.parametrize("future", [False, True])
def test_time_gate_capture_storage_report_same_call(tmp_path, monkeypatch, future):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "iteration8"))
    import r13_contract_replay as replay
    sh = ZoneInfo("Asia/Shanghai")
    fixed = (datetime.now(sh) + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)
    monkeypatch.setattr(replay.shadow, "datetime", FrozenDatetime)
    quote = fixed.replace(hour=23 if future else 9).replace(tzinfo=None).isoformat()
    monkeypatch.setattr(replay.m0, "PRICE_DAY", quote)
    _, rec = replay.capture(tmp_path, monkeypatch)
    report = replay.shadow.build_shadow_report(store_path=tmp_path / "capture.jsonl", days=7)
    stored = json.loads((tmp_path / "capture.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    emit("O1_capture_report", future=future, quote=quote,
         effective=rec.mid_effective, stored=stored["mid_effective"],
         denominator=report["cur_mid_effective"], reasons=rec.mid_binding["drop_reasons"])
    assert rec.mid_effective is (not future)
    assert stored["mid_effective"] is (not future)
    assert report["cur_mid_effective"] == int(not future)
    if future:
        assert "quote_cutoff_future_mid" in rec.mid_binding["drop_reasons"]
