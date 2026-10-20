"""Meetings: participants and consent, recordings, transcripts."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.auth import Principal
from app.api.deps import MEETINGS_READ, MEETINGS_WRITE, Meetings
from app.api.schemas import (
    ConsentRequest,
    CreateMeetingRequest,
    IngestTranscriptRequest,
    InviteRequest,
    StartRecordingRequest,
    UpdateMeetingRequest,
)
from app.meetings.service import MeetingPage, MeetingView, RecordingStart, TranscriptView
from app.meetings.transcripts import Segment

router = APIRouter(prefix="/workspaces/{workspace_id}/meetings")


@router.post("", status_code=201)
def create_meeting(
    _: Annotated[Principal, MEETINGS_WRITE], request: CreateMeetingRequest, meetings: Meetings
) -> MeetingView:
    """The acting member hosts and is added as a participant."""
    return meetings.create(**request.model_dump())


@router.get("", dependencies=[MEETINGS_READ])
def list_meetings(
    meetings: Meetings,
    status: Annotated[str | None, Query(pattern="^(scheduled|live|ended|canceled)$")] = None,
    project_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MeetingPage:
    """Members see the meetings they host or attend; the trusted backend sees all."""
    return meetings.list(status, project_id, limit, offset)


@router.get("/{meeting_id}", dependencies=[MEETINGS_READ])
def get_meeting(meeting_id: str, meetings: Meetings) -> MeetingView:
    return meetings.get(meeting_id)


@router.patch("/{meeting_id}", dependencies=[MEETINGS_WRITE])
def update_meeting(
    meeting_id: str, request: UpdateMeetingRequest, meetings: Meetings
) -> MeetingView:
    """Host only. `live` stamps the start time, `ended` and `canceled` the end time."""
    return meetings.update(meeting_id, **request.model_dump(exclude_unset=True))


@router.post("/{meeting_id}/participants", dependencies=[MEETINGS_WRITE])
def invite(meeting_id: str, request: InviteRequest, meetings: Meetings) -> MeetingView:
    return meetings.invite(meeting_id, request.member_ids)


@router.put("/{meeting_id}/consent", dependencies=[MEETINGS_WRITE])
def answer_consent(meeting_id: str, request: ConsentRequest, meetings: Meetings) -> MeetingView:
    """A participant agrees to (or declines) being recorded. Only for themselves."""
    return meetings.consent(meeting_id, request.status)


@router.post("/{meeting_id}/recordings", status_code=201, dependencies=[MEETINGS_WRITE])
def start_recording(
    meeting_id: str, request: StartRecordingRequest, meetings: Meetings
) -> RecordingStart:
    """Trusted backend only, and only once every participant has granted consent. Returns an
    upload link like the files API; `complete` the file, then it is transcribed automatically."""
    return meetings.start_recording(meeting_id, **request.model_dump())


@router.put("/{meeting_id}/transcript", dependencies=[MEETINGS_WRITE])
def ingest_transcript(
    meeting_id: str, request: IngestTranscriptRequest, meetings: Meetings
) -> TranscriptView:
    """Hand in a ready-made transcript (for example live captions). Replaces the earlier one."""
    segments = [Segment(**item.model_dump()) for item in request.segments]
    return meetings.ingest_transcript(meeting_id, segments, request.language)


@router.get("/{meeting_id}/transcript", dependencies=[MEETINGS_READ])
def get_transcript(
    meeting_id: str,
    meetings: Meetings,
    limit: Annotated[int, Query(ge=0, le=2000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TranscriptView:
    return meetings.transcript(meeting_id, limit, offset)
