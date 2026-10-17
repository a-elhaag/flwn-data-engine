# flwn Data Engine

Authentication, service connections, MCP tools and errors. Endpoint reference: Swagger UI at `/docs`.

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
  "workspace_id": "3f2b8c1e-5a47-4d0e-9c1a-7b6d2e8f4a10",
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

## 2. REST API

The REST API has 100+ endpoints across memory, files, meetings, atoms, approvals, planning,
identity and collaboration, so it is documented in one place that cannot go stale: the live
Swagger UI at `/docs` (alias `/swagger`). It lists every endpoint with a description, the required
access, request and response schemas, and example bodies. The raw spec is at `/openapi.json`.

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
3. Confirm the endpoints are grouped by tag and every request and response schema is listed under Schemas.
4. Open the [OpenAPI JSON](http://127.0.0.1:8002/openapi.json).
5. Call `GET /healthz` without authentication.
6. Call `GET /readyz` with `X-Data-API-Key`.
7. Create a workspace token with `POST /auth/tokens`.
8. Call a workspace memory endpoint with `Authorization: Bearer <token>`.
9. Connect an MCP client to `/mcp` with the same workspace token.
