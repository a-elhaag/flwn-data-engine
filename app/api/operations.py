"""Hand-written Swagger summary and description per endpoint, keyed by "METHOD path".

Applied to the OpenAPI document in one place; a test fails if a route is missing here.
"""

W = "/workspaces/{workspace_id}"

OPERATIONS: dict[str, tuple[str, str]] = {
    # System
    "GET /healthz": ("Liveness check", "Returns 200 while the process is up. No auth."),
    "GET /readyz": (
        "Readiness check",
        "Checks the database and storage. 503 when a dependency is down. Needs the service key.",
    ),
    "POST /auth/tokens": (
        "Mint a workspace token",
        "Trusted backend only. Returns a signed token locked to one workspace, limited to the requested scopes. Without `scopes` it can only read memory and files.",
    ),
    # Memory
    f"POST {W}/memories": (
        "Remember",
        "Store one memory. Near-identical text is deduplicated and `deduplicated` is true. Needs `memory:write`.",
    ),
    f"GET {W}/memories": (
        "Browse memories",
        "Newest first, cursor-paginated. Filter by `source` or `agent`; superseded memories are hidden unless `include_superseded`. Needs `memory:read`.",
    ),
    f"DELETE {W}/memories": (
        "Purge all memories",
        "Deletes every memory in the workspace. Irreversible. Pass `confirm` equal to the workspace id. Trusted backend only.",
    ),
    f"POST {W}/memories/batch": (
        "Remember many",
        "Store up to 50 memories in one call. Results keep the request order. Needs `memory:write`.",
    ),
    f"POST {W}/memories/recall": (
        "Recall",
        "Semantic search over memory, ranked by score. Optionally limit to some `sources`. Needs `memory:read`.",
    ),
    f"GET {W}/memories/stats": (
        "Memory statistics",
        "Counts of total, active, superseded, pinned and never-recalled memories, plus a per-source breakdown.",
    ),
    f"POST {W}/memories/cleanup": (
        "Clean up memories",
        "Delete stale memories using `retention_days`. Use `dry_run` to preview. Trusted backend only.",
    ),
    f"POST {W}/memories/organize": (
        "Organize memories",
        "Cluster similar memories and merge or supersede duplicates. Use `dry_run` to preview. Trusted backend only.",
    ),
    f"POST {W}/sprint-completed": (
        "Sprint completed",
        "Runs the end-of-sprint cleanup. Same result shape as cleanup. Trusted backend only.",
    ),
    f"GET {W}/memories/{{point_id}}": (
        "Open a memory",
        "Fetch one memory. 404 if it is not in this workspace.",
    ),
    f"PATCH {W}/memories/{{point_id}}": (
        "Revise a memory",
        "Replace the text of a memory. Needs `memory:write`.",
    ),
    f"DELETE {W}/memories/{{point_id}}": (
        "Forget a memory",
        "Delete one memory. Returns 204. Needs `memory:delete`.",
    ),
    f"PUT {W}/memories/{{point_id}}/pin": (
        "Pin or unpin a memory",
        "Set `pinned` true or false. Needs `memory:write`.",
    ),
    # Files
    f"POST {W}/files": (
        "Start an upload",
        "Registers the file and returns a one-time `upload_url`. PUT the bytes there, then call **Complete upload**. Needs `files:write`.",
    ),
    f"GET {W}/files": (
        "List files",
        "Filter by `kind`, `project_id` or `folder_id`. Needs `files:read`.",
    ),
    f"POST {W}/files/search": (
        "Search file contents",
        "Semantic search over indexed file text. Returns the best matching chunks with page and heading. Needs `files:read`.",
    ),
    f"POST {W}/files/{{file_id}}/reindex": (
        "Reindex a file",
        "Re-run indexing, for example after an indexing error. Needs `files:write`.",
    ),
    f"POST {W}/files/{{file_id}}/complete": (
        "Complete an upload",
        "Call after the bytes are uploaded. Needs `files:write`.",
    ),
    f"GET {W}/files/{{file_id}}": (
        "Get a file",
        "Metadata and indexing status for one file. Needs `files:read`.",
    ),
    f"DELETE {W}/files/{{file_id}}": (
        "Delete a file",
        "Deletes the file. Returns 204. Needs `files:delete`.",
    ),
    f"GET {W}/files/{{file_id}}/download": (
        "Get a download link",
        "Returns a short-lived signed URL. Needs `files:read`.",
    ),
    # Meetings
    f"POST {W}/meetings": (
        "Create a meeting",
        "Creates a meeting with optional participants. The caller becomes host. Needs `meetings:write`.",
    ),
    f"GET {W}/meetings": (
        "List meetings",
        "Filter by `status` or `project_id`. Needs `meetings:read`.",
    ),
    f"GET {W}/meetings/{{meeting_id}}": (
        "Get a meeting",
        "Includes each participant's consent status. Needs `meetings:read`.",
    ),
    f"PATCH {W}/meetings/{{meeting_id}}": (
        "Update a meeting",
        "Rename, reschedule, or set `live`, `ended` or `canceled`. Needs `meetings:write`.",
    ),
    f"POST {W}/meetings/{{meeting_id}}/participants": (
        "Invite participants",
        "Adds workspace members. Each starts with consent pending. Needs `meetings:write`.",
    ),
    f"PUT {W}/meetings/{{meeting_id}}/consent": (
        "Answer recording consent",
        "Grant or decline consent to be recorded. Needs `meetings:write`.",
    ),
    f"POST {W}/meetings/{{meeting_id}}/recordings": (
        "Start a recording",
        "Registers a recording and returns an upload ticket. Needs `meetings:write`.",
    ),
    f"PUT {W}/meetings/{{meeting_id}}/transcript": (
        "Ingest a transcript",
        "Replaces the meeting transcript with the given timed segments. Needs `meetings:write`.",
    ),
    f"GET {W}/meetings/{{meeting_id}}/transcript": (
        "Get the transcript",
        "Returns segments in order, paginated. Needs `meetings:read`.",
    ),
    # Decision conflicts
    f"POST {W}/decision-conflicts": (
        "Flag a conflict",
        "Record that a proposal contradicts an earlier decision memory. Needs `conflicts:write`.",
    ),
    f"GET {W}/decision-conflicts": (
        "List conflicts",
        "Filter by `status` or `decision_id`. Needs `conflicts:read`.",
    ),
    f"GET {W}/decision-conflicts/{{conflict_id}}": (
        "Get a conflict",
        "Includes the decision text and the flagged proposal. Needs `conflicts:read`.",
    ),
    f"PUT {W}/decision-conflicts/{{conflict_id}}/resolution": (
        "Resolve a conflict",
        "Mark it `accepted`, `dismissed` or `resolved`, with an optional note.",
    ),
    # Atoms
    f"POST {W}/atoms": (
        "Create an atom",
        "An atom is a scheduled agent with daily run, cost and action budgets. Starts as a draft. Trusted backend only.",
    ),
    f"GET {W}/atoms": ("List atoms", "Trusted backend only."),
    f"GET {W}/atoms/scheduler-state": (
        "Scheduler state",
        "What the scheduler needs to decide what to run next. Trusted backend only.",
    ),
    f"POST {W}/atoms/{{atom_id}}/runs/release-stale": (
        "Release stale runs",
        "Releases runs stuck for longer than `older_than_seconds`. Trusted backend only.",
    ),
    f"GET {W}/atoms/{{atom_id}}": (
        "Get an atom",
        "Returns one atom. Trusted backend only.",
    ),
    f"PATCH {W}/atoms/{{atom_id}}": (
        "Configure an atom",
        "Rename it, change budgets or model tier, or set `draft`, `paused` or `killed`. Null values are rejected. Trusted backend only.",
    ),
    f"DELETE {W}/atoms/{{atom_id}}": ("Delete an atom", "Returns 204. Trusted backend only."),
    f"POST {W}/atoms/{{atom_id}}/versions": (
        "Propose a version",
        "Adds a new instruction version. It does nothing until activated. Trusted backend only.",
    ),
    f"POST {W}/atoms/{{atom_id}}/activate": (
        "Activate a version",
        "Makes the version live and the atom active. Trusted backend only.",
    ),
    f"POST {W}/atoms/{{atom_id}}/rollback": (
        "Roll back to a version",
        "Re-activates an earlier version. Trusted backend only.",
    ),
    f"PUT {W}/atoms/{{atom_id}}/grants": (
        "Grant access",
        "Gives the atom `summary`, `read` or `write` access to one resource. Trusted backend only.",
    ),
    f"DELETE {W}/atoms/{{atom_id}}/grants/{{grant_id}}": (
        "Revoke a grant",
        "Returns 204. Trusted backend only.",
    ),
    f"POST {W}/atoms/{{atom_id}}/schedules": (
        "Add a schedule",
        "Give exactly one of `cron` or `interval_minutes` (at least 5). Trusted backend only.",
    ),
    f"PUT {W}/atoms/{{atom_id}}/schedules/{{schedule_id}}": (
        "Replace a schedule",
        "Trusted backend only.",
    ),
    f"DELETE {W}/atoms/{{atom_id}}/schedules/{{schedule_id}}": (
        "Delete a schedule",
        "Trusted backend only.",
    ),
    f"PUT {W}/atoms/{{atom_id}}/connections/{{connection_id}}": (
        "Replace a connection",
        "Updates a toolkit connection in place. `toolkit_version` must be pinned, not `latest`. Trusted backend only.",
    ),
    f"DELETE {W}/atoms/{{atom_id}}/connections/{{connection_id}}": (
        "Delete a connection",
        "Trusted backend only.",
    ),
    f"PUT {W}/atoms/{{atom_id}}/connections": (
        "Add or update a connection",
        "Links the atom to a Composio toolkit account with a permission ceiling and an allow-list of tools. Trusted backend only.",
    ),
    f"POST {W}/atoms/{{atom_id}}/skills": (
        "Attach a skill",
        "Attaches a promoted skill version to the atom. Trusted backend only.",
    ),
    f"PATCH {W}/atoms/{{atom_id}}/skills/{{attachment_id}}": (
        "Enable or disable a skill",
        "Trusted backend only.",
    ),
    f"POST {W}/atoms/{{atom_id}}/runs": (
        "Start a run",
        "Starts a run for a schedule slot. `idempotency_key` guards against duplicate runs. Fails if a daily budget would be exceeded. Trusted backend only.",
    ),
    # Approvals
    f"POST {W}/approvals": (
        "Request an approval",
        "An atom asks a human to approve one tool call before it runs. Needs `atoms:run`.",
    ),
    f"GET {W}/approvals/ready": (
        "List approved, unclaimed approvals",
        "What the executor should run next. Trusted backend only.",
    ),
    f"GET {W}/approvals/{{approval_id}}": (
        "Get an approval",
        "Status, payload and decision. Needs `atoms:run`.",
    ),
    f"PUT {W}/approvals/{{approval_id}}/decision": (
        "Approve or reject",
        "A human decides.",
    ),
    f"POST {W}/approvals/{{approval_id}}/execution-run": (
        "Bootstrap the execution run",
        "Creates the run that will carry out an approved action. Trusted backend only.",
    ),
    f"POST {W}/approvals/{{approval_id}}/claim": (
        "Claim an approval",
        "Locks an approved action so only one executor runs it. Needs `atoms:run`.",
    ),
    f"PUT {W}/approvals/{{approval_id}}/outcome": (
        "Report the outcome",
        "Record `succeeded`, `failed` or `unknown` and the tool result. Needs `atoms:run`.",
    ),
    # Skills
    f"POST {W}/skills/{{skill_id}}/promote": (
        "Promote a skill",
        "Makes a local skill available to the whole workspace. Trusted backend only.",
    ),
    f"DELETE {W}/skills/{{skill_id}}": ("Delete a skill", "Soft delete. Trusted backend only."),
    # Workspaces
    "POST /workspaces": (
        "Create a workspace",
        "Trusted backend only. `slug` is lowercase letters, digits and hyphens.",
    ),
    "GET /workspaces": (
        "List workspaces",
        "Paginated with `limit` and `offset`. Trusted backend only.",
    ),
    f"GET {W}": ("Get a workspace", "Trusted backend only."),
    f"PATCH {W}": ("Update a workspace", "Only the fields you send change. Trusted backend only."),
    f"DELETE {W}": (
        "Delete a workspace",
        "Deletes the workspace. Irreversible. Trusted backend only.",
    ),
    # Identity
    f"POST {W}/members": (
        "Create a member",
        "`type` is `HUMAN` (needs name and email) or `AI` (needs name and `agent_kind`). Role is `owner`, `admin`, `member` or `viewer`.",
    ),
    f"GET {W}/members": ("List members", "Paginated with `limit` and `offset`."),
    f"GET {W}/members/{{member_id}}": ("Get a member", "Returns one member."),
    f"PATCH {W}/members/{{member_id}}": (
        "Update a member",
        "Change role, status, name, email or settings.",
    ),
    f"DELETE {W}/members/{{member_id}}": ("Remove a member", "Returns 204."),
    f"POST {W}/teams": ("Create a team", "`key` is a short unique code such as `ENG`."),
    f"GET {W}/teams": ("List teams", "Paginated with `limit` and `offset`."),
    f"GET {W}/teams/{{team_id}}": ("Get a team", "Returns one team."),
    f"PATCH {W}/teams/{{team_id}}": ("Update a team", "Only the fields you send change."),
    f"DELETE {W}/teams/{{team_id}}": ("Delete a team", "Returns 204."),
    f"PUT {W}/teams/{{team_id}}/members": (
        "Replace team members",
        "Sets the full member list. Members not listed are removed.",
    ),
    # Planning
    f"POST {W}/projects": (
        "Create a project",
        "`key` becomes the prefix of task identifiers, for example `DATA-1`.",
    ),
    f"GET {W}/projects": ("List projects", "Paginated with `limit` and `offset`."),
    f"GET {W}/projects/{{project_id}}": ("Get a project", "Returns one project."),
    f"PATCH {W}/projects/{{project_id}}": (
        "Update a project",
        "Status is `backlog`, `planned`, `in_progress`, `paused`, `completed` or `canceled`.",
    ),
    f"DELETE {W}/projects/{{project_id}}": ("Delete a project", "Returns 204."),
    f"POST {W}/projects/{{project_id}}/work-items": (
        "Create a work item",
        "A work item groups related tasks, for example one feature.",
    ),
    f"GET {W}/projects/{{project_id}}/work-items": (
        "List work items",
        "Paginated with `limit` and `offset`.",
    ),
    f"GET {W}/projects/{{project_id}}/work-items/{{work_item_id}}": (
        "Get a work item",
        "Returns one work item.",
    ),
    f"PATCH {W}/projects/{{project_id}}/work-items/{{work_item_id}}": (
        "Update a work item",
        "Priority is `none`, `low`, `medium`, `high` or `urgent`.",
    ),
    f"DELETE {W}/projects/{{project_id}}/work-items/{{work_item_id}}": (
        "Delete a work item",
        "Returns 204.",
    ),
    f"POST {W}/projects/{{project_id}}/tasks": (
        "Create a task",
        "Each task gets a number and identifier such as `DATA-1`. Needs the `work_item_id` it belongs to.",
    ),
    f"GET {W}/projects/{{project_id}}/tasks": (
        "List tasks",
        "Paginated with `limit` and `offset`.",
    ),
    f"GET {W}/projects/{{project_id}}/tasks/{{task_id}}": ("Get a task", "Returns one task."),
    f"PATCH {W}/projects/{{project_id}}/tasks/{{task_id}}": (
        "Update a task",
        "Only the fields you send change.",
    ),
    f"DELETE {W}/projects/{{project_id}}/tasks/{{task_id}}": ("Delete a task", "Returns 204."),
    # Collaboration
    f"POST {W}/comments": (
        "Create a comment",
        "Attach a comment to a task, work item, project, project update, sprint or doc. Set `parent_comment_id` to reply.",
    ),
    f"GET {W}/comments": (
        "List comments",
        "Filter by `target_type` and `target_id`. Paginated with `limit` and `offset`.",
    ),
    f"GET {W}/comments/{{comment_id}}": ("Get a comment", "Returns one comment."),
    f"PATCH {W}/comments/{{comment_id}}": (
        "Edit or resolve a comment",
        "Change the body or set `resolved`.",
    ),
    f"DELETE {W}/comments/{{comment_id}}": ("Delete a comment", "Returns 204."),
    f"POST {W}/docs": (
        "Create a doc",
        "A doc is a page made of blocks. Can nest under `parent_doc_id`.",
    ),
    f"GET {W}/docs": ("List docs", "Paginated with `limit` and `offset`."),
    f"GET {W}/docs/{{doc_id}}": (
        "Get a doc",
        "Metadata only. Fetch the content with **List doc blocks**.",
    ),
    f"PATCH {W}/docs/{{doc_id}}": ("Update a doc", "Only the fields you send change."),
    f"DELETE {W}/docs/{{doc_id}}": ("Delete a doc", "Returns 204."),
    f"GET {W}/docs/{{doc_id}}/blocks": (
        "List doc blocks",
        "In display order. Paginated with `limit` and `offset`.",
    ),
    f"POST {W}/docs/{{doc_id}}/blocks": (
        "Create a block",
        "`type` is a block type such as `paragraph` or `heading_1`; `content` depends on the type.",
    ),
    f"PATCH {W}/docs/{{doc_id}}/blocks/{{block_id}}": (
        "Update a block",
        "Change type, content, parent or position.",
    ),
    f"DELETE {W}/docs/{{doc_id}}/blocks/{{block_id}}": ("Delete a block", "Returns 204."),
    f"POST {W}/channels": (
        "Create a channel",
        "`type` is `team`, `project`, `group`, `dm` or `agent`. Team channels need `team_id`; project channels need `project_id`.",
    ),
    f"GET {W}/channels": ("List channels", "Paginated with `limit` and `offset`."),
    f"GET {W}/channels/{{channel_id}}": ("Get a channel", "Returns one channel."),
    f"PATCH {W}/channels/{{channel_id}}": ("Update a channel", "Change name, topic or privacy."),
    f"DELETE {W}/channels/{{channel_id}}": (
        "Archive a channel",
        "Archives rather than deletes. Returns 204.",
    ),
    f"PUT {W}/channels/{{channel_id}}/members": (
        "Replace channel members",
        "Sets the full member list. Members not listed are removed.",
    ),
    f"POST {W}/channels/{{channel_id}}/messages": (
        "Send a message",
        "Set `parent_message_id` to reply in a thread.",
    ),
    f"GET {W}/channels/{{channel_id}}/messages": (
        "List messages",
        "Paginated with `limit` and `offset`.",
    ),
    f"GET {W}/channels/{{channel_id}}/messages/{{message_id}}": (
        "Get a message",
        "Returns one message.",
    ),
    f"PATCH {W}/channels/{{channel_id}}/messages/{{message_id}}": (
        "Edit or pin a message",
        "Change the body or set `pinned`.",
    ),
    f"DELETE {W}/channels/{{channel_id}}/messages/{{message_id}}": (
        "Delete a message",
        "Soft delete. Returns 204.",
    ),
}
