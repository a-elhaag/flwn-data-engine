"""Conflict-flag errors, mapped to HTTP statuses by app.api.errors."""


class ConflictNotFound(Exception):
    """No such flagged conflict in this workspace."""


class ConflictStateError(Exception):
    """The conflict is no longer open."""


class HumanRequired(Exception):
    """Only a human member may close a flag: the ledger flags, people decide."""
