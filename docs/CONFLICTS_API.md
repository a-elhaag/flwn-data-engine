# Decision conflicts

The Decision Ledger lives in the AI engine. It judges a proposed action against decisions (memories
with `source: "decision"`) and **flags** the ones it contradicts here. A flag stays open until a human
member resolves it. The ledger flags, it never blocks; an agent can never close a flag.

Base: `/workspaces/{ws}/decision-conflicts`. Scopes: `conflicts:read`, `conflicts:write` (flag only).

| Method | Path | Who | Purpose | MCP tool |
| --- | --- | --- | --- | --- |
| POST | `` | `conflicts:write` | Flag. Body: `decision_id` (memory id from recall), `proposal`, `explanation`, optional `similarity` 0-1. Returns 201. An identical open flag (same decision and proposal) is reused | `conflict_flag` |
| GET | `` | `conflicts:read` | List, newest first. `status` (open, accepted, dismissed, resolved), `decision_id`, `limit`, `offset` | `conflicts_list` |
| GET | `/{id}` | `conflicts:read` | One flag with the decision text | - |
| PUT | `/{id}/resolution` | service key, acting as a **human** member | Body: `status` (`accepted` the work is allowed, `dismissed` false alarm, `resolved` the work was changed to fit), optional `note` | - |

Resolving needs the trusted backend: send the service key and `X-Acting-Member-Id` of an active
**human** member. Agent tokens get `403` on this route, and so does an AI member or a call with no
member. A flag that is not open returns `409`. Each flag and resolution leaves an audit event.

Other errors: `404` unknown flag, or `decision_id` is not a memory in this workspace; `422` validation.
