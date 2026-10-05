"""Domain errors mapped to HTTP statuses by the API layer."""


class MemoryNotFound(Exception):
    """No memory with this id exists in the workspace."""


class MaintenanceBusy(Exception):
    """Another maintenance run (sweep/organize) holds this workspace."""


class ConfirmationRequired(Exception):
    """A destructive operation was called without the matching confirmation."""
