"""Domain errors mapped to HTTP statuses by the API layer."""

from app.db.errors import WorkspaceNotFound  # noqa: F401  (re-exported)
from app.domain.error_codes import DomainError


class MemoryNotFound(DomainError):
    """No memory with this id exists in the workspace."""

    code = "memory_not_found"


class MemberNotFound(DomainError):
    """The acting member does not exist in the workspace (for example, removed mid-request)."""

    code = "member_not_found"


class MaintenanceBusy(DomainError):
    """Another maintenance run (sweep/organize) holds this workspace."""

    code = "maintenance_busy"


class ConfirmationRequired(DomainError):
    """A destructive operation was called without the matching confirmation."""

    code = "confirmation_mismatch"
