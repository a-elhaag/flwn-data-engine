from uuid import UUID

from sqlalchemy.orm import Session

from db.enums import MemberType
from db.models import Member, Organization
from domain.crud import apply_update, archive
from domain.errors import not_found
from schemas.organization import OrganizationCreate, OrganizationUpdate


def create_organization(
    session: Session, data: OrganizationCreate
) -> tuple[Organization, Member]:
    """Organization and its first (creator) member, in one transaction. A missing
    user fails an FK on the first flush and nothing is saved."""
    org = Organization(
        created_by_id=data.created_by_id, name=data.name, description=data.description
    )
    session.add(org)
    session.flush()
    member = Member(
        organization_id=org.id,
        user_id=data.created_by_id,
        type=MemberType.HUMAN,
        role=data.creator_member.role,
        created_by_id=None,
        workspace_id=None,
    )
    session.add(member)
    session.commit()
    session.refresh(org)
    session.refresh(member)
    return org, member


def get_organization(session: Session, org_id: UUID) -> Organization:
    org = session.get(Organization, org_id)
    if org is None:
        raise not_found("ORGANIZATION")
    return org


def update_organization(
    session: Session, org: Organization, data: OrganizationUpdate
) -> Organization:
    return apply_update(session, org, data)


def archive_organization(session: Session, org: Organization) -> None:
    archive(session, org)
