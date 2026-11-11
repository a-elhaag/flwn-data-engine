"""Trusted workspace project and task management."""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select

from app.db import events
from app.db.models.identity import Member, Team, TeamMember, Workspace
from app.db.models.planning import Backlog, Milestone, Project, Sprint, Task, WorkItem
from app.db.session import session_for
from app.domain.error_codes import DomainError, Invalid, InvalidReference

TASK_KEY = re.compile(r"^([A-Z][A-Z0-9]{0,9})-([1-9][0-9]*)$")


class PlanningNotFound(DomainError, LookupError):
    code = "planning_not_found"


class PlanningConflict(DomainError, ValueError):
    code = "planning_conflict"


def _workspace(session, workspace_id):
    if session.scalar(select(Workspace.id).where(Workspace.id == workspace_id)) is None:
        raise PlanningNotFound("workspace not found", code="workspace_not_found")


def _project(session, workspace_id, project_id):
    row = session.scalar(
        select(Project).where(
            Project.workspace_id == workspace_id,
            Project.id == uuid.UUID(str(project_id)),
        )
    )
    if row is None:
        raise PlanningNotFound("project not found", code="project_not_found")
    return row


def _work_item(session, workspace_id, project_id, work_item_id):
    row = session.scalar(
        select(WorkItem).where(
            WorkItem.workspace_id == workspace_id,
            WorkItem.project_id == project_id,
            WorkItem.id == uuid.UUID(str(work_item_id)),
        )
    )
    if row is None:
        raise PlanningNotFound("work item not found", code="work_item_not_found")
    return row


def _task(session, workspace_id, project_id, task_id):
    row = session.scalar(
        select(Task).where(
            Task.workspace_id == workspace_id,
            Task.project_id == project_id,
            Task.id == uuid.UUID(str(task_id)),
        )
    )
    if row is None:
        raise PlanningNotFound("task not found", code="task_not_found")
    return row


def _attach_task_counts(session, workspace_id, work_items):
    """Set `task_counts` ({status: n}, non-deleted tasks) on each work item, in one query."""
    counts = {row.id: {} for row in work_items}
    if counts:
        grouped = session.execute(
            select(Task.work_item_id, Task.status, func.count())
            .where(
                Task.workspace_id == workspace_id,
                Task.work_item_id.in_(list(counts)),
            )
            .group_by(Task.work_item_id, Task.status)
        )
        for work_item_id, status, n in grouped:
            counts[work_item_id][status] = n
    for row in work_items:
        row.task_counts = counts[row.id]
    return work_items


def _check_project_refs(session, workspace_id, fields):
    if fields.get("team_id") is not None:
        team_id = uuid.UUID(str(fields["team_id"]))
        if (
            session.scalar(
                select(Team.id).where(
                    Team.workspace_id == workspace_id,
                    Team.id == team_id,
                )
            )
            is None
        ):
            raise InvalidReference("team")
        fields["team_id"] = team_id
    if fields.get("lead_id") is not None:
        lead_id = uuid.UUID(str(fields["lead_id"]))
        if (
            session.scalar(
                select(Member.id).where(
                    Member.workspace_id == workspace_id,
                    Member.id == lead_id,
                    Member.status == "active",
                )
            )
            is None
        ):
            raise InvalidReference("lead")
        fields["lead_id"] = lead_id


def _member_in_workspace(session, workspace_id, member_id):
    found = session.scalar(
        select(Member.id).where(
            Member.workspace_id == workspace_id,
            Member.id == uuid.UUID(str(member_id)),
        )
    )
    if found is None:
        raise PlanningNotFound("member not found", code="member_not_found")
    return found


def _task_view(session, row):
    project = session.get(Project, row.project_id)
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "project_id": row.project_id,
        "work_item_id": row.work_item_id,
        "sprint_id": row.sprint_id,
        "milestone_id": row.milestone_id,
        "parent_task_id": row.parent_task_id,
        "number": row.number,
        "identifier": f"{project.key}-{row.number}",
        "title": row.title,
        "description": row.description,
        "requirements": row.requirements,
        "acceptance_criteria": row.acceptance_criteria,
        "priority": row.priority,
        "status": row.status,
        "estimate": row.estimate,
        "due_date": row.due_date,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "sort_order": row.sort_order,
        "assignee_id": row.assignee_id,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class ProjectService:
    def __init__(self, workspace_id, actor=None):
        self.workspace_id = uuid.UUID(str(workspace_id))
        self.actor = str(actor) if actor else None  # who is acting, for the audit log

    def list(self, limit=50, offset=0, *, team_id=None):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return list(
                session.scalars(
                    select(Project)
                    .where(
                        Project.workspace_id == self.workspace_id,
                        Project.team_id == team_id if team_id is not None else True,
                    )
                    .order_by(Project.created_at, Project.id)
                    .limit(limit)
                    .offset(offset)
                )
            )

    def get(self, project_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return _project(session, self.workspace_id, project_id)

    def for_member(self, member_id, limit=50, offset=0):
        """Projects a member leads or whose team they belong to."""
        with session_for(str(self.workspace_id)) as session:
            member = _member_in_workspace(session, self.workspace_id, member_id)
            on_team = (
                select(TeamMember.team_id)
                .where(TeamMember.workspace_id == self.workspace_id, TeamMember.member_id == member)
                .scalar_subquery()
            )
            return list(
                session.scalars(
                    select(Project)
                    .where(
                        Project.workspace_id == self.workspace_id,
                        or_(Project.lead_id == member, Project.team_id.in_(on_team)),
                    )
                    .order_by(Project.created_at, Project.id)
                    .limit(limit)
                    .offset(offset)
                )
            )

    def create(self, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            _check_project_refs(session, self.workspace_id, fields)
            row = Project(workspace_id=self.workspace_id, created_by=self.actor, **fields)
            session.add(row)
            session.flush()
            backlog = Backlog(workspace_id=self.workspace_id, project_id=row.id, name="Backlog")
            session.add(backlog)
            session.flush()
            events.record(session, str(self.workspace_id), self.actor, "project", row.id, "created")
            return row

    def update(self, project_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = _project(session, self.workspace_id, project_id)
            _check_project_refs(session, self.workspace_id, fields)
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(
                session, str(self.workspace_id), self.actor, "project", row.id, "updated", fields
            )
            return row

    def delete(self, project_id):
        """Hard delete. Work items, tasks and comments go with the project (ON DELETE CASCADE);
        the audit log keeps a snapshot of each name."""
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = _project(session, self.workspace_id, project_id)
            snapshots = [("project", row.id, {"name": row.name})]
            for item in session.scalars(
                select(WorkItem).where(
                    WorkItem.workspace_id == self.workspace_id, WorkItem.project_id == row.id
                )
            ):
                snapshots.append(("work_item", item.id, {"title": item.title}))
            for task in session.scalars(
                select(Task).where(
                    Task.workspace_id == self.workspace_id, Task.project_id == row.id
                )
            ):
                snapshots.append(("task", task.id, {"title": task.title}))
            session.delete(row)
            session.flush()
            for entity, entity_id, changes in snapshots:
                events.record(
                    session,
                    str(self.workspace_id),
                    self.actor,
                    entity,
                    entity_id,
                    "deleted",
                    changes,
                )


class WorkItemService:
    def __init__(self, workspace_id, actor=None):
        self.workspace_id = uuid.UUID(str(workspace_id))
        self.actor = str(actor) if actor else None  # who is acting, for the audit log

    def list(self, project_id, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            items = list(
                session.scalars(
                    select(WorkItem)
                    .where(
                        WorkItem.workspace_id == self.workspace_id,
                        WorkItem.project_id == project.id,
                    )
                    .order_by(WorkItem.created_at, WorkItem.id)
                    .limit(limit)
                    .offset(offset)
                )
            )
            return _attach_task_counts(session, self.workspace_id, items)

    def get(self, project_id, work_item_id):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = _work_item(session, self.workspace_id, project.id, work_item_id)
            return _attach_task_counts(session, self.workspace_id, [row])[0]

    def create(self, project_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            backlog = session.scalar(
                select(Backlog).where(
                    Backlog.workspace_id == self.workspace_id,
                    Backlog.project_id == project.id,
                )
            )
            if backlog is None:
                backlog = Backlog(
                    workspace_id=self.workspace_id, project_id=project.id, name="Backlog"
                )
                session.add(backlog)
                session.flush()
            row = WorkItem(
                workspace_id=self.workspace_id,
                created_by=self.actor,
                project_id=project.id,
                backlog_id=backlog.id,
                **fields,
            )
            session.add(row)
            session.flush()
            events.record(
                session, str(self.workspace_id), self.actor, "work_item", row.id, "created"
            )
            return row

    def update(self, project_id, work_item_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = _work_item(session, self.workspace_id, project.id, work_item_id)
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(
                session, str(self.workspace_id), self.actor, "work_item", row.id, "updated", fields
            )
            return _attach_task_counts(session, self.workspace_id, [row])[0]

    def delete(self, project_id, work_item_id):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = _work_item(session, self.workspace_id, project.id, work_item_id)
            snapshots = [("work_item", row.id, {"title": row.title})]
            for task in session.scalars(
                select(Task).where(
                    Task.workspace_id == self.workspace_id,
                    Task.project_id == project.id,
                    Task.work_item_id == row.id,
                )
            ):
                snapshots.append(("task", task.id, {"title": task.title}))
            session.delete(row)  # its tasks go with it (ON DELETE CASCADE)
            session.flush()
            for entity, entity_id, changes in snapshots:
                events.record(
                    session,
                    str(self.workspace_id),
                    self.actor,
                    entity,
                    entity_id,
                    "deleted",
                    changes,
                )


class TaskService:
    def __init__(self, workspace_id, actor=None):
        self.workspace_id = uuid.UUID(str(workspace_id))
        self.actor = str(actor) if actor else None  # who is acting, for the audit log

    def list(
        self,
        project_id,
        limit=50,
        offset=0,
        *,
        assignee_id=None,
        status=None,
        sprint_id=None,
        work_item_id=None,
        backlog=None,
    ):
        """backlog=True: tasks in no sprint. backlog=False: tasks in a sprint."""
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            query = select(Task).where(
                Task.workspace_id == self.workspace_id,
                Task.project_id == project.id,
            )
            if assignee_id is not None:
                query = query.where(Task.assignee_id == assignee_id)
            if status is not None:
                query = query.where(Task.status == status)
            if sprint_id is not None:
                query = query.where(Task.sprint_id == sprint_id)
            if work_item_id is not None:
                query = query.where(Task.work_item_id == work_item_id)
            if backlog is not None:
                query = query.where(
                    Task.sprint_id.is_(None) if backlog else Task.sprint_id.is_not(None)
                )
            rows = session.scalars(query.order_by(Task.number, Task.id).limit(limit).offset(offset))
            return [_task_view(session, row) for row in rows]

    def by_key(self, key):
        """A task by its readable identifier, such as FLWN-12."""
        found = TASK_KEY.match(key)
        if found is None:
            raise Invalid("task key looks like FLWN-12", code="task_key_invalid")
        with session_for(str(self.workspace_id)) as session:
            row = session.scalar(
                select(Task)
                .join(
                    Project,
                    (Project.workspace_id == Task.workspace_id) & (Project.id == Task.project_id),
                )
                .where(
                    Task.workspace_id == self.workspace_id,
                    Project.key == found.group(1),
                    Task.number == int(found.group(2)),
                )
            )
            if row is None:
                raise PlanningNotFound("task not found", code="task_not_found")
            return _task_view(session, row)

    def assigned_to(self, member_id, limit=50, offset=0):
        """Every open-or-closed task assigned to a member, across the workspace's projects."""
        with session_for(str(self.workspace_id)) as session:
            member = _member_in_workspace(session, self.workspace_id, member_id)
            rows = session.scalars(
                select(Task)
                .join(
                    Project,
                    (Project.workspace_id == Task.workspace_id) & (Project.id == Task.project_id),
                )
                .where(
                    Task.workspace_id == self.workspace_id,
                    Task.assignee_id == member,
                )
                .order_by(Task.created_at, Task.id)
                .limit(limit)
                .offset(offset)
            )
            return [_task_view(session, row) for row in rows]

    def get(self, project_id, task_id):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            return _task_view(session, _task(session, self.workspace_id, project.id, task_id))

    def create(self, project_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            work_item = _work_item(
                session, self.workspace_id, project.id, fields.pop("work_item_id")
            )
            self._validate_task_refs(session, project, fields)
            row = Task(
                workspace_id=self.workspace_id,
                created_by=self.actor,
                project_id=project.id,
                work_item_id=work_item.id,
                **fields,
            )
            session.add(row)
            session.flush()
            session.refresh(row)  # `number` is assigned by a database trigger
            events.record(session, str(self.workspace_id), self.actor, "task", row.id, "created")
            return _task_view(session, row)

    def update(self, project_id, task_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = _task(session, self.workspace_id, project.id, task_id)
            if "work_item_id" in fields:
                work_item = _work_item(
                    session, self.workspace_id, project.id, fields.pop("work_item_id")
                )
                row.work_item_id = work_item.id
            self._validate_task_refs(session, project, fields, task_id=row.id)
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(
                session, str(self.workspace_id), self.actor, "task", row.id, "updated", fields
            )
            return _task_view(session, row)

    def delete(self, project_id, task_id):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = _task(session, self.workspace_id, project.id, task_id)
            snapshot, row_id = {"title": row.title}, row.id
            session.delete(row)  # its sub-tasks are detached, not deleted (ON DELETE SET NULL)
            session.flush()
            events.record(
                session, str(self.workspace_id), self.actor, "task", row_id, "deleted", snapshot
            )

    def _validate_task_refs(self, session, project, fields, task_id=None):
        self._refuse_completed_sprint(session, project, fields, task_id)
        for key, model in (("sprint_id", Sprint), ("milestone_id", Milestone)):
            if fields.get(key) is not None:
                value = uuid.UUID(str(fields[key]))
                if (
                    session.scalar(
                        select(model.id).where(
                            model.workspace_id == self.workspace_id,
                            model.project_id == project.id,
                            model.id == value,
                        )
                    )
                    is None
                ):
                    raise InvalidReference(key.removesuffix("_id"))
                fields[key] = value
        if fields.get("assignee_id") is not None:
            value = uuid.UUID(str(fields["assignee_id"]))
            if (
                session.scalar(
                    select(Member.id).where(
                        Member.workspace_id == self.workspace_id,
                        Member.id == value,
                        Member.status == "active",
                    )
                )
                is None
            ):
                raise InvalidReference("assignee")
            fields["assignee_id"] = value
        if fields.get("parent_task_id") is not None:
            value = uuid.UUID(str(fields["parent_task_id"]))
            if (
                value == task_id
                or session.scalar(
                    select(Task.id).where(
                        Task.workspace_id == self.workspace_id,
                        Task.project_id == project.id,
                        Task.id == value,
                    )
                )
                is None
            ):
                raise InvalidReference("parent_task")
            if task_id is not None:
                self._refuse_task_cycle(session, task_id, value)
            fields["parent_task_id"] = value

    def _refuse_completed_sprint(self, session, project, fields, task_id):
        """A task cannot be put into a completed sprint (staying in one it is already in is fine)."""
        if fields.get("sprint_id") is None:
            return
        wanted = uuid.UUID(str(fields["sprint_id"]))
        status = session.scalar(
            select(Sprint.status).where(
                Sprint.workspace_id == self.workspace_id,
                Sprint.project_id == project.id,
                Sprint.id == wanted,
            )
        )
        if status != "completed":  # a missing sprint is reported as not found below
            return
        current = (
            session.scalar(select(Task.sprint_id).where(Task.id == task_id)) if task_id else None
        )
        if current != wanted:
            raise PlanningConflict("sprint_completed", code="sprint_completed")

    def _refuse_task_cycle(self, session, task_id, parent_id):
        """A task cannot be its own ancestor: walk up from the new parent and look for it."""
        seen, current = set(), parent_id
        while current is not None and current not in seen:
            if current == task_id:
                raise PlanningConflict("task_cycle", code="task_cycle")
            seen.add(current)
            current = session.scalar(
                select(Task.parent_task_id).where(
                    Task.workspace_id == self.workspace_id, Task.id == current
                )
            )


def _sprint(session, workspace_id, project_id, sprint_id):
    row = session.scalar(
        select(Sprint).where(
            Sprint.workspace_id == workspace_id,
            Sprint.project_id == project_id,
            Sprint.id == uuid.UUID(str(sprint_id)),
        )
    )
    if row is None:
        raise PlanningNotFound("sprint not found", code="sprint_not_found")
    return row


SPRINT_MOVES = {("planned", "active"), ("active", "completed")}


class SprintService:
    def __init__(self, workspace_id, actor=None):
        self.workspace_id = uuid.UUID(str(workspace_id))
        self.actor = str(actor) if actor else None  # who is acting, for the audit log

    def list(self, project_id, status=None, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            query = select(Sprint).where(
                Sprint.workspace_id == self.workspace_id, Sprint.project_id == project.id
            )
            if status is not None:
                query = query.where(Sprint.status == status)
            return list(
                session.scalars(
                    query.order_by(Sprint.created_at, Sprint.id).limit(limit).offset(offset)
                )
            )

    def get(self, project_id, sprint_id):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            return _sprint(session, self.workspace_id, project.id, sprint_id)

    def create(self, project_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = Sprint(workspace_id=self.workspace_id, project_id=project.id, **fields)
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), self.actor, "sprint", row.id, "created")
            return row

    def update(self, project_id, sprint_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = _sprint(session, self.workspace_id, project.id, sprint_id)
            if "status" in fields:
                self._move(session, row, fields.pop("status"))
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(
                session, str(self.workspace_id), self.actor, "sprint", row.id, "updated", fields
            )
            return row

    def start(self, project_id, sprint_id):
        """planned -> active. 409 if the project already has an active sprint."""
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = _sprint(session, self.workspace_id, project.id, sprint_id)
            self._move(session, row, "active")
            session.flush()
            events.record(session, str(self.workspace_id), self.actor, "sprint", row.id, "started")
            return row

    def complete(self, project_id, sprint_id, move_open_tasks_to="backlog"):
        """active -> completed. Unfinished tasks go to the backlog or a planned sprint, in one go."""
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = _sprint(session, self.workspace_id, project.id, sprint_id)
            target = None
            if move_open_tasks_to != "backlog":
                target = _sprint(session, self.workspace_id, project.id, move_open_tasks_to)
                if target.id == row.id or target.status != "planned":
                    raise PlanningConflict(
                        "target_sprint_not_planned", code="target_sprint_not_planned"
                    )
            self._move(session, row, "completed")
            open_tasks = session.scalars(
                select(Task).where(
                    Task.workspace_id == self.workspace_id,
                    Task.sprint_id == row.id,
                    Task.status.not_in(("done", "canceled")),
                )
            ).all()
            for task in open_tasks:
                task.sprint_id = target.id if target else None
            session.flush()
            events.record(
                session,
                str(self.workspace_id),
                self.actor,
                "sprint",
                row.id,
                "completed",
                {"moved_tasks": len(open_tasks)},
            )
            return row

    def _move(self, session, row, status):
        """Planned -> active -> completed only, one active sprint per project, times kept."""
        if status == row.status:
            return
        if (row.status, status) not in SPRINT_MOVES:
            raise PlanningConflict("invalid_sprint_transition", code="invalid_sprint_transition")
        if status == "active":
            other = session.scalar(
                select(Sprint.id).where(
                    Sprint.workspace_id == self.workspace_id,
                    Sprint.project_id == row.project_id,
                    Sprint.status == "active",
                )
            )
            if other is not None:
                raise PlanningConflict("sprint_already_active", code="sprint_already_active")
            row.started_at = datetime.now(UTC)
        else:
            row.completed_at = datetime.now(UTC)
        row.status = status

    def delete(self, project_id, sprint_id):
        """Hard delete. The database clears sprint_id on its tasks, which go back to the backlog."""
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = _sprint(session, self.workspace_id, project.id, sprint_id)
            session.delete(row)
            session.flush()
            events.record(session, str(self.workspace_id), self.actor, "sprint", row.id, "deleted")
