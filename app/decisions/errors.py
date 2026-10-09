"""Decision Ledger errors, mapped to HTTP statuses by app.api.errors."""


class DecisionNotFound(Exception):
    """No such decision in this workspace."""


class ConflictNotFound(Exception):
    """No such flagged conflict in this workspace."""


class DecisionStateError(Exception):
    """The change is not allowed from the decision's current status. The message says why."""


class HumanRequired(Exception):
    """Only a human member may do this: the ledger flags conflicts, people decide."""
