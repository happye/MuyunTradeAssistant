"""Independent synthetic architecture probes; no production files or network.

Run from any directory with the project venv Python and -B. JSON goes to stdout.
An observed_defect=true is a reproduced counterexample, not a passing product test.
"""
import io
import contextlib
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.dont_write_bytecode = True
NOW = datetime(2026, 9, 27, 8, tzinfo=timezone.utc)
PUB = datetime(2026, 9, 1, tzinfo=timezone.utc)


def run():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    results = []
    with tempfile.TemporaryDirectory(prefix="muyun_arch_review_") as temp, contextlib.chdir(temp):
        root = Path(temp).resolve()
        os.environ.update(HOME=str(root), USERPROFILE=str(root), PYTHONDONTWRITEBYTECODE="1")

        def audit(event, args):
            if event in ("socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
                raise RuntimeError("probe disallows network and subprocesses")
            target = None
            if event == "open":
                filename, mode, flags = args
                if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                    isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
                ):
                    target = filename
            elif event in ("os.mkdir", "os.remove", "os.rmdir"):
                target = args[0]
            elif event in ("os.rename", "os.replace"):
                for name in args[:2]:
                    if not Path(name).resolve().is_relative_to(root):
                        raise RuntimeError(f"out-of-temp mutation: {event}")
            if target is not None and not isinstance(target, int):
                if not Path(target).resolve().is_relative_to(root):
                    raise RuntimeError(f"out-of-temp mutation: {event}")

        sys.addaudithook(audit)
        from src.core.claim_extraction import ClaimRecord, SourceDocument, verify_claim_tiered
        from src.data.account_service import AccountService, FillInput
        from src.data.account_snapshot import AccountSnapshot
        from src.data.portfolio import PortfolioManager
        from src.data.proposals import Proposal
        from src.core.research import ThesisAssertion, evaluate_assertion
        from src.data.research_snapshot import EvidenceRecord, EvidenceSnapshot

        def record(name, fn):
            try:
                results.append({"id": name, **fn()})
            except Exception as exc:
                results.append({"id": name, "probe_error": f"{type(exc).__name__}: {exc}"})

        def claim(body, statement, **kw):
            doc = SourceDocument(canonical_uri="probe://synthetic", content_hash=SourceDocument.body_hash(body),
                                 security_ids=["600000"], published_at=PUB, body=body)
            item = ClaimRecord(security_id="600000", subject="600000", statement=statement,
                               citation_uri=doc.canonical_uri, citation_hash=doc.content_hash,
                               quote_text=body, published_at=PUB, **kw)
            out = verify_claim_tiered(item, [doc], as_of=NOW)
            return {"observed_defect": out.level.value == "FACT_CHECKED", "level": out.level.value,
                    "body": body, "statement": statement, "failures": out.failures}

        record("D1a_metric_binding", lambda: claim("公司营收100万元，净利润10万元。", "公司净利润100万元",
               event_type="earnings", value=100, unit="万元"))
        record("D1b_sign", lambda: claim("公司净利润-100万元。", "公司净利润100万元",
               event_type="earnings", value=100, unit="万元"))
        record("D1c_decimal", lambda: claim("公司签订0.900万元订单。", "公司签订900万元订单",
               event_type="order", value=900, unit="万元"))
        record("D1d_negation_binding", lambda: claim("本期无新订单。", "公司不存在偿债风险",
               event_type="order", negation_flag=True))

        def bad_tail():
            svc = AccountService(root / "tail" / "events.jsonl")
            svc.opening_import(opening_cash=10000, trade_date="2026-09-20")
            with svc.log.path.open("a", encoding="utf-8") as stream:
                stream.write('{"truncated":')
            before = svc.snapshot()
            receipt = svc.confirm_fill("after_tail", svc.account_version(), FillInput(
                security_id="600519", action="BUY", quantity=100, price=10, fee=0, trade_date="2026-09-21"))
            after = svc.snapshot()
            return {"observed_defect": receipt.ok and not after.holdings, "receipt": receipt.status,
                    "before_completeness": before.data_completeness, "after_completeness": after.data_completeness,
                    "isolated_events": after.isolated_events, "cash": after.cash_available,
                    "holdings": [h.model_dump(mode="json") for h in after.holdings]}
        record("D2_truncated_tail_then_append", bad_tail)

        def manager(name):
            folder = root / name
            folder.mkdir()
            return PortfolioManager(str(folder / "portfolio.yaml"), str(folder / "proposals.json"))

        def crash_projection():
            pm = manager("crash")
            pm.add_position("600519", entry_price=10, ratio=.1)
            with patch.object(pm._proposals, "record_fill", side_effect=RuntimeError("simulated crash after portfolio save")):
                try:
                    pm.confirm_fill("600519", "BUY", .1, price=10, fill_id="same_fill")
                except RuntimeError:
                    pass
            pm2 = PortfolioManager(pm.portfolio_path, str(pm._proposals.path))
            before = pm2.get_position("600519").current_ratio
            receipt = pm2.confirm_fill("600519", "BUY", .1, price=10, fill_id="same_fill")
            after = pm2.get_position("600519").current_ratio
            return {"observed_defect": after > before, "after_crash_ratio": before,
                    "after_retry_ratio": after, "retry_ok": receipt.ok, "duplicate": receipt.duplicate}
        record("D3_projection_crash_gap", crash_projection)

        def partial_cli():
            import src.cli.main as main
            import src.data.account_service as account_module
            from rich.console import Console
            pm = manager("partial")
            pm.add_position("600519", entry_price=10, ratio=.1)
            proposal = pm._proposals.add_proposal(Proposal(stock_code="600519", position_action="CLOSE_ALL",
                                                         target_ratio=0, current_ratio=.1))
            ledger = root / "partial" / "events.jsonl"
            svc = AccountService(ledger)
            svc.opening_import(opening_cash=10000, trade_date="2026-09-20", lots=[{
                "security_id": "600519", "quantity": 1000, "cost_price": 10, "acquired_at": "2026-09-20"}])
            output = io.StringIO()
            with patch.object(main, "PortfolioManager", return_value=pm), \
                 patch.object(main, "console", Console(file=output, width=160)), \
                 patch.object(account_module, "DEFAULT_LEDGER_PATH", ledger):
                main.manage_positions("confirm", stock_code="600519", qty=100, price=10, trade_date="2026-09-21")
            remaining = sum(h.quantity for h in svc.snapshot().holdings)
            view = pm.get_position("600519")
            return {"observed_defect": remaining == 900 and view is None, "remaining_quantity": remaining,
                    "portfolio_record_exists": view is not None, "proposal_status": proposal.status.value,
                    "cli_output": output.getvalue()}
        record("D4_quantity_partial_sell_cli", partial_cli)

        def proposition():
            from src.core.factor_compute import cash_conversion_v1, roe_observed_v1
            a = ThesisAssertion(security_id="600519", horizon="LONG", proposition_type="cash_sustainability",
                                description="盈利和现金持续性已建立", evidence_requirements=["cash_conversion_v1", "roe_observed_v1"])
            records = [{"metric": metric, "value": value, "period_end": "2026-06-30",
                        "published_at": PUB.isoformat()}
                       for metric, value in [("CFOToNP", -2), ("netProfit", 500000), ("roeAvg", -.1)]]
            caps = {x.factor_id: x for x in [cash_conversion_v1(records), roe_observed_v1(records)]}
            out = evaluate_assertion(a, available_capabilities=caps, verified_evidence_ids=set(), as_of=NOW)
            return {"observed_defect": out.evaluation.value == "TRUE", "evaluation": out.evaluation.value,
                    "factor_values": {key: {"status": x.status, "value": x.value} for key, x in caps.items()},
                    "scope": "real factor functions plus pure evaluator; not whole LONG VALID"}
        record("D5_capability_is_not_proposition", proposition)

        def subject():
            r = EvidenceRecord(security_id="000002", source_kind="financial", metric_or_claim="roeAvg",
                               value=12, published_at=PUB, available_at=PUB, fetched_at=PUB)
            snap = EvidenceSnapshot.build("600519", NOW, [r], strict=False)
            return {"observed_defect": bool(snap.records), "snapshot_security": snap.security_id,
                    "record_securities": [x.security_id for x in snap.records]}
        record("D6_snapshot_subject_boundary", subject)

        def cash():
            snap = AccountSnapshot(as_of=NOW, cash_available=1000, cash_reserved=200)
            return {"observed_defect": snap.cash_deployable != 1000,
                    "declared_already_net_available": 1000, "reserved": 200, "deployable": snap.cash_deployable}
        record("D7_reserved_semantics", cash)
        sources = ["src/core/claim_extraction.py", "src/core/research.py", "src/core/factor_compute.py",
                   "src/data/account_service.py", "src/data/account_snapshot.py", "src/data/portfolio.py",
                   "src/data/proposals.py", "src/data/research_snapshot.py", "src/cli/main.py"]
        print(json.dumps({"review_baseline": "def45d1", "executed_at": datetime.now(timezone.utc).isoformat(),
                          "source_sha256": {p: hashlib.sha256((REPO / p).read_bytes()).hexdigest() for p in sources},
                          "synthetic": True, "network": "blocked",
                          "write_boundary": "temporary root enforced by audit hook", "results": results},
                         ensure_ascii=False, indent=2))
    return 1 if any("probe_error" in r for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(run())
