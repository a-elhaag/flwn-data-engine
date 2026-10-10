"""Trusted workspace project and task management."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import events
from app.db.models.identity import Member, Team, Workspace
from app.db.models.planning import Backlog, Milestone, Project, Sprint, Task, WorkItem
from app.db.session import session_for


class PlanningNotFound(LookupError):
    pass


class PlanningConflict(ValueError):
    pass


def _workspace(session, workspace_id):
    if (
        session.scalar(
            select(Workspace.id).where(Workspace.id == workspace_id, Workspace.deleted_at.is_(None))
        )
        is None
    ):
        raise PlanningNotFound("workspace not found")


def _project(session, workspace_id, project_id):
    row = session.scalar(
        select(Project).where(
            Project.workspace_id == workspace_id,
            Project.id == uuid.UUID(str(project_id)),
            Project.deleted_at.is_(None),
        )
    )
    if row is None:
        raise PlanningNotFound("project not found")
    return row


def _work_item(session, workspace_id, project_id, work_item_id):
    row = session.scalar(
        select(WorkItem).where(
            WorkItem.workspace_id == workspace_id,
            WorkItem.project_id == project_id,
            WorkItem.id == uuid.UUID(str(work_item_id)),
            WorkItem.deleted_at.is_(None),
        )
    )
    if row is None:
        raise PlanningNotFound("work item not found")
    return row


def _task(session, workspace_id, project_id, task_id):
    row = session.scalar(
        select(Task).where(
            Task.workspace_id == workspace_id,
            Task.project_id == project_id,
            Task.id == uuid.UUID(str(task_id)),
            Task.deleted_at.is_(None),
        )
    )
    if row is None:
        raise PlanningNotFound("task not found")
    return row


def _check_project_refs(session, workspace_id, fields):
    if fields.get("team_id") is not None:
        team_id = uuid.UUID(str(fields["team_id"]))
        if (
            session.scalar(
                select(Team.id).where(
                    Team.workspace_id == workspace_id,
                    Team.id == team_id,
                    Team.deleted_at.is_(None),
                )
            )
            is None
        ):
            raise PlanningNotFound("team not found")
        fields["team_id"] = team_id
    if fields.get("lead_id") is not None:
        lead_id = uuid.UUID(str(fields["lead_id"]))
        if (
            session.scalar(
                select(Member.id).where(
                    Member.workspace_id == workspace_id,
                    Member.id == lead_id,
                    Member.deleted_at.is_(None),
                    Member.status == "active",
                )
            )
            is None
        ):
            raise PlanningNotFound("active member not found")
        fields["lead_id"] = lead_id


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
    def __init__(self, workspace_id):
        self.workspace_id = uuid.UUID(str(workspace_id))

    def list(self, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return list(
                session.scalars(
                    select(Project)
                    .where(
                        Project.workspace_id == self.workspace_id,
                        Project.deleted_at.is_(None),
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

    def create(self, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            _check_project_refs(session, self.workspace_id, fields)
            row = Project(workspace_id=self.workspace_id, **fields)
            session.add(row)
            session.flush()
            backlog = Backlog(workspace_id=self.workspace_id, project_id=row.id, name="Backlog")
            session.add(backlog)
            session.flush()
            events.record(session, str(self.workspace_id), None, "project", row.id, "created")
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
                session, str(self.workspace_id), None, "project", row.id, "updated", fields
            )
            return row

    def delete(self, project_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = _project(session, self.workspace_id, project_id)
            now = datetime.now(UTC)
            work_items = list(
                session.scalars(
                    select(WorkItem).where(
                        WorkItem.workspace_id == self.workspace_id,
                        WorkItem.project_id == row.id,
                        WorkItem.deleted_at.is_(None),
                    )
                )
            )
            for item in work_items:
                item.deleted_at = now
                tasks = session.scalars(
                    select(Task).where(
                        Task.workspace_id == self.workspace_id,
                        Task.project_id == row.id,
                        Task.work_item_id == item.id,
                        Task.deleted_at.is_(None),
                    )
                )
                for task in tasks:
                    task.deleted_at = now
                    events.record(session, str(self.workspace_id), None, "task", task.id, "deleted")
                events.record(
                    session, str(self.workspace_id), None, "work_item", item.id, "deleted"
                )
            row.deleted_at = now
            session.flush()
            events.record(session, str(self.workspace_id), None, "project", row.id, "deleted")


class WorkItemService:
    def __init__(self, workspace_id):
        self.workspace_id = uuid.UUID(str(workspace_id))

    def list(self, project_id, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            return list(
                session.scalars(
                    select(WorkItem)
                    .where(
                        WorkItem.workspace_id == self.workspace_id,
                        WorkItem.project_id == project.id,
                        WorkItem.deleted_at.is_(None),
                    )
                    .order_by(WorkItem.created_at, WorkItem.id)
                    .limit(limit)
                    .offset(offset)
                )
            )

    def get(self, project_id, work_item_id):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            return _work_item(session, self.workspace_id, project.id, work_item_id)

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
                project_id=project.id,
                backlog_id=backlog.id,
                **fields,
            )
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), None, "work_item", row.id, "created")
            return row

    def update(self, project_id, work_item_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = _work_item(session, self.workspace_id, project.id, work_item_id)
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(
                session, str(self.workspace_id), None, "work_item", row.id, "updated", fields
            )
            return row

    def delete(self, project_id, work_item_id):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = _work_item(session, self.workspace_id, project.id, work_item_id)
            now = datetime.now(UTC)
            tasks = session.scalars(
                select(Task).where(
                    Task.workspace_id == self.workspace_id,
                    Task.project_id == project.id,
                    Task.work_item_id == row.id,
                    Task.deleted_at.is_(None),
                )
            )
            for task in tasks:
                task.deleted_at = now
                events.record(session, str(self.workspace_id), None, "task", task.id, "deleted")
            row.deleted_at = now
            session.flush()
            events.record(session, str(self.workspace_id), None, "work_item", row.id, "deleted")


class TaskService:
    def __init__(self, workspace_id):
        self.workspace_id = uuid.UUID(str(workspace_id))

    def list(self, project_id, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            rows = session.scalars(
                select(Task)
                .where(
                    Task.workspace_id == self.workspace_id,
                    Task.project_id == project.id,
                    Task.deleted_at.is_(None),
                )
                .order_by(Task.number, Task.id)
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
                project_id=project.id,
                work_item_id=work_item.id,
                **fields,
            )
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), None, "task", row.id, "created")
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
            events.record(session, str(self.workspace_id), None, "task", row.id, "updated", fields)
            return _task_view(session, row)

    def delete(self, project_id, task_id):
        with session_for(str(self.workspace_id)) as session:
            project = _project(session, self.workspace_id, project_id)
            row = _task(session, self.workspace_id, project.id, task_id)
            row.deleted_at = datetime.now(UTC)
            session.flush()
            events.record(session, str(self.workspace_id), None, "task", row.id, "deleted")

    def _validate_task_refs(self, session, project, fields, task_id=None):
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
                    raise PlanningNotFound(f"{key.removesuffix('_id')} not found")
                fields[key] = value
        if fields.get("assignee_id") is not None:
            value = uuid.UUID(str(fields["assignee_id"]))
            if (
                session.scalar(
                    select(Member.id).where(
                        Member.workspace_id == self.workspace_id,
                        Member.id == value,
                        Member.deleted_at.is_(None),
                        Member.status == "active",
                    )
                )
                is None
            ):
                raise PlanningNotFound("active assignee not found")
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
                        Task.deleted_at.is_(None),
                    )
                )
                is None
            ):
                raise PlanningNotFound("parent task not found")
            fields["parent_task_id"] = value
