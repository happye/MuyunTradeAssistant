"""Maintain architect-owned evidence only; never touch product or user data."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def main():
    prior = json.loads((REPO / "plan/fusion/iteration5/REVIEW_INDEX.json").read_text(encoding="utf-8-sig"))
    groups = {name: {path: hashlib.sha256((REPO / path).read_bytes()).hexdigest()
                     for path in paths} for name, paths in prior["groups"].items()}
    additions = ["src/core/analysis_service.py", "src/core/decision_contract.py",
                 "src/data/akshare_client.py", "src/data/models.py", "src/core/strategy_layer.py",
                 "src/core/decision_engine.py", "src/core/orchestrator.py", "src/chat/tools.py",
                 "src/scanner/scanner_engine.py", "src/tui/app.py", "src/web/app.py"]
    additions += [p.relative_to(REPO).as_posix() for p in (REPO / "tests/core").glob("test_l[0-3]_*.py")]
    additions += [p.relative_to(REPO).as_posix() for p in (REPO / "tests/evidence/k3_original_pilot").glob("*") if p.is_file()]
    groups["L_delivery_and_consumers"] = {p: hashlib.sha256((REPO / p).read_bytes()).hexdigest() for p in additions}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    index = {"schema_version": 4, "reviewed_at": "2026-10-01", "baseline_commit": head,
             "scope": "Scoped L acceptance, consumer/source probes; not whole-file certification",
             "report": "R12_ACCEPTANCE.md", "milestones": "../../../MILESTONES.md",
             "checks": {"targeted_tests": {"passed": 281, "full_suite_rerun": False,
                                             "artifact": "TARGETED_TEST_RESULTS.txt"},
                        "open_findings": ["W1", "W2"], "local_originals_passed": 6,
                        "normative_design_cases_passed": 19}, "groups": groups}
    (HERE / "REVIEW_INDEX.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = [HERE / name for name in ("README.md", "R12_ACCEPTANCE.md", "DESIGN_VALIDATION.md", "DELIVERY_PLAN.md")]
    files += [REPO / name for name in ("MILESTONES.md", "plan/fusion/STATUS.md", "plan/fusion/RESUME.md")]
    missing = []
    for f in files:
        for target in re.findall(r"\]\(([^)]+)\)", f.read_text(encoding="utf-8-sig")):
            if "://" not in target and not (f.parent / target.split("#")[0]).exists():
                missing.append({"file": str(f.relative_to(REPO)), "target": target})
    for p in HERE.glob("*.json"):
        json.loads(p.read_text(encoding="utf-8-sig"))
    for p in HERE.glob("*.py"):
        ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))
    probes = json.loads((HERE / "ACCEPTANCE_PROBE_RESULTS.json").read_text(encoding="utf-8-sig"))
    assert not any("probe_error" in r for r in probes["results"])
    result = {"date": "2026-10-01", "missing_links": missing,
              "fingerprinted_files": len({p for paths in groups.values() for p in paths}),
              "json_and_ast": "PASS", "probe_errors": 0,
              "docs": [str(f.relative_to(REPO)) for f in files]}
    (HERE / "DOC_SYNC_INDEX.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    assert not missing


if __name__ == "__main__":
    main()
