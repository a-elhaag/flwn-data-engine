"""Domain errors mapped to HTTP statuses by the API layer."""


class MemoryNotFound(Exception):
    """No memory with this id exists in the workspace."""


class WorkspaceNotFound(Exception):
    """The workspace does not exist, so nothing can be written to it."""


class MemberNotFound(Exception):
    """The acting member does not exist in the workspace (for example, removed mid-request)."""


class MaintenanceBusy(Exception):
    """Another maintenance run (sweep/organize) holds this workspace."""


class ConfirmationRequired(Exception):
    """A destructive operation was called without the matching confirmation."""
