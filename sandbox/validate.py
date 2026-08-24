"""Apply and validate a generated patch inside the AutoHeal sandbox container."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(command: list[str], cwd: Path, timeout: int, *, stdin: str | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a bounded command and capture its output."""
    return subprocess.run(command, cwd=cwd, env=env, input=stdin, text=True, capture_output=True, timeout=timeout, check=False)


def main() -> int:
    """Apply the patch and execute the repository's PySpark tests."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--patch", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    source_workspace = args.workspace.resolve()
    workspace = Path("/tmp/autoheal-workspace")
    shutil.copytree(source_workspace, workspace, dirs_exist_ok=True)

    if args.patch == "-":
        patch = sys.stdin.read()
    else:
        patch_source = Path(args.patch)
        if patch_source.exists():
            patch = patch_source.read_text(encoding="utf-8")
        else:
            print(f"PATCH_MISSING\nExpected patch at {patch_source}")
            return 2

    if patch.strip():
        applied = run(["git", "apply", "--recount", "--whitespace=nowarn", "-"], workspace, 60, stdin=patch)
        if applied.returncode:
            print(f"PATCH_FAILED\n{applied.stderr or applied.stdout}")
            return 2

    test_environment = {**os.environ, "PYTHONPATH": str(workspace / "src")}
    tests = run(["python", "-m", "pytest", "-q"], workspace, args.timeout, env=test_environment)
    if tests.returncode == 5:
        compile_check = run(["python", "-m", "compileall", "-q", "src"], workspace, args.timeout, env=test_environment)
        print("PYTEST_NO_TESTS\nFalling back to Python compilation validation.")
        print(f"STDOUT:\n{compile_check.stdout}\nSTDERR:\n{compile_check.stderr}")
        return compile_check.returncode
    print(f"STDOUT:\n{tests.stdout}\nSTDERR:\n{tests.stderr}")
    return tests.returncode


if __name__ == "__main__":
    raise SystemExit(main())
