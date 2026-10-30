"""Domain errors mapped to HTTP statuses by the API layer."""

from app.db.errors import WorkspaceNotFound  # noqa: F401  (re-exported)


class MemoryNotFound(Exception):
    """No memory with this id exists in the workspace."""


class MemberNotFound(Exception):
    """The acting member does not exist in the workspace (for example, removed mid-request)."""


class MaintenanceBusy(Exception):
    """Another maintenance run (sweep/organize) holds this workspace."""


class ConfirmationRequired(Exception):
    """A destructive operation was called without the matching confirmation."""
