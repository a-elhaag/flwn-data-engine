"""Hand-written Swagger summary and description per endpoint, keyed by "METHOD path".

Applied to the OpenAPI document in one place; a test fails if a route is missing here.
"""

W = "/workspaces/{workspace_id}"

OPERATIONS: dict[str, tuple[str, str]] = {
    "GET /healthz": ("Liveness check", "Returns 200 while the process is up. No auth."),
    "GET /readyz": (
        "Readiness check",
        "Checks the database and storage. 503 when a dependency is down. Needs the service key.",
    ),
    "POST /auth/revocations": (
        "Revoke a token",
        "Trusted backend only. Lists a token's `jti` so it is refused from now until its own expiry. Safe to repeat.",
    ),
    "GET /auth/session": (
        "Check a token",
        "Any bearer token. 200 when the token is valid, not expired, not revoked and its member is active; 401 or 403 otherwise. Lets a service that only relays the token refuse a revoked one.",
    ),
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
    f"POST {W}/atoms": (
        "Create an atom",
        "An atom is a scheduled agent with daily run, cost and action budgets. Starts as a draft. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"GET {W}/atoms": (
        "List atoms",
        "Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"GET {W}/atoms/{{atom_id}}": (
        "Get an atom",
        "Returns one atom. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"PATCH {W}/atoms/{{atom_id}}": (
        "Configure an atom",
        "Rename it, change budgets or model tier, or set `draft`, `paused` or `killed`. Null values are rejected. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"DELETE {W}/atoms/{{atom_id}}": (
        "Delete an atom",
        "Returns 204. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"POST {W}/atoms/{{atom_id}}/versions": (
        "Propose a version",
        "Adds a new instruction version. It does nothing until activated. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"POST {W}/atoms/{{atom_id}}/activate": (
        "Activate a version",
        "Makes the version live and the atom active. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"POST {W}/atoms/{{atom_id}}/rollback": (
        "Roll back to a version",
        "Re-activates an earlier version. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"PUT {W}/atoms/{{atom_id}}/grants": (
        "Grant access",
        "Gives the atom `summary`, `read` or `write` access to one resource. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"DELETE {W}/atoms/{{atom_id}}/grants/{{grant_id}}": (
        "Revoke a grant",
        "Returns 204. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"POST {W}/atoms/{{atom_id}}/schedules": (
        "Add a schedule",
        "Give exactly one of `cron` or `interval_minutes` (at least 5). Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"PUT {W}/atoms/{{atom_id}}/schedules/{{schedule_id}}": (
        "Replace a schedule",
        "Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"DELETE {W}/atoms/{{atom_id}}/schedules/{{schedule_id}}": (
        "Delete a schedule",
        "Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"PUT {W}/atoms/{{atom_id}}/connections/{{connection_id}}": (
        "Replace a connection",
        "Updates a toolkit connection in place. `toolkit_version` must be pinned, not `latest`. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"DELETE {W}/atoms/{{atom_id}}/connections/{{connection_id}}": (
        "Delete a connection",
        "Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"PUT {W}/atoms/{{atom_id}}/connections": (
        "Add or update a connection",
        "Links the atom to a Composio toolkit account with a permission ceiling and an allow-list of tools. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"POST {W}/atoms/{{atom_id}}/skills": (
        "Attach a skill",
        "Attaches a promoted skill version to the atom. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"PATCH {W}/atoms/{{atom_id}}/skills/{{attachment_id}}": (
        "Enable or disable a skill",
        "Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"POST {W}/approvals": (
        "Request an approval",
        "An atom asks a human to approve one tool call before it runs. Needs `atoms:run`.",
    ),
    f"GET {W}/approvals/{{approval_id}}": (
        "Get an approval",
        "Status, payload and decision. Needs `atoms:run`.",
    ),
    f"PUT {W}/approvals/{{approval_id}}/decision": (
        "Approve or reject",
        "A human decides.",
    ),
    f"POST {W}/approvals/{{approval_id}}/claim": (
        "Claim an approval",
        "Locks an approved action so only one executor runs it. Needs `atoms:run`.",
    ),
    f"PUT {W}/approvals/{{approval_id}}/outcome": (
        "Report the outcome",
        "Record `succeeded`, `failed` or `unknown` and the tool result. Needs `atoms:run`.",
    ),
    f"POST {W}/skills/{{skill_id}}/promote": (
        "Promote a skill",
        "Makes a local skill available to the whole workspace. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    f"DELETE {W}/skills/{{skill_id}}": (
        "Delete a skill",
        "Hard delete. Needs `atoms:admin` (a human member's token) or the service key acting as a human member.",
    ),
    "POST /workspaces": (
        "Create a workspace",
        "Trusted backend only. Creates the workspace and an active HUMAN `owner` member for `owner_user_id` in one transaction. 404 if that user is missing, 409 if the slug is taken.",
    ),
    "GET /workspaces": (
        "List workspaces",
        "Paginated with `limit` and `offset`. Trusted backend only.",
    ),
    f"GET {W}": ("Get a workspace", "Needs a token for this workspace with `identity:read`."),
    f"PATCH {W}": (
        "Update a workspace",
        "Only the fields you send change. Needs a token with the `admin` scope.",
    ),
    f"DELETE {W}": (
        "Delete a workspace",
        "Hard delete: everything inside the workspace goes too. Needs a token with the `admin` scope.",
    ),
    "POST /users": (
        "Create a user",
        "Trusted backend only. A user is a person's global account; they join workspaces as members. Email is unique ignoring case.",
    ),
    "GET /users": (
        "List users",
        "Trusted backend only. Optional `email` finds one account, ignoring case. Paginated with `limit` and `offset`.",
    ),
    "GET /users/{user_id}": ("Get a user", "Trusted backend only."),
    "GET /users/{user_id}/memberships": (
        "List a user's memberships",
        "Trusted backend only (service key, no acting member). Every membership of the user in a live workspace, with the workspace's name and slug; filter with `status` and `type`. Deleted members and workspaces are left out. An empty list is 200; 404 if the user is missing or deleted.",
    ),
    "PATCH /users/{user_id}": (
        "Update a user",
        "Only the fields you send change. Trusted backend only.",
    ),
    "DELETE /users/{user_id}": (
        "Delete a user",
        "Hard delete. Their memberships go too (team seats with them; references to them become null, the audit log keeps the name). Trusted backend only. Returns 204, or 409 `last_owner` while they are the last active owner of a workspace, or 409 `user_in_use` while they still own atoms, skills or versions.",
    ),
    f"POST {W}/members": (
        "Create a member",
        "`type` is `HUMAN` (needs name and email) or `AI` (needs name and `agent_kind`). Role is `owner`, `admin`, `member` or `viewer`.",
    ),
    f"GET {W}/members": ("List members", "Paginated with `limit` and `offset`."),
    f"GET {W}/members/{{member_id}}": ("Get a member", "Returns one member."),
    f"PATCH {W}/members/{{member_id}}": (
        "Update a member",
        "Change role, status or settings. An AI member's name and avatar can also change; a person's name, email and avatar belong to their user account, so sending them is 422. 409 `last_owner` if the workspace would be left without an active human owner; AI members cannot be owners (422).",
    ),
    f"DELETE {W}/members/{{member_id}}": (
        "Remove a member",
        "Returns 204, or 409 `last_owner` if this is the workspace's last active owner.",
    ),
    f"POST {W}/ownership/transfer": (
        "Transfer ownership",
        "Makes `to_member_id` (an active HUMAN member) an owner in one transaction; `demote_self_to` also steps the acting owner down. The acting member (X-Acting-Member-Id) must be a current owner (403); an invalid target is 422; 409 `last_owner` if the result has no owner.",
    ),
    f"POST {W}/teams": ("Create a team", "`key` is a short unique code such as `ENG`."),
    f"GET {W}/teams": ("List teams", "Paginated with `limit` and `offset`."),
    f"GET {W}/teams/{{team_id}}": ("Get a team", "Returns one team."),
    f"PATCH {W}/teams/{{team_id}}": ("Update a team", "Only the fields you send change."),
    f"DELETE {W}/teams/{{team_id}}": (
        "Delete a team",
        "Returns 204. 409 while the team still owns projects; move them first.",
    ),
    f"PUT {W}/teams/{{team_id}}/members": (
        "Replace team members",
        "Sets the full member list. Members not listed are removed. Members who stay keep their role and join date; new ones join as `member` and must be active. Suspended members already on the team may stay.",
    ),
    f"GET {W}/teams/{{team_id}}/members": (
        "List team members",
        "Each member with their team role (`lead` or `member`), join date and workspace status. Paginated.",
    ),
    f"POST {W}/teams/{{team_id}}/members": (
        "Add a team member",
        "Adds one active workspace member with a role. Needs an administrator. 409 if already on the team.",
    ),
    f"PATCH {W}/teams/{{team_id}}/members/{{member_id}}": (
        "Change a team member's role",
        "Set the role to `lead` or `member`. Needs an administrator.",
    ),
    f"DELETE {W}/teams/{{team_id}}/members/{{member_id}}": (
        "Remove a team member",
        "Takes one member off the team; the others are unchanged. Needs an administrator. Returns 204.",
    ),
    f"GET {W}/members/{{member_id}}/teams": (
        "List a member's teams",
        "Every team the member belongs to. Paginated.",
    ),
    f"POST {W}/invitations": (
        "Invite a person",
        "Creates an invitation for an email and a role. Returns the token once; only its hash is stored. Valid for 7 days. 409 if the person is already an active member; a pending invitation for the same email is replaced. Needs an administrator.",
    ),
    f"GET {W}/invitations": (
        "List invitations",
        "Paginated with `limit` and `offset`. Tokens are never returned here. Needs an administrator.",
    ),
    f"POST {W}/invitations/{{invitation_id}}/revoke": (
        "Revoke an invitation",
        "Only a pending invitation can be revoked. Needs an administrator.",
    ),
    f"POST {W}/invitations/accept": (
        "Accept an invitation",
        "Trusted backend only. Checks the token, expiry and status, then makes the person an active member in one step. 409 if the invitation is expired, revoked or used, or the email belongs only to a deleted account. Returns the member.",
    ),
    f"POST {W}/projects": (
        "Create a project",
        "`key` becomes the prefix of task identifiers, for example `DATA-1`.",
    ),
    f"GET {W}/projects": (
        "List projects",
        "Paginated with `limit` and `offset`. Pass `team_id` to list only that team's projects.",
    ),
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
        'Paginated with `limit` and `offset`. Each item has `task_counts`: its non-deleted tasks per status, for example `{"todo": 3, "done": 5}`, computed in one query per page.',
    ),
    f"GET {W}/projects/{{project_id}}/work-items/{{work_item_id}}": (
        "Get a work item",
        "Returns one work item with its `task_counts`.",
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
        "Filter by `assignee_id`, `status`, `sprint_id`, `work_item_id`, or `backlog` (`true`: in no sprint). Paginated with `limit` and `offset`; `has_more` says whether another page follows.",
    ),
    f"GET {W}/tasks/by-key/{{key}}": (
        "Get a task by its key",
        "The readable identifier, such as `FLWN-12`: the project key, a hyphen and the task number.",
    ),
    f"GET {W}/members/{{member_id}}/tasks": (
        "List a member's tasks",
        "Every task assigned to the member, across the workspace's projects. Paginated.",
    ),
    f"GET {W}/members/{{member_id}}/projects": (
        "List a member's projects",
        "Projects the member leads or whose team they belong to. Paginated.",
    ),
    f"GET {W}/projects/{{project_id}}/tasks/{{task_id}}": ("Get a task", "Returns one task."),
    f"PATCH {W}/projects/{{project_id}}/tasks/{{task_id}}": (
        "Update a task",
        "Only the fields you send change.",
    ),
    f"DELETE {W}/projects/{{project_id}}/tasks/{{task_id}}": ("Delete a task", "Returns 204."),
    f"POST {W}/projects/{{project_id}}/sprints": (
        "Create a sprint",
        "A new sprint is always `planned`. `end_date` cannot be before `start_date`.",
    ),
    f"GET {W}/projects/{{project_id}}/sprints": (
        "List sprints",
        "Optional `status` filter. Paginated with `limit` and `offset`.",
    ),
    f"GET {W}/projects/{{project_id}}/sprints/{{sprint_id}}": (
        "Get a sprint",
        "Returns one sprint.",
    ),
    f"PATCH {W}/projects/{{project_id}}/sprints/{{sprint_id}}": (
        "Update a sprint",
        "Only the fields you send change. Status moves only planned to active and active to completed (409 `invalid_sprint_transition`); a project has one active sprint (409 `sprint_already_active`).",
    ),
    f"POST {W}/projects/{{project_id}}/sprints/{{sprint_id}}/start": (
        "Start a sprint",
        "planned to active. 409 `sprint_already_active` if the project has an active sprint. Sets `started_at`.",
    ),
    f"POST {W}/projects/{{project_id}}/sprints/{{sprint_id}}/complete": (
        "Complete a sprint",
        "active to completed, in one transaction. Sets `completed_at` and moves unfinished tasks (not done or canceled) to `move_open_tasks_to`: `backlog` (default) or a planned sprint of the project. Tasks cannot be added to a completed sprint (409 `sprint_completed`).",
    ),
    f"DELETE {W}/projects/{{project_id}}/sprints/{{sprint_id}}": (
        "Delete a sprint",
        "Hard delete. Its tasks are kept and go back to the backlog (`sprint_id` becomes null). Returns 204.",
    ),
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
        "Hard delete. Returns 204.",
    ),
    "GET /auth-store/credentials": (
        "Look up a password hash by email",
        "Trusted backend only. Email is matched ignoring case. Returns the user id and the stored hash; 404 `credential_not_found` if no account has a password.",
    ),
    "PUT /auth-store/credentials/{user_id}": (
        "Set a user's password hash",
        "Trusted backend only. Stores a hash the backend computed; the engine never sees the password. Replaces any previous hash. 404 `user_not_found` if the user is missing.",
    ),
    "DELETE /auth-store/credentials/{user_id}": (
        "Delete a user's password hash",
        "Trusted backend only. Idempotent: returns 204 whether or not the user had a password. Live refresh sessions are not touched; revoke them separately.",
    ),
    "POST /auth-store/sessions": (
        "Create a refresh session",
        "Trusted backend only. Stores the hash of a new refresh token for a user. Returns the session id.",
    ),
    "GET /auth-store/sessions": (
        "Find a live refresh session",
        "Trusted backend only. Looks up a refresh token by its hash. 404 `session_not_found` if it is unknown, revoked, replaced or expired.",
    ),
    "POST /auth-store/sessions/rotate": (
        "Rotate a refresh token",
        "Trusted backend only. Retires the old token and stores the new one in a single transaction. 401 `refresh_invalid` or `refresh_expired` for a bad token. 409 `refresh_reused` if the old token was already used: every session of that user is revoked.",
    ),
    "POST /auth-store/sessions/revoke": (
        "Revoke a refresh session",
        "Trusted backend only. Idempotent: revoking an unknown or revoked token still returns 204.",
    ),
    "POST /auth-store/users/{user_id}/sessions/revoke": (
        "Revoke every session of a user",
        "Trusted backend only. Revokes all live refresh sessions of the user. Idempotent.",
    ),
    "POST /auth-store/login-codes": (
        "Store a login code",
        "Trusted backend only. Stores the hash of a one-time code sent by email, with its expiry.",
    ),
    "POST /auth-store/login-codes/consume": (
        "Consume a login code",
        "Trusted backend only. A code works once. Returns the email it was issued for; 404 `code_invalid` if it is unknown, used or expired.",
    ),
    "PUT /auth-store/oauth-identities": (
        "Link an OAuth identity to a user",
        "Trusted backend only. Upserts the link for a provider (`google` or `github`) and the provider's subject id.",
    ),
    "GET /auth-store/oauth-identities/{provider}/{subject}": (
        "Find the user behind an OAuth identity",
        "Trusted backend only. Returns the user id; 404 `oauth_identity_not_found` if the identity is not linked.",
    ),
    "POST /rate-limits/count": (
        "Count recent hits",
        "Trusted backend only. Counts the hits for a bucket and key hash inside the window. Does not add a hit.",
    ),
    "POST /rate-limits/hit": (
        "Record a hit",
        "Trusted backend only. Adds one hit for a bucket and key hash, then returns the count inside the window. Hits older than a day are deleted.",
    ),
    "POST /rate-limits/reset": (
        "Clear a key's hits",
        "Trusted backend only. Deletes every hit of one bucket and key hash. Returns 204 whether or not there were any.",
    ),
    "PUT /ai-runs/{task_id}": (
        "Save a paused AI run",
        "Trusted backend only. Creates the run or replaces it. Replacing resets the review to `pending` with no reason. `state` is stored as given.",
    ),
    "GET /ai-runs/{task_id}": (
        "Get a paused AI run",
        "Trusted backend only. Returns the run and its review decision. 404 `ai_run_not_found` if the run is unknown.",
    ),
    "POST /ai-runs/{task_id}/claim": (
        "Claim a paused AI run to resume it",
        "Trusted backend only. Moves the run to `resuming` if it is at `expected_stage`, in one step, so a run is claimed once. 404 `ai_run_not_found` if the run is unknown. 409 `ai_run_not_claimable` if it is at another stage or already claimed.",
    ),
    "PATCH /ai-runs/{task_id}/review": (
        "Record a review decision",
        "Trusted backend only. Stores `approved` or `rejected` with an optional reason. 404 `ai_run_not_found` if the run is unknown. 409 `ai_run_not_in_review` if the run is waiting for plan approval, not review.",
    ),
    "DELETE /ai-runs/{task_id}": (
        "Delete a paused AI run",
        "Trusted backend only. Idempotent: returns 204 whether or not the run existed.",
    ),
}


from app.api.generic_crud import SUMMARIES as _GENERIC  # noqa: E402  (one entry per generated route)

OPERATIONS.update(_GENERIC)
