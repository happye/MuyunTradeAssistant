"""Replay R14 contracts on O delivery; historical evidence stays unchanged."""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

PREV = Path(__file__).resolve().parents[1] / "iteration8"
sys.path.insert(0, str(PREV))
spec = importlib.util.spec_from_file_location("r14_original_probes", PREV / "acceptance_probes.py")
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)
for name in dir(original):
    if name.startswith("test_") and name != "test_la_does_not_omit_ledger_only_holding":
        globals()[name] = getattr(original, name)


def test_la_does_not_omit_ledger_only_holding(tmp_path, monkeypatch):
    import src.data.akshare_client as market
    # R14 stopped before fetching. With O0 fixed the entry reaches the market;
    # replace that external dependency only, retaining the actual REPL pipeline.
    with patch.object(market, "get_stock_data", original.replay.m0._stub_market):
        original.test_la_does_not_omit_ledger_only_holding(tmp_path, monkeypatch)
