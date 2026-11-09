"""Version-pinned skills; discovery embeds metadata, never executable instructions."""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.db.atom_access import (
    AtomContext,
    current_context,
    privileged_atom_session,
    validate_context,
)
from app.db.models.atoms import Atom, AtomConnection, AtomSkill, CatalogSkill, Skill
from app.db.models.identity import Member
from app.db.session import tune_vector_search
from app.domain.error_codes import Forbidden, Invalid, Missing
from app.domain.memory import vectorizer
from app.integrations.ranking import FUSION_K, fuse

PERMISSIONS = {name: i for i, name in enumerate(("read", "draft", "write", "destructive"))}


def _uuid(value):
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _requirements(value):
    if not isinstance(value, list):
        raise Invalid("tools_required must be a list", code="invalid")
    for item in value:
        if not isinstance(item, dict) or set(item) != {"ref", "minimum_permission"}:
            raise Invalid("tools_required entries need ref and minimum_permission", code="invalid")
        if not isinstance(item["ref"], str) or not item["ref"].strip():
            raise Invalid("tool ref must be nonempty", code="invalid")
        if (
            not isinstance(item["minimum_permission"], str)
            or item["minimum_permission"] not in PERMISSIONS
        ):
            raise Invalid("unknown minimum_permission", code="invalid")
    return value


def _skill_dict(row, catalog=False, *, instructions=False):
    result = {
        "id": str(row.id),
        "name": row.name,
        "version": row.version,
        "description": row.description,
        "when_to_use": row.when_to_use,
        "tools_required": row.tools_required,
        "trust_tier": row.trust_tier,
        "community": row.trust_tier == "community",
        "catalog": catalog,
        "scope": "catalog" if catalog else row.scope,
    }
    if instructions:
        result["instructions"] = row.instructions
    return result


@dataclass
class _Candidate:
    id: tuple
    row: object
    catalog: bool

    @property
    def preference(self):
        return 0 if self.catalog else (2 if self.row.scope == "personal" else 1)


class SkillService:
    def __init__(self, workspace_id, actor, *, atom_id=None, run_id=None, bind=None):
        self.workspace_id = _uuid(workspace_id)
        self.actor = _uuid(actor)
        self.atom_id = _uuid(atom_id) if atom_id else None
        self.run_id = _uuid(run_id) if run_id else None
        self.bind = bind
        self._visible_owners = {self.actor}

    def _session(self):
        context = current_context()
        if context is not None and (
            str(self.actor) != str(context.member_id)
            or str(self.atom_id) != str(context.atom_id)
            or str(self.run_id) != str(context.run_id)
        ):
            raise Forbidden("service caller must match the bound atom context", code="denied")
        if context is None and self.atom_id and self.run_id:
            # Validate before elevation: a shared Connection retains transaction-local GUCs.
            validate_context(
                AtomContext(
                    str(self.workspace_id), str(self.atom_id), str(self.run_id), str(self.actor)
                ),
                bind=self.bind,
            )
        return privileged_atom_session(str(self.workspace_id), bind=self.bind)

    def _member(self, session):
        self._visible_owners = {self.actor}
        member = session.scalar(
            select(Member).where(
                Member.workspace_id == self.workspace_id,
                Member.id == self.actor,
                Member.status == "active",
            )
        )
        if member is None:
            raise Forbidden("active workspace member required", code="denied")
        if member.type != "HUMAN":
            if member.agent_kind != "atom" or not self.atom_id or not self.run_id:
                raise Forbidden("a bound atom run is required", code="denied")
            owner = session.scalar(
                select(Atom.owner_member_id).where(
                    Atom.workspace_id == self.workspace_id,
                    Atom.id == self.atom_id,
                    Atom.kind == "personal",
                )
            )
            if owner is not None:
                self._visible_owners.add(owner)
        return member

    def _atom(self, session, atom_id):
        member = self._member(session)
        target = _uuid(atom_id) if atom_id else self.atom_id
        if target is None:
            raise Invalid("atom_id is required", code="invalid")
        row = session.scalar(
            select(Atom)
            .where(
                Atom.workspace_id == self.workspace_id,
                Atom.id == target,
            )
            .with_for_update()
        )
        if row is None:
            raise Missing("atom not found", code="not_found")
        if member.type == "HUMAN":
            if row.kind == "personal":
                if row.owner_member_id != self.actor:
                    raise Forbidden("personal atom owner required", code="denied")
            elif member.role not in ("admin", "owner"):
                raise Forbidden("workspace admin required", code="denied")
        elif target != self.atom_id or row.member_id != self.actor:
            raise Forbidden("atom may only attach its own skills", code="denied")
        return row, member

    def _visible(self, model):
        conditions = [model.status == "active"]
        if model is Skill:
            conditions += [
                Skill.workspace_id == self.workspace_id,
                or_(Skill.scope == "workspace", Skill.owner_member_id.in_(self._visible_owners)),
            ]
        return conditions

    def search(self, query, limit=5):
        if not isinstance(query, str) or not query.strip():
            raise Invalid("query must be nonempty", code="invalid")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise Invalid("limit must be between 1 and 50", code="invalid")
        with self._session() as session:
            self._member(session)
            vector = vectorizer.embed_one(query)
            breadth = min(max(limit * 3, 10), 150)
            tune_vector_search(session, breadth)
            meaning, words = [], []
            for model in (Skill, CatalogSkill):
                catalog = model is CatalogSkill
                distance = model.embedding.cosine_distance(vector)
                for row, score in session.execute(
                    select(model, 1 - distance)
                    .where(
                        *self._visible(model),
                        model.embedding.is_not(None),
                    )
                    .order_by(distance, model.id)
                    .limit(breadth)
                ):
                    meaning.append((_Candidate((catalog, row.id), row, catalog), float(score)))
                tsquery = func.plainto_tsquery("simple", query)
                rank = func.ts_rank_cd(model.tsv, tsquery)
                for row, score in session.execute(
                    select(model, rank)
                    .where(
                        *self._visible(model),
                        model.tsv.op("@@")(tsquery),
                    )
                    .order_by(rank.desc(), model.id)
                    .limit(breadth)
                ):
                    words.append((_Candidate((catalog, row.id), row, catalog), float(score)))
            rankings = []
            scores = {}
            for hits in (meaning, words):
                hits.sort(key=lambda hit: (-hit[1], -hit[0].preference, str(hit[0].row.id)))
                ranking = [row for row, _ in hits[:breadth]]
                rankings.append(ranking)
                for position, row in enumerate(ranking, 1):
                    scores[row.id] = scores.get(row.id, 0) + 1 / (FUSION_K + position)
            ranked = fuse(*rankings)
            ranked.sort(key=lambda row: (-scores[row.id], -row.preference))
            return [
                dict(_skill_dict(row.row, row.catalog), score=scores[row.id])
                for row in ranked[:limit]
            ]

    def write(
        self,
        name,
        description,
        when_to_use,
        instructions,
        *,
        scope="workspace",
        version=1,
        tools_required=None,
        owner_member_id=None,
    ):
        if scope not in ("workspace", "personal"):
            raise Forbidden("catalog publication is a trusted service operation", code="denied")
        if type(version) is not int or version < 1:
            raise Invalid("version must be positive", code="invalid")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (name, description, when_to_use, instructions)
        ):
            raise Invalid("skill content must be nonempty", code="invalid")
        requirements = _requirements([] if tools_required is None else tools_required)
        with self._session() as session:
            member = self._member(session)
            if member.role == "viewer":
                raise Forbidden("viewers cannot write skills", code="denied")
            if scope == "workspace":
                if member.type == "HUMAN":
                    if member.role not in ("admin", "owner"):
                        raise Forbidden(
                            "workspace skill writes require a human admin", code="denied"
                        )
                else:
                    atom, _ = self._atom(session, self.atom_id)
                    if atom.kind != "workspace":
                        raise Forbidden(
                            "personal atoms may only write personal skills", code="denied"
                        )
            row = Skill(
                workspace_id=self.workspace_id,
                name=name,
                version=version,
                scope=scope,
                owner_member_id=self.actor if scope == "personal" else None,
                description=description,
                when_to_use=when_to_use,
                instructions=instructions,
                tools_required=requirements,
                trust_tier="community",
                embedding=vectorizer.embed_one(f"{description}\n{when_to_use}"),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError as exc:
                raise Invalid(
                    "skill name and version must be unique in the workspace", code="invalid"
                ) from exc
            return _skill_dict(row, instructions=True)

    def _admin_skill(self, session, skill_id):
        member = self._member(session)
        if member.type != "HUMAN" or member.role not in ("owner", "admin"):
            raise Forbidden("skill administration requires a human admin", code="denied")
        row = session.scalar(
            select(Skill)
            .where(
                *self._visible(Skill),
                Skill.id == _uuid(skill_id),
            )
            .with_for_update()
        )
        if row is None:
            raise Missing("skill not found", code="not_found")
        return row

    def promote(self, skill_id):
        """Make an admin's personal skill workspace-visible; catalog remains service-only."""
        with self._session() as session:
            row = self._admin_skill(session, skill_id)
            row.scope = "workspace"
            row.owner_member_id = None
            session.flush()
            return _skill_dict(row, instructions=True)

    def delete(self, skill_id):
        with self._session() as session:
            row = self._admin_skill(session, skill_id)
            skill = str(row.id)
            session.delete(row)  # atoms that had it detach it (ON DELETE CASCADE)
            session.flush()
            return {"id": skill, "deleted": True}

    def set_attachment(self, attachment_id, *, enabled, atom_id=None):
        if type(enabled) is not bool:
            raise Invalid("enabled must be boolean", code="invalid")
        with self._session() as session:
            atom, member = self._atom(session, atom_id)
            if member.type != "HUMAN" or member.role not in ("owner", "admin"):
                raise Forbidden("attachment overrides require a human admin", code="denied")
            row = session.scalar(
                select(AtomSkill)
                .where(
                    AtomSkill.workspace_id == self.workspace_id,
                    AtomSkill.atom_id == atom.id,
                    AtomSkill.id == _uuid(attachment_id),
                )
                .with_for_update()
            )
            if row is None:
                raise Missing("attachment not found", code="not_found")
            if enabled:
                model = CatalogSkill if row.catalog_skill_id else Skill
                skill = session.scalar(
                    select(model).where(
                        *self._visible(model),
                        model.id == (row.catalog_skill_id or row.skill_id),
                        model.version == row.skill_version,
                    )
                )
                if skill is None:
                    raise Missing("active skill version not found", code="not_found")
                self._connections(session, atom, skill.tools_required)
            row.enabled = enabled
            return {"id": str(row.id), "enabled": enabled}

    def _connections(self, session, atom, requirements):
        for requirement in _requirements(requirements):
            ref = requirement["ref"]
            if ref.startswith("flwn:"):
                if not ref.removeprefix("flwn:").strip():
                    raise Invalid("native tool ref is empty", code="invalid")
                continue
            if not ref.startswith("composio:") or "/" not in ref:
                raise Invalid(
                    "tool ref must be flwn:<tool> or composio:<toolkit>/<tool>", code="invalid"
                )
            toolkit, tool = ref.removeprefix("composio:").split("/", 1)
            if not toolkit or not tool or "/" in tool:
                raise Invalid("invalid Composio tool ref", code="invalid")
            connection = session.scalar(
                select(AtomConnection)
                .where(
                    AtomConnection.workspace_id == self.workspace_id,
                    AtomConnection.atom_id == atom.id,
                    AtomConnection.toolkit == toolkit,
                )
                .with_for_update()
            )
            if (
                connection is None
                or connection.status != "active"
                or not connection.composio_account_ref
            ):
                raise Forbidden("an active connected account is required", code="denied")
            if (
                not connection.toolkit_version.strip()
                or connection.toolkit_version.strip().lower() == "latest"
            ):
                raise Forbidden("a pinned toolkit version is required", code="denied")
            if tool not in connection.allowed_tools:
                raise Forbidden("tool is not explicitly allowed by the connection", code="denied")
            if (
                PERMISSIONS[requirement["minimum_permission"]]
                > PERMISSIONS[connection.permission_ceiling]
            ):
                raise Forbidden("tool exceeds connection permission ceiling", code="denied")
            if atom.kind == "personal" and connection.composio_user_id != str(atom.owner_member_id):
                raise Forbidden("personal connection must belong to the atom owner", code="denied")

    def attach(self, skill_id, *, skill_version, catalog=False, atom_id=None):
        if type(skill_version) is not int or skill_version < 1:
            raise Invalid("skill_version must be positive", code="invalid")
        with self._session() as session:
            atom, member = self._atom(session, atom_id)
            model = CatalogSkill if catalog else Skill
            row = session.scalar(
                select(model)
                .where(
                    *self._visible(model),
                    model.id == _uuid(skill_id),
                    model.version == skill_version,
                )
                .with_for_update()
            )
            if row is None:
                raise Missing("active skill version not found", code="not_found")
            if not catalog and row.scope == "personal" and atom.kind != "personal":
                raise Forbidden(
                    "personal skills cannot be attached to workspace atoms", code="denied"
                )
            self._connections(session, atom, row.tools_required)
            field = AtomSkill.catalog_skill_id if catalog else AtomSkill.skill_id
            attached = session.scalar(
                select(AtomSkill).where(
                    AtomSkill.workspace_id == self.workspace_id,
                    AtomSkill.atom_id == atom.id,
                    field == row.id,
                )
            )
            if attached is None:
                attached = AtomSkill(
                    workspace_id=self.workspace_id,
                    atom_id=atom.id,
                    skill_id=None if catalog else row.id,
                    catalog_skill_id=row.id if catalog else None,
                    skill_version=skill_version,
                    added_by="human" if member.type == "HUMAN" else "self",
                    added_by_member_id=self.actor,
                )
                session.add(attached)
            elif not attached.enabled:
                if member.type != "HUMAN" or member.role not in ("owner", "admin"):
                    raise Forbidden(
                        "disabled attachments require a human admin override", code="denied"
                    )
                attached.enabled = True
            session.flush()
            return {
                "id": str(attached.id),
                "atom_id": str(atom.id),
                "skill_id": str(row.id),
                "skill_version": skill_version,
                "catalog": catalog,
                "enabled": attached.enabled,
                "community": row.trust_tier == "community",
            }

    def attached(self, atom_id=None):
        """Load enabled pinned content, rechecking visibility and connection authorization."""
        return self.list_attached(atom_id)

    def list_attached(self, atom_id=None):
        with self._session() as session:
            atom, _ = self._atom(session, atom_id)
            result = []
            attachments = session.scalars(
                select(AtomSkill).where(
                    AtomSkill.workspace_id == self.workspace_id,
                    AtomSkill.atom_id == atom.id,
                    AtomSkill.enabled.is_(True),
                )
            ).all()
            for attached in attachments:
                catalog = attached.catalog_skill_id is not None
                model = CatalogSkill if catalog else Skill
                row = session.scalar(
                    select(model).where(
                        *self._visible(model),
                        model.id == (attached.catalog_skill_id or attached.skill_id),
                        model.version == attached.skill_version,
                    )
                )
                if row is not None:
                    self._connections(session, atom, row.tools_required)
                    result.append(_skill_dict(row, catalog, instructions=True))
            return result
