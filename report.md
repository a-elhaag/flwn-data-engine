# Memory Steward Handoff Report

Purpose: give a new coding session full context. Read this first, then `README.md`,
`docs/MEMORY_API.md`, `docs/MEMORY_TOOLS.md`.

## 1. Context

- Two repos, both on branch `ccr-9bc99585-jjmdov`:
  - `flwn-data-engine` (this repo): FastAPI data layer. Owns Memory Steward, Qdrant, embeddings, recall, cleanup.
  - `flwn-ai-engine`: agents and orchestration. Talks to memory only over HTTP (`agents/memory_steward/memory_steward.py`). Never touches Qdrant.
- Memory Steward moved from AI engine to data engine. This work finished it.
- Backend is NestJS (separate). It should mint agent tokens and authorize workspace membership.
- Postgres is **out of scope for now**. Memory goes straight to Qdrant.

## 2. What exists

### Memory operations (`memory/steward.py`)

| Op | Behavior |
| --- | --- |
| remember | LLM compresses text to `{fact, importance 1-5}`, stores fact embedding plus `raw_text`. Dedup: cosine >= `MEMORY_DEDUP_THRESHOLD` refreshes the existing memory and returns `deduplicated: true`. |
| ingest | 1-50 items, facts embedded in batched calls. |
| recall | Optional query rewrite (falls back to raw query on error), search top `limit*3`, rerank, bump recall counters. |
| open / browse / stats | Read one, page through (UUID cursor), counts by source. |
| revise | Replace text, keep id, payload and recall count, re-embed. |
| anchor | Pin or unpin. Pinned never age and are never swept or superseded. |
| forget | Delete one id. Idempotent. |
| purge | Delete whole workspace. Needs `confirm == workspace_id`. |
| sweep | Cleanup. Active, unpinned, never-recalled, older than retention. Importance >= 4 kept without LLM. Others judged in batches of `SWEEP_BATCH_SIZE` as strict JSON. Unsure, malformed, or failed means keep. Also purges superseded memories past retention. `dry_run` supported. |
| organize | Clusters near-duplicates (>= `MEMORY_ORGANIZE_THRESHOLD`), LLM answers distinct, duplicate, update, or merge. Losers get `status=superseded`, `superseded_by`, `superseded_at`. Never hard-deletes. Pinned never superseded. |
| on_sprint_completed / run_cleanup | Thin wrappers over sweep, kept for the old contract. |

Ranking (`retrieval/search.py`):
`similarity x (0.5 + 0.5 x recency) x (1 + 0.1 x ln(1 + recalls))`, recency = `0.5 ** (age / half_life)`,
age counted from the later of store time and last recall. Returned `score` is raw similarity.

Maintenance (sweep, organize) holds a per-workspace in-process lock. A second call returns 409.

### Auth and workspace locking (`api/auth.py`, `api/tokens.py`)

- Service: `X-Data-API-Key`. Any workspace, all routes.
- Agent: `Authorization: Bearer <JWT HS256>`. Claims: `ws`, `scope`, `sub`, `exp`, `iss`. Locked to its workspace (403 otherwise), checked against scope per route.
- Scopes: `memory:read`, `memory:write`, `memory:delete`. Admin routes (cleanup, organize, sprint-completed, purge, token mint, readyz) are service-only.
- `POST /auth/tokens` mints tokens. Needs `MEMORY_TOKEN_SECRET` (32+ chars). Blank means tokens and `/mcp` are disabled.
- Every Qdrant call goes through `vector_store._build_filter`, which always includes the workspace condition.

### MCP (`mcp_server.py`)

- Mounted at `/mcp`. Streamable HTTP, stateless, JSON responses.
- Bearer token required (ASGI `BearerGate` returns 401 before MCP parsing). Service key is not accepted.
- Workspace comes only from the token. No tool has a workspace argument.
- Tools: `memory_remember`, `memory_recall`, `memory_open`, `memory_browse`, `memory_revise`, `memory_forget`, `memory_ingest`, `memory_anchor`, `memory_pulse`. Annotations set (`readOnlyHint`, `destructiveHint`, `idempotentHint`).
- Not exposed: sweep, organize, purge, closeout.
- `create_mcp()` builds a fresh server per app, because a session manager can only `run()` once. `main.create_app()` uses it. Tests rely on this.

### REST routes

Full table in `docs/MEMORY_API.md`. Original routes and the `point_id` response field are unchanged for AI-engine compatibility. Cleanup response gained `scanned`, `kept`, `superseded_purged`, `dry_run`. Remember response gained `deduplicated`.

### AI engine adapter

`flwn-ai-engine/agents/memory_steward/memory_steward.py` exposes the same 9 tool names via `tool_specs()` / `call_tool()`, plus `sweep()` and `organize()` methods (code-only, not tools). Uses the service key.

## 3. File map (data engine)

```
main.py                 create_app(), lifespan (ensure_collection + MCP session manager)
config.py               all settings, see section 5
api/routes.py           REST routes
api/auth.py             Principal, allow(scope) dependency, service_only
api/tokens.py           mint / verify JWT
mcp_server.py           tools, BearerGate, create_mcp, build_app
memory/steward.py       MemorySteward and result dataclasses
memory/prompts.py       hardened prompts, strict JSON parsers
memory/vectorizer.py    prepare (compress) + embed seam
memory/errors.py        MemoryNotFound, MaintenanceBusy, ConfirmationRequired
retrieval/search.py     rewrite, search, rank_score, touch
clients/vector_store.py Qdrant access, workspace filter on every call
clients/inference.py    chat, embed, embed_many (Azure Foundry)
clients/health.py       readiness
docker-compose.yml      light local Qdrant
tests/harness.py        shared base: in-memory Qdrant, task-aware ChatStub, mocks
tests/test_memory_api.py        original contract tests + AI adapter round trip
tests/test_memory_features.py   new ops, auth, locking, MCP
docs/                   MEMORY_API.md, MEMORY_TOOLS.md
```

## 4. Run and test

```sh
pip install -r requirements.txt       # or: uv venv && uv pip install -r requirements.txt
python3 -m unittest discover -s tests # 32 tests, in-memory Qdrant, mocked inference
bash run.sh qdrant                    # light local Qdrant via Docker
bash run.sh                           # API on 127.0.0.1:8002
```

Test notes:
- Import `harness` before any app module in a test file. It sets the env vars `config.py` needs.
- `ChatStub.replies` is keyed by task name (`memory_steward.compress`, `.query_rewrite`, `.cleanup_relevance`, `.organize`). Set a reply to a string, a callable, or `None` (sweep: drop everything).
- Dedup is off in tests (`MEMORY_DEDUP_THRESHOLD = 1.1`) because mocks return identical embeddings. Turn it on per test with `patch.object`.
- `tests/test_memory_api.py::test_ai_adapter_round_trip` imports the sibling `flwn-ai-engine` repo from `../flwn-ai-engine`.

## 5. Configuration

| Var | Default | Meaning |
| --- | --- | --- |
| `DATA_API_KEY`, `AZURE_FOUNDRY_ENDPOINT`, `AZURE_FOUNDRY_KEY` | required | Service key and inference |
| `QDRANT_URL`, `QDRANT_API_KEY` | `http://localhost:6333`, blank | Cloud for prod, light container for dev |
| `MEMORY_CHAT_DEPLOYMENT`, `EMBEDDING_DEPLOYMENT` | `gpt-6-luna`, `embed-v-4-0` | Models |
| `MEMORY_TOKEN_SECRET` | blank | 32+ chars. Blank disables tokens and `/mcp` |
| `MEMORY_TOKEN_ISSUER`, `MEMORY_TOKEN_MAX_TTL_SECONDS` | `flwn-data-engine`, 86400 | Token policy |
| `MCP_ALLOWED_HOSTS` | blank | Comma-separated Host allow-list for `/mcp`. Blank disables the check |
| `MEMORY_DEDUP_THRESHOLD` | 0.97 | Above 1 disables dedup |
| `MEMORY_ORGANIZE_THRESHOLD` | 0.88 | Cluster similarity |
| `MEMORY_QUERY_REWRITE` | true | LLM rewrite on recall |
| `RECENCY_HALF_LIFE_DAYS` | 14 | Ranking |
| `SWEEP_SCAN_CAP`, `SWEEP_BATCH_SIZE`, `ORGANIZE_SCAN_CAP` | 5000, 20, 1000 | Maintenance bounds |

## 6. Decisions made

- REST stays the real API. MCP wraps the same steward for agents. GraphQL rejected (small fixed verbs, no graph).
- Workspace lives in the signed token, never in tool arguments.
- Soft supersede in organize, hard delete only in sweep after retention.
- Prompts treat stored text as untrusted data. Parsers never raise and default to keep.
- Tool names picked by the assistant, not confirmed by the user: `memory_open`, `memory_browse`, `memory_revise`, `memory_ingest`, `memory_anchor`, `memory_pulse`. Confirmed by the user: `memory_remember`, `memory_recall`, `memory_forget`, `memory_sweep`, `memory_organize`. Note `memory_sweep` and `memory_organize` exist only as REST routes and AI adapter methods, not MCP tools. Renaming is mechanical: `mcp_server.py` `_tool(name=...)`, adapter `tool_specs`/`call_tool`, docs, tests.

## 7. Known gaps and risks

1. **Never run against a live Qdrant server.** Only in-memory Qdrant. No Docker daemon in the build sandbox. Verify filters (`must_not` on missing `status`, payload index types, `set_payload` with a filter selector, scroll offsets) on a real server before deploy.
2. **MCP tested through the HTTP test client**, not a real MCP client. Try Claude Code or the MCP inspector against `/mcp` with a minted token.
3. **Vectorizing is synchronous.** Write embeds inline. No durable queue. The Postgres outbox plan reuses `memory/vectorizer.py`.
4. **Recall counters** are read-modify-write under a process-local lock. They can under-count across replicas.
5. **Maintenance lock is per process.** With several replicas, trigger sweep and organize from one caller.
6. **Cloud Qdrant is not deployed.** Hosting choice open (Qdrant Cloud vs own container service).
7. **`DNS rebinding` check is off by default** (`MCP_ALLOWED_HOSTS` blank). Keep the service on a private network or set the allow-list.
8. **Sweep and organize cost LLM calls.** Bounded by scan caps and batch size, but there is no rate or budget guard beyond the shared Foundry rate limiter.
9. **Stats `by_source`** scans up to `SWEEP_SCAN_CAP` records and flags `by_source_truncated`.
10. **Migration:** old points lack `importance`, `pinned`, `status`, `raw_text`. Code treats missing as defaults (importance 3, not pinned, active). `ensure_collection` now creates more payload indexes on startup.
11. The MCP spec changed on 2026-07-28 (sessions retired, per a blog summary). Check the current spec and the installed `mcp` SDK version if the transport misbehaves.

## 8. Suggested next steps

1. Run the stack against live Qdrant (`bash run.sh qdrant`), add an integration test marked to skip without `QDRANT_URL`.
2. Smoke-test `/mcp` with a real MCP client.
3. Confirm tool names with the user, rename if needed.
4. Decide cloud Qdrant host and deploy; set `QDRANT_URL`, `QDRANT_API_KEY`.
5. Backend (NestJS): call `POST /auth/tokens` per agent session; pass token to MCP clients.
6. Add a recall quality eval set (query to expected memory, hit@k).
7. Phase 3: PostgreSQL source of truth, transactional outbox with `LISTEN/NOTIFY` plus polling fallback, `FOR UPDATE SKIP LOCKED` workers calling `memory/vectorizer.py`, atomic recall counters in SQL, status field per memory.
8. Optional: schedule sweep and organize (cron or sprint-completed hook), expose `organize` and `sweep` metrics.
