"""Isolated Git workspaces and deterministic validation helpers."""

from pathlib import Path
import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from git import Repo
from core.exceptions import PatchApplicationError, ValidationTimeoutError

SANDBOX_IMAGE = "autoheal-pyspark-validator:local"


@contextmanager
def validation_workspace(repository_url: str, base_branch: str) -> Iterator[Path]:
    """Clone ``repository_url`` at the configured base branch into a temporary workspace."""
    workspace_root = os.getenv("AUTOHEAL_WORKSPACE_ROOT")
    if workspace_root is None:
        workspace_root = str(Path(__file__).resolve().parents[1] / ".autoheal-workspaces")
    Path(workspace_root).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="autoheal-validate-", dir=workspace_root) as directory:
        workspace = Path(directory)
        workspace.chmod(0o755)
        try:
            Repo.clone_from(repository_url, workspace)
            fetch = subprocess.run(
                ["git", "fetch", "--depth", "1", "origin", f"refs/heads/{base_branch}"],
                cwd=workspace,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            if fetch.returncode:
                raise PatchApplicationError(fetch.stderr or fetch.stdout)
            checkout = subprocess.run(
                ["git", "checkout", "--detach", "FETCH_HEAD"],
                cwd=workspace,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PatchApplicationError(f"Unable to create Git workspace: {exc}") from exc
        if checkout.returncode:
            raise PatchApplicationError(
                f"Base branch '{base_branch}' was not found in repository '{repository_url}'. "
                "Check REPO_NAME and GITHUB_BRANCH.\n"
                f"{checkout.stderr or checkout.stdout}"
            )
        yield workspace


def apply_patch(workspace: Path, patch_diff: str) -> None:
    """Apply a unified diff to ``workspace``."""
    try:
        result = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=workspace, input=patch_diff, text=True, capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        raise PatchApplicationError("Patch application timed out") from exc
    if result.returncode:
        raise PatchApplicationError(result.stderr or result.stdout)


def run_docker_validation(workspace: Path, patch_diff: str, timeout_seconds: int, image: str = SANDBOX_IMAGE) -> tuple[str, bool]:
    """Apply a patch and run PySpark tests inside an isolated Docker container."""
    if patch_diff.strip():
        patch_check = subprocess.run(
            ["git", "apply", "--recount", "--check", "--whitespace=nowarn", "-"],
            cwd=workspace,
            input=patch_diff,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if patch_check.returncode:
            output = f"PATCH_INVALID\n{patch_check.stderr or patch_check.stdout}"
            return output, False

    validator_path = Path(__file__).resolve().parents[1] / "sandbox" / "validate.py"
    command = [
        "docker", "run", "-i", "--rm", "--network=none", "--read-only",
        "--tmpfs", "/tmp:rw,exec,nosuid,size=1g", "--cap-drop=ALL",
        "--pids-limit", "256",
        "-v", f"{workspace.resolve()}:/workspace:ro",
        "-v", f"{validator_path}:/opt/autoheal/validate.py:ro",
        image,
        "--workspace", "/workspace", "--patch", "-",
        "--timeout", str(timeout_seconds),
    ]
    try:
        result = subprocess.run(command, input=patch_diff, text=True, capture_output=True, timeout=timeout_seconds + 120, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValidationTimeoutError("Docker validation exceeded its time limit") from exc
    output = f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return output, result.returncode == 0


def run_pytest(workspace: Path, timeout_seconds: int) -> tuple[str, bool]:
    """Run tests through the Docker sandbox."""
    return run_docker_validation(workspace, "", timeout_seconds)
