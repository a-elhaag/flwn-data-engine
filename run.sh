#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

case "${1:-}" in
  "") uv run uvicorn main:app --host 127.0.0.1 --port 8002 --reload ;;
  test) uv run python -m unittest discover -s tests -v ;;
  memory_steward)
    uv run python -c 'from clients.health import health_check; status = health_check(); print(status); assert all(status.values()), status'
    ;;
  *) echo "Known: test, memory_steward (blank = data API)" >&2; exit 1 ;;
esac