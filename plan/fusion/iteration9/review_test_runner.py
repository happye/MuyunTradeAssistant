"""Run the review's selected existing tests with guarded IO and temporary artifacts."""
import hashlib
import os
from pathlib import Path
import platform
import sys
import tempfile

REPO = Path(__file__).resolve().parents[3]
NAMES = ["j0_facts_assessment", "j1_mapping_boundary", "j2_account_service",
         "j3_research_application", "j4_mode_observation", "claim_extraction",
         "claim_verification_corpus", "research_snapshot", "research_service", "account_snapshot",
         "k0a_account_integrity", "k0a_projection_intent", "k0a_vertical_e2e",
         "k0b_facts_propositions", "k0c_plan_observation", "k1_shadow_v6", "k2a_research_loop"]
NAMES += ["l0_shadow_contract", "l1_account_reading", "l2_public_checkpoint", "l3_evidence_pack", "m0_account_terminal", "analysis_service", "horizon_policy", "batch_quotes", "iss089_realtime_quote", "n0_ctx_source_of_truth", "n1_canonical_arms", "o0_exceptional_account", "o2_long_unknown_weight"]


def _resolved_path(path):
    resolved = Path(os.fsdecode(path)).resolve()
    # Windows realpath may retain its extended prefix if a missing directory is
    # created between GetFinalPathName calls. Normalize only resolved drive/UNC
    # aliases; following symlinks/junctions must happen before this comparison.
    value = str(resolved)
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            return Path("\\\\" + value[8:])
        if (value.startswith("\\\\?\\") and len(value) >= 7
                and value[4].isalpha() and value[5:7] == ":\\"):
            return Path(value[4:])
    return resolved


def _require_writable(path, root):
    if not _resolved_path(path).is_relative_to(root):
        raise RuntimeError("Review test write outside temporary root blocked")


def _allow_index_fixture_read(path, mode, flags, *, frame=None):
    """Qualify the two repository fixtures' read-only protected-index access."""
    try:
        if mode is not None and mode not in ("r", "rb", "rt"):
            return False
        write_flags = (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC
                       | os.O_APPEND | os.O_EXCL | getattr(os, "O_TEMPORARY", 0)
                       | getattr(os, "O_TMPFILE", 0))
        if not isinstance(flags, int) or flags & write_flags:
            return False
        index = _resolved_path(REPO / "knowledge" / "index")
        if not _resolved_path(path).is_relative_to(index):
            return False
        conftest = _resolved_path(REPO / "tests" / "conftest.py")
        current = sys._getframe(1) if frame is None else frame
        while current is not None:
            code = current.f_code
            if _resolved_path(code.co_filename) == conftest and (
                code.co_name == "_index_digest"
                or (code.co_name == "_init"
                    and code.co_qualname == "_isolate_rag_index_dir.<locals>._init")
            ):
                return True
            current = current.f_back
        return False
    except Exception:
        return False


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(REPO))
    # Python's Windows platform detection can invoke cmd /c ver. Warm this read-only
    # stdlib cache before blocking child processes; product code remains guarded.
    platform.uname()
    real_state = _resolved_path(Path.home() / ".muyun")
    protected = [REPO / "portfolio.yaml", REPO / "portfolio.yaml.bak"]
    protected += list((REPO / "knowledge" / "index").glob("*"))
    protected = [p for p in protected if p.is_file()]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.exists()}
    protected_paths = {_resolved_path(p) for p in protected}
    checking_protection = False
    blocked_io = {"network": 0, "subprocess": 0, "real_state": 0}
    with tempfile.TemporaryDirectory(prefix="muyun_review_tests_") as temp:
        root = _resolved_path(temp)
        os.environ.update(HOME=str(root), USERPROFILE=str(root))
        os.environ.pop("MUYUN_TESTS_REAL_HOME", None)
        tempfile.tempdir = str(root)

        def writable(path):
            _require_writable(path, root)

        def audit(event, args):
            if event == "socket.connect":
                # Windows stdlib socketpair uses an ephemeral loopback connection for
                # asyncio's own wakeup pipe. Allow only that exact internal caller.
                caller = sys._getframe(1)
                address = args[1]
                internal_pair = (caller.f_code.co_name in ("socketpair", "_fallback_socketpair")
                                 and Path(caller.f_code.co_filename).name == "socket.py"
                                 and isinstance(address, tuple) and address[0] == "127.0.0.1")
                if not internal_pair:
                    blocked_io["network"] += 1
                    raise RuntimeError("Review test external IO blocked")
            elif event in ("socket.getaddrinfo", "subprocess.Popen"):
                blocked_io["subprocess" if event == "subprocess.Popen" else "network"] += 1
                raise RuntimeError("Review test external IO blocked")
            if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
                path = _resolved_path(args[0])
                mode, flags = args[1:3]
                if path.is_relative_to(real_state) or (
                    path in protected_paths and not checking_protection
                    and not _allow_index_fixture_read(path, mode, flags)
                ):
                    blocked_io["real_state"] += 1
                    raise RuntimeError("Review test real account access blocked")
                if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                    isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
                ):
                    writable(path)
            elif event in ("os.mkdir", "os.remove", "os.rmdir"):
                # mkdir(exist_ok=True) is audited even for existing directories.
                if event != "os.mkdir" or not Path(args[0]).is_dir():
                    writable(args[0])
            elif event in ("os.rename", "os.replace"):
                for path in args[:2]:
                    writable(path)
            elif event == "os.link":
                writable(args[1])

        sys.addaudithook(audit)
        import pytest
        # Optional method-retrieval infrastructure is outside the research/accept/shadow
        # assertion chain. Prevent local index initialization in this offline review.
        import src.cli.main as cli
        cli._cli_rag = lambda: None
        # CPython audit hooks cannot intercept native faiss file IO. Guard that boundary
        # explicitly as well; the initial review exposed an out-of-root temporary index.
        try:
            import faiss
            original_write_index = faiss.write_index
            def guarded_write_index(index, destination, *args):
                if isinstance(destination, (str, bytes, os.PathLike)):
                    writable(destination)
                else:
                    raise RuntimeError("Review cannot qualify native index destination")
                return original_write_index(index, destination, *args)
            faiss.write_index = guarded_write_index
        except ImportError:
            pass

        class ArtifactIsolation:
            def pytest_collection_modifyitems(self, items):
                for item in items:
                    if item.module.__name__.endswith("test_claim_verification_corpus"):
                        item.module.ARTIFACT = root / "claim_verification_report.json"

        code = pytest.main(["-q", "-p", "no:cacheprovider", "--basetemp", str(root / "pytest"),
                            "--log-file", str(root / "pytest.log")]
                           + (sys.argv[1:] or ([str(REPO / "tests" / "core" / f"test_{name}.py") for name in NAMES] + [str(REPO / "tests/data_sources/test_m1_quote_time.py"), str(REPO / "tests/data_sources/test_n2_time_gate.py"), str(REPO / "tests/data_sources/test_o1_time_gate_matrix.py")])),
                           plugins=[ArtifactIsolation()])
        checking_protection = True
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.exists()}
        checking_protection = False
        print("BLOCKED_IO_ATTEMPTS", blocked_io)
        print("PROTECTED_PORTFOLIO_HASHES_UNCHANGED", before == after)
        print("PROTECTED_KNOWLEDGE_AND_PORTFOLIO_FILES", len(protected))
        print("KNOWLEDGE_FILE_SET_UNCHANGED", set(p for p in (REPO / "knowledge" / "index").glob("*") if p.is_file()) == set(p for p in protected if p.parent == REPO / "knowledge" / "index"))
        assert set(p for p in (REPO / "knowledge" / "index").glob("*") if p.is_file()) == set(
            p for p in protected if p.parent == REPO / "knowledge" / "index")
        return code if before == after else 2


if __name__ == "__main__":
    raise SystemExit(main())
