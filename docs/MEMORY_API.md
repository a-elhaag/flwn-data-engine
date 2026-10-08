# Memory Steward REST API

Base URL: the data engine (default `http://localhost:8002`). Interactive docs at `/docs`.
Every memory route is scoped by `{workspace_id}` in the path. Workspace ids are UUIDs of rows
in the `workspaces` table: a malformed id is `422`, and writing to a workspace that does not
exist is `404`. Reading an unknown workspace returns nothing (it does not reveal which ids exist).

## Authentication

| Caller | Header | Allowed |
| --- | --- | --- |
| Service (trusted backend) | `X-Data-API-Key: <DATA_API_KEY>` | Every route, every workspace |
| Agent | `Authorization: Bearer <workspace token>` | Only its workspace, only its scopes |

Mint agent tokens with `POST /auth/tokens` (service key only):

```json
{"workspace_id": "8c1f0a52-5a8e-4b76-9d0e-3a1b6f2c7d11", "subject": "planner", "scopes": ["memory:read", "memory:write"], "ttl_seconds": 3600}
```

Scopes: `memory:read`, `memory:write`, `memory:delete`, and for files `files:read`,
`files:write`, `files:delete` (see [FILES_API.md](FILES_API.md)). Default when `scopes` is omitted: `memory:read` and `files:read` only; request write and
delete scopes explicitly. Max TTL:
`MEMORY_TOKEN_MAX_TTL_SECONDS`.
Requires `MEMORY_TOKEN_SECRET` (32+ chars). Maintenance and admin routes accept the service
key only.

Status codes: `401` bad/missing credentials or expired token, `403` token for another
workspace or missing scope or service-only route, `404` memory not found in this workspace or workspace does not exist,
`409` maintenance already running for the workspace, `422` validation, `503` readiness.

## Routes

| Method | Path | Scope | Purpose | MCP tool |
| --- | --- | --- | --- | --- |
| POST | `/workspaces/{ws}/memories` | write | Store one memory. Returns `point_id`, `deduplicated` | `memory_remember` |
| POST | `/workspaces/{ws}/memories/batch` | write | Store 1-50 memories, batched embedding | `memory_ingest` |
| POST | `/workspaces/{ws}/memories/recall` | read | Ranked search. Body: `query`, `agent`, `limit`, `sources` | `memory_recall` |
| GET | `/workspaces/{ws}/memories` | read | Page through memories. `limit`, `cursor`, `source`, `agent`, `include_superseded` | `memory_browse` |
| GET | `/workspaces/{ws}/memories/{id}` | read | One memory incl. `raw_text` | `memory_open` |
| GET | `/workspaces/{ws}/memories/stats` | read | Counts, by source | `memory_pulse` |
| PATCH | `/workspaces/{ws}/memories/{id}` | write | Replace text, keep id and history | `memory_revise` |
| PUT | `/workspaces/{ws}/memories/{id}/pin` | write | `{"pinned": true|false}` | `memory_anchor` |
| DELETE | `/workspaces/{ws}/memories/{id}` | delete | Delete one; idempotent, 204 | `memory_forget` |
| POST | `/workspaces/{ws}/memories/cleanup` | service | Sweep. `retention_days`, `dry_run` | - |
| POST | `/workspaces/{ws}/sprint-completed` | service | Same as cleanup, for sprint end | - |
| POST | `/workspaces/{ws}/memories/organize` | service | Dedupe/supersede. `dry_run`, `max_clusters` | - |
| DELETE | `/workspaces/{ws}/memories?confirm={ws}` | service | Purge the whole workspace | - |
| POST | `/auth/tokens` | service | Mint agent token | - |
| GET | `/healthz` | none | Liveness | - |
| GET | `/readyz` | service | Database (with pgvector), Foundry and storage readiness | - |

Browse pages by id; `next_cursor` is the last id of the page. Limits: text 1-100000 chars, recall `limit` 1-100, batch 1-50, retention 1-36500 days.

## Behavior

- **Write path.** Text goes to the LLM, which returns one key fact plus importance (1-5).
  The fact is embedded and stored; the original is kept as `raw_text`. If the model ignores
  the JSON format the reply is stored as the fact with importance 3.
- **Dedup.** A new fact with cosine similarity >= `MEMORY_DEDUP_THRESHOLD` to an existing
  memory refreshes that memory instead of storing a copy.
- **Recall.** Vector search and keyword search each return candidates and are merged by
  reciprocal rank fusion, so an exact term (a ticket id, a name) is found even when the embedding
  misses it. A Cohere reranker then scores up to `RECALL_CANDIDATES` (30) candidates against the
  query. If the reranker is off (`MEMORY_RERANK=false`) or fails, vector similarity is used.
- **Ranking.** `relevance x (0.5 + 0.5 x recency) x (1 + 0.1 x ln(1 + recalls))`, where relevance
  is the reranker's score (or cosine similarity without it). Recency halves every
  `RECENCY_HALF_LIFE_DAYS` from the last use (store or recall). Pinned memories never age.
  Superseded memories are excluded. The returned `score` is the relevance.
- **Query rewrite.** One LLM call per recall; falls back to the raw query on any error. Turn
  off with `MEMORY_QUERY_REWRITE=false`.
- **Sweep.** Considers active, unpinned, never-recalled memories older than the retention.
  Importance >= 4 is kept without asking. The rest go to the LLM in batches as strict JSON.
  Anything unsure, malformed, or failed is kept. Superseded memories past retention are
  purged. `dry_run` reports without deleting. Scans at most `SWEEP_SCAN_CAP` per run.
- **Organize.** Finds clusters of near-duplicates (>= `MEMORY_ORGANIZE_THRESHOLD`) and asks
  the LLM: distinct, duplicate, update, or merge. Losers are marked `superseded`, never
  deleted directly. Pinned memories are never superseded.
- **Exclusive maintenance.** Sweep and organize take a per-workspace Postgres advisory lock,
  so a second call from any process or replica returns 409. Postgres drops the lock if the
  process dies, so a crash never leaves it stuck.
- **Untrusted text.** Memory text is wrapped as data in every prompt and tag look-alikes are
  stripped.

## Storage

Memories are rows in the `memories` table (kind `fact`), with the embedding in a `vector(1536)`
column and a HNSW cosine index. Recall counters are one atomic `UPDATE`, so concurrent recalls
and several replicas never lose a count. A memory's `source` is stored as `source_type` and its
`agent` as `agent_name`. Superseded memories stay in the table until a sweep purges them.

## Known limits

- Keyword search uses the `simple` text-search configuration (no stemming, works for mixed
  languages); it matches whole words, not word parts.
- Without the reranker, ordering uses vector similarity only, which can rank a near-miss above
  an exact match (the reranker is what fixes that).
- With many workspaces in one HNSW index, a filtered search can return fewer rows than asked.
  Recall raises `hnsw.ef_search` and uses iterative scans when pgvector supports them (0.8+).
- Writes embed synchronously, inside the request.
- The model dimension must match `vector(1536)` (the `embed-v-4-0` default).
