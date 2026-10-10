# Atoms: data-engine contract

For the scheduler/worker implementation sequence, request examples and acceptance checklist,
see the [AI-engine integration guide](ATOMS_AI_ENGINE_INTEGRATION.md).

## Implementation status

The schema, human administration routes, atom MCP tools, run-scoped tokens, live grant
checks and restrictive RLS are implemented. Normal invocations are scheduled; approved parked
work uses a dedicated execution run. This service does not execute atoms, dispatch schedules,
call Composio, compose prompts or store OAuth tokens.

Use a run-scoped atom token, never a regular agent token. An atom member cannot mint an
unbound regular token to escape grant checks. The application database role **must be
non-superuser and NOBYPASSRLS**; administrative database credentials defeat RLS.

## Storage

An atom is an AI member with `agent_kind = 'atom'`. Its configuration lives in `atoms`, with
workspace-local references to its member and, for personal atoms, its owner. The atom's member
identity is permanent, and a schedule cannot be moved to another atom. A workspace atom
has no owner. Atoms start as `draft`; activation requires an active-version pointer belonging
to that same atom. Limits are explicit nonnegative values, not implicit unlimited defaults.

The schema also contains:

- `atom_versions`: numbered prompt, policy and tool snapshots. Only `status` and `eval` can
  change. Direct deletion is rejected; a parent atom/workspace purge can cascade. Rollback
  changes the atom's active-version pointer without rewriting history.
- `skills`: workspace and personal skill versions with an owner required only for personal
  scope. Embeddings and generated full-text search use description and usage guidance, not
  the full instructions. Hybrid search combines lexical and vector ranking, prefers local
  results on ties and returns metadata only; attached skills load their pinned instructions.
- `catalog_skills`: global, atom-readable skills. Only the trusted service may mutate the
  catalog; atoms cannot publish catalog entries or assign verified/official trust.
- `atom_skills`: attachments pin exactly one local or catalog skill and its exact version.
- `atom_schedules`: exactly one of a nonempty `cron` expression or `interval_minutes >= 5`,
  a PostgreSQL-recognized `timezone` (default `UTC`), `enabled` (default true), nullable
  `next_run_at`/`last_run_at`, and an object-valued `cursor` (default `{}`). Cron syntax and
  next-slot dispatch remain the scheduler's responsibility; the database does not execute
  cron expressions. There are no atom event/webhook triggers: monitoring polls on
  a schedule and resumes from its cursor.
- `atom_connections`: one row per `(atom_id, toolkit)`, with explicit `composio_user_id`,
  nullable `composio_account_ref`, required pinned `toolkit_version` (not empty or `latest`),
  `permission_ceiling` and `allowed_tools` (default `[]`). Empty tool lists mean all tools
  within the ceiling; otherwise entries are exact tool slugs. `status` defaults to `unknown`
  and also permits `active`, `expired` and `revoked`; `active` requires an account reference.
  Nullable `status_checked_at` and `status_detail` retain connection-health information.
  `connected_by` references a member in the same workspace. No OAuth tokens are stored.
- `atom_grants`: explicit resource, access level and constraints. A null resource ID represents
  the whole resource type. Wildcard grants are unique as well as resource-specific grants.
  Grant checks are live, fail closed on unsupported constraints and intersect a personal
  atom's grants with its owner's current visibility.

`agent_runs` now accepts `atom_id`, `atom_version_id` and `schedule_id` references,
workspace-unique idempotency keys, input/output token counts and a decimal cost. Atom runs must
use `trigger = 'schedule'` and their own member/version. Normal runs reference their schedule;
approval execution runs have no schedule. Deleting a schedule nulls the run's schedule reference
without changing its workspace or deleting the run.
Existing non-atom trigger types, including `webhook` and `ticket_event`, remain valid;
no generic `event` trigger was added. Scheduled idempotency keys identify the atom and UTC
scheduled slot, not delivery time or an arbitrary caller key. Duplicate starts return the
same run and its original cursor snapshot. Duplicate finishes do not reapply costs or advance
the cursor again. Run versions are pinned even if a human activates a newer version meanwhile.
`approvals.kind` accepts `atom_action`; tool name and arguments belong in `payload`.
An approval's one-time claim grants execution only once. A lost claim response fails closed;
claimed or failed execution runs never automatically requeue. Releasing a stale parent run
must not clear the approval's `execution_run_id` or `claimed_at`. This does not guarantee
exactly-once effects in external systems.

## Confirmed lifecycle and accounting decisions

- **Soft deletion:** atoms, local skills and catalog skills have `deleted_at`. Deleting an
  active atom must also move it out of `active` status. Retained versions, attachments and
  runs preserve history. Listing/search/load/run-start paths filter deleted records.
  Restore endpoints and retention purges are out of scope.
- **Costs:** record model calls in `llm_usage` and maintain the run total in `agent_runs.cost`.
  These are detailed and aggregate views of the same spend, not separate charges. Daily cap
  calculations must use run totals only; reporting must not sum the ledger and run totals
  together. Finish reconciliation is transactional and retry-safe. Bootstrap atomically checks
  recorded totals plus the new estimate and permits only one in-flight run per atom. Supply
  realistic estimated cost/actions: this service cannot interrupt external work that overruns
  its estimate.
- **Grant enforcement:** start with restrictive RLS policies querying indexed grants directly.
  No materialized permissions allow-list. Grants must be rechecked per call for immediate
  revocation. Raw resource access requires read/write; summary grants cannot return raw rows.
  Production-scale latency/load benchmarking remains a deployment responsibility.

## MCP API

| MCP tool | Scope | Responsibility |
| --- | --- | --- |
| `atom_load` | `atoms:read` | Active version, skills, schedules, caps and own grants |
| `atom_run_start` | `atoms:run` | Idempotent scheduled start, current cursor, active/deleted checks and daily caps |
| `atom_run_finish` | `atoms:run` | Final status/output, retry-safe usage accounting and cursor advancement |
| `atom_propose_version` | `atoms:propose` | Self-authored candidate only |
| `atom_approval_create` | `atoms:run` | Park exact external-action payload; notify assigned human |
| `atom_approval_get` | `atoms:run` | Read an approval belonging to the originating/executing run |
| `atom_approval_claim` | `atoms:run` | One-time execution authorization for the dedicated execution run |
| `atom_approval_outcome` | `atoms:run` | Record first execution outcome; repeats return stored outcome |
| `skills_search` | `skills:read` | Hybrid search with personal visibility and trust labels |
| `skill_write` | `skills:write` | Write workspace or personal skills |
| `skill_attach` | `skills:write` | Attach only within connection ceilings and allowed tool slugs |
| `atom_connection_report` | `atoms:run` | Report expired/revoked/unknown connections; pause after repeated failures, never reactivate or widen access |
| `aggregate_read` | `aggregate:read` plus live grant | Aggregate-only access without raw row fields |

Runtime atom/run identity always comes from the token, not tool arguments.

- `atom_load()` returns a flat atom object (no nested `atom` key), plus pinned `version`, live
  `grants`, `schedules` with cursors, `connections` with configuration/health, and enabled
  exact-version `skills` with instructions.
- `atom_run_start(schedule_id, scheduled_for, idempotency_key)` resumes the pre-created,
  token-bound run. It cannot create a different run. The run's `input.cursor` is the snapshot
  from bootstrap, preserved across retries.
- `atom_run_finish(status, output?, tokens_in=0, tokens_out=0, cost="0", cursor?, actions_count=0, model?)`
  accepts `succeeded`, `failed` or `canceled`. Reports cumulative totals, not increments.
  Cost must be a **JSON string**, e.g. `"0.018000"`: 1–6 integer digits and optionally
  1–6 fractional digits; numeric JSON values (including floats), exponents, negative values,
  NaN and infinity are rejected. The persisted value uses six decimal places. `model` is an
  optional nonblank string of at most 200 characters, recorded on the run and reconciled ledger.
  Put safe failure descriptions in `output`; there is no MCP `error` argument.
- `atom_propose_version(instructions, policy={}, tools=[])` creates a `candidate` only.
- `skills_search(query, limit=5)` returns `{"skills": [...]}`; limit is 1–50. Results have
  id/name/version/description/when_to_use/tools_required/trust_tier/community/catalog/scope/score,
  never instructions.
- `skill_write(name, description, when_to_use, instructions, scope, version=1, tools_required=[])`
  requires explicit `workspace` or `personal` scope. Workspace atoms may write workspace skills;
  personal atoms may write only personal skills. Atom-authored skills remain community trust.
  Personal ownership is forced to the caller, not supplied by the tool.
- `skill_attach(skill_id, skill_version, catalog=false)` attaches to the caller's atom only.
  Workspace atoms cannot attach personal skills. Every external tool requirement must fit an
  active connection, ceiling and allowed slug. Load rechecks these conditions; lowering a
  ceiling cannot leave a usable over-privileged attachment.
- `atom_connection_report(connection_id, status, detail?)` accepts expired/revoked/unknown,
  never active or configuration changes.
- `aggregate_read(resource_type, resource_id?, group_by?, trend?)` returns `{"count": n}`,
  optionally `groups: [{value, count}]` and `trend: [{period, count}]`. Only supported safe
  grouping/trend fields are accepted. IDs, titles and raw content are not returned.

Tool failures use the existing MCP error envelope. Model-written prose summaries belong to
**the AI engine**, using these safe aggregate results; this endpoint does not call a model
or pass private raw records to one.

## REST administration

Base: `/workspaces/{workspace_id}/atoms`. These routes require an active, nondeleted human
member with workspace role `owner` or `admin`, using an `atoms:admin` bearer token or a
service key plus `X-Acting-Member-Id` for that human. Personal ownership alone does not grant
administration to a workspace `member` or `viewer`. Atom tokens cannot contain
`atoms:admin`. The service key alone does not impersonate a human administrator. Never attach
it to runtime atom requests: valid service-key authentication takes precedence over REST bearer auth.

| Method and suffix | Body / purpose |
| --- | --- |
| `POST /` | `name`, explicit `max_runs_per_day`, `max_cost_per_day`, `max_actions_per_day`; optional description, kind (workspace/personal), owner_member_id, model_tier (small/medium) |
| `GET /`, `GET /{id}` | Nondeleted configuration and operational state |
| `PATCH /{id}` | Name/description/model tier/caps/status (draft/paused/killed); activation uses its own route |
| `DELETE /{id}` | Soft-delete and stop future execution; 204 |
| `POST /{id}/versions` | instructions, policy (object), tools (array); immutable candidate content |
| `POST /{id}/activate`, `POST /{id}/rollback` | version_id; human-approved pointer change |
| `PUT /{id}/grants` | resource_type, optional resource_id (null = wildcard), level (summary/read/write), constraints (object) |
| `DELETE /{id}/grants/{grant_id}` | Immediate revocation; 204 |
| `POST /{id}/schedules` | Exactly cron or interval_minutes >=5; timezone=UTC, enabled=true, optional aware next_run_at |
| `PUT /{id}/schedules/{schedule_id}` | Replace schedule configuration; cursor is not caller-editable |
| `DELETE /{id}/schedules/{schedule_id}` | Delete schedule, preserve run history; `{deleted: true}` |
| `PUT /{id}/connections` | Upsert by toolkit using connection fields below |
| `PUT /{id}/connections/{connection_id}` | Replace connection configuration by ID |
| `DELETE /{id}/connections/{connection_id}` | Remove connection; `{deleted: true}` |
| `POST /{id}/skills` | skill_id, skill_version, catalog=false |
| `PATCH /{id}/skills/{attachment_id}` | enabled (boolean) |

Connection bodies require toolkit, composio_user_id, toolkit_version, permission_ceiling;
optional composio_account_ref, allowed_tools=[], status=unknown. An active connection requires
an account reference. Versions must be pinned, never `latest`. PUT requests are replacement
configuration, not sparse patches. Unknown request fields are rejected.

Skill administration uses `/workspaces/{workspace_id}/skills/{skill_id}`:
`POST /promote` promotes an accessible personal skill to workspace scope;
`DELETE /` soft-deletes an accessible local skill. Both require human admin authentication as
above and service-level ownership/visibility checks; knowing a private skill ID grants no access.

REST errors use `{"detail": ...}`: 403 denied, 404 unavailable, 422 invalid input. Validation
errors may use FastAPI's structured detail list. Creates return 201 for atoms/versions/schedules;
other successful operations return 200 unless noted.

### Atomizer creation example

Use these headers on each administration request (UUIDs must be real workspace-local IDs):

```http
X-Data-API-Key: <service-key>
X-Acting-Member-Id: <active-human-owner-or-admin-id>
Content-Type: application/json
```

1. `POST /workspaces/{workspace_id}/atoms` (201):

   ```json
   {
     "name": "Daily monitor",
     "description": "Summarize changes",
     "kind": "workspace",
     "model_tier": "small",
     "max_runs_per_day": 24,
     "max_cost_per_day": "2.000000",
     "max_actions_per_day": 100
   }
   ```

   Returns the atom, including `id`, `member_id` and `status="draft"`. For personal atoms,
   use `kind="personal"` and `owner_member_id` of an active human in this workspace.
   Workspace atoms must omit the owner or set it to null.
2. `POST /workspaces/{workspace_id}/atoms/{atom_id}/versions` (201):

   ```json
   {"instructions": "Summarize permitted changes.", "policy": {}, "tools": []}
   ```

   Returns the immutable candidate, including its `id` and numbered `version`.
3. `PUT /workspaces/{workspace_id}/atoms/{atom_id}/grants` (200):

   ```json
   {"resource_type": "project", "resource_id": "<project-id>", "level": "summary", "constraints": {}}
   ```

   Resource types: project, team, collection, channel, folder, memory, meeting, connection.
   Omit `resource_id` or use null for a wildcard. Supported constraints are described below.
4. `POST /workspaces/{workspace_id}/atoms/{atom_id}/schedules` (201):

   ```json
   {"interval_minutes": 15, "timezone": "UTC", "enabled": true, "next_run_at": "2026-10-10T09:00:00Z"}
   ```

   Alternatively use `"cron": "0 9 * * *"` **instead of** interval_minutes. Returns schedule ID.
5. If external tools are needed, `PUT /workspaces/{workspace_id}/atoms/{atom_id}/connections` (200):

   ```json
   {
     "toolkit": "outlook",
     "composio_user_id": "workspace:<workspace-id>",
     "composio_account_ref": "<connected-account-ref>",
     "toolkit_version": "20261001_00",
     "permission_ceiling": "read",
     "allowed_tools": ["LIST_MESSAGES"],
     "status": "active"
   }
   ```

   Account/version/slug values must come from the integration, not these illustrative placeholders.
   Personal connection identity must be the owner's member UUID. No credentials belong in this body.
6. `POST /workspaces/{workspace_id}/atoms/{atom_id}/activate` (200):

   ```json
   {"version_id": "<candidate-version-id>"}
   ```

### Activation and validation rules

- Activation requires a nondeleted, non-killed atom and a non-rejected version belonging to
  that atom/workspace. It sets the version and atom to active, rolling back the previous pointer.
  Rollback accepts only a version already `active` or `rolled_back`.
- **A schedule, grant, connection or skill is not required for activation.** To bootstrap
  scheduled work, an enabled schedule is required. Data access still requires grants; external
  tools require usable connections. Activation is not an authorization bypass.
- All three creation caps are required and nonnegative; zero means no capacity. Money accepts
  up to six fractional digits, with at most twelve total digits. Runtime admission uses recorded
  totals plus the requested estimate. `model_tier` is only `small` or `medium`.
- Names/instructions must contain non-whitespace text (1–20,000 characters). UUIDs must parse.
  Unknown body fields are forbidden; configuration fields other than description cannot be null.
  `PATCH status="active"` is invalid; use activation. Member/owner/kind are not patchable fields.
- Schedules require exactly one cadence, interval >=5 minutes or numeric five-field cron;
  timezone must be recognized, and supplied timestamps must include a UTC offset.
- Connection toolkit/version/slugs must be nonempty without whitespace; version cannot be
  `latest`. Ceilings are read/draft/write/destructive. Active status requires an account reference.
- Domain errors retain 403/404/422 with string `detail` prefixed by `denied:`, `not_found:`,
  `invalid:`, `invalid_state:`, `not_due:`, `busy:` or `cap_exhausted:`. Request-schema validation
  uses FastAPI's 422 `detail` list (`loc`, `msg`, `type`); authentication has its existing envelope.

## Scheduler handoff and tokens

1. A trusted service-key caller reads `GET /workspaces/{workspace_id}/atoms/scheduler-state`.
   Optional `?status=active&enabled=true` filters atom status and schedule enabled state;
   atoms with no matching schedule are omitted, and only matching schedules are returned.
   Without filters it includes drafts/paused/killed atoms and all schedules. Status accepts
   draft/active/paused/killed. This is not a queue; compute due slots in the scheduler.
2. Bootstrap with service-key-only `POST /workspaces/{workspace_id}/atoms/{id}/runs`:
   schedule_id, timezone-aware scheduled_for, idempotency_key, estimated_cost=0,
   estimated_actions=0. Returns a pinned run or its existing idempotent result.
3. Via existing `POST /auth/tokens`, mint with workspace_id, the atom's member_id, atom_id,
   run_id and the needed runtime scopes. Atom TTL defaults to and cannot exceed 900 seconds.
4. Use that bearer token for MCP load/start/resume and existing permitted resource tools.
   Every call revalidates live membership, owner, atom/run state and grants.
5. Finish with cumulative usage and optional cursor. Finish and approval get/claim/outcome
   allow bounded terminal retries, but terminal claims never grant fresh execution permission.
   Terminal runs cannot read/write resources. Killed/deleted atoms remain denied.

Same atom and normalized UTC slot return the same run even if the client `idempotency_key`
changes. Replacement tokens for the same live run coexist with earlier unexpired tokens;
minting does not revoke earlier tokens. All tokens remain subject to live state checks.
Workers capped below 900 seconds need no refresh in v1. Tokens are signed credentials, not
persisted sessions. No OAuth token or Composio execution is part of this handoff.

### Release abandoned runs

Prefer explicit stale release over heartbeats for the bounded v1 worker:

```http
POST /workspaces/{workspace_id}/atoms/{atom_id}/runs/release-stale
X-Data-API-Key: <service-key>
Content-Type: application/json
```

```json
{"older_than_seconds": 900}
```

Age is a required strict integer between 900 and 31,536,000 seconds. Only `running` runs
whose `started_at` is strictly older than server UTC now minus this age are released.
The response is `{"released_run_ids": ["<run-id>"]}`; repeats return an empty array.
Rows become `failed`, receive `ended_at`, and preserve existing output with `_released="stale"`
added. Token counts/cost/actions/ledger entries and schedule cursor/timestamps are untouched.
Terminal, queued and waiting_approval runs are never changed. Atom-row locking serializes
release with bootstrap/finish. Human/worker bearer tokens cannot call this endpoint.

Call before bootstrapping the latest due slot. Released tokens cannot continue resource work;
a repeat bootstrap of the **same slot** still returns the failed run rather than creating a new
one. Release does not cancel an external request already in flight or erase an approval claim.
There is no automatic requeue of an uncertain external action.

## Parked-action approvals

Base: `/workspaces/{workspace_id}/approvals`. There are four additional MCP tools (thirteen
atom tools total). The data engine persists decisions/claims/outcomes; the AI engine chooses
when approval is needed and performs external execution. Approval never expands grants or
connection ceilings.

| Method and suffix | Authentication | Body / result |
| --- | --- | --- |
| `POST` (base path) | Originating run token, `atoms:run` | Creation body below; 201, approval record |
| `GET /ready?limit=100` | Service key only | Approved, unexpired, no execution run; bare array |
| `GET /{approval_id}` | Originating or execution run token, `atoms:run` | Stored approval record |
| `PUT /{approval_id}/decision` | Service key + acting human | `{"status":"approved","comment":"Reviewed"}`; status approved/rejected |
| `POST /{approval_id}/execution-run` | Service key only | `{"estimated_cost":"0","estimated_actions":1}`; dedicated run |
| `POST /{approval_id}/claim` | Execution run token, `atoms:run` | No body; `{"execute":true,"approval":{...}}` once |
| `PUT /{approval_id}/outcome` | Execution run token, `atoms:run` | `{"status":"succeeded","result":{...}}`; stored approval |

Approval responses are flat objects with these fields (nullable fields remain present):

```text
id, workspace_id, created_at, atom_id, title, request_key,
run_id, report_id, kind, requested_by, assigned_to, status,
decided_by, decision_comment, decided_at, expires_at, payload,
execution_run_id, claimed_at, executed_at, execution_status, execution_result
```

Create returns HTTP 201 even on an idempotent replay. Execution bootstrap returns HTTP 200
and a flat run object, not an envelope:

```text
id, workspace_id, created_at, agent_id, atom_id, atom_version_id,
schedule_id, idempotency_key, tokens_in, tokens_out, cost,
trigger, triggered_by, project_id, work_item_id, task_id,
channel_id, meeting_id, thread_id, status, input, output,
error, model, started_at, ended_at
```

UUIDs/timestamps serialize as strings; run cost is a decimal string. Execution input includes
approval_id, approval_payload, estimated_cost and estimated_actions.

Worker creation example (REST body or MCP `atom_approval_create` arguments):

```json
{
  "kind": "atom_action",
  "title": "Send the reviewed report",
  "payload": {
    "tool_slug": "OUTLOOK_SEND",
    "arguments": {"subject": "Daily report"},
    "connection_id": "<connection-id>"
  },
  "assigned_to": "<human-member-id>",
  "request_key": "report:2026-10-10",
  "expires_at": "2026-10-11T09:00:00Z"
}
```

- `requested_by`, originating `run_id` and atom identity come from the token, never arguments.
  Kind defaults to atom_action; plan/pull_request/deploy/action are also accepted.
- Payload requires **exactly** tool_slug (nonblank), arguments (object) and connection_id (UUID).
  Arguments are preserved exactly as JSON, not checked against the external tool's own schema.
  Do not include credentials. REST slug length is at most 300 characters; MCP has no slug limit.
- Title is nonblank, at most 300 characters in REST or 200 in MCP. Request key is optional,
  nonblank and at most 200 characters. Without it a content hash is used, scoped to the
  originating run. Identical retries return the same approval; explicit-key content conflicts fail.
- Assigned human must be in the workspace. Only that exact active, non-viewer human can decide.
  Without an assignee, an active human workspace owner/admin or personal atom owner can decide.
  Decision comment is optional (max 4,000 characters). Same decision returns the first record,
  ignoring changed comments; opposite decisions fail. Authorization/expiry are checked on retries.
- Data engine creates the assigned-human notification **transactionally** with the approval.
  Retries do not duplicate it. No assigned human means no such notification; the AI engine must
  not create a duplicate. Notification delivery/UI remain outside this contract.
- `expires_at` is optional and timezone-aware; null/omitted means no expiry. Ready, decision,
  bootstrap and claim check it server-side. Expiry is **not materialized** into status: get returns
  stored status plus expires_at; the worker must interpret an elapsed expiry even if status still
  says approved. Rejected/expired work never becomes executable. A claimed outcome can still be
  recorded after expiry for audit. Repeating a decision/bootstrap/claim after expiry is rejected.
- Ready returns full approval records in `created_at ASC, id ASC` order; limit 1–200, default 100.
  There is no offset/cursor/count envelope. Bootstrap removes the row from ready eligibility;
  repeat the query to drain later batches. Being ready is not a guarantee of current budget or
  connection eligibility; bootstrap rechecks these conditions.

### Execute later, at most one authorized attempt

Finish the proposing run rather than waiting for a human. After approval, the trusted scheduler
bootstraps an execution run, then mints its token. Bootstrap is idempotent per approval and
creates a dedicated run with `trigger="schedule"`, `schedule_id=null`, key `approval:<id>`, and
immutable `input.approval_id` / `input.approval_payload`. It shares normal budgets and the
one-in-flight-per-atom rule; it does not advance a schedule cursor. The approved payload is
immutable in the database. Dispatch the returned input to the worker; do not use schedule resume
(`atom_run_start`) for this no-schedule run.

MCP signatures (all require `atoms:run`):

- `atom_approval_create(title, payload, request_key=null, kind="atom_action", assigned_to=null, expires_at=null)`
- `atom_approval_get(approval_id)`
- `atom_approval_claim(approval_id)`
- `atom_approval_outcome(approval_id, status, result=null)`

Only the first committed claim returning **`execute=true`** authorizes an external execution
attempt. A repeated claim returns false; if the first response is lost, **fail closed** rather
than executing. Terminal unclaimed runs cannot claim. Expiry or unavailable authorization may
reject a repeat instead of returning false. The scheduler never clears `execution_run_id` or
`claimed_at`; failed/crashed/claimed/uncertain runs are not automatically requeued.

Fresh bootstrap and first claim recheck the connection belongs to this atom/workspace, is active,
and allows the exact slug. The engine must still enforce connection ceilings and applicable
resource grants; the data engine does not classify arbitrary external tool semantics.

Record `succeeded`, `failed` or `unknown` outcome with a safe result object. First outcome wins;
repeats return it unchanged. Then call `atom_run_finish` with cumulative usage. Outcome does not
finish the run or charge usage. Approval get/claim/outcome accept bounded terminal-run retries
with still-valid tokens and live identity checks; terminal claim never grants fresh permission.
This provides durable at-most-once authorization, **not guaranteed exactly-once external effects**.
Unknown effects need provider/human reconciliation, not an automatic retry/reset endpoint.

Existing databases must rerun the additive upgrade before rolling out this contract. The new
approval columns and `app/db/sql/50_atom_approvals.sql` protect immutable payloads, bindings and
claims; legacy approval rows retain their previous behavior.

## Connection integration boundaries

Only a human admin may change a connection's ceiling, allowed tools or pinned version.
`atom_connection_report` updates health fields for the caller's connection, never sets
`active`, and pauses the atom after failures in **three distinct runs**. Repeated reports
within one run do not inflate this count. A human reconnection resets the failure window.
This gives atoms no general permission to edit status, budgets or permissions.

Personal connections use the owner's member UUID as `composio_user_id`. The proposed workspace
identity is `workspace:{workspace_id}`, pending integration confirmation. For now storage
requires an explicit nonempty identity rather than synthesizing an unconfirmed convention.
The AI engine, not this repository, creates Composio direct-tool sessions, applies the stored
filters, handles organization-wide throttling and observes external account failures.

Ceilings map to cumulative tags: `read` allows `readOnlyHint`; `draft` adds `createHint`;
`write` adds `updateHint`; `destructive` adds `destructiveHint`. Pinning plus allowed slugs
must be honored alongside those tags. Composio meta tools and account-connection flows are
not available to atoms.

## Live access and private writes

Supported constraints are `labels` (array of strings, all required) and `since_days`
(integer 0–365000, applied to created_at). Malformed/unknown constraints deny access.
Explicit and wildcard grants apply to canonical resources and known descendants; unknown
parent mappings deny. Grant levels do not bypass a personal owner's visibility.

Existing memory remember/batch paths force atom writes to `scope=agent`, with owner and
author equal to the atom member. This is not an access bypass: a live applicable write grant
is still required. Deduplication cannot modify shared or read-only memories. Ordinary human
memory defaults are unchanged. Pausing denies raw data, while report/finish remain available;
killing or soft-deleting denies all runtime access.

## Installation and verification

Postgres 15+ is required for nulls-not-distinct wildcard grant uniqueness. Use
`python -m app.db.install` for a fresh database; it creates tables and installs the atom
invariant triggers from `app/db/sql/30_atoms.sql`, but does not alter existing tables.

For an existing legacy database, take a backup and run this upgrade with admin credentials
**before rolling out the new app image**:

```sh
python -m app.db.upgrade_atoms --app-role flwn_app
```

Set `DATABASE_ADMIN_URL` explicitly; the upgrade does not fall back to `DATABASE_URL`.
`--app-role` must match the role in the runtime `DATABASE_URL`, not the admin role.
The additive, transactional, idempotent upgrade uses bounded locks and preserves existing
rows and the runtime role's login/password. **Never use `--reset` on an existing database.**
If the app image is rolled back, leave the additive schema in place; do not drop the new
columns or tables. This procedure does not establish that a production upgrade has run.

The installer also applies `app/db/sql/40_atom_access.sql` and `app/db/sql/50_atom_approvals.sql`.
Existing databases with the earlier Atoms release must also rerun the upgrade to add approval
columns/invariants. Install helper functions under a trusted owner able to bypass RLS; runtime
roles must not own these helpers or tenant tables.
Protect schema/function ownership and do not expose arbitrary SQL or internal elevation.

Tests use disposable PostgreSQL and cover schema invariants, live grant revocation, private
writes, cross-workspace denial, immutable pinning, soft retention, concurrent-safe lifecycle
accounting, skills/aggregates and real non-superuser REST/MCP integration. No production
migration, cron dispatcher, AI execution or production-scale load test is performed.
