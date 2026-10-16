"""T05 same-environment assembly failure then recovery timing.

The 21-run happy-path baseline stays in test_t05_assembly_baseline.py.
This file only times the existing read-failure and the next good request.
It does not change production semantics and does not read a real account.
"""
import hashlib
import sys
import threading
import time
import tracemalloc
from statistics import quantiles

import src.data.account_service as account_module
import src.data.portfolio as portfolio_module
import src.data.proposals as proposals_module
from src.data.portfolio import PortfolioManager

RUNS = 11
N = 30
PRICE_DAY = "2026-09-30"


def _codes():
    return [f"{600000 + i:06d}" for i in range(N)]


def _pct(samples, index):
    return quantiles(sorted(samples), n=100)[index]


def _assemble(codes):
    started = time.perf_counter()
    facts = PortfolioManager().request_account_facts()
    contexts = [
        facts.context_for(code, price=10.0, price_as_of=PRICE_DAY) for code in codes
    ]
    return facts, contexts, time.perf_counter() - started


def test_t05_same_env_failure_then_recovery(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        portfolio_module, "DEFAULT_PORTFOLIO_PATH", str(tmp_path / "portfolio.yaml")
    )
    monkeypatch.setattr(
        proposals_module, "DEFAULT_PROPOSALS_PATH", str(tmp_path / "proposals.json")
    )
    good = tmp_path / "account_events.jsonl"
    monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", good)
    codes = _codes()
    account_module.AccountService(good).opening_import(
        opening_cash=100000.0,
        trade_date="2026-09-20",
        lots=[
            {
                "security_id": code,
                "quantity": 100,
                "cost_price": 10.0,
                "acquired_at": "2026-09-20",
            }
            for code in codes
        ],
    )
    digest = hashlib.sha256(good.read_bytes()).hexdigest()
    calls = []
    original = account_module.AccountService.snapshot

    def snapshot(service):
        calls.append(1)
        return original(service)

    monkeypatch.setattr(account_module.AccountService, "snapshot", snapshot)
    threads_before = threading.active_count()
    tracemalloc.start()
    before_samples = []
    after_samples = []
    try:
        version = ""
        for _ in range(RUNS):
            mark = len(calls)
            facts, contexts, elapsed = _assemble(codes)
            before_samples.append(elapsed)
            assert len(calls) - mark == 1
            assert facts.account_version
            assert all(
                ctx.position_state == "HELD" and ctx.quantity == 100 for ctx in contexts
            )
            version = facts.account_version
        bad = tmp_path / "ledger_dir"
        bad.mkdir()
        monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", bad)
        mark = len(calls)
        started = time.perf_counter()
        failed = PortfolioManager().request_account_facts()
        failed_ctx = failed.context_for(codes[0], price=10.0, price_as_of=PRICE_DAY)
        failure_s = time.perf_counter() - started
        assert len(calls) - mark == 1
        assert failed._read_failed is True
        assert failed_ctx.position_state == "UNKNOWN"
        assert failed_ctx.quantity is None
        assert failed_ctx.confirmed_weight is None
        monkeypatch.setattr(account_module, "DEFAULT_LEDGER_PATH", good)
        for _ in range(RUNS):
            mark = len(calls)
            facts, contexts, elapsed = _assemble(codes)
            after_samples.append(elapsed)
            assert len(calls) - mark == 1
            assert facts.account_version == version
            assert all(
                ctx.position_state == "HELD" and ctx.quantity == 100 for ctx in contexts
            )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    threads_after = threading.active_count()
    assert hashlib.sha256(good.read_bytes()).hexdigest() == digest
    p50_before = _pct(before_samples, 49)
    p95_before = _pct(before_samples, 94)
    p50_after = _pct(after_samples, 49)
    p95_after = _pct(after_samples, 94)
    print(
        f"T05_FAILURE_RECOVERY runs={RUNS} stocks={N} snapshot_per_request=1 "
        f"before_p50_s={p50_before:.6f} before_p95_s={p95_before:.6f} "
        f"failure_s={failure_s:.6f} after_p50_s={p50_after:.6f} "
        f"after_p95_s={p95_after:.6f} peak_bytes={peak} "
        f"threads_before={threads_before} threads_after={threads_after} "
        f"ledger_bytes_unchanged=1",
        flush=True,
    )
    assert threads_after <= threads_before + 1
    assert p95_before < 2.0 and p95_after < 2.0 and failure_s < 2.0
    captured = capsys.readouterr()
    assert "T05_FAILURE_RECOVERY" in captured.out
    sys.stderr.write(captured.out)
