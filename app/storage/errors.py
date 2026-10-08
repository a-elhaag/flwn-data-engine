"""File and storage errors, mapped to HTTP statuses by app.api.errors."""


class FileNotFound(Exception):
    """No such file in this workspace."""


class StorageNotConfigured(Exception):
    """AZURE_STORAGE_ACCOUNT_URL is not set, so files cannot be stored."""


class UploadRejected(Exception):
    """The upload breaks a rule (too large, wrong size, unknown kind). The message says which."""


class UploadIncomplete(Exception):
    """The client has not uploaded the bytes yet."""


class InvalidReference(Exception):
    """The folder, project or team does not exist in this workspace."""
