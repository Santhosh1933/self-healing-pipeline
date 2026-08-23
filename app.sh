#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ ! -x ".venv/bin/uvicorn" ]]; then
    printf 'Missing .venv/bin/uvicorn. Install dependencies first with: .venv/bin/pip install -r requirements.txt\n' >&2
    exit 1
fi

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

exec .venv/bin/uvicorn main:app \
    --host "${HOST:-127.0.0.1}" \
    --port "${PORT:-8000}"