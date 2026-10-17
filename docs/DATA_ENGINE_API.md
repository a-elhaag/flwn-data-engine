# flwn Data Engine

Documentation for the REST API, Swagger UI, service connections, and MCP tools.

## 1. Authentication

### 1.1 Service API key

Send the configured service key in this header:

```http
X-Data-API-Key: <DATA_API_KEY>
```

The service key can access:

- `/readyz`
- `/auth/tokens`
- All workspace memory routes
- All maintenance routes

### 1.2 Workspace bearer token

Create a token with the service key:

```http
POST /auth/tokens
X-Data-API-Key: <DATA_API_KEY>
Content-Type: application/json
```

```json
{
  "workspace_id": "team-a",
  "subject": "agent",
  "scopes": [
    "memory:read",
    "memory:write",
    "memory:delete"
  ],
  "ttl_seconds": 3600
}
```

Example response:

```json
{
  "token": "<jwt>",
  "expires_at": 1730000000
}
```

Use the returned token as follows:

```http
Authorization: Bearer <token>
```

Available scopes:

| Scope | Allows |
| --- | --- |
| `memory:read` | Recall, browse, statistics, and open. |
| `memory:write` | Remember, batch ingest, revise, and pin. |
| `memory:delete` | Delete one memory. |

Bearer tokens are locked to the workspace in the token. The token workspace
must match the `{workspace_id}` in the URL.

## 2. REST API routers

### 2.1 System router

| Method | Path | Authentication | Description |
| --- | --- | --- | --- |
| `GET` | `/healthz` | None | Confirms that the process is alive. |
| `GET` | `/readyz` | Service API key | Checks configured service dependencies. Returns `503` when unavailable. |

Health response:

```json
{
  "status": "ok"
}
```

### 2.2 Authentication router

| Method | Path | Authentication | Description |
| --- | --- | --- | --- |
| `POST` | `/auth/tokens` | Service API key | Creates a workspace-scoped JWT. |

Token request fields:

| Field | Required | Description |
| --- | --- | --- |
| `workspace_id` | Yes | Workspace name or ID. |
| `subject` | No | Token subject. Defaults to `agent`. |
| `scopes` | No | Requested memory scopes. |
| `ttl_seconds` | No | Token lifetime. Defaults to `3600`. |

### 2.3 Workspace memory router

All paths below use `{workspace_id}` and require the appropriate scope or the
service API key.

| Method | Path | Scope | Description |
| --- | --- | --- | --- |
| `POST` | `/workspaces/{workspace_id}/memories` | `memory:write` | Stores one memory. |
| `POST` | `/workspaces/{workspace_id}/memories/batch` | `memory:write` | Stores 1-50 memories. |
| `POST` | `/workspaces/{workspace_id}/memories/recall` | `memory:read` | Searches relevant memories. |
| `GET` | `/workspaces/{workspace_id}/memories` | `memory:read` | Browses memories. |
| `GET` | `/workspaces/{workspace_id}/memories/stats` | `memory:read` | Returns memory statistics. |
| `GET` | `/workspaces/{workspace_id}/memories/{point_id}` | `memory:read` | Opens one memory. |
| `PATCH` | `/workspaces/{workspace_id}/memories/{point_id}` | `memory:write` | Revises one memory. |
| `PUT` | `/workspaces/{workspace_id}/memories/{point_id}/pin` | `memory:write` | Pins or unpins one memory. |
| `DELETE` | `/workspaces/{workspace_id}/memories/{point_id}` | `memory:delete` | Deletes one memory. Returns `204`. |

#### Store one memory

```http
POST /workspaces/team-a/memories
X-Data-API-Key: <DATA_API_KEY>
Content-Type: application/json
```

```json
{
  "text": "The billing service retries failed payments three times.",
  "source": "decision",
  "agent": "planner"
}
```

Response:

```json
{
  "point_id": "memory-uuid",
  "deduplicated": false
}
```

#### Store memories in a batch

```json
{
  "items": [
    {
      "text": "The billing service retries failed payments three times.",
      "source": "decision",
      "agent": "planner"
    }
  ]
}
```

The REST batch endpoint accepts 1-50 items.

#### Recall memories

```json
{
  "query": "How many payment retries are configured?",
  "agent": "reviewer",
  "limit": 5,
  "sources": ["decision"]
}
```

The REST `limit` must be between 1 and 100. `sources` is optional.

#### Browse memories

| Query parameter | Default | Description |
| --- | ---: | --- |
| `limit` | `20` | Number of results, from 1 to 100. |
| `cursor` | None | UUID cursor from the previous page. |
| `source` | None | Filter by source. |
| `agent` | None | Filter by storing agent. |
| `include_superseded` | `false` | Include superseded memories. |

#### Revise a memory

```json
{
  "text": "The billing service retries failed payments five times."
}
```

#### Pin a memory

```json
{
  "pinned": true
}
```

### 2.4 Maintenance router

Maintenance endpoints require the service API key. Bearer tokens cannot call
these routes.

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/workspaces/{workspace_id}/memories/cleanup` | Cleans up old memories. |
| `POST` | `/workspaces/{workspace_id}/memories/organize` | Organizes related memories. |
| `POST` | `/workspaces/{workspace_id}/sprint-completed` | Runs sprint-completion cleanup. |
| `DELETE` | `/workspaces/{workspace_id}/memories` | Purges workspace memories. |

Cleanup request:

```json
{
  "retention_days": 30,
  "dry_run": false
}
```

Organize request:

```json
{
  "dry_run": false,
  "max_clusters": 50
}
```

Purge requires confirmation matching the workspace ID:

```http
DELETE /workspaces/team-a/memories?confirm=team-a
X-Data-API-Key: <DATA_API_KEY>
```

## 3. Service connections

### 3.1 Azure Foundry connection

Azure Foundry provides chat and embedding inference.

| Environment variable | Default | Description |
| --- | --- | --- |
| `AZURE_FOUNDRY_ENDPOINT` | Required | Azure Foundry endpoint. |
| `AZURE_FOUNDRY_KEY` | Required | Azure Foundry key. |
| `MEMORY_CHAT_DEPLOYMENT` | `gpt-6-luna` | Chat deployment. |
| `EMBEDDING_DEPLOYMENT` | `embed-v-4-0` | Embedding deployment. |

### 3.2 Data flow

```text
REST or MCP request
        |
        v
Workspace authentication
        |
        v
Memory Steward
        |
        v
Azure Foundry
compression and
embeddings
```

## 4. MCP

### 4.1 Connection

MCP uses Streamable HTTP at:

```text
http://127.0.0.1:8002/mcp
```

Every MCP request must include:

```http
Authorization: Bearer <workspace-token>
```

Connection settings:

| Setting | Value |
| --- | --- |
| Transport | Streamable HTTP |
| URL | `http://127.0.0.1:8002/mcp` |
| Authentication | Workspace-scoped Bearer token |

The workspace comes from the signed token and is not supplied as a tool
argument.

### 4.2 MCP tools

| Tool | Scope | Description |
| --- | --- | --- |
| `memory_remember` | `memory:write` | Stores one durable fact or decision. |
| `memory_recall` | `memory:read` | Searches memory by meaning. |
| `memory_open` | `memory:read` | Reads one memory by UUID. |
| `memory_browse` | `memory:read` | Lists memories page by page. |
| `memory_revise` | `memory:write` | Replaces memory text. |
| `memory_forget` | `memory:delete` | Deletes one memory. |
| `memory_ingest` | `memory:write` | Stores up to 20 memories. |
| `memory_anchor` | `memory:write` | Pins or unpins a memory. |
| `memory_pulse` | `memory:read` | Returns memory statistics. |

MCP does not expose cleanup, organization, purge, or sprint-completion
maintenance operations.

### 4.3 MCP host protection

`MCP_ALLOWED_HOSTS` is a comma-separated list of accepted host names. When it
is configured, the MCP server enables DNS rebinding protection and allows HTTP
and HTTPS origins for those hosts. When it is blank, the additional host check
is disabled.

## 5. HTTP errors

| Status | Meaning |
| ---: | --- |
| `400` | Invalid purge confirmation. |
| `401` | Missing or invalid service key or bearer token. |
| `403` | Workspace mismatch, missing scope, or service-only operation. |
| `404` | Memory does not exist in the requested workspace. |
| `409` | Maintenance operation is already running. |
| `422` | Request validation or token request failure. |
| `503` | A readiness dependency is unavailable. |

## 6. Verification checklist

1. Start the API with `bash run.sh`.
2. Open Swagger UI at [http://127.0.0.1:8002/docs](http://127.0.0.1:8002/docs) or [http://127.0.0.1:8002/swagger](http://127.0.0.1:8002/swagger).
3. Confirm all 16 REST operations and their nested schemas are listed and expanded by default.
4. Open the [OpenAPI JSON](http://127.0.0.1:8002/openapi.json).
5. Call `GET /healthz` without authentication.
6. Call `GET /readyz` with `X-Data-API-Key`.
7. Create a workspace token with `POST /auth/tokens`.
8. Call a workspace memory endpoint with `Authorization: Bearer <token>`.
9. Connect an MCP client to `/mcp` with the same workspace token.
