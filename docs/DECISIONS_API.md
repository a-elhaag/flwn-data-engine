# Decisions API (Decision Ledger)

Records the team's decisions and checks proposed work against them. A decision is stored as a
memory of kind `decision` (so it is searchable and recalled like any memory, while `active`) plus a
structured row: title, rationale, the files it covers (`files_scope`), area and status.

The ledger **flags, people decide**. A conflict is never a block; it stays open until a human
member resolves it. Agents cannot resolve conflicts.

Scopes: `decisions:read` (list, get, check), `decisions:write` (record, edit, supersede, resolve,
and `check` with `record=true`). Tokens minted with no scopes get read only. The acting member comes
from the token or `X-Acting-Member-Id` (see [MEMORY_API.md](MEMORY_API.md#who-is-acting)).

## Routes

| Method | Path | Scope | Result |
| --- | --- | --- | --- |
| POST | `/workspaces/{ws}/decisions` | write | Record (201). A near-identical active decision is returned with `deduplicated: true` |
| GET | `/workspaces/{ws}/decisions` | read | Page. `status`, `area`, `team_id`, `project_id`, `q`, `limit`, `offset` |
| GET | `/workspaces/{ws}/decisions/{id}` | read | One decision (`id` is also its memory id) |
| PATCH | `/workspaces/{ws}/decisions/{id}` | write | Edit title, rationale, `files_scope`, area, or move between `proposed`, `active`, `rejected` |
| POST | `/workspaces/{ws}/decisions/{id}/supersede` | write | New decision replaces it; the old one is kept, marked `superseded`, linked (201) |
| POST | `/workspaces/{ws}/decisions/check` | read | Does a proposal go against an active decision? |
| GET | `/workspaces/{ws}/decisions/conflicts` | read | Flagged conflicts. `status`, `decision_id`, `task_id` |
| POST | `/workspaces/{ws}/decisions/conflicts/{id}/resolve` | write, human member | `accepted`, `dismissed` or `resolved`, with a note |

Errors: `404` unknown decision or conflict, `409` change not allowed from the current status
(superseded decisions are frozen; a conflict resolves once), `403` resolver is not a human member.

## Checking a proposal

`POST /decisions/check` with `{"proposed_action": "...", "record": false}` returns

```json
{"conflict": true,
 "conflicting_decision": {"id": "...", "text": "..."},
 "reasoning": "...",
 "conflicts": [{"id": null, "status": "unrecorded", "decision": {...}, "explanation": "..."}],
 "checked": 3, "judged": true}
```

`conflict`, `conflicting_decision` and `reasoning` keep the shape the AI engine's Decision Ledger
already uses (`record_decision(title, rationale, files_scope, agent)` maps to the record route,
`check_conflict(proposed_action, agent)` to this one). Optional `team_id`/`project_id` narrow the
decisions considered to the workspace-wide ones plus that team's/project's.

How it works: candidate decisions come from vector and keyword search (fused, then reranked); the
top five are shown to the model, which answers per decision as strict JSON. Anything malformed means
no conflict. Decision and proposal text are wrapped as untrusted data. If the model is unreachable the
response has `judged: false` and flags nothing. Writes nothing by default; `record: true` saves each
conflict as an open flag (asking again about the same proposal reuses the open flag).

Only `active` decisions are checked and recalled; `proposed`, `rejected` and `superseded` are kept
but hidden.

## MCP tools

`decision_record`, `decision_check` (read only, never persists), `decisions_list`.
