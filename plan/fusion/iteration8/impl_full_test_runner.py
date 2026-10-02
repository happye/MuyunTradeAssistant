"""实施方全量隔离离线测试 runner（O 批交付验证；参照 iteration6/review_test_runner.py 的守卫口径）。

结果写 iteration8/FULL_TEST_RESULTS.txt——不覆写 iteration6/7 的受审证据（R13/R14 引用）。
本 runner 属实施方工具（HOME 重定向+保护哈希）；socket/越界写阻断属架构师 runner 职责。

- HOME/USERPROFILE → 一次性临时目录（conftest 已做，双保险）
- 全量 pytest -q（离线缺省；test_all_api.py 由 conftest 收集期排除）
- 保护文件哈希前后核对：portfolio.yaml / portfolio.yaml.bak / knowledge/index/*
- 知识库文件集合前后一致
- 结果写入 plan/fusion/iteration8/FULL_TEST_RESULTS.txt
"""
import hashlib
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.dont_write_bytecode = True
    platform.uname()
    protected = [REPO / "portfolio.yaml", REPO / "portfolio.yaml.bak"]
    protected += list((REPO / "knowledge" / "index").glob("*"))
    protected = [p for p in protected if p.is_file()]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    index_files_before = {str(p.relative_to(REPO)) for p in (REPO / "knowledge" / "index").glob("*")}

    env = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix="muyun_impl_full_") as temp:
        root = Path(temp).resolve()
        env.update(HOME=str(root), USERPROFILE=str(root))
        env.pop("MUYUN_TESTS_REAL_HOME", None)
        env["MUYUN_TEMP_ROOT"] = str(root)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=str(REPO), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=3600)
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.exists()}
    index_files_after = {str(p.relative_to(REPO)) for p in (REPO / "knowledge" / "index").glob("*")}
    hashes_ok = all(after.get(k) == v for k, v in before.items())
    files_ok = index_files_before == index_files_after

    out = [
        proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "(no output)",
        f"returncode={proc.returncode}",
        f"PROTECTED_PORTFOLIO_AND_KNOWLEDGE_HASHES_UNCHANGED={hashes_ok}",
        f"PROTECTED_FILE_COUNT={len(protected)}",
        f"KNOWLEDGE_INDEX_FILE_SET_UNCHANGED={files_ok}",
    ]
    if proc.returncode != 0 and proc.stdout:
        # 失败摘要（最后 60 行）
        out.append("---- tail ----")
        out.extend(proc.stdout.strip().splitlines()[-60:])
    text = "\n".join(out)
    (REPO / "plan" / "fusion" / "iteration8" / "FULL_TEST_RESULTS.txt").write_text(
        text, encoding="utf-8")
    print(text)
    return 0 if (proc.returncode == 0 and hashes_ok and files_ok) else 2


if __name__ == "__main__":
    raise SystemExit(main())
