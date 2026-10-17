from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Query
from sqlalchemy.orm import Session

from api.auth import service_only
from db.models import Organization
from db.session import get_session
from domain.crud.organizations import get_organization
from domain.errors import DomainError

ServiceKey = Depends(service_only)
SessionDep = Annotated[Session, Depends(get_session)]


def org_header(
    x_organization_id: Annotated[
        str | None,
        Header(
            alias="X-Organization-Id",
            description="Organization this request is scoped to (required on every "
            "route that does not start with /organizations/{orgId}).",
        ),
    ] = None,
) -> UUID:
    if x_organization_id is None or not x_organization_id.strip():
        raise DomainError(
            400, "ORGANIZATION_ID_REQUIRED", "X-Organization-Id header is required"
        )
    try:
        return UUID(x_organization_id.strip())
    except ValueError:
        raise DomainError(
            422, "VALIDATION_ERROR", "X-Organization-Id must be a UUID", "X-Organization-Id"
        )


OrgId = Annotated[UUID, Depends(org_header)]


def org_from_path(orgId: UUID, session: SessionDep) -> Organization:  # noqa: N803
    return get_organization(session, orgId)


PathOrg = Annotated[Organization, Depends(org_from_path)]


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def pagination(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    return Pagination(limit, offset)


PageParams = Annotated[Pagination, Depends(pagination)]
