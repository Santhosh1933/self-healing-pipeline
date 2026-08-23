"""Apply and validate a generated patch inside the AutoHeal sandbox container."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def run(command: list[str], cwd: Path, timeout: int, *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a bounded command and capture its output."""
    return subprocess.run(command, cwd=cwd, input=stdin, text=True, capture_output=True, timeout=timeout, check=False)


def main() -> int:
    """Apply the patch and execute the repository's PySpark tests."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    source_workspace = args.workspace.resolve()
    workspace = Path("/tmp/autoheal-workspace")
    shutil.copytree(source_workspace, workspace, dirs_exist_ok=True)
    patch = (workspace / args.patch.name).read_text(encoding="utf-8")
    if patch.strip():
        applied = run(["git", "apply", "--whitespace=nowarn", "-"], workspace, 60, stdin=patch)
        if applied.returncode:
            print(f"PATCH_FAILED\n{applied.stderr or applied.stdout}")
            return 2

    tests = run(["python", "-m", "pytest", "-q"], workspace, args.timeout)
    print(f"STDOUT:\n{tests.stdout}\nSTDERR:\n{tests.stderr}")
    return tests.returncode


if __name__ == "__main__":
    raise SystemExit(main())
