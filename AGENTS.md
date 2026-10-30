# Flwn data engine: guide for AI agents

FastAPI service over Postgres (pgvector) that stores a workspace's memory, files, meetings,
planning, collaboration and atoms (AI workers). Read this first, then the generated references.

## Where things are

| Need | Read |
| --- | --- |
| Every REST route, with the auth it demands | [docs/api.md](docs/api.md) |
| MCP tools for agents (`POST /mcp`) | [docs/mcp.md](docs/mcp.md) |
| Tables and foreign keys | [docs/schema.md](docs/schema.md) |
| Environment variables and defaults | [docs/config.md](docs/config.md) |
| Request and response bodies | `GET /openapi.json`, or `postman/` |

The four `docs/*.md` files are generated from the code by `./run.sh docs`; a test fails when
they are stale. Never edit them by hand. Do not add hand-written API docs: they drift.

## Layout

- `app/api/` HTTP: `routes/` (one module per area), `schemas/` (bodies), `auth.py`, `tokens.py`
- `app/domain/` business logic with no HTTP: atoms, collaboration, decisions, identity, meetings,
  memory, planning, storage
- `app/integrations/` Foundry inference, ranking, `mcp/` (the MCP server)
- `app/db/` models, `sql/` (functions, triggers, row-level security), install, session
- `tests/` one folder per area; shared helpers at the top of `tests/`

## Rules that must hold

- **Workspace isolation twice.** Every query filters by workspace and the session sets it so
  Postgres row-level security rejects what the filter missed. The app must connect as a role
  without `BYPASSRLS` (see `.env.example`).
- **The workspace comes from the credential**, never from a tool argument or request body.
- **Credentials.** The service key (`X-Data-API-Key`, plus `X-Acting-Member-Id` to act as a
  member) is the trusted backend. Bearer tokens are signed by the backend (EdDSA or ES256) and
  only verified here; this service never mints them.
- **Atoms are checked live.** An atom run token is validated against the database on every
  request: the run must belong to the atom, the member must be the atom's, and `ctx` must equal
  the atom's kind.
- **Bytes never pass through the API.** Uploads and downloads use short-lived signed links.
- **Schema changes** go in the models and `app/db/sql/`; keep `migrations/` in step.

## Commands

`./run.sh` runs the API. `./run.sh test` runs the suite and needs Postgres with pgvector
(`TEST_DATABASE_URL` is an admin URL; tests create and drop their own database).
`./run.sh lint`, `./run.sh docs`, `./run.sh postman`.
