# Flwn Data Engine

FastAPI data layer. Owns Memory Steward, Qdrant access, memory compression,
embeddings, recall ranking, and cleanup. AI engine owns orchestration and Decision
Ledger; backend remains NestJS. Neither needs direct Qdrant credentials.

## Organization

- `main.py`: application startup and router registration.
- `config.py`: environment settings.
- `api/routes.py`: HTTP contracts, validation, and health endpoints.
- `api/auth.py`, `api/tokens.py`: service key and workspace-locked agent tokens.
- `mcp_server.py`: MCP tools for agents, mounted at `/mcp`.
- `memory/vectorizer.py`: compress + embed seam (reused by the future outbox worker).
- `memory/prompts.py`: hardened prompts and strict JSON parsing.
- `memory/steward.py`: extraction of facts/decisions from supplied workspace
  information, memory persistence, and cleanup. Source text is not limited to chats
  or transcripts; callers must supply it.
- `retrieval/search.py`: query rewriting, vector search, recency ranking, and recall
  tracking.
- `clients/`: Qdrant storage, shared chat/embedding inference, retries/rate limiting,
  and dependency readiness checks.
- `tests/`: API and AI-adapter behavior checks.

Current flow: supplied information -> Memory Steward -> embedding -> Qdrant;
query -> retrieval -> ranked memories. The existing MemorySteward recall method
remains a facade for retrieval, preserving the API contract.

The target architecture adds PostgreSQL as the source of truth, a transactional
outbox/NOTIFY auto-vectorizer, and hybrid retrieval with reranking. These are not
implemented here. There is no Redis queue or automatic ingestion of all workspace
information, and no placeholder modules for these future features.

## Run

Set environment variables using `.env.example`, then:

```sh
uv venv
uv pip install -r requirements.txt
bash run.sh
```

API: `http://localhost:8002`; OpenAPI: `/openapi.json`; interactive docs: `/docs`.
Qdrant must already be running at `QDRANT_URL`; service startup ensures the existing
`memory` collection and workspace payload index. AI engine never starts Qdrant.

Qdrant environments:

- **Cloud/prod:** own managed Qdrant deployment. Set `QDRANT_URL` and `QDRANT_API_KEY`.
- **Local dev:** `bash run.sh qdrant` starts a light container (`docker-compose.yml`)
  at `http://localhost:6333`, the default `QDRANT_URL`. No API key needed.
- **Tests:** in-memory Qdrant, no container.
- Memory still goes straight to Qdrant; Postgres holds the core domain schema.

## Postgres

Core domain schema (11 tables) lives in Postgres 15+ (SQLAlchemy models in `db/`,
Pydantic schemas in `schemas/`, Alembic migrations in `migrations/`).

```sh
bash run.sh postgres   # local container, db/user/password: flwn
bash run.sh migrate    # alembic upgrade head
```

`DATABASE_URL` format: `postgresql+psycopg://user:pass@host:5432/db`.

```sh
pip install -r requirements-dev.txt   # or: uv pip install -r requirements-dev.txt
pytest tests/unit          # no database needed
pytest tests/integration   # needs Postgres (bash run.sh postgres)
pytest                     # everything; `bash run.sh test` runs this
bash run.sh memory_steward
```

Integration tests use a separate database (`TEST_DATABASE_URL`, default
`postgresql+psycopg://flwn:flwn@localhost:5432/flwn_test`). The test run creates it if
needed, rebuilds the schema with Alembic and truncates every table after each test, so
**it refuses to run against any database whose name does not end in `_test`**. If
Postgres is unreachable the integration run stops with a message (it never skips
silently); unit tests still run. `DATABASE_URL` from `.env` is ignored by the tests.

Memory tests use in-memory Qdrant and mocked inference. `memory_steward` performs live
Qdrant and embedding readiness checks. Run separately with local and cloud
`QDRANT_URL`/`QDRANT_API_KEY` values before deployment; keys stay in environment.

## Try it with Postman

Collection and environment live in `docs/postman/`.

1. Start Postgres and the API: `bash run.sh postgres && bash run.sh migrate && bash run.sh`
   (the API also needs Qdrant at `QDRANT_URL`, see above).
2. In Postman, import `flwn-data-engine.postman_collection.json` and
   `flwn-local.postman_environment.json`, and select the "Flwn local" environment.
3. Set the `apiKey` variable to your `DATA_API_KEY` (the file only has a placeholder).
4. Run the **Full flow** folder with the Collection Runner. It is re-runnable: the
   email and project key are unique per run and every run creates a new organization.
   Then run **Error examples** for typical failures. The other folders hold one example
   request per route and reuse the ids the flow saved.

`X-Data-API-Key` is set on the collection; `X-Organization-Id` is added automatically
(except for `/users...` and `/organizations...`). Optional command line run:
`npx newman run docs/postman/flwn-data-engine.postman_collection.json -e docs/postman/flwn-local.postman_environment.json --env-var apiKey=<key>`.

## Docs

- [`docs/MEMORY_API.md`](docs/MEMORY_API.md): REST routes, auth, behavior, limits.
- [`docs/MEMORY_TOOLS.md`](docs/MEMORY_TOOLS.md): MCP tools and agent usage guide.

## Contract (original routes; see docs for the full set)

Protected endpoints require `X-Data-API-Key` matching `DATA_API_KEY`:

| Method | Path                                             | Body / result                                  |
| ------ | ------------------------------------------------ | ---------------------------------------------- |
| POST   | `/workspaces/{workspace_id}/memories`            | `text`, `source`, `agent` -> `point_id`        |
| POST   | `/workspaces/{workspace_id}/memories/recall`     | `query`, `agent`, `limit` -> ranked memories   |
| DELETE | `/workspaces/{workspace_id}/memories/{point_id}` | Workspace-scoped deletion; 204                 |
| POST   | `/workspaces/{workspace_id}/memories/cleanup`    | `retention_days` -> `deleted`                  |
| POST   | `/workspaces/{workspace_id}/sprint-completed`    | `retention_days` -> `deleted`                  |
| GET    | `/readyz`                                        | Qdrant + Foundry readiness; 503 if unavailable |
| GET    | `/healthz`                                       | Public process liveness only                   |

Limits: recall 1-100; retention 1-36500 days; nonblank workspace IDs and text.
Authentication is service-to-service, not end-user authorization. Trusted callers
must authorize workspace membership before choosing workspace IDs. Keep this API
on a private network and use TLS between services outside local development.

## Migration and Rollback

1. Deploy data engine against the **same Qdrant endpoint** previously used by AI.
   Retain collection `memory`, 1536-dimensional cosine vectors, UUIDs, and payloads.
   No export, deletion, re-embedding, or collection rename is needed.
2. Give data engine its own inference and Qdrant credentials. Set AI engine's
   `DATA_BASE_URL` and `DATA_API_KEY` to this service. Start data service first.
3. Verify readiness and workspace-scoped remember/recall against local and cloud.
   Existing AI versions may continue reading the unchanged collection during rollout.
4. After switching all callers, remove Qdrant credentials from AI runtime secrets.
   Old `.env` files are ignored by new AI configuration, not edited automatically.

Rollback: redeploy previous AI version with its previous Qdrant/inference secrets.
Stored data remains compatible. Never run destructive rollback scripts.

Run one data-service worker/replica while recall counters use a process-local lock.
Mixed-version or multi-process writers can lose counter increments. Cleanup retains
the existing 1000-candidate batch cap. Writes are not retried by AI adapter:
timeouts may mean committed writes, so automatic retries could duplicate memories.
