"""Errors raised by more than one domain, so no domain has to import another's errors."""


class WorkspaceNotFound(Exception):
    """The workspace does not exist, so nothing can be written to it."""


class InvalidReference(Exception):
    """The folder, project or team does not exist in this workspace."""
