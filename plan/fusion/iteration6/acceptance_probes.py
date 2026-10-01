"""K delivery boundary probes. Synthetic inputs; guarded temporary writes only."""
import contextlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.dont_write_bytecode = True


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    platform.uname()
    results = []
    with tempfile.TemporaryDirectory(prefix="muyun_k_accept_") as temp, contextlib.chdir(temp):
        root = Path(temp).resolve()
        os.environ.update(HOME=str(root), USERPROFILE=str(root))

        def check(p):
            if not Path(os.fsdecode(p)).resolve().is_relative_to(root):
                raise RuntimeError("write outside temporary root")

        def guard(event, args):
            if event in ("socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
                raise RuntimeError("external IO forbidden")
            if event == "open" and not isinstance(args[0], int):
                mode, flags = args[1:3]
                if (isinstance(mode, str) and any(x in mode for x in "wax+")) or (
                    isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
                ):
                    check(args[0])
            elif event in ("os.mkdir", "os.remove", "os.rmdir"):
                if event != "os.mkdir" or not Path(args[0]).is_dir():
                    check(args[0])
            elif event in ("os.rename", "os.replace"):
                for p in args[:2]:
                    check(p)
        sys.addaudithook(guard)
        from src.core.decision_policy import HorizonPlan, POLICY_ID_MID
        from src.core.research import ThesisAssessment, CheckpointCondition
        from src.core.research_service import ASSERTION_METHOD_VERSION
        from src.core.analysis_service import build_decision_packet
        from src.core.shadow_diff import capture_shadow, build_shadow_report, _output_fingerprint, _append_record
        from src.data.horizon_plans import HorizonPlanStore
        from src.data.research_store import AssessmentStore, ResearchStore
        from src.data.models import StrategyDecision, StrategyState
        from src.data.portfolio import PortfolioManager
        from src.core.research_application import ResearchApplicationService

        def run(name, fn):
            try:
                results.append({"id": name, **fn()})
            except Exception as exc:
                results.append({"id": name, "probe_error": f"{type(exc).__name__}: {exc}"})

        plans = HorizonPlanStore(root / "plans.json")
        assessments = AssessmentStore(root / "research")
        evaluated = datetime.now(timezone.utc)
        asm = ThesisAssessment(security_id="600519", horizon="MID", snapshot_id="snap-fixed",
                              status="VALID", method_version=ASSERTION_METHOD_VERSION, evaluated_as_of=evaluated)
        plan = HorizonPlan(plan_id="accepted-mid", security_id="600519", horizon="MID",
                           policy_id=POLICY_ID_MID, intent="synthetic acceptance case",
                           assessment_id=assessments.save(asm), snapshot_id="snap-fixed")
        plans.save(plan)
        assert plans.accept("600519", "MID")[0]
        assert plans.set_active_ref("600519", plans.get("600519", "MID"))[0]
        dr = SimpleNamespace(decision=SimpleNamespace(value="HOLD"), score=.6,
                             stock=SimpleNamespace(stock_code="600519", stock_name="fixture"), warnings=[])
        sd = StrategyDecision(decision="HOLD", position_action="HOLD_POSITION", position_ratio=.1,
                              lifecycle_before="HOLD", lifecycle_after="HOLD", new_state=StrategyState())
        ee = SimpleNamespace(effective_action=SimpleNamespace(value="HOLD"), blocked=False)
        pos = SimpleNamespace(stock_code="600519", current_ratio=.1, trade_plan=None)
        packet = build_decision_packet(dr, sd, ee, confirmed_ratio=.1, source="probe")
        rec = capture_shadow(dr, sd, ee, pos, packet=packet,
                             config={"fusion": {"mode": "capture_only", "shadow_capture": True}},
                             store_path=root / "shadow.jsonl", plans_store=plans, assessment_store=assessments,
                             account_version="synthetic-account-v1")

        def qualification():
            report = build_shadow_report(store_path=root / "shadow.jsonl", days=7)
            return {"observed_defect": rec.mid_effective and rec.mid_binding["quote_cutoff"] is None,
                    "quote_cutoff": rec.mid_binding["quote_cutoff"], "eligible": rec.mid_binding["eligible"],
                    "drop_reasons": rec.mid_binding["drop_reasons"], "mid_count": report["v6_mid_effective"],
                    "evidence_cutoff_is_capture_time": rec.mid_binding["evidence_cutoff"] == rec.as_of,
                    "assessment_time": evaluated.isoformat()}
        run("V1_missing_quote_counted_effective", qualification)

        def dedup():
            first = rec.model_copy(deep=True)
            second = rec.model_copy(deep=True)
            first.mid_binding["target_weight"] = .1
            second.mid_binding["target_weight"] = .2
            first.output_fingerprint = _output_fingerprint(first)
            second.output_fingerprint = _output_fingerprint(second)
            destination = root / "target_change.jsonl"
            assert _append_record(destination, first)
            saved = _append_record(destination, second)
            return {"observed_defect": not saved, "targets": [.1, .2],
                    "fingerprints_equal": first.output_fingerprint == second.output_fingerprint,
                    "second_saved": saved}
        run("V2_target_change_deduped", dedup)

        def quantity_context(ratio, action, after, case):
            pm = PortfolioManager(str(root / f"{case}.yaml"), str(root / f"{case}_proposals.json"))
            assert pm.add_position("600519", entry_price=10, ratio=ratio)
            out = pm.apply_quantity_fill("600519", action, quantity=100, price=10,
                                         trade_date="2026-09-30", fill_id=case,
                                         quantity_before=1000 if action == "SELL" else 0,
                                         quantity_after=after, avg_cost=10)
            p = pm.get_position("600519")
            state = pm.to_strategy_state("600519")
            return {"observed_defect": p.ratio_stale and state.current_position_ratio == ratio,
                    "ok": out.ok, "quantity": p.quantity_fact["quantity"], "ratio_stale": p.ratio_stale,
                    "strategy_ratio": state.current_position_ratio, "account_total_ratio": pm.get_total_position_ratio()}
        run("V3_stale_ratio_consumed", lambda: quantity_context(.1, "SELL", 900, "partial"))
        run("V3b_rebuilt_holding_ratio_zero", lambda: quantity_context(0, "BUY", 100, "rebuild"))

        def checkpoint_cli(with_ref=False):
            import start
            import src.cli.main as cli
            import src.core.research_application as application_module
            from rich.console import Console
            pub = "2026-09-01T08:00:00+00:00"
            body = "公司签订900万元订单。"
            document = {"claim": {"claim_id": "order1", "security_id": "600519", "subject": "600519",
                                   "event_type": "order", "statement": body, "value": 900, "unit": "万元",
                                   "quote_text": body, "citation_uri": "probe://order", "published_at": pub},
                        "document": {"canonical_uri": "probe://order", "body": body,
                                     "security_ids": ["600519"], "published_at": pub}}
            source = root / "claims.json"
            source.write_text(json.dumps([{"claim_id": "order1", "statement": body, "event_type": "order", "value": 900,
                                           "unit": "万元", "quote_text": body, "source_uri": "probe://order",
                                           "body": body, "published_at": pub}], ensure_ascii=False), encoding="utf-8")
            app = ResearchApplicationService(store=ResearchStore(root / "cli_research"),
                                             plans_store=HorizonPlanStore(root / "cli_plans.json"),
                                             assessment_store=AssessmentStore(root / "cli_research"))
            received = []
            original = app.run

            def capture(*args, **kwargs):
                result = original(*args, **kwargs)
                received.append((result, kwargs))
                return result
            command = f"research 600519 --claims {source} --checkpoint 到期复核 --confirm-risk --json"
            if with_ref:
                command += " --ref order1"
            mode, args = start.parse_input(command)
            with patch.object(application_module, "ResearchApplicationService", return_value=app), \
                 patch.object(app, "run", side_effect=capture), \
                 patch.object(cli, "console", Console(file=io.StringIO(), width=150)), \
                 contextlib.redirect_stdout(io.StringIO()):
                start.run_cli(mode, args)
            result, kwargs = received[0]
            assessment = app.assessment_store.load(result.assessment_ids["MID"])
            window = next(a for a in assessment.required_assertions if a.proposition_type == "window_and_refutation")
            cp = kwargs["checkpoints"][0]
            fixed_cp = cp.model_copy(update={"evidence_refs": ["order1"]})
            control = original("600519", as_of=result.as_of, documents=[document], checkpoints=[fixed_cp])
            control_asm = app.assessment_store.load(control.assessment_ids["MID"])
            control_window = next(a for a in control_asm.required_assertions if a.proposition_type == "window_and_refutation")
            return {"observed_defect": window.evaluation.value == "UNKNOWN" and control_window.evaluation.value == "TRUE",
                    "verified_claims": result.verified_claims, "user_confirmed": cp.user_confirmed,
                    "cli_evidence_refs": cp.evidence_refs, "cli_window": window.evaluation.value,
                    "service_with_explicit_refs_window": control_window.evaluation.value,
                    "assessment_status": assessment.status.value,
                    "scope": "checkpoint binding only; does not claim whole MID VALID"}
        run("V4_public_checkpoint_unreachable", checkpoint_cli)
        run("V4_explicit_ref_positive", lambda: checkpoint_cli(True))

        # W1: exercise actual adapters with quantity holdings and stale zero projection.
        def terminal_quantity():
            pm = PortfolioManager(str(root / "w1.yaml"), str(root / "w1_proposals.json"))
            pm.add_position("600519", entry_price=10, ratio=0)
            pm.apply_quantity_fill("600519", "BUY", quantity=100, price=10,
                                   trade_date="2026-09-30", fill_id="w1", quantity_before=0,
                                   quantity_after=100, avg_cost=10)
            holding = pm.get_position("600519")
            state = pm.to_strategy_state("600519")
            exit_sd = sd.model_copy(update={"decision": __import__("src.data.models", fromlist=["SignalType"]).SignalType.SELL,
                "position_action": __import__("src.data.models", fromlist=["PositionAction"]).PositionAction.CLOSE_ALL,
                "position_ratio": 0.0, "sell_path": "fundamental_alert", "new_state": state})
            terminal = build_decision_packet(dr, exit_sd, ee, confirmed_ratio=holding.current_ratio)
            s = capture_shadow(dr, exit_sd, ee, holding, packet=terminal,
                config={"fusion": {"mode": "capture_only", "shadow_capture": True}},
                store_path=root / "w1_shadow.jsonl", plans_store=plans, assessment_store=assessments,
                account_version="fixture", quote_as_of=evaluated.isoformat())
            unknown_reduce = sd.model_copy(update={"position_action":
                __import__("src.data.models", fromlist=["PositionAction"]).PositionAction.REDUCE,
                "position_ratio": None, "new_state": state})
            fixed_weight_only = build_decision_packet(dr, unknown_reduce, ee, confirmed_ratio=None)
            return {"observed_defect": terminal.desired_action.value == "WAIT",
                "quantity": holding.quantity_held, "strategy_weight": state.current_position_ratio,
                "raw_projection": holding.current_ratio, "strategy_exit": exit_sd.position_action.value,
                "terminal_action": terminal.desired_action.value, "shadow_mid_action": s.fusion_mid_action,
                "adapter_reduce_with_unknown_weight": fixed_weight_only.desired_action.value,
                "scope": "real adapters; call arguments match l/la/chat, no live account"}
        run("W1_quantity_exit_lost_at_terminal", terminal_quantity)

        def shadow_targets():
            from src.data.models import PositionAction, SignalType
            valid_plan = plans.get("600519", "MID").model_copy(update={"policy_version": "fixture-policy-v1"})
            plans.save(valid_plan)
            assert plans.accept("600519", "MID")[0]
            p = SimpleNamespace(stock_code="600519", current_ratio=.1, trade_plan=None,
                                ratio_stale=True, quantity_held=900)
            reduce_sd = sd.model_copy(update={"decision": SignalType.SELL,
                "position_action": PositionAction.REDUCE, "position_ratio": None,
                "sell_path": "weak_sell", "new_state": StrategyState(current_position_ratio=None)})
            pkt = build_decision_packet(dr, reduce_sd, ee, confirmed_ratio=.1)
            s = capture_shadow(dr, reduce_sd, ee, p, packet=pkt,
                config={"fusion": {"mode": "capture_only", "shadow_capture": True}},
                store_path=root / "w1b_shadow.jsonl", plans_store=plans, assessment_store=assessments,
                account_version="fixture", quote_as_of=evaluated.isoformat())
            arm = s.mid_binding["arms"]["fusion"]
            return {"observed_defect": arm["target"] is not None,
                "legacy_target": s.mid_binding["arms"]["legacy"]["target"],
                "fusion_arm": arm, "effective": s.mid_effective,
                "strategy_weight": None, "stale_projection": .1}
        run("W1b_shadow_stale_weight", shadow_targets)

        def quote_source():
            import src.data.akshare_client as market
            from datetime import timedelta
            old_day = (datetime.now().astimezone() - timedelta(days=2)).date().isoformat()
            row = [old_day, "sh.600519", "10", "11", "9", "10", "10", "100000", "1000000"]
            ticks = iter([True, False])
            rs = SimpleNamespace(error_code="0", fields="date,code,open,high,low,close,preclose,volume,amount".split(","),
                                 next=lambda: next(ticks), get_row_data=lambda: row)
            cls = market.AKShareClient
            with patch.object(market, "_ensure_baostock_login", return_value=True), \
                 patch.object(market.bs, "query_history_k_data_plus", return_value=rs):
                quote = cls._fetch_baostock_realtime("600519")
            with patch.object(cls, "get_realtime_quote", return_value=quote), \
                 patch.object(cls, "get_historical_kline", return_value=None):
                stock = cls._calculate_indicators_uncached("600519")
            pm = PortfolioManager(str(root / "w1.yaml"), str(root / "w1_proposals.json"))
            current_day = datetime.now().astimezone().date().isoformat()
            state = pm.to_strategy_state("600519", price=stock.price, price_as_of=stock.quote_as_of,
                                         nav=10000, nav_as_of=current_day)
            return {"observed_defect": stock.quote_as_of[:10] != old_day,
                "source_row_date": old_day, "returned_quote_as_of": stock.quote_as_of,
                "source": quote["source"], "weight_with_todays_nav": state.current_position_ratio}
        run("W2_old_close_relabelled_current", quote_source)

        def replay_window():
            from datetime import timedelta
            app = ResearchApplicationService(store=ResearchStore(root / "repeat_research"),
                plans_store=HorizonPlanStore(root / "repeat_plans.json"),
                assessment_store=AssessmentStore(root / "repeat_research"))
            a = app.run("600519", as_of=evaluated)
            b = app.run("600519", as_of=evaluated + timedelta(seconds=1))
            return {"same_material": True, "as_of_delta_seconds": 1,
                "same_run_id": a.run_id == b.run_id, "same_snapshot_id": a.snapshot_id == b.snapshot_id,
                "first_revisions": a.draft_revisions, "second_revisions": b.draft_revisions,
                "second_kinds": b.draft_kinds,
                "scope": "K2b preflight; different as_of is a legitimate new evaluation, not old fixed-input idempotency failure"}
        run("B1_research_auto_run_revision_baseline", replay_window)
        def account_read_baseline():
            import time
            import src.data.account_service as accounts
            from src.data.models import StockData
            ledger = root / "baseline_ledger.jsonl"
            accounts.AccountService(ledger).opening_import(opening_cash=100000,
                trade_date="2026-09-20", lots=[])
            pm = PortfolioManager(str(root / "baseline_portfolio.yaml"), str(root / "baseline_proposals.json"))
            stock = StockData(stock_code="600519", stock_name="fixture", volume=100000,
                              price=10, quote_as_of=evaluated.isoformat())
            original_snapshot = accounts.AccountService.snapshot
            counts = []
            with patch.object(accounts, "DEFAULT_LEDGER_PATH", ledger):
                for n in (1, 10, 30):
                    calls = []
                    def counted(service, *args, **kwargs):
                        calls.append(1)
                        return original_snapshot(service, *args, **kwargs)
                    t = time.perf_counter()
                    with patch.object(accounts.AccountService, "snapshot", counted):
                        for _ in range(n):
                            pm.strategy_state_for("600519", stock)
                    counts.append({"symbols": n, "ledger_replays": len(calls),
                                   "elapsed_ms": round((time.perf_counter() - t) * 1000, 3)})
            return {"measurements": counts, "scope": "synthetic one-event ledger, call-count baseline only; not production p95"}
        run("B2_per_symbol_account_replay", account_read_baseline)
        def actual_arm_fingerprint():
            first = rec.model_copy(deep=True)
            second = rec.model_copy(deep=True)
            first.mid_binding["arms"]["fusion"].update(target=.1, target_state="KNOWN")
            second.mid_binding["arms"]["fusion"].update(target=.2, target_state="KNOWN")
            return {"observed_defect": _output_fingerprint(first) == _output_fingerprint(second),
                    "field": "mid_binding.arms.fusion.target", "targets": [.1, .2]}
        run("V2_current_schema_target_positive", actual_arm_fingerprint)
        print(json.dumps({"baseline": "679afa6", "synthetic": True, "results": results}, ensure_ascii=False, indent=2))
    return int(any("probe_error" in r for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
