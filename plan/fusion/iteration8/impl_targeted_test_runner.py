"""实施方目标回归 runner（O 批交付；架构师 review_test_runner.py 同守卫口径）。

清单 = R14 架构师 30 文件（423 passed 口径）+ O 批新 3 文件（test_o0/
test_o1/test_o2）。结果写 iteration8/TARGETED_TEST_RESULTS_O.txt——
不覆盖架构师的 TARGETED_TEST_RESULTS.txt（R14 受审证据）。

守卫与架构师 runner 相同：HOME/USERPROFILE 重定向临时根、审计钩子阻断
socket/subprocess/真实账户读写/越界写、faiss 原生写守卫、保护文件哈希
（portfolio.yaml/.bak + knowledge/index/*）前后核对。结果文件由本 runner
在审计钩子解除后写入（pytest.main 返回后）。
"""
import hashlib
import io
import os
from pathlib import Path
import platform
import sys
import tempfile

REPO = Path(__file__).resolve().parents[3]
# R14 架构师 30 文件（423 passed 口径）
NAMES = ["j0_facts_assessment", "j1_mapping_boundary", "j2_account_service",
         "j3_research_application", "j4_mode_observation", "claim_extraction",
         "claim_verification_corpus", "research_snapshot", "research_service", "account_snapshot",
         "k0a_account_integrity", "k0a_projection_intent", "k0a_vertical_e2e",
         "k0b_facts_propositions", "k0c_plan_observation", "k1_shadow_v6", "k2a_research_loop"]
NAMES += ["l0_shadow_contract", "l1_account_reading", "l2_public_checkpoint", "l3_evidence_pack", "m0_account_terminal", "analysis_service", "horizon_policy", "batch_quotes", "iss089_realtime_quote", "n0_ctx_source_of_truth", "n1_canonical_arms"]
# O 批新增 3 文件
NAMES += ["o0_exceptional_account"]
EXTRA = [str(REPO / "tests/data_sources/test_m1_quote_time.py"),
         str(REPO / "tests/data_sources/test_n2_time_gate.py"),
         str(REPO / "tests/data_sources/test_o1_time_gate_matrix.py"),
         str(REPO / "tests/core/test_o2_long_unknown_weight.py")]

OUT = REPO / "plan" / "fusion" / "iteration8" / "TARGETED_TEST_RESULTS_O.txt"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(REPO))
    platform.uname()
    real_state = (Path.home() / ".muyun").resolve()
    protected = [REPO / "portfolio.yaml", REPO / "portfolio.yaml.bak"]
    protected += list((REPO / "knowledge" / "index").glob("*"))
    protected = [p for p in protected if p.is_file()]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.exists()}
    target_files = [str(REPO / "tests" / "core" / f"test_{name}.py") for name in NAMES] + EXTRA
    code = 0
    lines: list[str] = []
    with tempfile.TemporaryDirectory(prefix="muyun_impl_o_tests_") as temp:
        root = Path(temp).resolve()
        os.environ.update(HOME=str(root), USERPROFILE=str(root))
        os.environ.pop("MUYUN_TESTS_REAL_HOME", None)
        tempfile.tempdir = str(root)

        def writable(path):
            if not Path(os.fsdecode(path)).resolve().is_relative_to(root):
                raise RuntimeError("Test write outside temporary root blocked")

        def audit(event, args):
            if event in ("socket.connect", "socket.getaddrinfo", "subprocess.Popen"):
                raise RuntimeError("Test external IO blocked")
            if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(args[0])).resolve()
                if path.is_relative_to(real_state):
                    raise RuntimeError("Real account access blocked")
                mode, flags = args[1:3]
                if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                    isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
                ):
                    writable(path)
            elif event in ("os.mkdir", "os.remove", "os.rmdir"):
                if event != "os.mkdir" or not Path(args[0]).is_dir():
                    writable(args[0])
            elif event in ("os.rename", "os.replace"):
                for path in args[:2]:
                    writable(path)

        sys.addaudithook(audit)
        import pytest
        import src.cli.main as cli
        cli._cli_rag = lambda: None
        try:
            import faiss
            original_write_index = faiss.write_index

            def guarded_write_index(index, destination, *args):
                if isinstance(destination, (str, bytes, os.PathLike)):
                    writable(destination)
                else:
                    raise RuntimeError("Cannot qualify native index destination")
                return original_write_index(index, destination, *args)
            faiss.write_index = guarded_write_index
        except ImportError:
            pass

        class ArtifactIsolation:
            def pytest_collection_modifyitems(self, items):
                for item in items:
                    if item.module.__name__.endswith("test_claim_verification_corpus"):
                        item.module.ARTIFACT = root / "claim_verification_report.json"

        buf = io.StringIO()
        code = pytest.main(["-q", "-p", "no:cacheprovider", "--basetemp", str(root / "pytest"),
                            "--log-file", str(root / "pytest.log")] + target_files,
                           plugins=[ArtifactIsolation()])
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.exists()}
        hashes_ok = before == after
        files_ok = (set(p for p in (REPO / "knowledge" / "index").glob("*") if p.is_file())
                    == set(p for p in protected if p.parent == REPO / "knowledge" / "index"))
        lines.append(f"pytest returncode={int(code)}")
        lines.append(f"PROTECTED_PORTFOLIO_AND_KNOWLEDGE_HASHES_UNCHANGED={hashes_ok}")
        lines.append(f"PROTECTED_FILE_COUNT={len(protected)}")
        lines.append(f"KNOWLEDGE_FILE_SET_UNCHANGED={files_ok}")
        lines.append(f"COLLECTED_FILES={len(target_files)}")
        lines.append("COLLECTION:")
        lines.extend("  " + t.replace(str(REPO), "") for t in target_files)
        if hashes_ok and files_ok and int(code) == 0:
            result = 0
        elif not (hashes_ok and files_ok):
            result = 2
        else:
            result = int(code)
    # 结果经 stdout 输出、由调用方 shell 重定向写 TARGETED_TEST_RESULTS_O.txt
    # （审计钩子无法移除——runner 内部写仓库文件会被自己的 writable 拦截）
    print("\n".join(lines))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
