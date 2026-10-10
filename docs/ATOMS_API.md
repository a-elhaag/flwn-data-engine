# Atoms: data-engine contract

For the scheduler/worker implementation sequence, request examples and acceptance checklist,
see the [AI-engine integration guide](ATOMS_AI_ENGINE_INTEGRATION.md).

## Implementation status

The schema, human administration routes, nine MCP tools, run-scoped tokens, live grant
checks and restrictive RLS are implemented. Atoms run on schedules only. This service does
not execute atoms, dispatch schedules, call Composio, compose prompts or store OAuth tokens.

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
use `trigger = 'schedule'` and their own member, version and schedule. Deleting a schedule
nulls the run's schedule reference without changing its workspace or deleting the run.
Existing non-atom trigger types, including `webhook` and `ticket_event`, remain valid;
no generic `event` trigger was added. Scheduled idempotency keys identify the atom and UTC
scheduled slot, not delivery time or an arbitrary caller key. Duplicate starts return the
same run and its original cursor snapshot. Duplicate finishes do not reapply costs or advance
the cursor again. Run versions are pinned even if a human activates a newer version meanwhile.
`approvals.kind` accepts `atom_action`; tool name and arguments belong in `payload`.

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
- `atom_run_finish(status, output?, tokens_in=0, tokens_out=0, cost=0, cursor?, actions_count=0)`
  accepts `succeeded`, `failed` or `canceled`. Reports cumulative totals, not increments.
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

Base: `/workspaces/{workspace_id}/atoms`. These routes require an active human member with
workspace-admin authority (or the personal atom's owner, as applicable), using an `atoms:admin`
bearer token or a service key plus `X-Acting-Member-Id` for that human. Atom tokens cannot contain
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

## Scheduler handoff and tokens

1. A trusted service-key caller reads `GET /workspaces/{workspace_id}/atoms/scheduler-state`
   to discover configuration and schedules, including drafts. The scheduler must select active,
   enabled, due schedules and compute cron slots; this endpoint does not dispatch work.
2. Bootstrap with service-key-only `POST /workspaces/{workspace_id}/atoms/{id}/runs`:
   schedule_id, timezone-aware scheduled_for, idempotency_key, estimated_cost=0,
   estimated_actions=0. Returns a pinned run or its existing idempotent result.
3. Via existing `POST /auth/tokens`, mint with workspace_id, the atom's member_id, atom_id,
   run_id and the needed runtime scopes. Atom TTL defaults to and cannot exceed 900 seconds.
4. Use that bearer token for MCP load/start/resume and existing permitted resource tools.
   Every call revalidates live membership, owner, atom/run state and grants.
5. Finish with cumulative usage and optional cursor. Only finish allows a terminal retry;
   terminal runs cannot continue reading/writing resources. Killed/deleted atoms remain denied.

Runs, tokens and delivery retries are persistent; no OAuth token or Composio execution is
part of this handoff. The scheduler/AI engine must handle token expiry and external retries.

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

The installer also applies `app/db/sql/40_atom_access.sql`. Install helper functions under
a trusted owner able to bypass RLS; runtime roles must not own these helpers or tenant tables.
Protect schema/function ownership and do not expose arbitrary SQL or internal elevation.

Tests use disposable PostgreSQL and cover schema invariants, live grant revocation, private
writes, cross-workspace denial, immutable pinning, soft retention, concurrent-safe lifecycle
accounting, skills/aggregates and real non-superuser REST/MCP integration. No production
migration, cron dispatcher, AI execution or production-scale load test is performed.
