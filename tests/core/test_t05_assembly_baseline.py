"""T05 同环境装配只读耗时基线。

测一次请求装配 30 只持仓、再逐只取上下文的墙钟 p50/p95。
不改生产语义，不读真实账户，不打网络。
"""
import sys
import threading
import time
import tracemalloc
from statistics import quantiles

import test_o0_exceptional_account as o0
from src.data import account_service
from src.data.portfolio import PortfolioManager

RUNS = 21
N = 30
PRICE_DAY = "2026-09-30"


def _codes():
    return [f"{600000 + i:06d}" for i in range(N)]


def test_t05_thirty_stock_assembly_reads_ledger_once(tmp_path, monkeypatch, capsys):
    o0._isolate(tmp_path, monkeypatch)
    codes = _codes()
    account_service.AccountService(account_service.DEFAULT_LEDGER_PATH).opening_import(
        opening_cash=100000.0,
        trade_date="2026-09-20",
        lots=[{"security_id": code, "quantity": 100, "cost_price": 10.0,
               "acquired_at": "2026-09-20"} for code in codes],
    )
    calls = []
    original = account_service.AccountService.snapshot

    def snapshot(service):
        calls.append(1)
        return original(service)

    monkeypatch.setattr(account_service.AccountService, "snapshot", snapshot)
    threads_before = threading.active_count()
    tracemalloc.start()
    samples = []
    try:
        for _ in range(RUNS):
            before = len(calls)
            started = time.perf_counter()
            facts = PortfolioManager().request_account_facts()
            contexts = [facts.context_for(code, price=10.0, price_as_of=PRICE_DAY)
                        for code in codes]
            samples.append(time.perf_counter() - started)
            assert len(calls) - before == 1
            assert len(contexts) == N
            assert all(ctx.position_state == "HELD" and ctx.quantity == 100
                       for ctx in contexts)
            assert facts.account_version
            del facts, contexts
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    threads_after = threading.active_count()
    ordered = sorted(samples)
    p50 = quantiles(ordered, n=100)[49]
    p95 = quantiles(ordered, n=100)[94]
    print(
        f"T05_BASELINE runs={RUNS} stocks={N} snapshot_per_request=1 "
        f"p50_s={p50:.6f} p95_s={p95:.6f} max_s={ordered[-1]:.6f} "
        f"peak_bytes={peak} threads_before={threads_before} threads_after={threads_after}",
        flush=True,
    )
    assert threads_after <= threads_before + 1
    assert p95 < 2.0
    captured = capsys.readouterr()
    assert "T05_BASELINE" in captured.out
    sys.stderr.write(captured.out)
