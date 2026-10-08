#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

case "${1:-}" in
  "") uv run uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload ;;
  postgres) docker compose up -d postgres ;;
  install) uv run python -m app.db.install ;;
  erd) uv run python -m app.db.erd ;;
  lint) uv run ruff check . && uv run ruff format --check . ;;
  test) uv run python -m unittest discover -s tests -v ;;
  ready)
    uv run python -c 'from app.clients.health import health_check; status = health_check(); print(status); assert all(status.values()), status'
    ;;
  *)
    echo "Known: postgres (local container), install (create schema), erd (schema graph), lint, test, ready (live dependency check); blank = run the API" >&2
    exit 1
    ;;
esac
