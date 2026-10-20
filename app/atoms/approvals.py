"""Human approvals with a durable, one-time permission to attempt an external effect.

A lost claim response fails closed. Neither claims nor execution runs are recycled;
this is not distributed exactly-once execution of the external tool.
"""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import or_, select

from app.atoms.access import current_context
from app.atoms.service import (
    AtomPermissionError,
    AtomService,
    AtomStateError,
    _amount,
    _count,
    _time,
    _uuid,
    _view,
)
from app.db import session as db
from app.db.models.atoms import AtomConnection
from app.db.models.collab import Notification
from app.db.models.identity import Member
from app.db.models.memory import AgentRun, Approval
from app.db.session import workspace_session


class ApprovalService(AtomService):
    def _bound(self):
        if not self.atom_id or not self.run_id or not self.actor:
            raise AtomPermissionError("a bound atom run is required")

    def _trusted(self):
        if not self.trusted or current_context() is not None or self.atom_id or self.run_id:
            raise AtomPermissionError("service credentials required")

    def _human(self, session, member_id):
        member = self._row(session, Member, member_id)
        if (
            member.type != "HUMAN"
            or member.status != "active"
            or member.deleted_at
            or member.role == "viewer"
        ):
            raise AtomPermissionError("active non-viewer human required")
        return member

    def _connection(self, session, atom_id, payload):
        connection = self._row(
            session, AtomConnection, payload["connection_id"], atom_id=atom_id, lock=True
        )
        if connection.status != "active":
            raise AtomStateError("approval connection is not active")
        if connection.allowed_tools and payload["tool_slug"] not in connection.allowed_tools:
            raise AtomPermissionError("tool is not allowed by this connection")
        return connection

    @staticmethod
    def _approved(row):
        if row.status != "approved":
            raise AtomStateError("approval is not approved")
        if row.expires_at and row.expires_at <= datetime.now(UTC):
            raise AtomStateError("approval has expired")

    def _approval(self, session, approval_id, *, lock=False):
        row = self._row(session, Approval, approval_id, lock=lock)
        if row.atom_id is None:
            raise AtomStateError("legacy approval is not an atom execution approval")
        if self.atom_id and row.atom_id != self.atom_id:
            raise AtomPermissionError("approval belongs to another atom")
        return row

    def create_approval(
        self, *, kind, title, payload, request_key=None, assigned_to=None, expires_at=None
    ):
        self._bound()
        if kind not in ("plan", "pull_request", "deploy", "action", "atom_action"):
            raise ValueError("invalid approval kind")
        if not isinstance(title, str) or not title.strip() or len(title) > 300:
            raise ValueError("title must contain 1 to 300 characters")
        if request_key is not None and (
            not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 200
        ):
            raise ValueError("request_key must contain 1 to 200 characters")
        if (
            not isinstance(payload, dict)
            or set(payload) != {"tool_slug", "arguments", "connection_id"}
            or not isinstance(payload["tool_slug"], str)
            or not payload["tool_slug"].strip()
            or not isinstance(payload["arguments"], dict)
        ):
            raise ValueError("payload requires tool_slug, arguments object and connection_id")
        payload = deepcopy(payload)
        payload["connection_id"] = str(_uuid(payload["connection_id"]))
        expires_at = _time(expires_at) if expires_at else None
        assigned_to = _uuid(assigned_to)
        if request_key is None:
            canonical = json.dumps(
                [
                    kind,
                    title,
                    payload,
                    str(assigned_to) if assigned_to else None,
                    expires_at.isoformat() if expires_at else None,
                ],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            request_key = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        with self._session() as session:
            atom = self._atom(session, self.atom_id, lock=True)
            run = self._row(session, AgentRun, self.run_id, atom_id=atom.id, lock=True)
            if self.actor != atom.member_id or run.status != "running":
                raise AtomPermissionError("only the current running atom may request approval")
            existing = session.scalar(
                select(Approval).where(
                    Approval.workspace_id == self.workspace,
                    Approval.run_id == run.id,
                    Approval.request_key == request_key,
                )
            )
            if existing:
                if (
                    existing.kind != kind
                    or existing.title != title
                    or existing.payload != payload
                    or existing.assigned_to != assigned_to
                    or existing.expires_at != expires_at
                ):
                    raise AtomStateError("request_key already used for a different approval")
                return _view(existing)
            if expires_at and expires_at <= datetime.now(UTC):
                raise AtomStateError("approval has expired")
            self._connection(session, atom.id, payload)
            if assigned_to:
                self._human(session, assigned_to)
            row = Approval(
                workspace_id=self.workspace,
                atom_id=atom.id,
                run_id=run.id,
                requested_by=atom.member_id,
                kind=kind,
                title=title,
                payload=payload,
                request_key=request_key,
                assigned_to=assigned_to,
                expires_at=expires_at,
            )
            session.add(row)
            session.flush()
            if assigned_to:
                session.add(
                    Notification(
                        workspace_id=self.workspace,
                        recipient_id=assigned_to,
                        actor_id=atom.member_id,
                        type="approval_requested",
                        entity_type="approval",
                        entity_id=row.id,
                        title=title,
                    )
                )
                session.flush()
            return _view(row)

    def get_approval(self, approval_id):
        self._bound()
        with self._session(finishing=True) as session:
            row = self._approval(session, approval_id)
            if self.run_id not in (row.run_id, row.execution_run_id):
                raise AtomPermissionError("approval is not bound to this run")
            return _view(row)

    def decide(self, approval_id, *, status, comment=None):
        self._trusted()
        if not self.actor:
            raise AtomPermissionError("X-Acting-Member-Id is required")
        if status not in ("approved", "rejected"):
            raise ValueError("decision must be approved or rejected")
        with workspace_session(
            self.bind if self.bind is not None else db.engine(), self.workspace_id
        ) as session:
            row = self._approval(session, approval_id)
            atom = self._atom(session, row.atom_id, lock=True)
            row = self._approval(session, approval_id, lock=True)
            human = self._human(session, self.actor)
            if row.assigned_to:
                if row.assigned_to != human.id:
                    raise AtomPermissionError("only the assigned human may decide")
            elif human.role not in ("owner", "admin") and atom.owner_member_id != human.id:
                raise AtomPermissionError("unassigned approval requires an owner or admin")
            if row.expires_at and row.expires_at <= datetime.now(UTC):
                raise AtomStateError("approval has expired")
            if row.status != "pending":
                if row.status == status:
                    return _view(row)
                raise AtomStateError("approval already decided")
            row.status, row.decided_by = status, human.id
            row.decision_comment, row.decided_at = comment, datetime.now(UTC)
            session.flush()
            return _view(row)

    def ready(self, *, limit=100):
        self._trusted()
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._session(scheduler=True) as session:
            return [
                _view(row)
                for row in session.scalars(
                    select(Approval)
                    .where(
                        Approval.workspace_id == self.workspace,
                        Approval.atom_id.is_not(None),
                        Approval.status == "approved",
                        Approval.execution_run_id.is_(None),
                        Approval.claimed_at.is_(None),
                        Approval.executed_at.is_(None),
                        or_(Approval.expires_at.is_(None), Approval.expires_at > datetime.now(UTC)),
                    )
                    .order_by(Approval.created_at, Approval.id)
                    .limit(limit)
                )
            ]

    def bootstrap(self, approval_id, *, estimated_cost=0, estimated_actions=1):
        self._trusted()
        estimated_cost, estimated_actions = _amount(estimated_cost), _count(estimated_actions)
        with self._session(scheduler=True) as session:
            row = self._approval(session, approval_id)
            atom = self._atom(session, row.atom_id, lock=True)
            row = self._approval(session, approval_id, lock=True)
            self._approved(row)
            if row.execution_run_id:
                return _view(self._row(session, AgentRun, row.execution_run_id, atom_id=atom.id))
            if atom.status != "active" or not atom.active_version_id:
                raise AtomStateError("atom must be active")
            if atom.owner_member_id:
                self._human(session, atom.owner_member_id)
            self._connection(session, atom.id, row.payload)
            now = datetime.now(UTC)
            self._check_run_budget(session, atom, now, estimated_cost, estimated_actions)
            run = AgentRun(
                workspace_id=self.workspace,
                agent_id=atom.member_id,
                atom_id=atom.id,
                atom_version_id=atom.active_version_id,
                trigger="schedule",
                status="running",
                idempotency_key=f"approval:{row.id}",
                started_at=now,
                input={
                    "approval_id": str(row.id),
                    "approval_payload": deepcopy(row.payload),
                    "estimated_cost": str(estimated_cost),
                    "estimated_actions": estimated_actions,
                },
            )
            session.add(run)
            session.flush()
            row.execution_run_id = run.id
            session.flush()
            return _view(run)

    def claim(self, approval_id):
        self._bound()
        with self._session(finishing=True) as session:
            atom = self._atom(session, self.atom_id, lock=True)
            row = self._approval(session, approval_id, lock=True)
            if self.actor != atom.member_id or row.execution_run_id != self.run_id:
                raise AtomPermissionError("only the bound execution run may claim")
            self._approved(row)
            if row.claimed_at:
                return {"execute": False, "approval": _view(row)}
            run = self._row(session, AgentRun, self.run_id, atom_id=atom.id, lock=True)
            if run.status != "running" or atom.status != "active":
                raise AtomStateError("execution run and atom must be active")
            self._connection(session, atom.id, row.payload)
            row.claimed_at = datetime.now(UTC)
            session.flush()
            return {"execute": True, "approval": _view(row)}

    def outcome(self, approval_id, *, status, result=None):
        self._bound()
        if status not in ("succeeded", "failed", "unknown"):
            raise ValueError("outcome must be succeeded, failed or unknown")
        if result is not None and not isinstance(result, dict):
            raise ValueError("result must be an object")
        with self._session(finishing=True) as session:
            atom = self._atom(session, self.atom_id, lock=True)
            row = self._approval(session, approval_id, lock=True)
            if self.actor != atom.member_id or row.execution_run_id != self.run_id:
                raise AtomPermissionError("only the bound execution run may mark outcome")
            if not row.claimed_at:
                raise AtomStateError("approval must be claimed before recording outcome")
            if row.executed_at is None:
                row.execution_status, row.execution_result = status, deepcopy(result or {})
                row.executed_at = datetime.now(UTC)
                session.flush()
            return _view(row)
