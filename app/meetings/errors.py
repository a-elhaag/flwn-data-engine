"""Meeting errors, mapped to HTTP statuses by app.api.errors."""


class MeetingNotFound(Exception):
    """No such meeting, or the caller is not part of it (which looks the same on purpose)."""


class MeetingForbidden(Exception):
    """Only the meeting's host (or the trusted backend) may do this."""


class ConsentMissing(Exception):
    """Recording needs every participant's consent. The message names who has not given it."""


class MeetingStateError(Exception):
    """The change is not allowed from the meeting's current status."""
