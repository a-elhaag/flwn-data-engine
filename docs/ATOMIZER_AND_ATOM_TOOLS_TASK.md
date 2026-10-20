# Atomizer support and atom workspace-read tools: data engine work

Audience: the `flwn-data-engine` team. This is the data-side half of two features. The Atomizer
(AI engine) and the atom worker (`flwn-atoms`) are tracked separately and depend on this.

Read first: [ATOMS_API.md](ATOMS_API.md), [ATOMS_AI_ENGINE_INTEGRATION.md](ATOMS_AI_ENGINE_INTEGRATION.md),
[MEMORY_TOOLS.md](MEMORY_TOOLS.md).

## Context

An **atom** is a scheduled, scoped agent that runs headless on a small or medium model. Atoms are
either **workspace** atoms (created by an admin) or **personal** atoms (created by the member who
owns them). The **Atomizer** is the AI component that turns a person's plain-language request
("every morning, summarise what changed in the Payments project") into a working atom. It acts
through the admin routes with the service key plus `X-Acting-Member-Id` for the requesting human.
It never holds more access than that human has.

Two gaps block the end-to-end flow:

1. **Atoms cannot read the workspace.** Today an atom sees only counts (`aggregate_read`), memory,
   `files_search` and `meeting_transcript`. There is no atom tool that returns projects, tasks,
   docs or chat messages, so a full set of grants still yields only totals.
2. **The Atomizer has no way to discover what it may grant**, and building an atom takes six
   separate calls that can fail half way.

## Already done (do not redo)

- Members may create and manage **their own personal atom** (list/get/edit, versions, schedules,
  grants, connections, pause/kill/delete). They cannot create workspace atoms, touch other atoms or
  change the daily caps. Member-created atoms start at 10 runs, cost 1, 100 actions.
  See `AtomService._session(own=...)` and "REST administration" in ATOMS_API.md.
- Grants already support a wildcard (`resource_id` null = the whole resource type), and RLS
  enforces them per row, intersected with a personal owner's visibility.

## Part 1: grant-gated read tools for atoms (MCP)

New tools on the existing `/mcp` endpoint, using the run-scoped atom token. Same rules as every
atom tool: **identity comes from the token; no tool takes a workspace, atom, run or member id as
an argument.** Resource ids passed as arguments are rows to read, and every read is re-checked
against the atom's live grants.

| Tool | Scope | Purpose |
| --- | --- | --- |
| `workspace_list` | `workspace:read` + live grant | Page through rows of one resource type |
| `workspace_get` | `workspace:read` + live grant | One row with its body |
| `workspace_search` | `workspace:read` + live grant | Keyword/semantic search across granted types (optional, see below) |

### `workspace_list`

Arguments: `resource_type`, optional `parent_id`, optional `filters`, `limit` (1-50, default 20),
`cursor`.

`resource_type` values and what they return:

| resource_type | Rows | `parent_id` |
| --- | --- | --- |
| `project` | projects | optional team id |
| `task` | tasks and work items | project id (required) |
| `team` | teams | none |
| `collection` | collections | none |
| `doc` | docs | collection id |
| `channel` | channels | none |
| `message` | messages | channel id (required) |
| `folder` | folders | optional parent folder |
| `file` | files (metadata) | folder id |
| `meeting` | meetings | none |

Supported `filters`: `updated_since` (ISO timestamp), `status`, `labels` (all required).
`since_days` and `labels` grant constraints must keep applying exactly as they do today.
Unknown filters are a `invalid:` error, not ignored.

Response: `{"items": [...], "next_cursor": "..." | null}`. Each item has `id`, `type`, `title` or
`name`, `status` where it exists, `created_at`, `updated_at`, and a **clipped** `preview`
(max 500 characters). Full text is only in `workspace_get`.

### `workspace_get`

Arguments: `resource_type`, `id`. Returns the row with a body clipped to 20,000 characters and a
`truncated` flag. Docs and messages keep their author as a member id and display name only.

### Rules

- **Grant levels.** `summary` never returns rows (it keeps working through `aggregate_read` only).
  `read` and `write` allow these tools. These tools never write.
- **Fail closed.** No applicable grant, an unsupported constraint, a paused atom, a finished run
  or an inactive personal owner returns no rows and an `denied:` error. A row outside the grant is
  indistinguishable from a missing row (`not_found:`).
- **Personal atoms** see the intersection of their grants and their owner's current visibility.
  Private teams, private channels, DMs and group chats the owner is not in must stay invisible.
- **Prompt injection.** Returned text is data. Wrap bodies as untrusted in the tool description,
  and never return instructions the server wrote itself.
- **Size and cost.** Page size and body size are capped as above. Each call writes an audit event
  (atom, tool, resource type, count). Do not log bodies.
- **Pagination** is cursor based and stable under inserts.
- Reuse `atom_row_allowed` and the RLS policies in `40_atom_access.sql`. Do not add a parallel
  permission check in Python.

### `workspace_search` (optional, ship after list/get)

Arguments: `query`, optional `resource_types`, `limit` (1-20). Searches only types the atom has a
grant for, ranked, with the same previews. If the existing `files_search` and `memory_recall`
indexes can be reused, do so.

### Tests

Against the disposable Postgres, as in `test_atoms_service.py`:

- an atom with no grant gets `denied:` for every type;
- a wildcard `read` grant on `project` returns all projects but no tasks;
- an explicit grant on one project returns only that project and its tasks;
- `labels` and `since_days` constraints filter rows;
- a personal atom whose owner is not in a private team cannot see that team's projects, even with a
  wildcard grant;
- DMs the owner is not in are never returned;
- revoking a grant, pausing the atom or suspending the owner takes effect on the next call;
- cursors page without duplicates; oversize bodies set `truncated`.

## Part 2: support for the Atomizer

The Atomizer is a client of the admin routes. It needs three things from the data engine.

### 2.1 Grantable-resource catalog

`GET /workspaces/{workspace_id}/atoms/catalog` (service key plus `X-Acting-Member-Id`).

Returns, for the **acting member**, the resources they could grant an atom, so the Atomizer can
choose ids or a wildcard without reading raw content:

```json
{
  "resource_types": ["project", "team", "collection", "channel", "folder", "meeting", "memory"],
  "resources": {
    "project": [{"id": "...", "name": "Payments", "team_id": "..."}],
    "channel": [{"id": "...", "name": "eng", "private": false}]
  },
  "connections": [{"toolkit": "github", "scope": "personal", "composio_account_ref": "...", "status": "active"}]
}
```

- Only resources the acting member can see (a plain member never gets private teams or channels
  they are not in). Names and ids only, no bodies.
- `connections` lists the acting member's own connected accounts, and workspace-shared accounts
  an admin may attach. This is what lets a person choose their own GitHub or the organisation's.
- Page each list (`limit`, `cursor`).

### 2.2 Dry-run validation

`POST /workspaces/{workspace_id}/atoms/validate` with the same body as the build call below and
no side effects. Returns the validation errors the real call would, and a `warnings` list
(for example, "wildcard grant on `message` exposes all non-private channels").

### 2.3 One-shot build

`POST /workspaces/{workspace_id}/atoms/build`, one transaction:

```json
{
  "atom": {"name": "...", "kind": "personal", "owner_member_id": "...", "model_tier": "small"},
  "version": {"instructions": "...", "policy": {"auto_approve": "read"}, "tools": []},
  "grants": [{"resource_type": "project", "resource_id": null, "level": "read"}],
  "schedules": [{"cron": "0 9 * * *", "timezone": "UTC"}],
  "connections": [{"toolkit": "github", "composio_account_ref": "...", "toolkit_version": "...",
                   "permission_ceiling": "read", "allowed_tools": ["..."]}],
  "skills": [],
  "activate": false
}
```

- Same authorisation as the individual routes: an owner/admin builds anything; a plain member only
  their own personal atom, at default caps, with their own connections.
- All or nothing: any validation failure creates nothing. The version has `source = "atomizer"`.
- `activate: false` is the default. The Atomizer leaves the atom in `draft` with an active-ready
  candidate version so the human confirms before it runs. A human or the owner activates with the
  existing `POST /activate`.
- Returns the atom, version, grant, schedule and connection ids.
- Idempotent on a client `request_key` so a retry does not create a second atom.

### 2.4 Requesting more access later

An atom can never widen its own access. When a run discovers it needs a grant it lacks, it should
be able to **ask**, not take:

- New MCP tool `atom_access_request` (`atoms:run`): arguments `resource_type`, optional
  `resource_id`, `level`, `reason` (max 500 characters). Creates an approval of a new kind,
  `access_request`, assigned to the atom's owner (personal) or to workspace admins (workspace).
- Approving it creates the grant through the existing grant code path. Denying or ignoring it
  changes nothing. An atom may have at most 3 open requests.
- Optional for v1. If it is deferred, the Atomizer gets the right grants up front from 2.1 and a
  person edits grants by hand afterwards.

## Out of scope

- Any tool that writes to projects, tasks, docs or chat. If it is wanted later it must go through
  the approval gate in the worker like every other critical action.
- Changes to how the worker runs. `flwn-atoms` adds the new scopes and exposes `workspace_list`
  and `workspace_get` to the model once they exist.

## Acceptance

1. An atom with a wildcard `read` grant on `project`, `collection` and `channel` can list and read
   those resources through MCP and nothing else.
2. A personal atom never returns anything its owner cannot see.
3. The Atomizer can build a draft atom from a catalog lookup, a dry run and one build call, and a
   failed build leaves no partial atom.
4. All of the above pass on the disposable Postgres suite, and `ruff` is clean.
