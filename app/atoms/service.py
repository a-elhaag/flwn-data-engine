"""Transactional Atom control plane and scheduled-run accounting.

Only active human owners/admins administer atoms. Atom callers can load their
configuration, propose (never activate) a version, and report their own run.
"""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, inspect, select

from app.db.models.atoms import (
    PERMISSION_CEILINGS,
    RESOURCE_TYPES,
    Atom,
    AtomConnection,
    AtomGrant,
    AtomSchedule,
    AtomVersion,
)
from app.db.models.identity import Member
from app.db.models.memory import AgentRun, LlmUsage


class AtomError(ValueError):
    pass


class AtomPermissionError(PermissionError):
    pass


class AtomNotFound(LookupError):
    pass


class AtomStateError(AtomError):
    pass


class AtomBudgetExceeded(AtomStateError):
    pass


def _uuid(value):
    return uuid.UUID(str(value)) if value is not None else None


def _json(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


def _view(row):
    return {col.key: _json(getattr(row, col.key)) for col in inspect(row).mapper.column_attrs}


def _time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AtomError("a timezone-aware timestamp is required")
    return value.astimezone(UTC)


def _amount(value):
    try:
        value = Decimal(str(value))
        if not value.is_finite() or value < 0 or value >= 1000000:
            raise AtomError("amount must be finite, nonnegative, and below 1000000")
        return value.quantize(Decimal("0.000001"))
    except InvalidOperation:
        raise AtomError("invalid amount") from None


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AtomError("count must be a nonnegative integer")
    return value


def _object(value):
    if value is not None and not isinstance(value, dict):
        raise AtomError("expected an object")
    return dict(value or {})


def _tools(value):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise AtomError("tools must be nonempty strings")
    return list(value)


def _validate_cron(cron):
    if not isinstance(cron, str) or len(cron.split()) != 5:
        raise AtomError("cron must have five fields")
    for field, (low, high) in zip(
        cron.split(), ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7)), strict=True
    ):
        try:
            for part in field.split(","):
                base, *step = part.split("/")
                if len(step) > 1 or (step and not 1 <= int(step[0]) <= high - low + 1):
                    raise ValueError
                if base == "*":
                    continue
                bounds = [int(value) for value in base.split("-")]
                if len(bounds) not in (1, 2) or not low <= bounds[0] <= bounds[-1] <= high:
                    raise ValueError
        except ValueError:
            raise AtomError("invalid numeric cron field") from None


class AtomService:
    def __init__(
        self,
        workspace_id,
        actor=None,
        *,
        member_id=None,
        atom_id=None,
        run_id=None,
        bind=None,
        trusted=False,
    ):
        self.workspace = _uuid(workspace_id)
        self.workspace_id = str(self.workspace)
        self.actor = _uuid(member_id or actor)
        self.atom_id = _uuid(atom_id)
        self.run_id = _uuid(run_id)
        self.bind = bind
        self.trusted = trusted

    @contextmanager
    def _session(self, *, admin=False, atom_id=None, run_id=None, finishing=False, scheduler=False):
        from app.atoms.access import bind_context, current_context, privileged_atom_session

        target = _uuid(atom_id) or self.atom_id
        run_target = _uuid(run_id) or self.run_id
        context = current_context()
        if context is not None:
            if (
                _uuid(context.workspace_id) != self.workspace
                or _uuid(context.member_id) != self.actor
                or target != _uuid(context.atom_id)
                or (run_target is not None and run_target != _uuid(context.run_id))
                or admin
            ):
                raise AtomPermissionError("caller context does not match the requested operation")
        # Only finalization may accept a terminal bound run; all identity and owner
        # checks below still execute before data is read or changed.
        with (
            bind_context(None if finishing else context),
            privileged_atom_session(self.workspace_id, bind=self.bind) as session,
        ):
            member = session.scalar(
                select(Member).where(
                    Member.workspace_id == self.workspace,
                    Member.id == self.actor,
                    Member.status == "active",
                    Member.deleted_at.is_(None),
                )
            )
            if scheduler and self.trusted and self.actor is None and context is None:
                yield session
                return
            if member is None:
                raise AtomPermissionError("active workspace member required")
            human_admin = member.type == "HUMAN" and member.role in ("owner", "admin")
            if admin or member.type == "HUMAN":
                if not human_admin:
                    raise AtomPermissionError("active human owner/admin required")
            else:
                if member.agent_kind != "atom" or target is None or run_target is None:
                    raise AtomPermissionError("a bound atom and run are required")
                atom = self._atom(session, target)
                run = self._row(session, AgentRun, run_target)
                if (
                    atom.member_id != member.id
                    or run.agent_id != member.id
                    or run.atom_id != atom.id
                ):
                    raise AtomPermissionError("atom/run identity mismatch")
                if atom.status not in ("active", "paused"):
                    raise AtomPermissionError("atom is not available")
                # Finished-run retries are accepted only by finish_run, which returns stored data.
                if run.status != "running" and not finishing:
                    raise AtomPermissionError("run is not running")
                if atom.owner_member_id:
                    owner = self._row(session, Member, atom.owner_member_id)
                    if owner.type != "HUMAN" or owner.status != "active" or owner.deleted_at:
                        raise AtomPermissionError("personal owner must remain active")
            yield session

    def _row(self, session, model, row_id, *, atom_id=None, lock=False):
        query = select(model).where(model.workspace_id == self.workspace, model.id == _uuid(row_id))
        if atom_id is not None:
            query = query.where(model.atom_id == _uuid(atom_id))
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = session.scalar(query)
        if row is None:
            raise AtomNotFound(f"{model.__tablename__} not found")
        return row

    def _atom(self, session, atom_id, *, lock=False):
        atom = self._row(session, Atom, atom_id, lock=lock)
        if atom.deleted_at is not None:
            raise AtomNotFound("atom not found")
        if self.actor == atom.member_id and atom.status not in ("active", "paused"):
            raise AtomPermissionError("atom is not available")
        return atom

    def create(
        self,
        name,
        *,
        kind="workspace",
        owner_member_id=None,
        description=None,
        model_tier="small",
        max_runs_per_day=10,
        max_cost_per_day="1",
        max_actions_per_day=100,
    ):
        if kind not in ("workspace", "personal") or bool(owner_member_id) != (kind == "personal"):
            raise AtomError("personal atoms require an owner; workspace atoms cannot have one")
        self._validate_config(
            dict(
                name=name,
                model_tier=model_tier,
                max_runs_per_day=max_runs_per_day,
                max_cost_per_day=max_cost_per_day,
                max_actions_per_day=max_actions_per_day,
            )
        )
        with self._session(admin=True) as session:
            if owner_member_id:
                owner = self._row(session, Member, owner_member_id)
                if owner.type != "HUMAN" or owner.status != "active" or owner.deleted_at:
                    raise AtomError("personal owner must be an active human")
            member = Member(workspace_id=self.workspace, type="AI", agent_kind="atom", name=name)
            session.add(member)
            session.flush()
            atom = Atom(
                workspace_id=self.workspace,
                member_id=member.id,
                kind=kind,
                owner_member_id=_uuid(owner_member_id),
                name=name,
                description=description,
                model_tier=model_tier,
                max_runs_per_day=max_runs_per_day,
                max_cost_per_day=_amount(max_cost_per_day),
                max_actions_per_day=max_actions_per_day,
            )
            session.add(atom)
            session.flush()
            return _view(atom)

    @staticmethod
    def _validate_config(fields):
        allowed = {
            "name",
            "description",
            "model_tier",
            "max_runs_per_day",
            "max_cost_per_day",
            "max_actions_per_day",
        }
        if fields.keys() - allowed:
            raise AtomError("unsupported configuration fields")
        if "name" in fields and (not isinstance(fields["name"], str) or not fields["name"].strip()):
            raise AtomError("name is required")
        if "model_tier" in fields and fields["model_tier"] not in ("small", "medium"):
            raise AtomError("invalid model tier")
        for key in ("max_runs_per_day", "max_actions_per_day"):
            if key in fields:
                _count(fields[key])
        if "max_cost_per_day" in fields:
            fields["max_cost_per_day"] = _amount(fields["max_cost_per_day"])

    def configure(self, atom_id, **fields):
        self._validate_config(fields)
        with self._session(admin=True) as session:
            atom = self._atom(session, atom_id, lock=True)
            for key, value in fields.items():
                setattr(atom, key, value)
            session.flush()
            return _view(atom)

    def _configuration(self, session, atom):
        version_id = atom.active_version_id
        if self.run_id:
            version_id = self._row(session, AgentRun, self.run_id, atom_id=atom.id).atom_version_id
        result = _view(atom)
        result["version"] = (
            _view(self._row(session, AtomVersion, version_id, atom_id=atom.id))
            if version_id
            else None
        )
        for key, model in (
            ("grants", AtomGrant),
            ("schedules", AtomSchedule),
            ("connections", AtomConnection),
        ):
            result[key] = [
                _view(row)
                for row in session.scalars(
                    select(model)
                    .where(model.workspace_id == self.workspace, model.atom_id == atom.id)
                    .order_by(model.id)
                )
            ]
        return result

    def get(self, atom_id=None):
        target = atom_id or self.atom_id
        with self._session(atom_id=target, scheduler=True) as session:
            return self._configuration(session, self._atom(session, target))

    def list(self):
        with self._session(admin=True, scheduler=True) as session:
            return [
                self._configuration(session, a)
                for a in session.scalars(
                    select(Atom)
                    .where(Atom.workspace_id == self.workspace, Atom.deleted_at.is_(None))
                    .order_by(Atom.created_at, Atom.id)
                )
            ]

    def propose_candidate(
        self, atom_id=None, *, instructions, policy=None, tools=None, source=None, eval=None
    ):
        target = atom_id or self.atom_id
        if not isinstance(instructions, str) or not instructions.strip():
            raise AtomError("instructions are required")
        with self._session(atom_id=target) as session:
            atom = self._atom(session, target, lock=True)
            member = self._row(session, Member, self.actor)
            effective_source = "self" if member.type == "AI" else (source or "human")
            if effective_source not in ("self", "human", "atomizer"):
                raise AtomError("invalid version source")
            number = (
                session.scalar(
                    select(func.coalesce(func.max(AtomVersion.version), 0)).where(
                        AtomVersion.workspace_id == self.workspace, AtomVersion.atom_id == atom.id
                    )
                )
                + 1
            )
            version = AtomVersion(
                workspace_id=self.workspace,
                atom_id=atom.id,
                version=number,
                instructions=instructions,
                policy=_object(policy),
                tools=_tools(tools),
                source=effective_source,
                status="candidate",
                eval=_object(eval),
                created_by=self.actor,
            )
            session.add(version)
            session.flush()
            return _view(version)

    def load_active_config(self, atom_id=None):
        from app.atoms.skills import SkillService

        target = atom_id or self.atom_id
        with self._session(atom_id=target) as session:
            atom = self._atom(session, target)
            if not atom.active_version_id:
                raise AtomStateError("atom has no active configuration")
            result = self._configuration(session, atom)
        # SkillService revalidates enabled pins, owner visibility and live tool
        # permissions. Do not retain an atom lock across its own transaction.
        result["skills"] = SkillService(
            self.workspace_id,
            self.actor,
            atom_id=self.atom_id,
            run_id=self.run_id,
            bind=self.bind,
        ).attached(atom_id=target)
        return result

    def activate(self, atom_id, version_id):
        return self._activate(atom_id, version_id, rollback=False)

    def rollback(self, atom_id, version_id):
        return self._activate(atom_id, version_id, rollback=True)

    def _activate(self, atom_id, version_id, *, rollback):
        with self._session(admin=True) as session:
            atom = self._atom(session, atom_id, lock=True)
            if atom.status == "killed":
                raise AtomStateError("killed atoms cannot be activated")
            version = self._row(session, AtomVersion, version_id, atom_id=atom.id)
            if version.status == "rejected" or (
                rollback and version.status not in ("active", "rolled_back")
            ):
                raise AtomStateError("version cannot be activated")
            if atom.active_version_id and atom.active_version_id != version.id:
                self._row(session, AtomVersion, atom.active_version_id).status = "rolled_back"
            version.status = "active"
            atom.active_version_id, atom.status = version.id, "active"
            session.flush()
            return _view(atom)

    def pause(self, atom_id):
        return self._state(atom_id, "paused")

    def kill(self, atom_id):
        return self._state(atom_id, "killed")

    def delete(self, atom_id):
        return self._state(atom_id, "killed", deleted=True)

    def _state(self, atom_id, status, *, deleted=False):
        with self._session(admin=True) as session:
            atom = self._atom(session, atom_id, lock=True)
            if atom.status == "killed" and status != "killed":
                raise AtomStateError("killed atoms cannot be resumed")
            atom.status = status
            if deleted:
                atom.deleted_at = datetime.now(UTC)
            if status == "killed":
                for schedule in session.scalars(
                    select(AtomSchedule).where(
                        AtomSchedule.workspace_id == self.workspace, AtomSchedule.atom_id == atom.id
                    )
                ):
                    schedule.enabled = False
                for run in session.scalars(
                    select(AgentRun).where(
                        AgentRun.workspace_id == self.workspace,
                        AgentRun.atom_id == atom.id,
                        AgentRun.status.in_(("queued", "running", "waiting_approval")),
                    )
                ):
                    run.status, run.ended_at = "canceled", datetime.now(UTC)
            session.flush()
            return _view(atom)

    def _list_children(self, model, atom_id):
        with self._session(atom_id=atom_id) as session:
            atom = self._atom(session, atom_id)
            return [
                _view(row)
                for row in session.scalars(
                    select(model)
                    .where(model.workspace_id == self.workspace, model.atom_id == atom.id)
                    .order_by(model.id)
                )
            ]

    def list_grants(self, atom_id):
        return self._list_children(AtomGrant, atom_id)

    def list_schedules(self, atom_id):
        return self._list_children(AtomSchedule, atom_id)

    def list_connections(self, atom_id):
        return self._list_children(AtomConnection, atom_id)

    def _delete_child(self, model, atom_id, row_id):
        with self._session(admin=True) as session:
            atom = self._atom(session, atom_id, lock=True)
            row = self._row(session, model, row_id, atom_id=atom.id)
            session.delete(row)
            return {"deleted": True}

    def delete_grant(self, atom_id, grant_id):
        return self._delete_child(AtomGrant, atom_id, grant_id)

    def delete_schedule(self, atom_id, schedule_id):
        return self._delete_child(AtomSchedule, atom_id, schedule_id)

    def delete_connection(self, atom_id, connection_id):
        return self._delete_child(AtomConnection, atom_id, connection_id)

    def set_grant(
        self, atom_id, *, resource_type, resource_id=None, level="read", constraints=None
    ):
        if resource_type not in RESOURCE_TYPES or level not in ("summary", "read", "write"):
            raise AtomError("invalid grant")
        with self._session(admin=True) as session:
            atom = self._atom(session, atom_id, lock=True)
            if resource_id is not None:
                from app.db.base import Base

                table_name = {
                    "project": "projects",
                    "team": "teams",
                    "collection": "collections",
                    "channel": "channels",
                    "folder": "folders",
                    "memory": "memories",
                    "meeting": "meetings",
                    "connection": "atom_connections",
                }[resource_type]
                table = Base.metadata.tables[table_name]
                exists = select(table.c.id).where(
                    table.c.workspace_id == self.workspace, table.c.id == _uuid(resource_id)
                )
                if "deleted_at" in table.c:
                    exists = exists.where(table.c.deleted_at.is_(None))
                if resource_type == "connection":
                    exists = exists.where(table.c.atom_id == atom.id)
                if session.scalar(exists) is None:
                    raise AtomNotFound("grant resource not found")
            row = session.scalar(
                select(AtomGrant).where(
                    AtomGrant.workspace_id == self.workspace,
                    AtomGrant.atom_id == atom.id,
                    AtomGrant.resource_type == resource_type,
                    AtomGrant.resource_id == _uuid(resource_id),
                )
            )
            if row is None:
                row = AtomGrant(
                    workspace_id=self.workspace,
                    atom_id=atom.id,
                    resource_type=resource_type,
                    resource_id=_uuid(resource_id),
                )
                session.add(row)
            row.level, row.constraints = level, _object(constraints)
            session.flush()
            return _view(row)

    def set_schedule(
        self,
        atom_id,
        *,
        schedule_id=None,
        cron=None,
        interval_minutes=None,
        timezone="UTC",
        enabled=True,
        next_run_at=None,
    ):
        if (cron is None) == (interval_minutes is None):
            raise AtomError("exactly one cadence is required")
        if interval_minutes is not None and (_count(interval_minutes) < 5):
            raise AtomError("interval must be at least five minutes")
        if cron is not None:
            _validate_cron(cron)
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise AtomError("invalid timezone") from None
        with self._session(admin=True) as session:
            atom = self._atom(session, atom_id, lock=True)
            row = (
                self._row(session, AtomSchedule, schedule_id, atom_id=atom.id)
                if schedule_id
                else AtomSchedule(workspace_id=self.workspace, atom_id=atom.id)
            )
            row.cron, row.interval_minutes, row.timezone, row.enabled = (
                cron,
                interval_minutes,
                timezone,
                enabled,
            )
            row.next_run_at = _time(next_run_at) if next_run_at else None
            session.add(row)
            session.flush()
            return _view(row)

    def set_connection(
        self,
        atom_id,
        *,
        toolkit,
        composio_user_id,
        toolkit_version,
        permission_ceiling,
        connection_id=None,
        composio_account_ref=None,
        allowed_tools=None,
        status="unknown",
        status_detail=None,
    ):
        if any(
            not isinstance(v, str) or not v.strip()
            for v in (toolkit, composio_user_id, toolkit_version)
        ):
            raise AtomError("connection identity and pinned version are required")
        if (
            toolkit_version.strip().lower() == "latest"
            or permission_ceiling not in PERMISSION_CEILINGS
        ):
            raise AtomError("invalid version or permission ceiling")
        if status not in ("active", "expired", "revoked", "unknown"):
            raise AtomError("invalid connection status")
        if status == "active" and not composio_account_ref:
            raise AtomError("active connection requires an account reference")
        with self._session(admin=True) as session:
            atom = self._atom(session, atom_id, lock=True)
            if atom.kind == "personal" and composio_user_id != str(atom.owner_member_id):
                raise AtomError("personal connections must use the owner's member UUID")
            row = (
                self._row(session, AtomConnection, connection_id, atom_id=atom.id)
                if connection_id
                else session.scalar(
                    select(AtomConnection).where(
                        AtomConnection.workspace_id == self.workspace,
                        AtomConnection.atom_id == atom.id,
                        AtomConnection.toolkit == toolkit,
                    )
                )
            )
            if row is None:
                row = AtomConnection(workspace_id=self.workspace, atom_id=atom.id)
                session.add(row)
            row.toolkit, row.composio_user_id, row.toolkit_version = (
                toolkit,
                composio_user_id,
                toolkit_version,
            )
            row.permission_ceiling, row.composio_account_ref = (
                permission_ceiling,
                composio_account_ref,
            )
            row.allowed_tools, row.status, row.status_detail = (
                _tools(allowed_tools),
                status,
                status_detail,
            )
            row.connected_by, row.status_checked_at = self.actor, datetime.now(UTC)
            session.flush()
            return _view(row)

    def report_connection(
        self, connection_id, *, status, status_detail=None, atom_id=None, run_id=None
    ):
        if status not in ("expired", "revoked", "unknown"):
            raise AtomError("atoms may only report expired, revoked, or unknown connections")
        target, run_target = atom_id or self.atom_id, run_id or self.run_id
        if target is None or run_target is None:
            raise AtomPermissionError("a bound atom and run are required")
        with self._session(atom_id=target, run_id=run_target) as session:
            atom = self._atom(session, target, lock=True)
            run = self._row(session, AgentRun, run_target, atom_id=atom.id, lock=True)
            if (
                run.agent_id != self.actor
                or atom.member_id != self.actor
                or run.status != "running"
            ):
                raise AtomPermissionError("only the running atom may report its connection")
            connection = self._row(
                session, AtomConnection, connection_id, atom_id=atom.id, lock=True
            )
            output = dict(run.output or {})
            reports = dict(output.get("_connection_reports", {}))
            key = str(connection.id)
            if key not in reports:
                history = [
                    value["_connection_reports"][key]
                    for value in session.scalars(
                        select(AgentRun.output).where(
                            AgentRun.workspace_id == self.workspace,
                            AgentRun.atom_id == atom.id,
                            AgentRun.output["_connection_reports"].has_key(key),
                        )
                    )
                ]
                latest = max(history, key=lambda item: item.get("reported_at", ""), default={})
                checked = _json(connection.status_checked_at)
                # An admin reconnection changes status_checked_at outside this report
                # chain, starting a fresh failure generation without deleting history.
                generation = (
                    latest.get("generation") if latest.get("reported_at") == checked else checked
                )
                reported_at = datetime.now(UTC)
                reports[key] = {
                    "status": status,
                    "detail": status_detail,
                    "reported_at": reported_at.isoformat(),
                    "generation": generation,
                }
                output["_connection_reports"] = reports
                run.output = output
                connection.status, connection.status_detail = status, status_detail
                connection.status_checked_at = reported_at
                failed_runs = 1 + sum(item.get("generation") == generation for item in history)
                if failed_runs >= 3:
                    atom.status = "paused"
            session.flush()
            return _view(connection)

    def start_run(self, atom_id, *, schedule_id, slot, estimated_cost=0, estimated_actions=0):
        slot = _time(slot)
        estimated_cost, estimated_actions = _amount(estimated_cost), _count(estimated_actions)
        key = f"atom:{_uuid(atom_id)}:{slot.isoformat()}"
        with self._session(atom_id=atom_id, scheduler=True) as session:
            atom = self._atom(session, atom_id, lock=True)
            schedule = self._row(session, AtomSchedule, schedule_id, atom_id=atom.id, lock=True)
            existing = session.scalar(
                select(AgentRun).where(
                    AgentRun.workspace_id == self.workspace, AgentRun.idempotency_key == key
                )
            )
            if existing:
                if self.actor == atom.member_id and (
                    existing.id != self.run_id or existing.schedule_id != schedule.id
                ):
                    raise AtomPermissionError("atom may only retry its bound run")
                return _view(existing)
            if self.actor == atom.member_id:
                raise AtomPermissionError("atom may not create another invocation")
            if atom.status != "active" or not schedule.enabled or not atom.active_version_id:
                raise AtomStateError("atom and schedule must be active")
            if schedule.next_run_at and slot < schedule.next_run_at:
                raise AtomStateError("schedule slot is not due")
            now = datetime.now(UTC)
            if slot > now:
                raise AtomStateError("schedule slot is in the future")
            runs = list(
                session.scalars(
                    select(AgentRun).where(
                        AgentRun.workspace_id == self.workspace, AgentRun.atom_id == atom.id
                    )
                )
            )
            if any(run.status in ("queued", "running", "waiting_approval") for run in runs):
                raise AtomStateError("atom already has a running invocation")
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            daily = [run for run in runs if run.started_at and run.started_at >= today]
            cost = sum((run.cost for run in daily), Decimal(0))
            actions = sum((run.output or {}).get("_actions", 0) for run in daily)
            if (
                len(daily) >= atom.max_runs_per_day
                or cost >= atom.max_cost_per_day
                or cost + estimated_cost > atom.max_cost_per_day
                or actions >= atom.max_actions_per_day
                or actions + estimated_actions > atom.max_actions_per_day
            ):
                raise AtomBudgetExceeded("daily atom budget exhausted")
            run = AgentRun(
                workspace_id=self.workspace,
                agent_id=atom.member_id,
                atom_id=atom.id,
                atom_version_id=atom.active_version_id,
                schedule_id=schedule.id,
                idempotency_key=key,
                trigger="schedule",
                triggered_by=self.actor,
                status="running",
                started_at=now,
                input={
                    "scheduled_slot": slot.isoformat(),
                    "cursor": _object(schedule.cursor),
                    "estimated_cost": str(estimated_cost),
                    "estimated_actions": estimated_actions,
                },
            )
            session.add(run)
            session.flush()
            return _view(run)

    def finish_run(
        self,
        run_id,
        *,
        status,
        tokens_in=0,
        tokens_out=0,
        cost=0,
        actions=0,
        output=None,
        cursor=None,
        error=None,
        model=None,
    ):
        if status not in ("succeeded", "failed", "canceled"):
            raise AtomError("invalid terminal status")
        tokens_in, tokens_out, actions = _count(tokens_in), _count(tokens_out), _count(actions)
        cost, output = _amount(cost), _object(output)
        if any(key.startswith("_") for key in output):
            raise AtomError("reserved run output key")
        if cursor is not None:
            cursor = _object(cursor)
        # Scope lookup is tenant-filtered; the atom lock always precedes run/schedule locks.
        with self._session(atom_id=self.atom_id, run_id=run_id, finishing=True) as session:
            run = self._row(session, AgentRun, run_id)
            if run.atom_id is None:
                raise AtomNotFound("atom run not found")
            atom = self._atom(session, run.atom_id, lock=True)
            session.refresh(run, with_for_update=True)
            if run.status in ("succeeded", "failed", "canceled"):
                return _view(run)
            if run.status != "running":
                raise AtomStateError("run is not running")
            metadata = {k: v for k, v in (run.output or {}).items() if k.startswith("_")}
            run.output = {**output, **metadata, "_actions": actions}
            run.status, run.tokens_in, run.tokens_out, run.cost = (
                status,
                tokens_in,
                tokens_out,
                cost,
            )
            run.error, run.model, run.ended_at = error, model, datetime.now(UTC)
            # Per-call usage may already exist. Add only the unrecorded remainder;
            # budgets use the run total, never run cost plus its usage rows.
            usage = session.execute(
                select(
                    func.coalesce(func.sum(LlmUsage.input_tokens), 0),
                    func.coalesce(func.sum(LlmUsage.output_tokens), 0),
                    func.coalesce(func.sum(LlmUsage.cost_usd), 0),
                ).where(LlmUsage.workspace_id == self.workspace, LlmUsage.run_id == run.id)
            ).one()
            run.tokens_in, run.tokens_out, run.cost = (
                max(tokens_in, usage[0]),
                max(tokens_out, usage[1]),
                max(cost, usage[2]),
            )
            delta = (run.tokens_in - usage[0], run.tokens_out - usage[1], run.cost - usage[2])
            if any(delta) or not session.scalar(
                select(LlmUsage.id)
                .where(LlmUsage.workspace_id == self.workspace, LlmUsage.run_id == run.id)
                .limit(1)
            ):
                session.add(
                    LlmUsage(
                        workspace_id=self.workspace,
                        run_id=run.id,
                        member_id=atom.member_id,
                        task_type="atom.run",
                        model=model or atom.model_tier,
                        input_tokens=delta[0],
                        output_tokens=delta[1],
                        cost_usd=delta[2],
                    )
                )
            if status == "succeeded" and run.schedule_id:
                schedule = self._row(
                    session, AtomSchedule, run.schedule_id, atom_id=atom.id, lock=True
                )
                slot = _time(run.input["scheduled_slot"])
                if schedule.last_run_at is None or slot > schedule.last_run_at:
                    if cursor is not None:
                        schedule.cursor = cursor
                    schedule.last_run_at = slot
                    if schedule.interval_minutes:
                        schedule.next_run_at = slot + timedelta(minutes=schedule.interval_minutes)
            session.flush()
            return _view(run)

    def propose(self, atom_id, instructions, policy=None, tools=None):
        return self.propose_candidate(
            atom_id, instructions=instructions, policy=policy, tools=tools
        )

    def load(self, atom_id=None):
        return self.load_active_config(atom_id)

    def revoke_grant(self, atom_id, grant_id):
        return self.delete_grant(atom_id, grant_id)

    def run_start(self, atom_id, schedule_id, scheduled_for, idempotency_key=None, **kwargs):
        # Caller-provided keys never override the atom/UTC-slot uniqueness boundary.
        return self.start_run(atom_id, schedule_id=schedule_id, slot=scheduled_for, **kwargs)

    def run_finish(self, run_id, *, actions_count=0, **kwargs):
        return self.finish_run(run_id, actions=actions_count, **kwargs)

    def connection_report(self, run_id, connection_id, status, detail=None):
        return self.report_connection(
            connection_id, status=status, status_detail=detail, run_id=run_id
        )
