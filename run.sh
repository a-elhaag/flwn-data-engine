#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

case "${1:-}" in
  "") uv run uvicorn main:app --host 127.0.0.1 --port 8002 --reload ;;
  qdrant) docker compose up -d qdrant ;;
  postgres) docker compose up -d postgres ;;
  migrate) uv run alembic upgrade head ;;
  test) uv run pytest ;;
  memory_steward)
    uv run python -c 'from clients.health import health_check; status = health_check(); print(status); assert all(status.values()), status'
    ;;
  *) echo "Known: qdrant (light local container), postgres (local container), migrate (alembic upgrade head), test, memory_steward (blank = data API)" >&2; exit 1 ;;
esac