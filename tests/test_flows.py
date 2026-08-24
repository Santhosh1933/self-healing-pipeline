import asyncio
import json
from types import SimpleNamespace
from pathlib import Path

from fastapi.testclient import TestClient

import agents.nodes as nodes_module
import utils.git_tools as git_tools_module
from utils.github_client import GitHubClient
from agents.graph import route_classification, route_validation
from api.routes import create_router
from config.settings import Settings
from main import create_app


PAYLOAD = json.loads((Path(__file__).parent / "fixtures" / "databricks_failure.json").read_text(encoding="utf-8"))


def test_health_flow():
    settings = Settings(gemini_api_key="test", github_token="test", repo_name="owner/repo")
    client = TestClient(create_app(settings))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_flow_accepts_valid_failure(monkeypatch):
    observed = {}

    settings = Settings(gemini_api_key="test", github_token="test", repo_name="owner/repo")
    app = create_app(settings)
    monkeypatch.setattr("api.routes.BackgroundTasks.add_task", lambda tasks, function, state: observed.update(state=state))
    client = TestClient(app)
    response = client.post("/webhook/pipeline-failure", json=PAYLOAD)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "run_id": PAYLOAD["run_id"]}
    assert observed["state"]["task_key"] == PAYLOAD["task_key"]
    assert observed["state"]["repository_url"] == PAYLOAD["repository_url"]


def test_databricks_payload_classifies_as_code_defect():
    assert route_classification({"error_message": PAYLOAD["error_message"], "classification": "CODE_DEFECT"}) == "rca_discovery_node"


def test_transient_flow_ends_in_devops_alert():
    assert route_classification({"classification": "TRANSIENT"}) == "alert_devops_node"
    result = asyncio.run(nodes_module.alert_devops_node({"error_message": "timeout"}))

    assert result["status"] == "devops_alerted"


def test_code_defect_flow_enters_repair():
    assert route_classification({"classification": "CODE_DEFECT"}) == "rca_discovery_node"
    assert route_validation({"status": "validation_failed", "retry_count": 1}) == "fix_generator_node"
    assert route_validation({"status": "validated", "retry_count": 1}) == "pr_creator_node"


def test_retry_flow_escalates_after_max_attempts():
    assert route_validation({"status": "validation_failed", "retry_count": 3}) == "escalate_human_node"
    result = asyncio.run(nodes_module.escalate_human_node({"validation_output": "PATCH_INVALID"}))

    assert result["status"] == "human_escalation"


def test_patch_extraction_flow_accepts_fenced_diff():
    response = SimpleNamespace(content="```diff\n--- a/demo.py\n+++ b/demo.py\n@@ -1 +1 @@\n-old\n+new\n```")

    assert nodes_module._diff(response).startswith("--- a/demo.py")


def test_patch_extraction_flow_repairs_hunk_counts():
    response = SimpleNamespace(content="--- a/demo.py\n+++ b/demo.py\n@@ -70,8 +70,8 @@\n context\n-old\n+new\n")

    assert "@@ -70,2 +70,2 @@" in nodes_module._diff(response)
    assert nodes_module._diff(response).endswith("\n")
    assert "--- a/demo.py" in nodes_module._diff(response)
    assert "+++ b/demo.py" in nodes_module._diff(response)


def test_src_layout_resolution_flow():
    class Content:
        path = "src/package/module.py"

    class Repository:
        def get_contents(self, path, ref):
            if path == "src/package/module.py":
                return Content()
            raise nodes_module.GithubException(404, "Not Found")

    result = nodes_module._repository_file(Repository(), "package/module.py", "main")

    assert result.path == "src/package/module.py"


def test_validation_flow_rejects_invalid_patch(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="corrupt patch")

    monkeypatch.setattr(git_tools_module.subprocess, "run", fake_run)
    workspace = tmp_path / "repo"
    workspace.mkdir()

    output, passed = nodes_module.run_docker_validation(workspace, "bad patch", 60)

    assert passed is False
    assert output == "PATCH_INVALID\ncorrupt patch"
    assert len(calls) == 1


def test_release_always_creates_unique_branch(monkeypatch):
    created_refs = []

    class Repository:
        clone_url = "https://github.com/owner/repo.git"

        def get_branch(self, branch):
            return SimpleNamespace(commit=SimpleNamespace(sha="base-sha"))

        def create_git_ref(self, ref, sha):
            created_refs.append(ref)

    client = object.__new__(GitHubClient)
    client.settings = Settings(gemini_api_key="test", github_token="test", repo_name="owner/repo")
    client.repository = Repository()
    monkeypatch.setattr("utils.github_client.uuid.uuid4", lambda: SimpleNamespace(hex="0123456789abcdef"))
    monkeypatch.setattr("utils.github_client.tempfile_directory", lambda: (_ for _ in ()).throw(AssertionError("release should stop before clone")))

    try:
        client.create_repair_pr({"run_id": "run", "retry_count": 1})
    except AssertionError:
        pass

    assert created_refs == ["refs/heads/autoheal/run-1-0123456789ab"]


def test_release_clones_configured_base_branch(monkeypatch):
    cloned = {}

    class Repository:
        clone_url = "https://github.com/owner/repo.git"

        def get_branch(self, branch):
            return SimpleNamespace(commit=SimpleNamespace(sha="base-sha"))

        def create_git_ref(self, ref, sha):
            pass

    def fake_clone(url, directory, branch):
        cloned["branch"] = branch
        raise AssertionError("stop after clone arguments are captured")

    client = object.__new__(GitHubClient)
    client.settings = Settings(gemini_api_key="test", github_token="test", repo_name="owner/repo", github_branch="rewiring")
    client.repository = Repository()
    monkeypatch.setattr("utils.github_client.uuid.uuid4", lambda: SimpleNamespace(hex="0123456789abcdef"))
    monkeypatch.setattr("utils.github_client.Repo.clone_from", fake_clone)

    try:
        client.create_repair_pr({"run_id": "run", "retry_count": 1})
    except AssertionError:
        pass

    assert cloned["branch"] == "rewiring"
