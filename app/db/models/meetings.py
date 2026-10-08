"""Flwn Meet: meetings, consent, recordings, transcripts (also used for chat voice notes)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    EMPTY_JSON,
    Base,
    Created,
    IdPk,
    Stamps,
    Tenant,
    one_of,
    tenant_unique,
    tfk,
)


class Meeting(IdPk, Tenant, Stamps, Base):
    __tablename__ = "meetings"

    team_id: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]
    channel_id: Mapped[uuid.UUID | None]
    title: Mapped[str]
    host_id: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(server_default="scheduled")
    scheduled_at: Mapped[datetime | None]
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    room_ref: Mapped[str | None]  # id of the WebRTC / LiveKit room

    __table_args__ = (
        tenant_unique(),
        one_of("status", "scheduled", "live", "ended", "canceled"),
        tfk("team_id", "teams", "set null"),
        tfk("project_id", "projects", "set null"),
        tfk("channel_id", "channels", "set null"),
        tfk("host_id", "members", "set null"),
        Index(
            "ix_meetings_project",
            "project_id",
            "started_at",
            postgresql_where=text("project_id is not null"),
        ),
    )


class MeetingParticipant(Tenant, Base):
    __tablename__ = "meeting_participants"

    meeting_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    member_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    joined_at: Mapped[datetime | None]
    left_at: Mapped[datetime | None]
    # Recording must be impossible without consent; the app checks every participant's row.
    consent_status: Mapped[str] = mapped_column(server_default="pending")
    consented_at: Mapped[datetime | None]

    __table_args__ = (
        one_of("consent_status", "pending", "granted", "declined"),
        tfk("meeting_id", "meetings", "cascade"),
        tfk("member_id", "members", "cascade"),
    )


class Recording(IdPk, Tenant, Stamps, Base):
    __tablename__ = "recordings"

    meeting_id: Mapped[uuid.UUID]
    file_id: Mapped[uuid.UUID | None]
    consent_snapshot: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)  # who consented, when
    duration_ms: Mapped[int | None]
    status: Mapped[str] = mapped_column(server_default="recording")
    retention_until: Mapped[datetime | None]

    __table_args__ = (
        tenant_unique(),
        one_of("status", "recording", "processing", "ready", "failed", "deleted"),
        tfk("meeting_id", "meetings", "cascade"),
        tfk("file_id", "files", "set null"),
    )


class Transcript(IdPk, Tenant, Created, Base):
    """Speech to text for a recording, a call, or a chat voice note (file and message set)."""

    __tablename__ = "transcripts"

    file_id: Mapped[uuid.UUID | None]
    meeting_id: Mapped[uuid.UUID | None]
    message_id: Mapped[uuid.UUID | None]
    language: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default="pending")
    full_text: Mapped[str | None]
    model: Mapped[str | None]
    duration_ms: Mapped[int | None]

    __table_args__ = (
        tenant_unique(),
        one_of("status", "pending", "processing", "ready", "failed"),
        CheckConstraint("num_nonnulls(file_id, meeting_id, message_id) >= 1", name="has_source"),
        tfk("file_id", "files", "cascade"),
        tfk("meeting_id", "meetings", "cascade"),
        tfk("message_id", "messages", "cascade"),
    )


class TranscriptSegment(IdPk, Tenant, Base):
    __tablename__ = "transcript_segments"

    transcript_id: Mapped[uuid.UUID]
    sequence: Mapped[int]
    speaker_member_id: Mapped[uuid.UUID | None]
    speaker_label: Mapped[str | None]  # "Speaker 2" when diarization can't name the member
    start_ms: Mapped[int]
    end_ms: Mapped[int]
    text: Mapped[str]

    __table_args__ = (
        CheckConstraint("end_ms >= start_ms", name="span"),
        tfk("transcript_id", "transcripts", "cascade"),
        tfk("speaker_member_id", "members", "set null"),
        Index(
            "ix_transcript_segments_transcript_id_sequence",
            "transcript_id",
            "sequence",
            unique=True,
        ),
    )
