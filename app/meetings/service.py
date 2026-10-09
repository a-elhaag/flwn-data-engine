"""Meetings: who is in them, who agreed to be recorded, recordings and transcripts.

Visibility follows participation. The trusted backend (service key, acting for no one) sees every
meeting; a member sees only meetings they host or attend; to anyone else a meeting does not exist.
Recording needs every participant's consent and can only be started by the trusted backend.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from app.db import events
from app.db.models.identity import Member, User
from app.db.models.meetings import (
    Meeting,
    MeetingParticipant,
    Recording,
    Transcript,
    TranscriptSegment,
)
from app.db.session import session_for
from app.meetings import transcripts
from app.meetings.errors import ConsentMissing, MeetingForbidden, MeetingNotFound, MeetingStateError
from app.meetings.transcripts import Segment
from app.storage.errors import InvalidReference
from app.storage.files import FileService, UploadTicket

NEXT_STATUS = {
    "scheduled": {"live", "ended", "canceled"},
    "live": {"ended", "canceled"},
    "ended": set(),
    "canceled": set(),
}


@dataclass
class ParticipantView:
    member_id: str
    name: str | None
    consent_status: str
    consented_at: datetime | None
    joined_at: datetime | None
    left_at: datetime | None


@dataclass
class MeetingView:
    id: str
    title: str
    status: str
    host_id: str | None
    team_id: str | None
    project_id: str | None
    channel_id: str | None
    scheduled_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    participants: list[ParticipantView] = field(default_factory=list)


@dataclass
class MeetingPage:
    items: list[MeetingView]


@dataclass
class RecordingStart:
    recording_id: str
    upload: UploadTicket


@dataclass
class SegmentView:
    sequence: int
    start_ms: int
    end_ms: int
    text: str
    speaker_label: str | None
    speaker_member_id: str | None
    speaker_name: str | None


@dataclass
class TranscriptView:
    id: str
    status: str
    language: str | None
    duration_ms: int | None
    model: str | None
    full_text: str | None
    segments: list[SegmentView]
    total_segments: int


# A human's name is on their user account; only AI members carry their own.
NAME = func.coalesce(User.name, Member.name)


def _id(value) -> str | None:
    return str(value) if value else None


class MeetingService:
    """Meetings for one workspace. `actor` is the member acting; `trusted` is the backend itself."""

    def __init__(
        self,
        workspace_id: str,
        actor: str | None = None,
        trusted: bool = False,
        files: FileService | None = None,
    ):
        self.workspace_id = str(uuid.UUID(workspace_id))
        self.workspace = uuid.UUID(self.workspace_id)
        self.actor = uuid.UUID(actor) if actor else None
        self.trusted = trusted
        self.files = files

    def _log(self, session, meeting_id, action: str, **changes) -> None:
        events.record(
            session,
            self.workspace_id,
            str(self.actor) if self.actor else None,
            "meeting",
            meeting_id,
            action,
            changes,
        )

    def _flush(self, session) -> None:
        try:
            session.flush()
        except IntegrityError as exc:
            if isinstance(exc.orig, psycopg.errors.ForeignKeyViolation):
                raise InvalidReference(
                    "member, team, project or channel not found in this workspace"
                ) from None
            raise

    # -- access -----------------------------------------------------------------------------------

    def _meeting(self, session, meeting_id: str, *, host_only: bool = False) -> Meeting:
        try:
            wanted = uuid.UUID(meeting_id)
        except ValueError:
            raise MeetingNotFound(meeting_id) from None
        meeting = session.scalars(
            select(Meeting).where(Meeting.workspace_id == self.workspace, Meeting.id == wanted)
        ).one_or_none()
        if meeting is None or not self._is_part_of(session, meeting):
            raise MeetingNotFound(meeting_id)
        if host_only and not (self.trusted or meeting.host_id == self.actor):
            raise MeetingForbidden("only the host can do this")
        return meeting

    def _is_part_of(self, session, meeting: Meeting) -> bool:
        if self.trusted:
            return True
        if self.actor is None:
            return False
        if meeting.host_id == self.actor:
            return True
        return self._participant(session, meeting) is not None

    def _participant(self, session, meeting: Meeting) -> MeetingParticipant | None:
        return session.scalars(
            select(MeetingParticipant).where(
                MeetingParticipant.workspace_id == self.workspace,
                MeetingParticipant.meeting_id == meeting.id,
                MeetingParticipant.member_id == self.actor,
            )
        ).one_or_none()

    def _view(self, session, meeting: Meeting) -> MeetingView:
        rows = session.execute(
            select(MeetingParticipant, NAME)
            .join(
                Member,
                and_(
                    Member.workspace_id == MeetingParticipant.workspace_id,
                    Member.id == MeetingParticipant.member_id,
                ),
            )
            .outerjoin(User, User.id == Member.user_id)
            .where(
                MeetingParticipant.workspace_id == self.workspace,
                MeetingParticipant.meeting_id == meeting.id,
            )
            .order_by(NAME, MeetingParticipant.member_id)
        )
        return MeetingView(
            id=str(meeting.id),
            title=meeting.title,
            status=meeting.status,
            host_id=_id(meeting.host_id),
            team_id=_id(meeting.team_id),
            project_id=_id(meeting.project_id),
            channel_id=_id(meeting.channel_id),
            scheduled_at=meeting.scheduled_at,
            started_at=meeting.started_at,
            ended_at=meeting.ended_at,
            participants=[
                ParticipantView(
                    str(p.member_id), name, p.consent_status, p.consented_at, p.joined_at, p.left_at
                )
                for p, name in rows
            ],
        )

    # -- meetings ------------------------------------------------------------------------------------

    def create(
        self,
        *,
        title: str,
        participants: list[uuid.UUID],
        team_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        channel_id: uuid.UUID | None = None,
        scheduled_at: datetime | None = None,
        host_id: uuid.UUID | None = None,
    ) -> MeetingView:
        """The acting member hosts. The trusted backend acting for no one may name the host."""
        host = self.actor or (host_id if self.trusted else None)
        with session_for(self.workspace_id) as session:
            meeting = Meeting(
                workspace_id=self.workspace,
                title=title.strip(),
                host_id=host,
                team_id=team_id,
                project_id=project_id,
                channel_id=channel_id,
                scheduled_at=scheduled_at,
            )
            session.add(meeting)
            self._flush(session)
            for member in dict.fromkeys(([host] if host else []) + list(participants)):
                session.add(
                    MeetingParticipant(
                        workspace_id=self.workspace, meeting_id=meeting.id, member_id=member
                    )
                )
            self._flush(session)
            self._log(session, meeting.id, "created", title=meeting.title)
            return self._view(session, meeting)

    def get(self, meeting_id: str) -> MeetingView:
        with session_for(self.workspace_id) as session:
            return self._view(session, self._meeting(session, meeting_id))

    def list(
        self,
        status: str | None = None,
        project_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> MeetingPage:
        statement = select(Meeting).where(Meeting.workspace_id == self.workspace)
        if not self.trusted:
            if self.actor is None:
                return MeetingPage([])
            attending = select(MeetingParticipant.meeting_id).where(
                MeetingParticipant.workspace_id == self.workspace,
                MeetingParticipant.member_id == self.actor,
            )
            statement = statement.where((Meeting.host_id == self.actor) | Meeting.id.in_(attending))
        if status:
            statement = statement.where(Meeting.status == status)
        if project_id:
            statement = statement.where(Meeting.project_id == project_id)
        statement = statement.order_by(
            Meeting.started_at.desc().nulls_last(), Meeting.created_at.desc(), Meeting.id
        )
        with session_for(self.workspace_id) as session:
            rows = session.scalars(statement.limit(limit).offset(offset)).all()
            return MeetingPage([self._view(session, row) for row in rows])

    def update(
        self,
        meeting_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
        scheduled_at: datetime | None = None,
    ) -> MeetingView:
        with session_for(self.workspace_id) as session:
            meeting = self._meeting(session, meeting_id, host_only=True)
            if title:
                meeting.title = title.strip()
            if scheduled_at:
                meeting.scheduled_at = scheduled_at
            if status and status != meeting.status:
                if status not in NEXT_STATUS[meeting.status]:
                    raise MeetingStateError(f"a {meeting.status} meeting cannot become {status}")
                now = datetime.now(UTC)
                if status == "live":
                    meeting.started_at = now
                elif status in ("ended", "canceled"):
                    meeting.ended_at = now
                meeting.status = status
            self._log(session, meeting.id, "updated", status=meeting.status)
            return self._view(session, meeting)

    def invite(self, meeting_id: str, member_ids: list[uuid.UUID]) -> MeetingView:
        with session_for(self.workspace_id) as session:
            meeting = self._meeting(session, meeting_id, host_only=True)
            if meeting.status in ("ended", "canceled"):
                raise MeetingStateError(f"a {meeting.status} meeting cannot take new participants")
            if session.scalars(
                select(Recording.id).where(
                    Recording.workspace_id == self.workspace, Recording.meeting_id == meeting.id
                )
            ).first():
                # someone added now was never asked before the recording began
                raise MeetingStateError("a recorded meeting cannot take new participants")
            have = set(
                session.scalars(
                    select(MeetingParticipant.member_id).where(
                        MeetingParticipant.workspace_id == self.workspace,
                        MeetingParticipant.meeting_id == meeting.id,
                    )
                )
            )
            for member in dict.fromkeys(member_ids):
                if member not in have:
                    session.add(
                        MeetingParticipant(
                            workspace_id=self.workspace, meeting_id=meeting.id, member_id=member
                        )
                    )
            self._flush(session)
            self._log(session, meeting.id, "invited", members=[str(m) for m in member_ids])
            return self._view(session, meeting)

    # -- consent and recordings --------------------------------------------------------------------------

    def consent(self, meeting_id: str, status: str) -> MeetingView:
        """A participant answers for themselves, and only for themselves."""
        with session_for(self.workspace_id) as session:
            meeting = self._meeting(session, meeting_id)
            participant = self._participant(session, meeting) if self.actor else None
            if participant is None:
                raise MeetingForbidden("only a participant can answer for themselves")
            if meeting.status in ("ended", "canceled"):
                raise MeetingStateError(f"a {meeting.status} meeting cannot change consent")
            participant.consent_status = status
            participant.consented_at = datetime.now(UTC) if status == "granted" else None
            self._log(session, meeting.id, f"consent_{status}")
            return self._view(session, meeting)

    def _consented(self, session, meeting: Meeting) -> tuple[dict, set[uuid.UUID]]:
        """Who agreed, and who is present. Raises unless every participant has granted consent."""
        people = session.scalars(
            select(MeetingParticipant).where(
                MeetingParticipant.workspace_id == self.workspace,
                MeetingParticipant.meeting_id == meeting.id,
            )
        ).all()
        waiting = [str(p.member_id) for p in people if p.consent_status != "granted"]
        if not people or waiting:
            raise ConsentMissing(
                "every participant must consent before recording or transcribing"
                + (f"; missing: {', '.join(waiting)}" if waiting else "; there are none")
            )
        snapshot = {
            str(p.member_id): p.consented_at.isoformat() if p.consented_at else None for p in people
        }
        return snapshot, {p.member_id for p in people}

    def start_recording(
        self, meeting_id: str, *, name: str, content_type: str, size_bytes: int
    ) -> RecordingStart:
        """Register a recording and get its upload link. Everyone present must have agreed."""
        if not self.trusted or self.files is None:
            raise MeetingForbidden("recordings are started by the meeting service")
        with session_for(self.workspace_id) as session:
            meeting = self._meeting(session, meeting_id)
            if meeting.status not in ("live", "ended"):
                raise MeetingStateError("only a live or ended meeting can have a recording")
            snapshot, _ = self._consented(session, meeting)
            team_id, project_id = meeting.team_id, meeting.project_id
        ticket = self.files.start_upload(
            kind="recording",
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            source="meeting",
            team_id=team_id,
            project_id=project_id,
        )
        with session_for(self.workspace_id) as session:
            recording = Recording(
                workspace_id=self.workspace,
                meeting_id=uuid.UUID(meeting_id),
                file_id=uuid.UUID(ticket.file_id),
                consent_snapshot=snapshot,
                status="processing",
            )
            session.add(recording)
            self._flush(session)
            self._log(session, meeting_id, "recording_started", file_id=ticket.file_id)
            return RecordingStart(str(recording.id), ticket)

    # -- transcripts ------------------------------------------------------------------------------------------

    def ingest_transcript(
        self, meeting_id: str, segments: list[Segment], language: str | None = None
    ) -> TranscriptView:
        """Store a ready-made transcript (live captions), replacing the meeting's earlier supplied
        one. Host or trusted backend only: it is a record of what people said. It needs everyone's
        consent like a recording does, and every named speaker must be a participant."""
        with session_for(self.workspace_id) as session:
            meeting = self._meeting(session, meeting_id, host_only=True)
            if meeting.status not in ("live", "ended"):
                raise MeetingStateError("only a live or ended meeting can have a transcript")
            _, present = self._consented(session, meeting)
            if any(s.speaker_member_id and s.speaker_member_id not in present for s in segments):
                raise InvalidReference("a speaker is not a participant of this meeting")
            known = {s.speaker_member_id for s in segments if s.speaker_member_id}
            names = dict(
                session.execute(
                    select(Member.id, NAME)
                    .outerjoin(User, User.id == Member.user_id)
                    .where(Member.workspace_id == self.workspace, Member.id.in_(known))
                ).all()
            )
            segments = [  # a speaker given only as a member is labelled with their name
                Segment(
                    s.start_ms,
                    s.end_ms,
                    s.text,
                    s.speaker_label or names.get(s.speaker_member_id),
                    s.speaker_member_id,
                )
                for s in segments
            ]
            row = transcripts.store(
                session,
                self.workspace,
                segments=segments,
                language=language,
                model="supplied",
                duration_ms=max((s.end_ms for s in segments), default=0),
                meeting_id=meeting.id,
            )
            self._flush(session)
            self._log(session, meeting.id, "transcript_ingested", segments=len(segments))
            return self._transcript_view(session, row, limit=0, offset=0)

    def transcript(self, meeting_id: str, limit: int = 500, offset: int = 0) -> TranscriptView:
        with session_for(self.workspace_id) as session:
            meeting = self._meeting(session, meeting_id)
            row = session.scalars(
                select(Transcript)
                .where(
                    Transcript.workspace_id == self.workspace,
                    Transcript.meeting_id == meeting.id,
                    Transcript.status == "ready",
                )
                .order_by(Transcript.file_id.is_(None), Transcript.created_at.desc())
                .limit(1)
            ).first()
            if row is None:
                raise MeetingNotFound(meeting_id)
            return self._transcript_view(session, row, limit, offset)

    def _transcript_view(self, session, row: Transcript, limit: int, offset: int) -> TranscriptView:
        rows = session.execute(
            select(TranscriptSegment, NAME)
            .outerjoin(
                Member,
                and_(
                    Member.workspace_id == TranscriptSegment.workspace_id,
                    Member.id == TranscriptSegment.speaker_member_id,
                ),
            )
            .outerjoin(User, User.id == Member.user_id)
            .where(
                TranscriptSegment.workspace_id == self.workspace,
                TranscriptSegment.transcript_id == row.id,
            )
            .order_by(TranscriptSegment.sequence)
            .limit(limit)
            .offset(offset)
        ).all()
        total = session.scalar(
            select(func.count(TranscriptSegment.id)).where(
                TranscriptSegment.workspace_id == self.workspace,
                TranscriptSegment.transcript_id == row.id,
            )
        )
        return TranscriptView(
            id=str(row.id),
            status=row.status,
            language=row.language,
            duration_ms=row.duration_ms,
            model=row.model,
            full_text=row.full_text,
            segments=[
                SegmentView(
                    s.sequence,
                    s.start_ms,
                    s.end_ms,
                    s.text,
                    s.speaker_label,
                    _id(s.speaker_member_id),
                    name,
                )
                for s, name in rows
            ],
            total_segments=total or 0,
        )
