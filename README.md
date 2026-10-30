# Flwn Data Engine

The data layer behind Flwn: a FastAPI service that owns the Postgres database, the
**Memory Steward** (team memory with recall, cleanup and consolidation), and **file storage**
on Azure Blob. The Go backend and the AI engine are callers; neither touches the database or
storage directly.

## How it fits together

```
Go backend ─┐                      ┌─ PostgreSQL + pgvector   (source of truth, memory, vectors)
            ├─ REST / MCP ─ app ───┤
AI engine ──┘                      └─ Azure Blob Storage      (files, voice notes, recordings, reports)
                                        │
                                        └─ Azure AI Foundry   (chat + embeddings)
```

- **One database.** Workspaces, projects, tasks, docs, chat and memory live in Postgres. Memory
  vectors are a column (`pgvector`), so a memory and its embedding commit together.
- **Workspace isolation twice.** Every query filters by workspace, and the session sets the
  workspace so Postgres row-level security rejects anything the filter missed. This only works if
  the app connects as a role without `BYPASSRLS` (see Configuration).
- **Bytes never pass through the API.** Uploads and downloads use short-lived signed links.

For AI agents and tools: start with [`AGENTS.md`](AGENTS.md); the API, MCP, schema and config references in `docs/` are generated from the code (`./run.sh docs`).

## Project structure

```
app/
  main.py              application factory
  core/                config.py (settings from the environment), health.py
  api/                 HTTP layer
    routes/            one module per area: memory, files, meetings, planning, identity, atoms ...
    schemas/           request and response bodies: common, atoms, approvals, collab, identity, planning
    auth.py, tokens.py, revocation.py   service key and workspace-locked, scoped tokens
    deps.py, errors.py, paging.py, generic_crud.py, dev_session.py
  domain/              business logic, no HTTP
    atoms/             atom service, skills, approvals, aggregate
    collaboration/  decisions/  identity/  meetings/  planning/
    memory/            Memory Steward: steward, store, recall, prompts, vectorizer
    storage/           blobs, files, indexer, extract, chunking, search, media, transcripts
  integrations/        inference.py (Foundry), ranking.py, resilience.py, mcp/ (MCP server at /mcp)
  openapi/             Swagger metadata: field docs, examples, summaries, business slice, explorer page
  db/                  models/ by domain, sql/ (functions, triggers, RLS), install, session, seed, erd
migrations/            Alembic: one frozen baseline, later changes on top
tests/                 one folder per area (atoms, auth, api, memory ...) plus shared support modules
postman/               generated collection, environment, business OpenAPI slice
docs/                  generated references: api, mcp, schema, config (build.py)
AGENTS.md              guide for AI agents
infra/                 provision.sh, deploy.sh (Azure)
.github/workflows/     CI
```

## Run it

```sh
uv venv && uv pip install -r requirements-dev.txt
cp .env.example .env          # fill in DATA_API_KEY and the Foundry settings
bash run.sh postgres          # local Postgres with pgvector (Docker)
bash run.sh migrate           # create the schema (Alembic; `run.sh install` does the same from the models)
bash run.sh                   # API on http://localhost:8002 (docs at /docs)
```

Other commands: `bash run.sh test`, `bash run.sh lint`, `bash run.sh ready` (live dependency
check), `bash run.sh postman` (rebuild the
Postman collection). Tests are discovered with `-s tests -t tests`.

### Who can call what

- **Service key (`X-Data-API-Key`):** the trusted backend. Send `X-Acting-Member-Id` to record
  changes against a member.
- **Bearer token:** signed by the backend (EdDSA or ES256) and verified here with `TOKEN_JWKS` or
  `TOKEN_JWKS_URL`. Locked to one workspace and limited to its scopes. This service never mints
  tokens. `AGENT_TOKENS_ENABLED=false` switches them off.
- With the key, private channels and private teams are visible to an admin, but direct and agent
  channels are visible only to their members. Without a member id, changes are not attributed.


### Database installation and Atoms upgrade

Use `bash run.sh migrate` (Alembic, connecting as `DATABASE_ADMIN_URL`) for a fresh database.
Future schema changes are new migrations in `migrations/versions/`. `python -m app.db.install`
builds the same schema from the models and does not alter existing tables.
For an existing legacy database, take a backup, then run the admin upgrade **before app rollout**:

```sh
python -m app.db.upgrade_atoms --app-role flwn_app
```

The upgrade requires `DATABASE_ADMIN_URL` (no fallback to `DATABASE_URL`). Set `--app-role` to
the runtime database role, replacing `flwn_app` if needed. It is additive, transactional and
idempotent, uses bounded locks, and preserves rows and the existing role's login/password.
Never use `--reset` on an existing database. Rolling back to the old image leaves the additive
schema in place.

## Configuration

See `.env.example`. The settings that matter most:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | What the API connects as. **Must not be a superuser or `BYPASSRLS` role**, or workspace isolation is silently off. Azure's admin role has `BYPASSRLS`. |
| `DATABASE_ADMIN_URL` | Administrative schema installation/upgrade connection; required by the Atoms upgrade. Never use it for the API. |
| `AZURE_STORAGE_ACCOUNT_URL` | Enables the file routes. Access is by Entra ID, no keys. |
| `AZURE_FOUNDRY_*`, `EMBEDDING_DEPLOYMENT` | Chat and embedding models. |
| `TOKEN_JWKS` / `TOKEN_JWKS_URL` | The backend's public keys (a JWKS document, or its URL). `kid` picks the key. |
| `TOKEN_ISSUER`, `TOKEN_AUDIENCE` | Expected `iss` (default `flwn-backend`) and this service's name in `aud` (default `data-engine`). |

Create the least-privilege role with `APP_DB_PASSWORD=... python -m app.db.install --app-role flwn_app`.

## Azure

`infra/provision.sh` records how the resources were created: resource group `flwn-data-engine`
in UAE North, PostgreSQL Flexible Server (Burstable B1ms, about $19/month), and a storage account
with private containers `workspace-files`, `chat-media`, `meeting-recordings` and
`agent-reports`, 7-day soft delete, and recordings moved to the Cool tier after 30 days.

## CI

`.github/workflows/ci.yml` runs on pushes to `main` and pull requests: lint and format check,
the test suite against a pgvector Postgres service, then a Docker build with a health-check
smoke test. CI does not deploy.

[`infra/deploy.sh`](infra/deploy.sh) provides a manual Azure Container Apps deployment path
after [`infra/provision.sh`](infra/provision.sh): it creates ACR, a managed identity, networking
and private endpoints, builds the image in ACR, and creates or updates the app. It reads secrets
from the gitignored `.env`. These scripts describe deployment intent; their presence does not
confirm that resources are provisioned or that a deployment is healthy.

## Known gaps

- Recall is hybrid (vector + keyword) with a reranker, but there is no relevance cut-off yet: it
  always returns the best few, even when none is a good match.
- File search has no relevance cut-off yet: it returns the best few chunks even when none matches.
- Writes embed synchronously; there is no background worker.
- Several tables still have no REST API (milestones, labels, folders, invitations and more).
