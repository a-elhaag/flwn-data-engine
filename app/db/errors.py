"""Errors raised by more than one domain, so no domain has to import another's errors."""

from app.domain.error_codes import DomainError


class WorkspaceNotFound(DomainError):
    """The workspace does not exist, so nothing can be written to it."""

    code = "workspace_not_found"


class InvalidReference(DomainError):
    """The folder, project or team does not exist in this workspace."""

    code = "reference_not_found"
