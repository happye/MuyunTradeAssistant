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

        def checkpoint_cli():
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
            source.write_text(json.dumps([{"statement": body, "event_type": "order", "value": 900,
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
            mode, args = start.parse_input(f"research 600519 --claims {source} --checkpoint 到期复核 --confirm-risk --json")
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
                    "scope": "checkpoint binding only; does not claim whole MID VALID"}
        run("V4_public_checkpoint_unreachable", checkpoint_cli)
        print(json.dumps({"baseline": "7dbe57d", "synthetic": True, "results": results}, ensure_ascii=False, indent=2))
    return int(any("probe_error" in r for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
