# Operations Guide

## Local Development

Create and activate the virtual environment, install `requirements.txt`, and load local variables from `.env` before starting the service. Never commit `.env` or real API tokens.

```bash
source .venv/bin/activate
set -a
source .env
set +a
uvicorn main:app --reload
```

Check liveness:

```bash
curl http://localhost:8000/health
```

## Failure Processing

1. Databricks emits a failure event with the task, run, traceback, and Spark/Delta context.
2. The webhook returns `202` and starts the LangGraph execution in the background.
3. The classifier routes infrastructure failures to alerting or deterministic PySpark/Delta failures to RCA.
4. The repair agent generates a unified diff against the configured GitHub base branch.
5. The validator applies the diff in a temporary Git checkout, sets `PYTHONPATH=src`
   for `src`-layout repositories, and runs `pytest -q`. If no pytest tests are
   discovered, it falls back to Python compilation validation.
6. Failed validation returns feedback to the fix agent until the three-attempt limit is reached.
7. A passing repair creates a GitHub Issue and linked PR.
8. Human review and CI/CD checks control deployment.

## Logs

Logs are JSON objects written to standard error. Context fields include `run_id`, `job_id`, `task_key`, `step`, and `status`. Forward them to the local log collector or platform logging system rather than storing them in application files.

## Docker Sandbox

Build the validator image from the repository root:

```bash
docker build -t autoheal-pyspark-validator:local sandbox
```

Only the validation phase runs in Docker. The service first creates a temporary Git checkout of the configured GitHub base branch. It then mounts that checkout read-only at `/workspace` and starts the validator with `--network=none`, `--read-only`, `--cap-drop=ALL`, a PID limit, and a temporary `/tmp`. The container copies the checkout into `/tmp`, applies the generated patch there, sets `PYTHONPATH=src` for `src`-layout repositories, and runs `python -m pytest -q`; repositories without tests use Python compilation validation. The temporary checkout and container are removed afterward.

The image provides Python, Git, pytest, and PySpark. Install project-specific test dependencies in `sandbox/Dockerfile` before testing a real pipeline repository.

If Docker is installed through Snap and cannot access `/tmp` bind mounts, set `AUTOHEAL_WORKSPACE_ROOT` to a project-local ignored directory:

```bash
mkdir -p .autoheal-workspaces
export AUTOHEAL_WORKSPACE_ROOT="$PWD/.autoheal-workspaces"
```

## Troubleshooting

### Service returns `503`

Required settings were not loaded. Check `GEMINI_API_KEY`, `GITHUB_TOKEN`, and `REPO_NAME`, then restart the process.

### Patch validation fails

Inspect the validation output in the graph state and GitHub Issue. Common causes are an incorrect base branch, a patch that does not apply cleanly, missing local PySpark dependencies, or a schema/test contract failure.

### GitHub PR creation fails

Verify the token can read the repository, create unique branches, push commits, create Issues, and open pull requests. Confirm `REPO_NAME` and `GITHUB_BRANCH` are correct. PR creation clones `GITHUB_BRANCH`, not the repository default branch.

### Databricks failure is missing

Check that the task decorator can write to `task_failure_logs` and that the fallback task uses `run_if: AT_LEAST_ONE_FAILED`. For CDF delivery, check consumer checkpoints and event lag.

## Production Checklist

- Use a GitHub App or narrowly scoped token.
- Keep production credentials outside the repair sandbox.
- Configure bounded retry and repair budgets.
- Redact tracebacks and payload samples before GitHub attachment.
- Add repository-specific PySpark, schema, and Delta contract tests.
- Run the complete flow in a staging Databricks environment first.
- Require owner approval for high-impact tables and schema changes.
- Preserve deployment artifacts for rollback.
