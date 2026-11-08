#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

case "${1:-}" in
  "") uv run uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload ;;
  postgres) docker compose up -d postgres ;;
  install) uv run python -m app.db.install ;;
  migrate) uv run alembic upgrade head ;;
  seed) shift; uv run python -m app.db.seed "$@" ;;
  docs) PYTHONPATH=. uv run python docs/build.py ;;
  postman) PYTHONPATH=. uv run python postman/build.py ;;
  openapi-business) uv run python -c 'import json; from app.openapi.business_spec import business_openapi; from app.main import app; print(json.dumps(business_openapi(app.openapi()), indent=2))' > postman/flwn-business.openapi.json ;;
  lint)uv run ruff check . && uv run ruff format --check . ;;
  test) uv run python -m unittest discover -s tests -t tests -v ;;
  ready)
    uv run python -c 'from app.core.health import health_check; status = health_check(); print(status); assert all(status.values()), status'
    ;;
  *)
    echo "Known: postgres (local container), install (create schema), migrate (alembic upgrade head), seed (add simulated workspaces; --reset rebuilds them), docs (regenerate docs/*.md), postman (regenerate the collection), openapi-business, lint, test, ready (live dependency check); blank = run the API" >&2
    exit 1
    ;;
esac
