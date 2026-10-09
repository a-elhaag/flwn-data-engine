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

## Project structure

```
app/
  main.py              application factory
  config.py            settings from the environment
  api/                 HTTP layer
    routes/            memory.py, files.py, meetings.py, system.py (health, token minting)
    schemas.py         request and response bodies
    deps.py            shared dependencies and required permissions
    auth.py, tokens.py service key and workspace-locked agent tokens
    errors.py          domain errors mapped to HTTP statuses
  meetings/            meetings, consent, recordings, transcripts
  mcp_tools/           MCP server for agents, mounted at /mcp
  memory/              Memory Steward
    steward.py         write, read, revise, sweep, organize
    store.py           every SQL statement for memories
    recall.py          query rewrite, search, ranking
    prompts.py         hardened prompts and strict JSON parsing
    vectorizer.py      compress + embed
  storage/             Azure Blob and file search
    blobs.py           signed links, upload checks
    files.py           file registry
    indexer.py         background worker: extract, chunk, embed
    extract.py         text, PDF (pypdf), scans and images (Parse)
    chunking.py        structure-aware chunks with page and heading citations
    search.py          hybrid search over file chunks
  clients/             Foundry inference, retries and rate limiting, readiness checks
  db/                  schema
    models/            SQLAlchemy models by domain
    sql/               functions, triggers, row-level security
    install.py         creates the schema (no migration history, see below)
    session.py         engine, workspace-scoped sessions, advisory locks
    erd.py             draws the schema as an interactive graph
tests/                 unit and database tests
docs/                  API and design documents
infra/provision.sh     the Azure resources, as code
.github/workflows/     CI
```

## Run it

```sh
uv venv && uv pip install -r requirements-dev.txt
cp .env.example .env          # fill in DATA_API_KEY and the Foundry settings
bash run.sh postgres          # local Postgres with pgvector (Docker)
bash run.sh install           # create the schema
bash run.sh                   # API on http://localhost:8002 (docs at /docs)
```

Other commands: `bash run.sh test`, `bash run.sh lint`, `bash run.sh ready` (live dependency
check), `bash run.sh erd` (rebuild `docs/schema-graph.html`).

### Database: no migrations

The database was created fresh, so the models are the schema. `python -m app.db.install` creates
whatever is missing and is safe to re-run; `--reset` drops every table first (development only).
When the schema must change on a database that holds real data, add Alembic then.

## Configuration

See `.env.example`. The settings that matter most:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | What the API connects as. **Must not be a superuser or `BYPASSRLS` role**, or workspace isolation is silently off. Azure's admin role has `BYPASSRLS`. |
| `DATABASE_ADMIN_URL` | Used only to install the schema; may be the admin role. |
| `AZURE_STORAGE_ACCOUNT_URL` | Enables the file routes. Access is by Entra ID, no keys. |
| `AZURE_FOUNDRY_*`, `EMBEDDING_DEPLOYMENT` | Chat and embedding models. |
| `MEMORY_TOKEN_SECRET` | 32+ characters. Enables agent tokens and `/mcp`. |

Create the least-privilege role with `APP_DB_PASSWORD=... python -m app.db.install --app-role flwn_app`.

## Azure

`infra/provision.sh` records how the resources were created: resource group `flwn-data-engine`
in UAE North, PostgreSQL Flexible Server (Burstable B1ms, about $19/month), and a storage account
with private containers `workspace-files`, `chat-media`, `meeting-recordings` and
`agent-reports`, 7-day soft delete, and recordings moved to the Cool tier after 30 days.

## CI

`.github/workflows/ci.yml` runs on every push and pull request: lint and format check, the test
suite against a pgvector Postgres service, then a Docker build with a health-check smoke test.
Deployment is not automated yet: it needs a container registry and a host (for example Azure
Container Apps), which are not provisioned.

## Docs

- [`docs/MEMORY_API.md`](docs/MEMORY_API.md): memory routes, auth, behavior, limits.
- [`docs/MEMORY_TOOLS.md`](docs/MEMORY_TOOLS.md): MCP tools and the agent usage guide.
- [`docs/AI_ENGINE_UPDATE.md`](docs/AI_ENGINE_UPDATE.md): what the AI engine must update.
- [`docs/MEETINGS_API.md`](docs/MEETINGS_API.md): meetings, consent, recordings, transcripts.
- [`docs/FILES_API.md`](docs/FILES_API.md): file upload, download and storage layout.
- [`docs/schema-graph.html`](docs/schema-graph.html): the database as an interactive graph.

## Known gaps

- Recall is hybrid (vector + keyword) with a reranker, but there is no relevance cut-off yet: it
  always returns the best few, even when none is a good match.
- File search has no relevance cut-off yet: it returns the best few chunks even when none matches.
- Writes embed synchronously; there is no background worker.
- Task, project and workspace CRUD endpoints are not built; the tables exist.
