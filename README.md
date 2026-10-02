# Flwn Data Engine

FastAPI data layer. Owns Memory Steward, Qdrant access, memory compression,
embeddings, recall ranking, and cleanup. AI engine owns orchestration and Decision
Ledger; backend remains NestJS. Neither needs direct Qdrant credentials.

This migration implements memory only, not the diagram's PostgreSQL API, ingestion
worker, Redis queue, extraction pipeline, hybrid search, or reranking.

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

```sh
bash run.sh test
bash run.sh memory_steward
```

Tests use in-memory Qdrant and mocked inference. `memory_steward` performs live
Qdrant and embedding readiness checks. Run separately with local and cloud
`QDRANT_URL`/`QDRANT_API_KEY` values before deployment; keys stay in environment.

## Contract

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
