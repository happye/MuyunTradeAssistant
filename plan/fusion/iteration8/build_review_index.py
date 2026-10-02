"""Record R14 scope, current source fingerprints and reproducible test manifest."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8").strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(filename, marker):
    out = []
    for line in (HERE / filename).read_text(encoding="utf-8-sig").splitlines():
        if marker in line and "{" in line.split(marker, 1)[-1]:
            try:
                out.append(json.loads(line.split(marker, 1)[1]))
            except json.JSONDecodeError:
                pass
    return out


replay = records("R13_REPLAY_RESULTS.txt", "R13_RESULT ")
probes = records("ACCEPTANCE_PROBE_RESULTS.txt", "R14_RESULT ")
assert len(replay) == 17 and len(probes) == 6, (len(replay), len(probes))
raw = (HERE / "ACCEPTANCE_PROBE_RESULTS.txt").read_text(encoding="utf-8-sig")
assert "5 failed, 2 passed" in raw and "R14_M2_PROTOTYPE_ASSERTIONS 25" in raw
assert "ImportError:" not in raw and "ValidationError:" not in raw
for name in ("TARGETED_TEST_RESULTS.txt", "R13_REPLAY_RESULTS.txt", "ACCEPTANCE_PROBE_RESULTS.txt"):
    log = (HERE / name).read_text(encoding="utf-8-sig")
    assert "PROTECTED_PORTFOLIO_HASHES_UNCHANGED True" in log
    assert "KNOWLEDGE_FILE_SET_UNCHANGED True" in log

changed = git("diff", "--name-only", "73ba005", "HEAD", "--", "src", "tests", "start.py", "configs").splitlines()
reviewed = sorted(set(changed) | {
    "src/core/shadow_diff.py", "src/core/decision_policy.py", "src/data/portfolio.py",
    "src/cli/main.py", "start.py", "src/cli/today_service.py",
    "plan/fusion/iteration6/m2_readonly_view_prototype.py",
    "plan/fusion/iteration7/实施方报告_N系列交付.md",
})
assert all((ROOT / name).is_file() for name in reviewed)
protected = [ROOT / "portfolio.yaml", ROOT / "portfolio.yaml.bak"] + sorted(
    p for p in (ROOT / "knowledge/index").iterdir() if p.is_file())

# Extract the fixed list without importing the test runner or product modules.
import ast
tree = ast.parse((HERE / "review_test_runner.py").read_text(encoding="utf-8"))
names = next(ast.literal_eval(node.value) for node in tree.body
             if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NAMES" for t in node.targets))
for node in tree.body:
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == "NAMES":
        names += ast.literal_eval(node.value)
test_files = [f"tests/core/test_{name}.py" for name in names] + [
    "tests/data_sources/test_m1_quote_time.py", "tests/data_sources/test_n2_time_gate.py"]
assert len(test_files) == 30 and all((ROOT / p).is_file() for p in test_files)
(HERE / "ACCEPTANCE_PROBE_RESULTS.json").write_text(json.dumps({
    "review": "R14", "head": git("rev-parse", "HEAD"),
    "r13_replay_passed": 17, "new_contract_failed": 5, "new_compatibility_passed": 1,
    "prototype_assertions_passed": 25, "replay_observations": replay, "incremental_observations": probes,
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
index = {
    "schema_version": 6, "reviewed_at": "2026-10-02", "baseline_commit": git("rev-parse", "HEAD"),
    "prior_review": "../iteration7/REVIEW_INDEX.json", "report": "R14_ACCEPTANCE.md",
    "tasks": "DELIVERY_PLAN.md", "scope": "Incremental N0-N2 review and M2 prototype; not whole-file certification",
    "checks": {
        "targeted_tests": {"files": 30, "passed": 423, "manifest": test_files, "full_suite_rerun": False},
        "r13_contract_replay": {"passed": 17, "api_adaptation": "explicit account_context and prototype store_status"},
        "incremental_probes": {"failed": 5, "passed": 1}, "M2_prototype_assertions_passed": 25,
        "open_findings": ["Y1", "Y2", "Y3", "Y4"], "K1_frozen": False,
        "M2_production_authorized": False, "capture_only": True,
    },
    "changed_since_R13": changed,
    "source_sha256": {name: sha(ROOT / name) for name in reviewed},
    "protected_current_snapshot_sha256": {p.relative_to(ROOT).as_posix(): sha(p) for p in protected},
    "protection_note": "Before/after equality and file-set checks executed by each guarded runner; current hashes for next handoff.",
    "artifacts_sha256": {p.name: sha(p) for p in sorted(HERE.iterdir()) if p.is_file() and p.name != "REVIEW_INDEX.json"},
}
(HERE / "REVIEW_INDEX.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"review": "R14", "head": index["baseline_commit"][:7], "targeted_files": len(test_files),
                  "replay": len(replay), "new_probes": len(probes), "protected": len(protected)}, ensure_ascii=False))
