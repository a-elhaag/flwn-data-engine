# Decision conflicts

The Decision Ledger lives in the AI engine. It judges a proposed action against decisions (memories
with `source: "decision"`) and **flags** the ones it contradicts here. A flag stays open until a human
member resolves it. The ledger flags, it never blocks; an agent can never close a flag.

Base: `/workspaces/{ws}/decision-conflicts`. Endpoints, parameters and schemas are in the Swagger UI at
`/docs`. Scopes: `conflicts:read`, `conflicts:write` (flag only). MCP tools: `conflict_flag` and
`conflicts_list`.

- Flagging an identical open flag (same decision and proposal) reuses the existing one.
- Statuses: `open`, then `accepted` (the work is allowed), `dismissed` (false alarm) or `resolved`
  (the work was changed to fit).
- Resolving is service key only, acting as a **human** member.

Resolving needs the trusted backend: send the service key and `X-Acting-Member-Id` of an active
**human** member. Agent tokens get `403` on this route, and so does an AI member or a call with no
member. A flag that is not open returns `409`. Each flag and resolution leaves an audit event.

Other errors: `404` unknown flag, or `decision_id` is not a memory in this workspace; `422` validation.
