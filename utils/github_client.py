"""GitHub Issue and pull request operations."""

from pathlib import Path
import uuid
from github import Github
from git import Repo
from config.settings import Settings
from core.exceptions import PatchApplicationError
from utils.git_tools import apply_patch


class GitHubClient:
    """Create branches, commits, Issues, and pull requests for validated repairs."""

    def __init__(self, settings: Settings) -> None:
        """Connect to the configured GitHub repository."""
        self.settings = settings
        self.github = Github(settings.github_token.get_secret_value(), base_url=settings.github_base_url)
        self.repository = self.github.get_repo(settings.repo_name)

    def create_repair_pr(self, state: dict[str, object]) -> tuple[str, str]:
        """Commit a validated patch and open a linked Issue and pull request."""
        base = self.repository.get_branch(self.settings.github_branch)
        branch = f"autoheal/{state['run_id']}-{state['retry_count']}-{uuid.uuid4().hex[:12]}"
        self.repository.create_git_ref(ref=f"refs/heads/{branch}", sha=base.commit.sha)
        with tempfile_directory() as directory:
            repo = Repo.clone_from(self.repository.clone_url, directory, branch=self.settings.github_branch)
            repo.git.checkout("-b", branch)
            patch_path = directory / ".autoheal.patch"
            patch_path.write_text(str(state["patch_diff"]), encoding="utf-8")
            try:
                apply_patch(directory, patch_path.read_text(encoding="utf-8"))
            except PatchApplicationError:
                repo.close()
                raise
            repo.git.add(A=True)
            repo.index.commit(f"fix: self-heal pipeline failure {state['run_id']}")
            repo.remote("origin").push(branch)
            repo.close()
        issue = self.repository.create_issue(title=f"Pipeline failure: {state['job_id']} / {state['task_key']}", body=_issue_body(state))
        pull_request = self.repository.create_pull(title=f"fix: self-heal pipeline failure {state['run_id']}", body=f"Closes #{issue.number}", head=branch, base=self.settings.github_branch)
        return issue.html_url, pull_request.html_url


class tempfile_directory:
    """Small context manager providing a typed temporary Path."""

    def __enter__(self) -> Path:
        import tempfile
        self._manager = tempfile.TemporaryDirectory(prefix="autoheal-pr-")
        return Path(self._manager.__enter__())

    def __exit__(self, *args: object) -> None:
        self._manager.__exit__(*args)


def _issue_body(state: dict[str, object]) -> str:
    """Build the incident Issue body from workflow state."""
    return f"## AutoHeal incident\n\nRun: `{state['run_id']}`\n\nCommit: `{state['commit_sha']}`\n\nRoot cause:\n{state.get('root_cause', '')}\n\nValidation:\n```text\n{state.get('validation_output', '')}\n```\n\nStack trace:\n```text\n{state.get('stack_trace', '')}\n```"
