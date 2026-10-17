from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Workspace
from domain.crud import apply_update, archive, fetch_page, get_or_404, save
from schemas.workspace import WorkspaceCreate, WorkspaceUpdate


def create_workspace(session: Session, org_id: UUID, data: WorkspaceCreate) -> Workspace:
    return save(session, Workspace(organization_id=org_id, **data.model_dump()))


def list_workspaces(
    session: Session, org_id: UUID, archived: bool | None, limit: int, offset: int
):
    stmt = select(Workspace).where(Workspace.organization_id == org_id)
    if archived is not None:
        stmt = stmt.where(Workspace.archived_at.is_not(None) == archived)
    return fetch_page(
        session, stmt.order_by(Workspace.created_at, Workspace.id), limit, offset
    )


def get_workspace(session: Session, org_id: UUID, workspace_id: UUID) -> Workspace:
    return get_or_404(session, Workspace, org_id, workspace_id, "WORKSPACE")


def update_workspace(
    session: Session, org_id: UUID, workspace_id: UUID, data: WorkspaceUpdate
) -> Workspace:
    return apply_update(session, get_workspace(session, org_id, workspace_id), data)


def archive_workspace(session: Session, org_id: UUID, workspace_id: UUID) -> None:
    archive(session, get_workspace(session, org_id, workspace_id))
