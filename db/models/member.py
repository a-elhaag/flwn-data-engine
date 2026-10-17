import uuid

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, IdMixin, TimestampMixin
from db.enums import MemberRole, MemberStatus, MemberType
from db.models.user import User


class Member(IdMixin, TimestampMixin, Base):
    __tablename__ = "members"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("organization_id", "user_id"),
        ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["created_by_id", "organization_id"],
            ["members.id", "members.organization_id"],
        ),
        CheckConstraint(
            "(type = 'HUMAN' AND user_id IS NOT NULL AND name IS NULL AND email IS NULL)"
            " OR (type = 'AI' AND user_id IS NULL AND name IS NOT NULL)",
            name="human_or_ai",
        ),
        CheckConstraint(
            "name IS NULL OR char_length(btrim(name)) > 0", name="name_not_blank"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    type: Mapped[MemberType] = mapped_column(Enum(MemberType, name="member_type"))
    name: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(254))
    role: Mapped[MemberRole] = mapped_column(
        Enum(MemberRole, name="member_role"), server_default="MEMBER"
    )
    status: Mapped[MemberStatus] = mapped_column(
        Enum(MemberStatus, name="member_status"), server_default="ACTIVE"
    )

    user: Mapped[User | None] = relationship(lazy="joined")

    @property
    def display_name(self) -> str | None:
        return self.user.name if self.type == MemberType.HUMAN and self.user else self.name

    @property
    def display_email(self) -> str | None:
        return (
            self.user.email if self.type == MemberType.HUMAN and self.user else self.email
        )


Index(
    "uq_members_organization_id_name_ai",
    Member.organization_id,
    func.lower(Member.name),
    unique=True,
    postgresql_where=text("type = 'AI'"),
)
Index(
    "uq_members_organization_id_email_ai",
    Member.organization_id,
    func.lower(Member.email),
    unique=True,
    postgresql_where=text("type = 'AI' AND email IS NOT NULL"),
)
