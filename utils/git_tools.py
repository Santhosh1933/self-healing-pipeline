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
    with tempfile.TemporaryDirectory(prefix="autoheal-validate-", dir=workspace_root) as directory:
        workspace = Path(directory)
        try:
            Repo.clone_from(repository_url, workspace)
            checkout = subprocess.run(["git", "checkout", "--detach", base_branch], cwd=workspace, text=True, capture_output=True, timeout=60, check=False)
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
    patch_path = workspace / ".autoheal.patch"
    patch_path.write_text(patch_diff, encoding="utf-8")
    command = [
        "docker", "run", "--rm", "--network=none", "--read-only",
        "--tmpfs", "/tmp:rw,exec,nosuid,size=1g", "--cap-drop=ALL",
        "--pids-limit", "256",
        "-v", f"{workspace.resolve()}:/workspace:ro", image,
        "--workspace", "/workspace", "--patch", "/workspace/.autoheal.patch",
        "--timeout", str(timeout_seconds),
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout_seconds + 120, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValidationTimeoutError("Docker validation exceeded its time limit") from exc
    finally:
        patch_path.unlink(missing_ok=True)
    output = f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return output, result.returncode == 0


def run_pytest(workspace: Path, timeout_seconds: int) -> tuple[str, bool]:
    """Run tests through the Docker sandbox."""
    return run_docker_validation(workspace, "", timeout_seconds)
