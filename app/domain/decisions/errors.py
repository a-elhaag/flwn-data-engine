"""Conflict-flag errors, mapped to HTTP statuses by app.api.errors."""

from app.domain.error_codes import DomainError


class ConflictNotFound(DomainError):
    """No such flagged conflict in this workspace."""

    code = "conflict_not_found"


class ConflictStateError(DomainError):
    """The conflict is no longer open."""

    code = "conflict_already_resolved"


class HumanRequired(DomainError):
    """Only a human member may close a flag: the ledger flags, people decide."""

    code = "human_required"
