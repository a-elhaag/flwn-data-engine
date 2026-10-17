from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.enums import Priority, TaskStatus
from db.models import Project, Task
from domain.crud import apply_update
from domain.crud.projects import get_project
from domain.errors import not_found
from schemas.task import TaskCreate, TaskUpdate


def _with_key(task: Task, key: str) -> Task:
    """TaskRead.key = project key + number. Attached as a plain attribute."""
    task.key = f"{key}-{task.number}"
    return task


def _select_tasks():
    return select(Task, Project.key).join(Project, Task.project_id == Project.id)


def fetch_tasks(session: Session, conditions: list, limit: int, offset: int):
    stmt = (
        _select_tasks()
        .where(*conditions)
        .order_by(Task.number, Task.created_at, Task.id)
        .limit(limit + 1)
        .offset(offset)
    )
    rows = session.execute(stmt).all()
    tasks = [_with_key(task, key) for task, key in rows[:limit]]
    return tasks, len(rows) > limit


def create_task(session: Session, org_id: UUID, project_id: UUID, data: TaskCreate) -> Task:
    """Number assignment and insert share one transaction. The UPDATE locks the
    project row, so concurrent creates get distinct numbers; if the insert fails
    the counter rolls back too."""
    row = session.execute(
        text(
            "UPDATE projects SET task_counter = task_counter + 1 "
            "WHERE id = :project_id AND organization_id = :org_id "
            "RETURNING task_counter, key, organization_id"
        ),
        {"project_id": project_id, "org_id": org_id},
    ).one_or_none()
    if row is None:
        raise not_found("PROJECT")
    task = Task(
        organization_id=row.organization_id,
        project_id=project_id,
        number=row.task_counter,
        **data.model_dump(),
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return _with_key(task, row.key)


def list_tasks(
    session: Session,
    org_id: UUID,
    project_id: UUID,
    status: TaskStatus | None,
    priority: Priority | None,
    assignee_id: UUID | None,
    sprint_id: UUID | None,
    work_item_id: UUID | None,
    backlog: bool | None,
    limit: int,
    offset: int,
):
    get_project(session, org_id, project_id)
    conditions = [Task.project_id == project_id, Task.organization_id == org_id]
    for column, value in (
        (Task.status, status),
        (Task.priority, priority),
        (Task.assignee_id, assignee_id),
        (Task.sprint_id, sprint_id),
        (Task.work_item_id, work_item_id),
    ):
        if value is not None:
            conditions.append(column == value)
    if backlog:
        conditions.append(Task.sprint_id.is_(None))
    return fetch_tasks(session, conditions, limit, offset)


def get_task(session: Session, org_id: UUID, task_id: UUID) -> Task:
    row = session.execute(
        _select_tasks().where(Task.id == task_id, Task.organization_id == org_id)
    ).one_or_none()
    if row is None:
        raise not_found("TASK")
    return _with_key(*row)


def get_task_by_key(session: Session, org_id: UUID, project_key: str, number: int) -> Task:
    row = session.execute(
        _select_tasks().where(
            Project.key == project_key,
            Project.organization_id == org_id,
            Task.number == number,
            Task.organization_id == org_id,
        )
    ).one_or_none()
    if row is None:
        raise not_found("TASK")
    return _with_key(*row)


def update_task(session: Session, org_id: UUID, task_id: UUID, data: TaskUpdate) -> Task:
    task = get_task(session, org_id, task_id)
    return apply_update(session, task, data)


def delete_task(session: Session, org_id: UUID, task_id: UUID) -> None:
    task = get_task(session, org_id, task_id)
    session.delete(task)
    session.commit()
