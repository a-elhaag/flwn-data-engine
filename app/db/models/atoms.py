"""Atom configuration, immutable prompt versions, skill stores and explicit grants.

An atom's member is its identity; deleting an atom or skill is a soft delete so historical
runs and pinned skill versions remain meaningful. Connections contain references, not tokens.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    ForeignKeyConstraint,
    Index,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    EMPTY_JSON,
    EMPTY_LIST,
    Base,
    Created,
    IdPk,
    SoftDelete,
    Stamps,
    Tenant,
    one_of,
    tenant_unique,
    tfk,
)
from app.db.models.memory import EMBEDDING_DIM

RESOURCE_TYPES = (
    "project",
    "team",
    "collection",
    "channel",
    "folder",
    "memory",
    "meeting",
    "connection",
)
PERMISSION_CEILINGS = ("read", "draft", "write", "destructive")
SKILL_TSV = "to_tsvector('simple', coalesce(description, '') || ' ' || coalesce(when_to_use, ''))"


class Atom(IdPk, Tenant, Stamps, SoftDelete, Base):
    __tablename__ = "atoms"

    member_id: Mapped[uuid.UUID]
    kind: Mapped[str]
    owner_member_id: Mapped[uuid.UUID | None]
    name: Mapped[str]
    description: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default="draft")
    model_tier: Mapped[str]
    active_version_id: Mapped[uuid.UUID | None]
    max_runs_per_day: Mapped[int]
    max_cost_per_day: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    max_actions_per_day: Mapped[int]

    __table_args__ = (
        tenant_unique(),
        UniqueConstraint("member_id"),
        one_of("kind", "workspace", "personal"),
        one_of("status", "draft", "active", "paused", "killed"),
        one_of("model_tier", "small", "medium"),
        CheckConstraint(
            "(kind = 'personal' and owner_member_id is not null)"
            " or (kind = 'workspace' and owner_member_id is null)",
            name="owner_kind",
        ),
        CheckConstraint(
            "max_runs_per_day >= 0 and max_cost_per_day >= 0"
            " and max_cost_per_day < 'Infinity'::numeric and max_actions_per_day >= 0",
            name="nonnegative_caps",
        ),
        CheckConstraint(
            "status <> 'active' or (active_version_id is not null and deleted_at is null)",
            name="active_version_required",
        ),
        tfk("member_id", "members"),
        tfk("owner_member_id", "members"),
        # The version must belong to this atom, not just to the same workspace.
        ForeignKeyConstraint(
            ["workspace_id", "id", "active_version_id"],
            ["atom_versions.workspace_id", "atom_versions.atom_id", "atom_versions.id"],
            name="fk_atoms_active_version",
            use_alter=True,
        ),
    )


class AtomVersion(IdPk, Tenant, Created, Base):
    __tablename__ = "atom_versions"

    atom_id: Mapped[uuid.UUID]
    version: Mapped[int]
    instructions: Mapped[str]
    policy: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    tools: Mapped[list] = mapped_column(server_default=EMPTY_LIST)
    source: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="candidate")
    eval: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    created_by: Mapped[uuid.UUID]

    __table_args__ = (
        tenant_unique(),
        UniqueConstraint("atom_id", "version"),
        UniqueConstraint("workspace_id", "atom_id", "id"),
        CheckConstraint("version > 0", name="positive_version"),
        CheckConstraint("jsonb_typeof(policy) = 'object'", name="policy_object"),
        CheckConstraint("jsonb_typeof(tools) = 'array'", name="tools_array"),
        CheckConstraint("jsonb_typeof(eval) = 'object'", name="eval_object"),
        one_of("source", "human", "atomizer", "self"),
        one_of("status", "candidate", "active", "rolled_back", "rejected"),
        tfk("atom_id", "atoms", "cascade"),
        tfk("created_by", "members"),
    )


class SkillContent:
    name: Mapped[str]
    version: Mapped[int]
    description: Mapped[str]
    when_to_use: Mapped[str]
    instructions: Mapped[str]
    tools_required: Mapped[list] = mapped_column(server_default=EMPTY_LIST)
    trust_tier: Mapped[str] = mapped_column(server_default="community")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), deferred=True)
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, Computed(SKILL_TSV, persisted=True))
    status: Mapped[str] = mapped_column(server_default="active")


def _skill_constraints():
    return (
        CheckConstraint("version > 0", name="positive_version"),
        CheckConstraint("jsonb_typeof(tools_required) = 'array'", name="tools_required_array"),
        one_of("trust_tier", "official", "verified", "community"),
    )


class Skill(IdPk, Tenant, Stamps, SoftDelete, SkillContent, Base):
    __tablename__ = "skills"

    scope: Mapped[str]
    owner_member_id: Mapped[uuid.UUID | None]

    __table_args__ = (
        tenant_unique(),
        UniqueConstraint("workspace_id", "name", "version"),
        UniqueConstraint("workspace_id", "id", "version"),
        one_of("scope", "workspace", "personal"),
        CheckConstraint(
            "(scope = 'personal' and owner_member_id is not null)"
            " or (scope = 'workspace' and owner_member_id is null)",
            name="owner_scope",
        ),
        tfk("owner_member_id", "members"),
        *_skill_constraints(),
    )


class CatalogSkill(IdPk, Stamps, SoftDelete, SkillContent, Base):
    __tablename__ = "catalog_skills"

    __table_args__ = (
        UniqueConstraint("name", "version"),
        UniqueConstraint("id", "version"),
        *_skill_constraints(),
    )


for model in (Skill, CatalogSkill):
    Index(f"ix_{model.__tablename__}_tsv", model.tsv, postgresql_using="gin")
    Index(
        f"ix_{model.__tablename__}_embedding",
        model.embedding,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


class AtomSkill(IdPk, Tenant, Created, Base):
    __tablename__ = "atom_skills"

    atom_id: Mapped[uuid.UUID]
    skill_id: Mapped[uuid.UUID | None]
    catalog_skill_id: Mapped[uuid.UUID | None]
    skill_version: Mapped[int]
    added_by: Mapped[str]
    added_by_member_id: Mapped[uuid.UUID]
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")

    __table_args__ = (
        tenant_unique(),
        UniqueConstraint("atom_id", "skill_id"),
        UniqueConstraint("atom_id", "catalog_skill_id"),
        CheckConstraint("num_nonnulls(skill_id, catalog_skill_id) = 1", name="one_skill"),
        CheckConstraint("skill_version > 0", name="positive_version"),
        one_of("added_by", "human", "atomizer", "self"),
        tfk("atom_id", "atoms", "cascade"),
        tfk("added_by_member_id", "members"),
        ForeignKeyConstraint(
            ["workspace_id", "skill_id", "skill_version"],
            ["skills.workspace_id", "skills.id", "skills.version"],
        ),
        ForeignKeyConstraint(
            ["catalog_skill_id", "skill_version"], ["catalog_skills.id", "catalog_skills.version"]
        ),
    )


class AtomSchedule(IdPk, Tenant, Stamps, Base):
    __tablename__ = "atom_schedules"

    atom_id: Mapped[uuid.UUID]
    cron: Mapped[str | None]
    interval_minutes: Mapped[int | None]
    timezone: Mapped[str] = mapped_column(server_default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    next_run_at: Mapped[datetime | None]
    last_run_at: Mapped[datetime | None]
    cursor: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)

    __table_args__ = (
        tenant_unique(),
        CheckConstraint("num_nonnulls(cron, interval_minutes) = 1", name="one_cadence"),
        CheckConstraint("cron is null or btrim(cron) <> ''", name="nonempty_cron"),
        CheckConstraint(
            "interval_minutes is null or interval_minutes >= 5", name="minimum_interval"
        ),
        CheckConstraint("jsonb_typeof(cursor) = 'object'", name="cursor_object"),
        tfk("atom_id", "atoms", "cascade"),
        Index("ix_atom_schedules_atom_id", "atom_id"),
        Index("ix_atom_schedules_due", "enabled", "next_run_at"),
    )


class AtomConnection(IdPk, Tenant, Stamps, Base):
    __tablename__ = "atom_connections"

    atom_id: Mapped[uuid.UUID]
    toolkit: Mapped[str]
    composio_user_id: Mapped[str]
    composio_account_ref: Mapped[str | None]
    toolkit_version: Mapped[str]
    permission_ceiling: Mapped[str]
    allowed_tools: Mapped[list] = mapped_column(server_default=EMPTY_LIST)
    status: Mapped[str] = mapped_column(server_default="unknown")
    status_checked_at: Mapped[datetime | None]
    status_detail: Mapped[str | None]
    connected_by: Mapped[uuid.UUID]

    __table_args__ = (
        tenant_unique(),
        one_of("permission_ceiling", *PERMISSION_CEILINGS),
        one_of("status", "active", "expired", "revoked", "unknown"),
        CheckConstraint(
            "btrim(toolkit) <> '' and btrim(composio_user_id) <> ''", name="connection_identity"
        ),
        CheckConstraint(
            "btrim(toolkit_version) <> '' and lower(btrim(toolkit_version)) <> 'latest'",
            name="pinned_toolkit_version",
        ),
        CheckConstraint("jsonb_typeof(allowed_tools) = 'array'", name="allowed_tools_array"),
        CheckConstraint(
            'not jsonb_path_exists(allowed_tools, \'$[*] ? (@.type() != "string" || @ == "")\')',
            name="allowed_tools_slugs",
        ),
        CheckConstraint(
            "status <> 'active' or composio_account_ref is not null", name="active_account_required"
        ),
        CheckConstraint(
            "composio_account_ref is null or btrim(composio_account_ref) <> ''",
            name="nonempty_account_ref",
        ),
        UniqueConstraint("atom_id", "toolkit"),
        tfk("atom_id", "atoms", "cascade"),
        tfk("connected_by", "members"),
    )


class AtomGrant(IdPk, Tenant, Stamps, Base):
    __tablename__ = "atom_grants"

    atom_id: Mapped[uuid.UUID]
    resource_type: Mapped[str]
    resource_id: Mapped[uuid.UUID | None]
    level: Mapped[str]
    constraints: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)

    __table_args__ = (
        tenant_unique(),
        UniqueConstraint(
            "atom_id", "resource_type", "resource_id", postgresql_nulls_not_distinct=True
        ),
        one_of("resource_type", *RESOURCE_TYPES),
        one_of("level", "summary", "read", "write"),
        CheckConstraint("jsonb_typeof(constraints) = 'object'", name="constraints_object"),
        tfk("atom_id", "atoms", "cascade"),
        Index("ix_atom_grants_lookup", "workspace_id", "atom_id", "resource_type"),
    )
