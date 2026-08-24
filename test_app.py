import asyncio
import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path

import agents.graph as graph_module
import agents.nodes as nodes_module
import utils.git_tools as git_tools_module
from config.settings import Settings
from agents.graph import route_classification, route_validation


def _load_sandbox_validator():
    module_path = Path(__file__).resolve().parent / "sandbox" / "validate.py"
    spec = importlib.util.spec_from_file_location("sandbox_validate", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_transient_failures_are_alerted():
    assert route_classification({"classification": "TRANSIENT"}) == "alert_devops_node"


def test_code_defects_enter_rca():
    assert route_classification({"classification": "CODE_DEFECT"}) == "rca_discovery_node"


def test_validated_patch_creates_pr():
    assert route_validation({"status": "validated", "retry_count": 1}) == "pr_creator_node"


def test_failed_validation_retries_until_three_attempts():
    assert route_validation({"status": "validation_failed", "retry_count": 1}) == "fix_generator_node"
    assert route_validation({"status": "validation_failed", "retry_count": 3}) == "escalate_human_node"


def test_graph_awaits_async_classifier(monkeypatch):
    async def fake_classifier(state, settings):
        return {**state, "classification": "TRANSIENT", "status": "classified"}

    monkeypatch.setattr(graph_module, "classify_error_node", fake_classifier)
    settings = Settings(gemini_api_key="test", github_token="test", repo_name="owner/repo")
    result = asyncio.run(graph_module.build_graph(settings).ainvoke({"run_id": "graph-test", "status": "received"}))

    assert result["status"] == "devops_alerted"


def test_fix_generator_passes_settings_to_target_contents(monkeypatch):
    settings = Settings(gemini_api_key="test", github_token="test", repo_name="owner/repo")
    observed = {}

    def fake_target_contents(state, received_settings):
        observed["settings"] = received_settings
        return "--- demo.py ---\n   1 | VALUE = 'old'\n"

    class FakeModel:
        async def ainvoke(self, messages):
            return type("Response", (), {"content": "--- a/demo.py\n+++ b/demo.py\n@@ -1 +1 @@\n-VALUE = 'old'\n+VALUE = 'new'"})()

    monkeypatch.setattr(nodes_module, "_target_contents", fake_target_contents)
    monkeypatch.setattr(nodes_module, "_model", lambda name, received_settings: FakeModel())

    result = asyncio.run(nodes_module.fix_generator_node({"target_files": ["demo.py"]}, settings))

    assert observed["settings"] is settings
    assert result["status"] == "patch_generated"


def test_repository_file_resolves_src_layout():
    class Content:
        path = "src/package/module.py"

    class Repository:
        def get_contents(self, path, ref):
            if path == "src/package/module.py":
                return Content()
            raise nodes_module.GithubException(404, "Not Found")

    result = nodes_module._repository_file(Repository(), "package/module.py", "main")

    assert result.path == "src/package/module.py"


def test_validate_script_reads_patch_from_stdin(monkeypatch, tmp_path):
    validate = _load_sandbox_validator()
    source_workspace = tmp_path / "source-repo"
    source_workspace.mkdir()
    (source_workspace / "demo.py").write_text("print('ok')\n", encoding="utf-8")
    patch_text = "diff --git a/demo.py b demo.py\n--- a/demo.py\n+++ b demo.py\n@@\n-print('ok')\n+print('done')\n"

    captured = {"calls": []}

    def fake_run(command, cwd, timeout, stdin=None, env=None):
        captured["calls"].append({"command": command, "cwd": cwd, "stdin": stdin})

        class Result:
            returncode = 0
            stdout = "OK\n"
            stderr = ""

        return Result()

    monkeypatch.setattr(validate, "run", fake_run)
    monkeypatch.setattr(validate.sys, "stdin", type("Stdin", (), {"read": lambda self: patch_text})())
    monkeypatch.setattr(sys, "argv", ["validate.py", "--workspace", str(source_workspace), "--patch", "-", "--timeout", "30"])

    exit_code = validate.main()

    assert exit_code == 0
    assert any(call["command"][0:3] == ["git", "apply", "--recount"] for call in captured["calls"])
    assert any(call["stdin"] == patch_text for call in captured["calls"])


def test_validate_script_handles_missing_patch_file(monkeypatch, tmp_path):
    validate = _load_sandbox_validator()
    source_workspace = tmp_path / "source-repo"
    source_workspace.mkdir()
    (source_workspace / "demo.py").write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["validate.py", "--workspace", str(source_workspace), "--patch", str(tmp_path / ".autoheal.patch"), "--timeout", "30"])

    exit_code = validate.main()

    assert exit_code == 2


def test_validate_script_falls_back_to_compileall_without_tests(monkeypatch, tmp_path):
    validate = _load_sandbox_validator()
    source_workspace = tmp_path / "source-repo"
    (source_workspace / "src").mkdir(parents=True)
    (source_workspace / "src" / "demo.py").write_text("print('ok')\n", encoding="utf-8")
    calls = []

    def fake_run(command, cwd, timeout, stdin=None, env=None):
        calls.append(command)
        return type("Result", (), {"returncode": 5 if command[2:4] == ["pytest", "-q"] else 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(validate, "run", fake_run)
    monkeypatch.setattr(validate.sys, "stdin", type("Stdin", (), {"read": lambda self: ""})())
    monkeypatch.setattr(sys, "argv", ["validate.py", "--workspace", str(source_workspace), "--patch", "-", "--timeout", "30"])

    exit_code = validate.main()

    assert exit_code == 0
    assert any(command[2:4] == ["compileall", "-q"] for command in calls)


def test_run_docker_validation_mounts_current_validator(monkeypatch, tmp_path):
    captured = {"calls": []}

    def fake_run(command, input=None, text=None, capture_output=None, timeout=None, check=None, cwd=None):
        captured["calls"].append({"command": command, "input": input})
        return type("Result", (), {"returncode": 0, "stdout": "OK\n", "stderr": ""})()

    monkeypatch.setattr(git_tools_module.subprocess, "run", fake_run)
    workspace = tmp_path / "repo"
    workspace.mkdir()

    result = git_tools_module.run_docker_validation(workspace, "patch-data", 60)

    assert result[1] is True
    assert "STDOUT:\nOK\n\nSTDERR:\n" == result[0]
    docker_call = captured["calls"][-1]
    assert docker_call["command"][-4:] == ["--patch", "-", "--timeout", "60"]
    assert docker_call["input"] == "patch-data"
    assert any("sandbox/validate.py:/opt/autoheal/validate.py:ro" in item for item in docker_call["command"])


def test_run_docker_validation_rejects_corrupt_patch(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "corrupt patch"})()

    monkeypatch.setattr(git_tools_module.subprocess, "run", fake_run)
    workspace = tmp_path / "repo"
    workspace.mkdir()

    output, passed = git_tools_module.run_docker_validation(workspace, "bad patch", 60)

    assert passed is False
    assert output == "PATCH_INVALID\ncorrupt patch"
    assert len(calls) == 1


def test_validator_feedback_includes_rejected_patch(monkeypatch):
    @contextmanager
    def fake_workspace(repository_url, base_branch):
        yield None

    def fake_validation(workspace, patch_diff, timeout_seconds):
        return "PATCH_INVALID\ncorrupt patch", False

    monkeypatch.setattr(nodes_module, "validation_workspace", fake_workspace)
    monkeypatch.setattr(nodes_module, "run_docker_validation", fake_validation)
    settings = Settings(gemini_api_key="test", github_token="test", repo_name="owner/repo")
    state = {"repository_url": "https://github.com/owner/repo.git", "patch_diff": "bad patch"}

    result = asyncio.run(nodes_module.validator_node(state, settings))

    assert "GENERATED_PATCH:\nbad patch" in result["validation_output"]
