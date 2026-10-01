"""Maintain only R13 architect artifacts; no product or account mutations."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def git(*args):
    return subprocess.check_output(["git", *args], cwd=REPO, text=True, encoding="utf-8").strip()


def main():
    head = git("rev-parse", "HEAD")
    assert head.startswith("73ba005"), "New delivery: reassess before changing R13 artifacts"
    old = json.loads((HERE.parent / "iteration6/REVIEW_INDEX.json").read_text(encoding="utf-8-sig"))
    groups = {name: {path: hashlib.sha256((REPO / path).read_bytes()).hexdigest()
                     for path in paths} for name, paths in old["groups"].items()}
    added = ["src/data/portfolio.py", "src/core/shadow_diff.py", "src/core/analysis_service.py",
             "src/core/decision_policy.py", "src/data/akshare_client.py", "src/data/models.py",
             "src/core/orchestrator.py", "src/cli/main.py", "src/chat/tools.py", "src/cli/evidence.py",
             "tests/core/test_m0_account_terminal.py", "tests/data_sources/test_m1_quote_time.py",
             "tests/core/test_analysis_service.py", "tests/core/test_horizon_policy.py",
             "tests/core/test_batch_quotes.py", "tests/core/test_iss089_realtime_quote.py",
             "plan/fusion/iteration6/m2_readonly_view_prototype.py"]
    groups["M_delivery_and_consumers"] = {
        p: hashlib.sha256((REPO / p).read_bytes()).hexdigest() for p in added}
    changed = sorted({p for name, paths in old["groups"].items()
                      for p, digest in paths.items() if groups[name][p] != digest})
    result_text = (HERE / "ACCEPTANCE_PROBE_RESULTS.txt").read_text(encoding="utf-8-sig")
    rows = [json.loads(m.group(1)) for m in re.finditer(r"R13_RESULT (\{[^\n]+\})", result_text)]
    assert len(rows) == 17, len(rows)
    assert "15 failed, 2 passed" in result_text
    for name in ("TARGETED_TEST_RESULTS.txt", "ACCEPTANCE_PROBE_RESULTS.txt"):
        text = (HERE / name).read_text(encoding="utf-8-sig")
        assert "PROTECTED_PORTFOLIO_HASHES_UNCHANGED True" in text
        assert "PROTECTED_KNOWLEDGE_AND_PORTFOLIO_FILES 7" in text
        assert "KNOWLEDGE_FILE_SET_UNCHANGED True" in text
    probes = {"baseline_commit": head, "synthetic": True,
              "scope": "Real adapters and isolated stores. Chat upstream strategy/market are fixtures; no live account.",
              "contract_assertions": {"failed": 15, "passed": 2},
              "groups": ["X1", "X2", "X3", "X4 (M2 prototype only)"],
              "protected_files": 7, "protected_hashes_and_knowledge_set_unchanged": True,
              "results": rows}
    (HERE / "ACCEPTANCE_PROBE_RESULTS.json").write_text(
        json.dumps(probes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index = {"schema_version": 5, "reviewed_at": "2026-10-01", "baseline_commit": head,
             "prior_review": "../iteration6/REVIEW_INDEX.json",
             "scope": "Incremental M0/M1 and M2 prototype acceptance; not whole-file certification",
             "report": "R13_ACCEPTANCE.md", "milestones": "../../../MILESTONES.md",
             "checks": {"targeted_tests": {"files": 27, "passed": 381, "full_suite_rerun": False,
                                             "artifact": "TARGETED_TEST_RESULTS.txt"},
                        "contract_probes": {"failed": 15, "passed": 2,
                                            "artifact": "ACCEPTANCE_PROBE_RESULTS.json"},
                        "open_findings": ["X1", "X2", "X3", "X4"],
                        "W1_scope": "Accepted when quantity projection exists; remaining consumer gaps X1",
                        "W2_scope": "Old Baostock source date preserved; time validation gaps X3",
                        "K1_frozen": False, "M2_production_authorized": False},
             "changed_since_R12": changed, "groups": groups}
    (HERE / "REVIEW_INDEX.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    files = list(HERE.glob("*.md")) + [REPO / p for p in (
        "MILESTONES.md", "plan/fusion/STATUS.md", "plan/fusion/RESUME.md", "plan/fusion/README.md")]
    missing = []
    for f in files:
        for link in re.findall(r"\]\(([^)]+)\)", f.read_text(encoding="utf-8-sig")):
            if "://" not in link and not (f.parent / link.split("#")[0]).exists():
                # This tool creates its own result after checking the other documents.
                if link != "DOC_SYNC_INDEX.json":
                    missing.append({"file": str(f.relative_to(REPO)), "target": link})
    for p in HERE.glob("*.py"):
        ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))
    for p in HERE.glob("*.json"):
        json.loads(p.read_text(encoding="utf-8-sig"))
    product_diff = git("diff", "--name-only", "--", "src", "tests", "configs", "start.py")
    assert not product_diff, product_diff
    result = {"date": "2026-10-01", "head": head, "missing_links": missing,
              "json_and_ast": "PASS", "product_worktree_changes": [],
              "changed_source_paths_since_R12": changed,
              "fingerprinted_files": len({p for paths in groups.values() for p in paths}),
              "docs": [str(f.relative_to(REPO)) for f in files]}
    (HERE / "DOC_SYNC_INDEX.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
    print(json.dumps({"missing_links": missing, "json_and_ast": "PASS",
                      "fingerprinted_files": result["fingerprinted_files"],
                      "product_worktree_changes": []}, ensure_ascii=False))
    assert not missing


if __name__ == "__main__":
    main()
