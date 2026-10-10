"""Human-only local skill promotion and soft deletion."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.auth import WorkspaceId
from app.api.routes.atoms import ADMIN, _call
from app.atoms.skills import SkillService

router = APIRouter(prefix="/workspaces/{workspace_id}/skills", dependencies=[ADMIN])


def _service(request: Request, workspace_id: WorkspaceId):
    return SkillService(str(workspace_id), actor=request.state.principal.member_id)


Service = Annotated[SkillService, Depends(_service)]


@router.post("/{skill_id}/promote")
def promote(skill_id: UUID, service: Service):
    return _call(service.promote, str(skill_id))


@router.delete("/{skill_id}")
def delete(skill_id: UUID, service: Service):
    return _call(service.delete, str(skill_id))
