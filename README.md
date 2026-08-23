# AutoHeal-DataEngine

AutoHeal-DataEngine is a personal self-healing service for PySpark data pipelines running in Databricks Workflows. It classifies task failures, alerts and retries transient infrastructure failures, and uses an isolated agent workflow to diagnose and propose fixes for deterministic PySpark, Delta Lake, data, and schema defects.

## What Is Implemented

- FastAPI service with `GET /health` and `POST /webhook/pipeline-failure`.
- LangGraph workflow with conditional transient and code-defect paths.
- Gemini `gemini-3.6-flash` for failure classification.
- Gemini `gemini-3.6-flash` for PySpark/Databricks RCA and patch generation by default. Set `AUTOHEAL_REASONING_MODEL` separately if your account provides another supported model.
- YAML/Jinja2 prompt loading from `config/prompts.yaml`.
- Typed workflow state in `agents/state.py`.
- Structured JSON logs with run, job, task, step, and status context.
- Ephemeral Git checkout at the configured GitHub base branch.
- Docker-isolated patch application and PySpark `pytest` validation with a maximum of three attempts.
- GitHub Issue and linked pull request creation after validation.
- Human approval remains required before merge and deployment.

## Repository Layout

```text
config/
  settings.py       Environment-backed Pydantic settings
  prompts.yaml      PySpark/Databricks agent prompts
core/
  exceptions.py     Domain exceptions
  logger.py         Structured JSON logging
utils/
  git_tools.py      Temporary Git workspace and pytest execution
  github_client.py  GitHub branch, commit, Issue, and PR operations
  prompt_loader.py  YAML/Jinja2 prompt rendering
agents/
  state.py          Typed workflow state and LLM response models
  nodes.py          LangGraph node handlers
  graph.py          Graph assembly and conditional routing
api/
  schemas.py        HTTP models
  routes.py         FastAPI endpoints
main.py             Application factory and Uvicorn entrypoint
app.py              Backward-compatible app import
```

## Requirements

- Python 3.12 or newer.
- Git installed and available on `PATH`.
- A GitHub repository containing the PySpark/Databricks pipeline.
- Google Gemini API access.
- Databricks Runtime supplies PySpark for the pipeline itself. The service validates repository tests locally; install PySpark locally if those tests import `pyspark`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set these values in `.env` or export them in the shell:

| Variable                    | Description                                                                                         |
| --------------------------- | --------------------------------------------------------------------------------------------------- |
| `GEMINI_API_KEY`            | Google Gemini API key                                                                               |
| `GITHUB_TOKEN`              | GitHub token or App token with branch, Issue, and PR permissions                                    |
| `REPO_NAME`                 | Repository in `owner/name` form                                                                     |
| `GITHUB_BRANCH`             | Base branch, normally `main`                                                                        |
| `GITHUB_BASE_URL`           | GitHub API URL; defaults to `https://api.github.com`                                                |
| `PORT`                      | HTTP port; defaults to `8000`                                                                       |
| `SANDBOX_IMAGE`             | Docker validator image; defaults to `autoheal-pyspark-validator:local`                              |
| `AUTOHEAL_WORKSPACE_ROOT`   | Temporary checkout parent; use a Docker-accessible path such as `/tmp` or a project-local directory |
| `AUTOHEAL_CLASSIFIER_MODEL` | Classifier model; defaults to `gemini-3.6-flash`                                                    |
| `AUTOHEAL_REASONING_MODEL`  | RCA and patch model; defaults to `gemini-3.6-flash`                                                 |

The service reads environment variables directly. For a local shell, load the file before starting:

```bash
set -a
source .env
set +a
```

## Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Existing commands using `uvicorn app:app` continue to work through the compatibility shim.

## Build and Test the Docker Sandbox

The service runs on the host, while only patch application and repository tests run in Docker. For the MVP, the validator clones the configured GitHub base branch, mounts that checkout read-only into a disposable container, copies it into the container's temporary filesystem, applies the generated diff, and runs `pytest`.

Build the image from the repository root:

```bash
docker build -t autoheal-pyspark-validator:local sandbox
```

The container uses no network, a read-only root filesystem, a temporary `/tmp`, dropped Linux capabilities, and a process limit. The temporary workspace is deleted after validation.

The image includes Python, Git, pytest, and PySpark. Add repository-specific dependencies to `sandbox/Dockerfile` when the Databricks project needs them for tests.

## Webhook Contract

`POST /webhook/pipeline-failure` accepts:

```json
{
  "run_id": "run-123",
  "job_id": "daily-orders",
  "task_key": "silver_transform",
  "error_type": "Py4JJavaError",
  "error_message": "AnalysisException: missing column customer_id",
  "stack_trace": "...",
  "commit_sha": "",
  "repository_url": "https://github.com/Santhosh1933/brazilian-data-etl-pipeline.git"
}
```

The endpoint returns HTTP `202` immediately:

```json
{ "status": "accepted", "run_id": "run-123" }
```

The graph then follows one of these paths:

```text
TRANSIENT -> alert_devops_node -> END
CODE_DEFECT -> RCA -> fix generation -> validation
validation success -> GitHub Issue + PR -> human approval -> CI/CD
validation failure (< 3 attempts) -> fix generation
validation failure (3 attempts) -> human escalation
```

## Databricks Integration

The Databricks pipeline should send failure records containing the workflow run ID, job ID, task key, traceback, deployed commit SHA, and cluster/runtime metadata. Task-level capture should be implemented with `@monitor_task`; an end-of-DAG fallback using `run_if: AT_LEAST_ONE_FAILED` should cover driver and cluster crashes. The failure record can be emitted from the Delta `task_failure_logs` table through Change Data Feed or an equivalent webhook bridge.

For PySpark failures, preserve the original Spark error class and underlying Java/Py4J cause. Include relevant table names, schema versions, stage/task identifiers, and Delta operation details when available.

## Safety Boundaries

- Generated patches are validated in a temporary checkout of the configured GitHub base branch.
- The sandbox should not receive production credentials or write access to production systems.
- Patches must remain limited to approved repository paths.
- Secrets and sensitive data must be redacted before GitHub upload.
- Infrastructure retries and repair attempts are bounded.
- CI checks and human approval are required before production deployment.

## Development Checks

```bash
pytest -q
```

The included tests cover classification routing and the three-attempt validation decision. Gemini, GitHub, Databricks, and Docker integrations require mocks or a configured integration environment for end-to-end testing.

## Related Design

See [AUTOHEAL_DATAENGINE_HLD.md](AUTOHEAL_DATAENGINE_HLD.md) for the detailed architecture, failure flows, controls, and acceptance checks.
