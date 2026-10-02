"""R15 immutable review scope, evidence manifest and handoff consistency checks."""
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def git(*args):
    return subprocess.check_output(["git", "-c", "core.quotepath=false", *args], cwd=ROOT).decode("utf-8").strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, data):
    (HERE / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


target = (HERE / "TARGETED_TEST_RESULTS.txt").read_text(encoding="utf-8-sig")
raw = (HERE / "ACCEPTANCE_PROBE_RESULTS.txt").read_text(encoding="utf-8-sig")
assert "462 passed" in target and "4 failed, 9 passed" in raw
for log in (target, raw):
    assert "PROTECTED_PORTFOLIO_HASHES_UNCHANGED True" in log
    assert "KNOWLEDGE_FILE_SET_UNCHANGED True" in log
assert "ImportError:" not in raw and "ValidationError:" not in raw
observed = []
for line in raw.splitlines():
    for marker in ("R14_RESULT ", "R15_RESULT "):
        if marker in line:
            try:
                observed.append(json.loads(line.split(marker, 1)[1]))
            except json.JSONDecodeError:
                pass
assert len(observed) == 13, len(observed)
write("ACCEPTANCE_PROBE_RESULTS.json", {
    "review": "R15", "head": git("rev-parse", "HEAD"), "passed": 9, "failed": 4,
    "R14_replay_passed": 6, "incremental_passed": 3, "incremental_failed": 4,
    "finding_groups": ["Z1", "Z2", "Z3"], "observations": observed,
})

tree = ast.parse((HERE / "review_test_runner.py").read_text(encoding="utf-8-sig"))
names = []
for node in tree.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NAMES" for t in node.targets):
        names = ast.literal_eval(node.value)
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == "NAMES":
        names += ast.literal_eval(node.value)
manifest = [f"tests/core/test_{name}.py" for name in names] + [
    "tests/data_sources/test_m1_quote_time.py", "tests/data_sources/test_n2_time_gate.py",
    "tests/data_sources/test_o1_time_gate_matrix.py"]
assert len(manifest) == 33 and all((ROOT / name).is_file() for name in manifest)
assert not git("diff", "--name-only", "--", "src", "tests", "start.py", "configs")
changed = git("diff", "--name-only", "2b98842", "HEAD", "--", "src", "tests", "start.py", "configs").splitlines()
reviewed = sorted(set(changed) | {"src/core/analysis_service.py", "src/core/research_application.py", "src/cli/today_service.py"})
protected = [ROOT / "portfolio.yaml", ROOT / "portfolio.yaml.bak"] + sorted(p for p in (ROOT / "knowledge/index").iterdir() if p.is_file())

current_docs = ["MILESTONES.md", "plan/fusion/STATUS.md", "plan/fusion/RESUME.md",
                "plan/fusion/iteration9/R15_ACCEPTANCE.md", "plan/fusion/iteration9/DELIVERY_PLAN.md"]
missing = []
for name in current_docs:
    path = ROOT / name
    body = path.read_text(encoding="utf-8-sig")
    for link in re.findall(r"\]\(([^)]+)\)", body):
        clean = link.split("#")[0]
        if not clean or re.match(r"^[a-z]+:", clean):
            continue
        if (path.parent / clean).resolve() == HERE / "REVIEW_INDEX.json":
            continue  # Generated below in this same script.
        if not (path.parent / clean).exists():
            missing.append([name, link])
assert not missing, missing

# Scoped neat-freak inventory: preserve historical approvals and user documents.
# Read local markdown to locate current-review references; this is not a claim
# that every archived assertion was independently reviewed again.
updated = {"AGENTS.md", "README.md", "MILESTONES.md", "plan/fusion/README.md",
           "plan/fusion/STATUS.md", "plan/fusion/RESUME.md", ".learnings/LEARNINGS.md"}
files = set(ROOT.glob("*.md")) | set((ROOT / "docs").rglob("*.md"))
files |= set((ROOT / ".learnings").glob("*.md"))
files |= {ROOT / n for n in updated} | set(HERE.glob("*.md"))
inventory = []
for path in sorted(files):
    body = path.read_text(encoding="utf-8-sig")
    name = path.relative_to(ROOT).as_posix()
    inventory.append({"file": name, "lines": len(body.splitlines()),
                      "utf8_bytes": len(body.encode("utf-8")),
                      "current_review_reference": bool(re.search(r"R15|O0|shadow_v10|v0\.8\.27", body)),
                      "decision": "updated_current_handoff" if name in updated or path.parent == HERE
                                  else "preserved_no_R15_contract_change"})
write("DOC_SYNC_AUDIT.json", {"scope": "Incremental R15 handoff; historical approvals preserved, no broad archive rewrite",
                              "local_links_checked": current_docs, "files": inventory})

write("REVIEW_INDEX.json", {
    "schema_version": 7, "reviewed_at": "2026-10-02", "baseline_commit": git("rev-parse", "HEAD"),
    "prior_review": "../iteration8/REVIEW_INDEX.json", "report": "R15_ACCEPTANCE.md", "tasks": "DELIVERY_PLAN.md",
    "scope": "O0/O1/O2 incremental review; O1/O2 accepted, O0 partial, no whole-file certification",
    "checks": {"targeted": {"files": 33, "passed": 462, "manifest": manifest, "full_suite_rerun": False},
               "R14_replay_passed": 6, "incremental": {"passed": 3, "failed": 4},
               "closed": ["Y3", "Y4"], "open": ["Z1", "Z2", "Z3"],
               "K1_frozen": False, "M2_production_authorized": False, "capture_only": True},
    "source_sha256": {n: sha(ROOT / n) for n in reviewed}, "changed_since_R14": changed,
    "protected_current_snapshot_sha256": {p.relative_to(ROOT).as_posix(): sha(p) for p in protected},
    "protection_note": "Runner logs verify before/after equality and file set; these are current handoff hashes",
    "artifacts_sha256": {p.name: sha(p) for p in sorted(HERE.iterdir()) if p.is_file() and p.name != "REVIEW_INDEX.json"},
})
print(json.dumps({"review": "R15", "targeted_files": len(manifest), "observations": len(observed),
                  "protected_files": len(protected), "doc_inventory": len(inventory),
                  "local_links": "OK", "product_worktree": "clean"}, ensure_ascii=False))
