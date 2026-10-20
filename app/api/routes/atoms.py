"""Human atom administration and trusted scheduler bootstrap."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.atom_schemas import (
    ActivateVersion,
    AtomVersionBody,
    AttachmentState,
    ConfigureAtom,
    ConnectionBody,
    CreateAtom,
    GrantBody,
    ReleaseStaleRuns,
    ScheduleBody,
    SkillAttachment,
    StartRun,
)
from app.api.auth import WorkspaceId, allow
from app.atoms.service import AtomService

router = APIRouter(prefix="/workspaces/{workspace_id}/atoms")
ADMIN = allow("atoms:admin")


def _service(request: Request, workspace_id: WorkspaceId):
    p = request.state.principal
    return AtomService(str(workspace_id), actor=p.member_id)


Service = Annotated[AtomService, Depends(_service)]


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PermissionError as exc:
        raise HTTPException(403, f"denied: {exc}") from None
    except LookupError as exc:
        raise HTTPException(404, f"not_found: {exc}") from None
    except ValueError as exc:
        raise HTTPException(422, f"{getattr(exc, 'code', 'invalid')}: {exc}") from None


@router.post("", status_code=201, dependencies=[ADMIN])
def create_atom(body: CreateAtom, service: Service):
    return _call(service.create, **body.model_dump(mode="json"))


@router.get("", dependencies=[ADMIN])
def list_atoms(service: Service):
    return _call(service.list)


@router.get("/scheduler-state", dependencies=[allow(None)])
def scheduler_state(
    workspace_id: WorkspaceId,
    status: Literal["draft", "active", "paused", "killed"] | None = None,
    enabled: bool | None = None,
):
    service = AtomService(str(workspace_id), trusted=True)
    return _call(service.list, status=status, enabled=enabled)


@router.post("/{atom_id}/runs/release-stale", dependencies=[allow(None)])
def release_stale(workspace_id: WorkspaceId, atom_id: UUID, body: ReleaseStaleRuns):
    service = AtomService(str(workspace_id), trusted=True)
    return _call(service.release_stale, str(atom_id), **body.model_dump())


@router.get("/{atom_id}", dependencies=[ADMIN])
def get_atom(atom_id: UUID, service: Service):
    return _call(service.get, str(atom_id))


@router.patch("/{atom_id}", dependencies=[ADMIN])
def configure_atom(atom_id: UUID, body: ConfigureAtom, service: Service):
    return _call(
        service.configure, str(atom_id), **body.model_dump(mode="json", exclude_unset=True)
    )


@router.delete("/{atom_id}", status_code=204, dependencies=[ADMIN])
def delete_atom(atom_id: UUID, service: Service):
    _call(service.delete, str(atom_id))
    return Response(status_code=204)


@router.post("/{atom_id}/versions", status_code=201, dependencies=[ADMIN])
def propose_version(atom_id: UUID, body: AtomVersionBody, service: Service):
    return _call(service.propose, str(atom_id), **body.model_dump())


@router.post("/{atom_id}/activate", dependencies=[ADMIN])
def activate(atom_id: UUID, body: ActivateVersion, service: Service):
    return _call(service.activate, str(atom_id), str(body.version_id))


@router.post("/{atom_id}/rollback", dependencies=[ADMIN])
def rollback(atom_id: UUID, body: ActivateVersion, service: Service):
    return _call(service.rollback, str(atom_id), str(body.version_id))


@router.put("/{atom_id}/grants", dependencies=[ADMIN])
def grant(atom_id: UUID, body: GrantBody, service: Service):
    return _call(service.set_grant, str(atom_id), **body.model_dump(mode="json"))


@router.delete("/{atom_id}/grants/{grant_id}", status_code=204, dependencies=[ADMIN])
def revoke(atom_id: UUID, grant_id: UUID, service: Service):
    _call(service.revoke_grant, str(atom_id), str(grant_id))
    return Response(status_code=204)


@router.post("/{atom_id}/schedules", status_code=201, dependencies=[ADMIN])
def schedule(atom_id: UUID, body: ScheduleBody, service: Service):
    return _call(service.set_schedule, str(atom_id), **body.model_dump())


@router.put("/{atom_id}/schedules/{schedule_id}", dependencies=[ADMIN])
def replace_schedule(atom_id: UUID, schedule_id: UUID, body: ScheduleBody, service: Service):
    return _call(
        service.set_schedule, str(atom_id), schedule_id=str(schedule_id), **body.model_dump()
    )


@router.delete("/{atom_id}/schedules/{schedule_id}", dependencies=[ADMIN])
def delete_schedule(atom_id: UUID, schedule_id: UUID, service: Service):
    return _call(service.delete_schedule, str(atom_id), str(schedule_id))


@router.put("/{atom_id}/connections/{connection_id}", dependencies=[ADMIN])
def replace_connection(atom_id: UUID, connection_id: UUID, body: ConnectionBody, service: Service):
    return _call(
        service.set_connection, str(atom_id), connection_id=str(connection_id), **body.model_dump()
    )


@router.delete("/{atom_id}/connections/{connection_id}", dependencies=[ADMIN])
def delete_connection(atom_id: UUID, connection_id: UUID, service: Service):
    return _call(service.delete_connection, str(atom_id), str(connection_id))


@router.put("/{atom_id}/connections", dependencies=[ADMIN])
def connection(atom_id: UUID, body: ConnectionBody, service: Service):
    return _call(service.set_connection, str(atom_id), **body.model_dump())


@router.post("/{atom_id}/skills", dependencies=[ADMIN])
def attach_skill(workspace_id: WorkspaceId, atom_id: UUID, body: SkillAttachment, request: Request):
    from app.atoms.skills import SkillService

    service = SkillService(str(workspace_id), actor=request.state.principal.member_id)
    return _call(
        service.attach,
        str(body.skill_id),
        skill_version=body.skill_version,
        catalog=body.catalog,
        atom_id=str(atom_id),
    )


@router.patch("/{atom_id}/skills/{attachment_id}", dependencies=[ADMIN])
def configure_attachment(
    workspace_id: WorkspaceId,
    atom_id: UUID,
    attachment_id: UUID,
    body: AttachmentState,
    request: Request,
):
    from app.atoms.skills import SkillService

    service = SkillService(str(workspace_id), actor=request.state.principal.member_id)
    return _call(
        service.set_attachment, str(attachment_id), enabled=body.enabled, atom_id=str(atom_id)
    )


@router.post("/{atom_id}/runs", dependencies=[allow(None)])
def start_run(workspace_id: WorkspaceId, atom_id: UUID, body: StartRun):
    service = AtomService(str(workspace_id), actor=None, trusted=True)
    return _call(
        service.run_start,
        str(atom_id),
        schedule_id=str(body.schedule_id),
        scheduled_for=body.scheduled_for,
        idempotency_key=body.idempotency_key,
        estimated_cost=body.estimated_cost,
        estimated_actions=body.estimated_actions,
    )
