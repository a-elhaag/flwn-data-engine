"""Storing transcripts: from Azure Speech for a recording, voice note or audio file, or handed in
ready-made (live captions) for a meeting. A source has at most one transcript; storing again
replaces it."""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.clients.inference import Transcription
from app.db.models.meetings import Transcript, TranscriptSegment


@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int
    text: str
    speaker_label: str | None = None
    speaker_member_id: uuid.UUID | None = None


def segments_from(transcription: Transcription) -> list[Segment]:
    return [
        Segment(
            p.start_ms,
            max(p.end_ms, p.start_ms),
            p.text,
            f"Speaker {p.speaker}" if p.speaker else None,
        )
        for p in transcription.phrases
    ]


def turns(segments: list[Segment]) -> list[tuple[str | None, str]]:
    """Consecutive segments by the same speaker joined into one turn: (speaker, text)."""
    result: list[tuple[str | None, str]] = []
    for segment in segments:
        who = segment.speaker_label or (
            str(segment.speaker_member_id) if segment.speaker_member_id else None
        )
        if result and result[-1][0] == who:
            result[-1] = (who, f"{result[-1][1]} {segment.text}")
        else:
            result.append((who, segment.text))
    return result


def full_text(segments: list[Segment], names: dict[uuid.UUID, str] | None = None) -> str:
    def label(segment: Segment) -> str | None:
        if segment.speaker_member_id and names and segment.speaker_member_id in names:
            return names[segment.speaker_member_id]
        return segment.speaker_label

    lines: list[str] = []
    last = object()
    for segment in segments:
        who = label(segment)
        if who == last and lines:
            lines[-1] += f" {segment.text}"
        else:
            lines.append(f"{who}: {segment.text}" if who else segment.text)
        last = who
    return "\n".join(lines)


def store(
    session: Session,
    workspace_id: uuid.UUID,
    *,
    segments: list[Segment],
    language: str | None,
    model: str | None,
    duration_ms: int | None,
    file_id: uuid.UUID | None = None,
    meeting_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
) -> Transcript:
    """Replace the transcript of this file, meeting or message with these segments."""
    same_source = [Transcript.workspace_id == workspace_id]
    same_source += [
        Transcript.file_id == file_id if file_id else Transcript.file_id.is_(None),
        Transcript.meeting_id == meeting_id if meeting_id else Transcript.meeting_id.is_(None),
        Transcript.message_id == message_id if message_id else Transcript.message_id.is_(None),
    ]
    session.execute(delete(Transcript).where(*same_source))
    row = Transcript(
        workspace_id=workspace_id,
        file_id=file_id,
        meeting_id=meeting_id,
        message_id=message_id,
        language=language,
        status="ready",
        full_text=full_text(segments),
        model=model,
        duration_ms=duration_ms,
    )
    session.add(row)
    session.flush()
    session.add_all(
        TranscriptSegment(
            workspace_id=workspace_id,
            transcript_id=row.id,
            sequence=index,
            speaker_member_id=segment.speaker_member_id,
            speaker_label=segment.speaker_label,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            text=segment.text,
        )
        for index, segment in enumerate(segments)
    )
    return row


def for_source(session: Session, workspace_id: uuid.UUID, **source) -> Transcript | None:
    ((column, value),) = source.items()
    return session.scalars(
        select(Transcript).where(
            Transcript.workspace_id == workspace_id, getattr(Transcript, column) == value
        )
    ).first()
