from fastapi import APIRouter, Response

from domain.crud import organizations as orgs_crud
from domain.deps import PathOrg, ServiceKey, SessionDep
from domain.errors import DomainRoute, error_responses
from schemas.organization import (
    OrganizationCreate,
    OrganizationCreated,
    OrganizationRead,
    OrganizationUpdate,
)

router = APIRouter(
    prefix="/organizations",
    tags=["organizations"],
    route_class=DomainRoute,
    dependencies=[ServiceKey],
)


@router.post(
    "",
    status_code=201,
    response_model=OrganizationCreated,
    summary="Create an organization with its first member",
    description="One transaction: the organization plus the creator's HUMAN member "
    "(role from creatorMember, default OWNER). An unknown createdById returns 422 "
    "INVALID_REFERENCE and nothing is saved. No X-Organization-Id needed.",
    responses=error_responses(422),
)
def create_organization(body: OrganizationCreate, session: SessionDep):
    org, member = orgs_crud.create_organization(session, body)
    return {"organization": org, "member": member}


@router.get(
    "/{orgId}",
    response_model=OrganizationRead,
    summary="Get an organization",
    responses=error_responses(404, 422),
)
def get_organization(org: PathOrg):
    return org


@router.patch(
    "/{orgId}",
    response_model=OrganizationRead,
    summary="Update an organization",
    description="`archived: true` sets archivedAt, `false` clears it.",
    responses=error_responses(404, 422),
)
def update_organization(org: PathOrg, body: OrganizationUpdate, session: SessionDep):
    return orgs_crud.update_organization(session, org, body)


@router.delete(
    "/{orgId}",
    status_code=204,
    summary="Archive an organization",
    description="Archives (sets archivedAt); it does not remove the row.",
    responses=error_responses(404, 422),
)
def archive_organization(org: PathOrg, session: SessionDep):
    orgs_crud.archive_organization(session, org)
    return Response(status_code=204)
