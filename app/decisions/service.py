"""Flagged conflicts for the Decision Ledger, which lives in the AI engine.

The AI engine judges a proposed action against decisions (memories with source "decision") and
flags the ones it contradicts here. A flag stays open until a human member resolves it; agents
can flag but never close.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert

from app.db import events
from app.db.models.identity import Member
from app.db.models.memory import Decision, DecisionConflict, Memory
from app.db.session import session_for
from app.decisions.errors import ConflictNotFound, ConflictStateError, HumanRequired
from app.memory.errors import MemoryNotFound


@dataclass
class ConflictView:
    id: str
    status: str
    decision_id: str
    decision_text: str
    proposal: str | None
    explanation: str
    similarity: float | None
    flagged_by: str | None
    created_at: datetime | None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None


@dataclass
class ConflictPage:
    items: list[ConflictView]


def _id(value) -> str | None:
    return str(value) if value else None


def _view(conflict: DecisionConflict, memory: Memory) -> ConflictView:
    return ConflictView(
        id=str(conflict.id),
        status=conflict.status,
        decision_id=str(conflict.decision_id),
        decision_text=memory.text,
        proposal=conflict.proposal,
        explanation=conflict.explanation,
        similarity=conflict.similarity,
        flagged_by=_id(conflict.flagged_by),
        created_at=conflict.created_at,
        resolved_by=_id(conflict.resolved_by),
        resolved_at=conflict.resolved_at,
        resolution_note=conflict.resolution_note,
    )


def _joined():
    return select(DecisionConflict, Memory).join(
        Memory,
        and_(
            Memory.workspace_id == DecisionConflict.workspace_id,
            Memory.id == DecisionConflict.decision_id,
        ),
    )


class ConflictService:
    """Flags for one workspace. `actor` is the member doing the work."""

    def __init__(self, workspace_id: str, actor: str | None = None):
        self.workspace_id = str(uuid.UUID(workspace_id))
        self.workspace = uuid.UUID(self.workspace_id)
        self.actor = str(uuid.UUID(actor)) if actor else None

    def flag(
        self,
        decision_id: str,
        proposal: str,
        explanation: str,
        similarity: float | None = None,
    ) -> ConflictView:
        """Flag a proposal against a decision memory. The same open flag is reused, not repeated."""
        with session_for(self.workspace_id) as session:
            memory = session.scalars(
                select(Memory).where(
                    Memory.workspace_id == self.workspace, Memory.id == uuid.UUID(decision_id)
                )
            ).first()
            if memory is None:
                raise MemoryNotFound(decision_id)
            # The flag points at a `decisions` row; a decision stored as a plain memory has none yet.
            session.execute(
                insert(Decision)
                .values(workspace_id=self.workspace, memory_id=memory.id, statement=memory.text)
                .on_conflict_do_nothing()
            )
            row = session.scalars(
                select(DecisionConflict).where(
                    DecisionConflict.workspace_id == self.workspace,
                    DecisionConflict.decision_id == memory.id,
                    DecisionConflict.proposal == proposal,
                    DecisionConflict.status == "open",
                )
            ).first()
            if row is None:
                row = DecisionConflict(
                    workspace_id=self.workspace,
                    decision_id=memory.id,
                    proposal=proposal,
                    explanation=explanation,
                    similarity=similarity,
                    flagged_by=uuid.UUID(self.actor) if self.actor else None,
                )
                session.add(row)
                session.flush()
                events.record(
                    session,
                    self.workspace_id,
                    self.actor,
                    "decision",
                    memory.id,
                    "conflict_flagged",
                    {"conflict": str(row.id)},
                )
            else:
                row.explanation = explanation
            return _view(row, memory)

    def get(self, conflict_id: str) -> ConflictView:
        with session_for(self.workspace_id) as session:
            row = session.execute(
                _joined().where(
                    DecisionConflict.workspace_id == self.workspace,
                    DecisionConflict.id == uuid.UUID(conflict_id),
                )
            ).first()
            if row is None:
                raise ConflictNotFound(conflict_id)
            return _view(*row)

    def list(
        self,
        status: str | None = None,
        decision_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ConflictPage:
        statement = _joined().where(DecisionConflict.workspace_id == self.workspace)
        if status:
            statement = statement.where(DecisionConflict.status == status)
        if decision_id:
            statement = statement.where(DecisionConflict.decision_id == uuid.UUID(decision_id))
        statement = statement.order_by(DecisionConflict.created_at.desc(), DecisionConflict.id)
        with session_for(self.workspace_id) as session:
            rows = session.execute(statement.limit(limit).offset(offset))
            return ConflictPage([_view(c, m) for c, m in rows])

    def resolve(self, conflict_id: str, status: str, note: str | None = None) -> ConflictView:
        """A human decides: the work is allowed (accepted), it was a false alarm (dismissed), or
        the work was changed to fit (resolved)."""
        with session_for(self.workspace_id) as session:
            member = session.get(Member, uuid.UUID(self.actor)) if self.actor else None
            if member is None or member.type != "HUMAN":
                raise HumanRequired("only a human member can resolve a conflict")
            row = session.execute(
                _joined()
                .where(
                    DecisionConflict.workspace_id == self.workspace,
                    DecisionConflict.id == uuid.UUID(conflict_id),
                )
                .with_for_update(of=DecisionConflict)
            ).first()
            if row is None:
                raise ConflictNotFound(conflict_id)
            conflict, memory = row
            if conflict.status != "open":
                raise ConflictStateError(f"the conflict is already {conflict.status}")
            conflict.status, conflict.resolved_by = status, member.id
            conflict.resolved_at, conflict.resolution_note = datetime.now(UTC), note
            events.record(
                session,
                self.workspace_id,
                self.actor,
                "decision",
                conflict.decision_id,
                "conflict_resolved",
                {"conflict": conflict_id, "status": status},
            )
            return _view(conflict, memory)
