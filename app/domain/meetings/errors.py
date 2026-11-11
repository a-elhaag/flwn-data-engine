"""Meeting errors, mapped to HTTP statuses by app.api.errors."""

from app.domain.error_codes import DomainError


class MeetingNotFound(DomainError):
    """No such meeting, or the caller is not part of it (which looks the same on purpose)."""

    code = "meeting_not_found"


class MeetingForbidden(DomainError):
    """Only the meeting's host (or the trusted backend) may do this."""

    code = "meeting_forbidden"


class ConsentMissing(DomainError):
    """Recording needs every participant's consent. The message names who has not given it."""

    code = "consent_missing"


class MeetingStateError(DomainError):
    """The change is not allowed from the meeting's current status."""

    code = "meeting_state_invalid"
