from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.enums import Priority, WorkItemStatus, WorkItemType
from db.models import WorkItem
from domain.crud import apply_update, delete, fetch_page, get_or_404, save
from domain.crud.projects import get_project
from schemas.work_item import WorkItemCreate, WorkItemUpdate


def create_work_item(
    session: Session, org_id: UUID, project_id: UUID, data: WorkItemCreate
) -> WorkItem:
    project = get_project(session, org_id, project_id)
    return save(
        session,
        WorkItem(
            organization_id=project.organization_id,
            project_id=project.id,
            **data.model_dump(),
        ),
    )


def list_work_items(
    session: Session,
    org_id: UUID,
    project_id: UUID,
    status: WorkItemStatus | None,
    type: WorkItemType | None,
    priority: Priority | None,
    limit: int,
    offset: int,
):
    get_project(session, org_id, project_id)
    stmt = select(WorkItem).where(
        WorkItem.project_id == project_id, WorkItem.organization_id == org_id
    )
    for column, value in (
        (WorkItem.status, status),
        (WorkItem.type, type),
        (WorkItem.priority, priority),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    return fetch_page(
        session, stmt.order_by(WorkItem.created_at, WorkItem.id), limit, offset
    )


def get_work_item(session: Session, org_id: UUID, work_item_id: UUID) -> WorkItem:
    return get_or_404(session, WorkItem, org_id, work_item_id, "WORK_ITEM")


def update_work_item(
    session: Session, org_id: UUID, work_item_id: UUID, data: WorkItemUpdate
) -> WorkItem:
    return apply_update(session, get_work_item(session, org_id, work_item_id), data)


def delete_work_item(session: Session, org_id: UUID, work_item_id: UUID) -> None:
    """Hard delete; the DB sets tasks.work_item_id to NULL."""
    delete(session, get_work_item(session, org_id, work_item_id))
