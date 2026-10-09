"""The Decision Ledger: record decisions, check proposed work against them, flag conflicts.

A decision is a memory of kind 'decision' (so it is searchable like any memory) plus a structured
`decisions` row (title, rationale, the files it covers, status). Checking a proposal finds the
decisions it might contradict (by meaning and by exact words, reranked), then asks a model to judge
each one. The ledger only flags: a flagged conflict stays open until a human resolves it.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.exc import IntegrityError

from app.clients import inference
from app.config import settings
from app.db import events
from app.db.models.identity import Member
from app.db.models.memory import Decision, DecisionConflict, Memory, MemoryLink
from app.db.session import session_for, tune_vector_search
from app.decisions import judge
from app.decisions.errors import (
    ConflictNotFound,
    DecisionNotFound,
    DecisionStateError,
    HumanRequired,
)
from app.memory import vectorizer
from app.memory.recall import fuse, relevance
from app.memory.store import MemoryStore
from app.storage.errors import InvalidReference

logger = logging.getLogger(__name__)
CANDIDATES = 8  # decisions retrieved per search before reranking
JUDGED = 5  # decisions the model is asked about
IMPORTANCE = 5  # decisions are lasting: sweep never touches importance 4 and up
STATUSES = ("proposed", "active", "superseded", "rejected")


@dataclass
class DecisionView:
    id: str  # the decision's memory id: also what recall returns for it
    title: str
    rationale: str | None
    scope_paths: list[str]
    area: str | None
    status: str
    agent: str | None
    owner_member_id: str | None
    team_id: str | None
    project_id: str | None
    task_id: str | None
    work_item_id: str | None
    decided_at: datetime
    superseded_by: str | None = None


@dataclass
class ConflictView:
    id: str | None  # None when the check was not recorded
    status: str
    decision: DecisionView
    proposal: str | None
    explanation: str
    relevance: float | None
    task_id: str | None = None
    work_item_id: str | None = None
    created_at: datetime | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None


@dataclass
class Verdict:
    """Answer to "does this proposal go against a decision?". The first three fields keep the shape
    the AI engine's Decision Ledger already uses; `conflicts` has the full detail."""

    conflict: bool
    conflicting_decision: dict | None
    reasoning: str
    conflicts: list[ConflictView] = field(default_factory=list)
    checked: int = 0  # decisions the model judged
    judged: bool = True  # False when the model could not be reached, so nothing was flagged


@dataclass
class RecordResult:
    decision: DecisionView
    deduplicated: bool = False


@dataclass
class DecisionPage:
    items: list[DecisionView]


@dataclass
class ConflictPage:
    items: list[ConflictView]


def render(title: str, rationale: str | None, scope_paths: list[str]) -> str:
    """The text a decision is embedded and searched as."""
    lines = [f"Decision: {title.strip()}"]
    if rationale and rationale.strip():
        lines.append(f"Rationale: {rationale.strip()}")
    if scope_paths:
        lines.append(f"Affects: {', '.join(scope_paths)}")
    return "\n".join(lines)


def _id(value) -> str | None:
    return str(value) if value else None


def _view(memory: Memory, decision: Decision) -> DecisionView:
    return DecisionView(
        id=str(memory.id),
        title=decision.statement,
        rationale=decision.rationale,
        scope_paths=list(decision.scope_paths or []),
        area=decision.area,
        status=decision.status,
        agent=memory.agent_name,
        owner_member_id=_id(decision.owner_member_id),
        team_id=_id(memory.team_id),
        project_id=_id(memory.project_id),
        task_id=_id(decision.task_id),
        work_item_id=_id(decision.work_item_id),
        decided_at=decision.decided_at,
        superseded_by=_id(memory.superseded_by),
    )


def _visible(team_id: uuid.UUID | None, project_id: uuid.UUID | None):
    """With no team or project given, every decision applies. With one, the workspace-wide
    decisions plus that team's and that project's."""
    if not team_id and not project_id:
        return true()
    options = [Memory.scope == "workspace"]
    if team_id:
        options.append(Memory.team_id == team_id)
    if project_id:
        options.append(Memory.project_id == project_id)
    return or_(*options)


def _join_decisions(statement):
    return statement.join(
        Decision,
        and_(Decision.workspace_id == Memory.workspace_id, Decision.memory_id == Memory.id),
    )


class DecisionService:
    """The ledger for one workspace. `actor` is the member doing the work."""

    def __init__(self, workspace_id: str, actor: str | None = None):
        self.workspace_id = str(uuid.UUID(workspace_id))
        self.workspace = uuid.UUID(self.workspace_id)
        self.actor = str(uuid.UUID(actor)) if actor else None

    def _log(self, session, entity_id, action: str, **changes) -> None:
        events.record(
            session, self.workspace_id, self.actor, "decision", entity_id, action, changes
        )

    def _flush(self, session) -> None:
        try:
            session.flush()
        except IntegrityError as exc:
            if isinstance(exc.orig, psycopg.errors.ForeignKeyViolation):
                raise InvalidReference(
                    "team, project, task or work item not found in this workspace"
                ) from None
            raise

    def _get(self, session, decision_id: str) -> tuple[Memory, Decision]:
        row = session.execute(
            _join_decisions(select(Memory, Decision)).where(
                Memory.workspace_id == self.workspace, Memory.id == uuid.UUID(decision_id)
            )
        ).first()
        if row is None:
            raise DecisionNotFound(decision_id)
        return row

    # -- record, read, change ------------------------------------------------------------------

    def record(
        self,
        *,
        title: str,
        rationale: str | None = None,
        scope_paths: list[str] | None = None,
        area: str | None = None,
        alternatives: list[str] | None = None,
        agent: str = "decision_ledger",
        team_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        work_item_id: uuid.UUID | None = None,
        status: str = "active",
    ) -> RecordResult:
        """Record a decision. A near-identical active decision is returned instead of a copy."""
        paths = list(scope_paths or [])
        text = render(title, rationale, paths)
        vector = vectorizer.embed_one(text)
        with session_for(self.workspace_id) as session:
            if status == "active" and settings.MEMORY_DEDUP_THRESHOLD <= 1.0:
                for memory, decision, score in self._nearest(
                    session, vector, 1, team_id, project_id
                ):
                    if score >= settings.MEMORY_DEDUP_THRESHOLD:
                        return RecordResult(_view(memory, decision), deduplicated=True)
            return RecordResult(
                self._insert(
                    session,
                    text,
                    vector,
                    title,
                    rationale,
                    paths,
                    area,
                    alternatives,
                    agent,
                    team_id,
                    project_id,
                    task_id,
                    work_item_id,
                    status,
                )
            )

    def _insert(
        self,
        session,
        text,
        vector,
        title,
        rationale,
        paths,
        area,
        alternatives,
        agent,
        team_id,
        project_id,
        task_id,
        work_item_id,
        status,
    ) -> DecisionView:
        store = MemoryStore(session, self.workspace_id)
        memory = store.insert(
            text=text,
            raw_text=None,
            source="decision",
            agent=agent,
            importance=IMPORTANCE,
            embedding=vector,
            embedding_model=settings.EMBEDDING_DEPLOYMENT,
            now=time.time(),
            created_by=self.actor,
            kind="decision",
            scope="project" if project_id else "team" if team_id else "workspace",
            team_id=team_id,
            project_id=project_id,
            # only active decisions are recalled as truth; proposed and rejected ones are kept aside
            status="active" if status == "active" else "faded",
        )
        decision = Decision(
            workspace_id=self.workspace,
            memory_id=memory.id,
            statement=title.strip(),
            rationale=(rationale or "").strip() or None,
            alternatives={"options": alternatives or []},
            area=area,
            scope_paths=paths,
            status=status,
            owner_member_id=uuid.UUID(self.actor) if self.actor else None,
            work_item_id=work_item_id,
            task_id=task_id,
        )
        session.add(decision)
        self._flush(session)
        self._log(session, memory.id, "recorded", title=decision.statement, status=status)
        return _view(memory, decision)

    def get(self, decision_id: str) -> DecisionView:
        with session_for(self.workspace_id) as session:
            return _view(*self._get(session, decision_id))

    def list(
        self,
        status: str | None = None,
        area: str | None = None,
        team_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DecisionPage:
        statement = _join_decisions(select(Memory, Decision)).where(
            Memory.workspace_id == self.workspace, _visible(team_id, project_id)
        )
        if status:
            statement = statement.where(Decision.status == status)
        if area:
            statement = statement.where(Decision.area == area)
        if query:
            statement = statement.where(
                Memory.tsv.op("@@")(func.websearch_to_tsquery("simple", query))
            )
        statement = (
            statement.order_by(Decision.decided_at.desc(), Memory.id).limit(limit).offset(offset)
        )
        with session_for(self.workspace_id) as session:
            return DecisionPage([_view(m, d) for m, d in session.execute(statement)])

    def update(
        self,
        decision_id: str,
        *,
        title: str | None = None,
        rationale: str | None = None,
        scope_paths: list[str] | None = None,
        area: str | None = None,
        status: str | None = None,
    ) -> DecisionView:
        """Edit a decision, or move it between proposed, active and rejected.
        (A decision becomes superseded only by superseding it.)"""
        if status not in (None, "proposed", "active", "rejected"):
            raise DecisionStateError(
                "a decision becomes superseded only through the supersede call"
            )
        with session_for(self.workspace_id) as session:
            memory, decision = self._get(session, decision_id)
            if decision.status == "superseded":
                raise DecisionStateError("a superseded decision cannot be changed")
            new_title = title.strip() if title else decision.statement
            new_rationale = decision.rationale if rationale is None else (rationale.strip() or None)
            new_paths = decision.scope_paths if scope_paths is None else list(scope_paths)
            text = render(new_title, new_rationale, list(new_paths or []))
            changed_text = text != memory.text
        vector = vectorizer.embed_one(text) if changed_text else None
        with session_for(self.workspace_id) as session:
            memory, decision = self._get(session, decision_id)
            if decision.status == "superseded":
                raise DecisionStateError("a superseded decision cannot be changed")
            decision.statement, decision.rationale = new_title, new_rationale
            decision.scope_paths = list(new_paths or [])
            if area is not None:
                decision.area = area or None
            if vector is not None:
                MemoryStore(session, self.workspace_id).replace(
                    memory, text, vector, settings.EMBEDDING_DEPLOYMENT
                )
            if status:
                decision.status = status
                memory.status = "active" if status == "active" else "faded"
            self._log(
                session,
                memory.id,
                "updated",
                fields=sorted(
                    name
                    for name, value in (
                        ("title", title),
                        ("rationale", rationale),
                        ("scope_paths", scope_paths),
                        ("area", area),
                        ("status", status),
                    )
                    if value is not None
                ),
            )
            return _view(memory, decision)

    def supersede(
        self,
        decision_id: str,
        *,
        title: str,
        rationale: str | None = None,
        scope_paths: list[str] | None = None,
        area: str | None = None,
        agent: str = "decision_ledger",
    ) -> RecordResult:
        """Replace a decision with a new one. The old one stays, marked superseded and linked."""
        paths = list(scope_paths or [])
        text = render(title, rationale, paths)
        vector = vectorizer.embed_one(text)
        with session_for(self.workspace_id) as session:
            old_memory, old = self._get(session, decision_id)
            if old.status not in ("active", "proposed"):
                raise DecisionStateError(f"a {old.status} decision cannot be superseded")
            new = self._insert(
                session,
                text,
                vector,
                title,
                rationale,
                paths,
                area or old.area,
                None,
                agent,
                old_memory.team_id,
                old_memory.project_id,
                old.task_id,
                old.work_item_id,
                "active",
            )
            now = datetime.now(UTC)
            old.status = "superseded"
            old_memory.status, old_memory.superseded_by = "superseded", uuid.UUID(new.id)
            old_memory.superseded_at = now
            session.add(
                MemoryLink(
                    workspace_id=self.workspace,
                    from_memory_id=uuid.UUID(new.id),
                    to_memory_id=old_memory.id,
                    type="supersedes",
                )
            )
            self._log(session, old_memory.id, "superseded", by=new.id)
            return RecordResult(new)

    # -- checking a proposal -----------------------------------------------------------------------

    def _nearest(self, session, vector, limit, team_id, project_id):
        distance = Memory.embedding.cosine_distance(vector)
        tune_vector_search(session, limit)
        rows = session.execute(
            _join_decisions(select(Memory, Decision, (1 - distance).label("score")))
            .where(*self._active(team_id, project_id), Memory.embedding.is_not(None))
            .order_by(distance)
            .limit(limit)
        )
        return [(m, d, float(score)) for m, d, score in rows]

    def _active(self, team_id, project_id):
        return (
            Memory.workspace_id == self.workspace,
            Memory.kind == "decision",
            Memory.status == "active",
            Decision.status == "active",
            _visible(team_id, project_id),
        )

    def check(
        self,
        proposed_action: str,
        *,
        team_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        work_item_id: uuid.UUID | None = None,
        record: bool = False,
    ) -> Verdict:
        """Does the proposal go against an active decision? With `record`, each conflict found is
        saved as an open flag for a human to resolve; without it nothing is written."""
        vector = inference.embed(proposed_action)
        with session_for(self.workspace_id) as session:
            by_meaning = self._nearest(session, vector, CANDIDATES, team_id, project_id)
            tsquery = func.websearch_to_tsquery("simple", proposed_action)
            by_words = session.execute(
                _join_decisions(select(Memory, Decision))
                .where(*self._active(team_id, project_id), Memory.tsv.op("@@")(tsquery))
                .order_by(func.ts_rank_cd(Memory.tsv, tsquery).desc(), Memory.id)
                .limit(CANDIDATES)
            ).all()
            decisions = {m.id: d for m, d, _ in by_meaning} | {m.id: d for m, d in by_words}
            similarity = {m.id: score for m, _, score in by_meaning}
            candidates = fuse([m for m, _, _ in by_meaning], [m for m, _ in by_words])[:CANDIDATES]
            scores = relevance(proposed_action, candidates, similarity)
            ranked = sorted(candidates, key=lambda m: scores.get(m.id, 0.0), reverse=True)[:JUDGED]
            views = {m.id: _view(m, decisions[m.id]) for m in ranked}
            texts = {m.id: m.text for m in ranked}
        if not ranked:
            return Verdict(False, None, "No related decisions were found.")

        refs = {f"d{i}": memory.id for i, memory in enumerate(ranked)}
        try:
            reply = inference.chat(
                "decision_ledger.judge",
                judge.judge_prompt(
                    proposed_action, [(ref, texts[mid]) for ref, mid in refs.items()]
                ),
            )
        except Exception as exc:
            logger.warning("decision check: the judge was unavailable: %s", exc)
            return Verdict(
                False, None, "The judge was unavailable, so nothing was flagged.", [], 0, False
            )
        verdicts = judge.parse_verdicts(reply, list(refs))

        flagged = [
            (refs[ref], explanation)
            for ref, (is_conflict, explanation) in verdicts.items()
            if is_conflict
        ]
        conflicts = [
            ConflictView(
                id=None,
                status="unrecorded",
                decision=views[mid],
                proposal=proposed_action,
                explanation=explanation,
                relevance=scores.get(mid),
                task_id=_id(task_id),
                work_item_id=_id(work_item_id),
            )
            for mid, explanation in flagged
        ]
        if record and conflicts:
            self._record_conflicts(conflicts, task_id, work_item_id)
        if not conflicts:
            return Verdict(False, None, "No conflict with the recorded decisions.", [], len(refs))
        first = conflicts[0]
        return Verdict(
            conflict=True,
            conflicting_decision={
                "id": first.decision.id,
                "text": texts[uuid.UUID(first.decision.id)],
            },
            reasoning=" ".join(c.explanation for c in conflicts if c.explanation),
            conflicts=conflicts,
            checked=len(refs),
        )

    def _record_conflicts(self, conflicts: list[ConflictView], task_id, work_item_id) -> None:
        """Save each conflict as an open flag; asking again about the same proposal reuses it."""
        with session_for(self.workspace_id) as session:
            for view in conflicts:
                decision_id = uuid.UUID(view.decision.id)
                row = session.scalars(
                    select(DecisionConflict).where(
                        DecisionConflict.workspace_id == self.workspace,
                        DecisionConflict.decision_id == decision_id,
                        DecisionConflict.proposal == view.proposal,
                        DecisionConflict.status == "open",
                    )
                ).first()
                if row is None:
                    row = DecisionConflict(
                        workspace_id=self.workspace,
                        decision_id=decision_id,
                        task_id=task_id,
                        work_item_id=work_item_id,
                        similarity=view.relevance,
                        proposal=view.proposal,
                        explanation=view.explanation,
                        flagged_by=uuid.UUID(self.actor) if self.actor else None,
                    )
                    session.add(row)
                    self._flush(session)
                    self._log(session, decision_id, "conflict_flagged", conflict=str(row.id))
                else:
                    row.explanation = view.explanation
                view.id, view.status, view.created_at = str(row.id), row.status, row.created_at

    # -- flagged conflicts ----------------------------------------------------------------------------

    def _conflict_view(
        self, conflict: DecisionConflict, memory: Memory, decision: Decision
    ) -> ConflictView:
        return ConflictView(
            id=str(conflict.id),
            status=conflict.status,
            decision=_view(memory, decision),
            proposal=conflict.proposal,
            explanation=conflict.explanation,
            relevance=conflict.similarity,
            task_id=_id(conflict.task_id),
            work_item_id=_id(conflict.work_item_id),
            created_at=conflict.created_at,
            resolved_by=_id(conflict.resolved_by),
            resolved_at=conflict.resolved_at,
            resolution_note=conflict.resolution_note,
        )

    def conflicts(
        self,
        status: str | None = None,
        decision_id: str | None = None,
        task_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ConflictPage:
        statement = (
            select(DecisionConflict, Memory, Decision)
            .join(
                Memory,
                and_(
                    Memory.workspace_id == DecisionConflict.workspace_id,
                    Memory.id == DecisionConflict.decision_id,
                ),
            )
            .join(
                Decision,
                and_(Decision.workspace_id == Memory.workspace_id, Decision.memory_id == Memory.id),
            )
            .where(DecisionConflict.workspace_id == self.workspace)
        )
        if status:
            statement = statement.where(DecisionConflict.status == status)
        if decision_id:
            statement = statement.where(DecisionConflict.decision_id == uuid.UUID(decision_id))
        if task_id:
            statement = statement.where(DecisionConflict.task_id == task_id)
        statement = statement.order_by(DecisionConflict.created_at.desc(), DecisionConflict.id)
        with session_for(self.workspace_id) as session:
            rows = session.execute(statement.limit(limit).offset(offset))
            return ConflictPage([self._conflict_view(c, m, d) for c, m, d in rows])

    def resolve(self, conflict_id: str, status: str, note: str | None = None) -> ConflictView:
        """A human decides what a flagged conflict means: the work is allowed (accepted), it was a
        false alarm (dismissed), or the work was changed to fit (resolved)."""
        with session_for(self.workspace_id) as session:
            member = session.get(Member, uuid.UUID(self.actor)) if self.actor else None
            if member is None or member.type != "HUMAN":
                raise HumanRequired(
                    "only a human member can resolve a conflict: the ledger flags, people decide"
                )
            row = session.execute(
                select(DecisionConflict, Memory, Decision)
                .join(
                    Memory,
                    and_(
                        Memory.workspace_id == DecisionConflict.workspace_id,
                        Memory.id == DecisionConflict.decision_id,
                    ),
                )
                .join(
                    Decision,
                    and_(
                        Decision.workspace_id == Memory.workspace_id,
                        Decision.memory_id == Memory.id,
                    ),
                )
                .where(
                    DecisionConflict.workspace_id == self.workspace,
                    DecisionConflict.id == uuid.UUID(conflict_id),
                )
            ).first()
            if row is None:
                raise ConflictNotFound(conflict_id)
            conflict, memory, decision = row
            if conflict.status != "open":
                raise DecisionStateError(f"the conflict is already {conflict.status}")
            conflict.status, conflict.resolved_by = status, member.id
            conflict.resolved_at, conflict.resolution_note = datetime.now(UTC), note
            self._log(
                session,
                conflict.decision_id,
                "conflict_resolved",
                conflict=conflict_id,
                status=status,
            )
            return self._conflict_view(conflict, memory, decision)
