# CRUD status

Which parts of the data model can be created, read, updated and deleted through the REST API,
which can only partly, and which have a table but no API yet. The endpoint reference itself is
the Swagger UI at `/docs`. Last checked against the routes in `app/api/routes/`.

Legend: **C** create, **R** read (list and get), **U** update, **D** delete.

## Complete

| Area | Resource | C | R | U | D | Notes |
|---|---|---|---|---|---|---|
| Identity | Users | yes | yes | yes | yes | Global accounts, backend only. Delete is a soft delete. |
| Identity | Workspaces | yes | yes | yes | yes | Delete is a soft delete. |
| Identity | Members | yes | yes | yes | yes | |
| Identity | Teams | yes | yes | yes | yes | Team members are replaced as a whole list. |
| Planning | Projects | yes | yes | yes | yes | |
| Planning | Work items | yes | yes | yes | yes | |
| Planning | Sprints | yes | yes | yes | yes | Delete is hard; its tasks go back to the backlog. |
| Planning | Tasks | yes | yes | yes | yes | The "tickets". Numbered per project (`DATA-1`). Also by key, with filters, and per member. |
| Collaboration | Comments | yes | yes | yes | yes | On tasks, work items, projects, project updates, sprints and docs. |
| Collaboration | Docs | yes | yes | yes | yes | |
| Collaboration | Doc blocks | yes | yes | yes | yes | |
| Collaboration | Channels | yes | yes | yes | archive | Delete archives the channel. Members are replaced as a list. |
| Collaboration | Messages | yes | yes | yes | yes | Delete is soft. |
| Atoms | Atoms | yes | yes | yes | yes | |

## Partial

| Resource | What exists | What is missing |
|---|---|---|
| Memory | create, batch create, list, get, revise, pin, delete, purge, stats, cleanup, organize | Nothing structural. Updates are limited to revise and pin. |
| Files | start upload, complete, list, get, download, reindex, delete, search | Update (rename, move to a folder). |
| Meetings | create, list, get, update, invite, consent, recordings | Delete or cancel-and-remove. Meetings can only be set to `canceled`. |
| Transcripts | replace (`PUT`), get | Edit or delete a single segment. |
| Decision conflicts | flag, list, get, resolve | Delete. |
| Approvals | create, get, decide, claim, outcome, list of ready ones | List of all approvals (filter by status or atom), delete. |
| Skills | promote, delete | Create, list, get and update over REST. Skills are created through the `skill_write` MCP tool. |
| Atom schedules | create, replace, delete | List and get on their own (they are read through the atom). |
| Atom grants | create or replace, delete | List and get on their own. |
| Atom connections | create or replace, delete | List and get on their own. |
| Atom skill attachments | attach, enable or disable | List, get and detach. |
| Atom versions | propose, activate, roll back | List and get on their own. |

## Not implemented (table exists, no API)

| Area | Tables | Why it matters |
|---|---|---|
| Planning | Sprints, milestones | Tasks already carry `sprint_id` and `milestone_id`, but neither can be created. |
| Planning | Labels (and task and work-item label links) | No way to tag tickets. |
| Planning | Backlogs, task relations (blocks, duplicates), project updates, saved views | |
| Docs | Collections, doc versions, links | Docs already carry `collection_id`, but collections cannot be created. |
| Files | Folders | Files already carry `folder_id`, but folders cannot be created. |
| Social | Reactions, favorites, subscriptions, notifications | |
| Identity | Invitations | Members cannot be invited by email. |

Highest value for a ticketing product: sprints, milestones, labels and invitations.

## Other work that is specified but not built

[ATOMIZER_AND_ATOM_TOOLS_TASK.md](ATOMIZER_AND_ATOM_TOOLS_TASK.md) describes the atom workspace-read
MCP tools (`workspace_list`, `workspace_get`, `workspace_search`) and the Atomizer support
(grantable-resource catalog, dry-run, one-shot build). None of those tools exist in
`app/mcp_tools/server.py` yet.
