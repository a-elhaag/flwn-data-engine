"""File and storage errors, mapped to HTTP statuses by app.api.errors."""

from app.db.errors import InvalidReference  # noqa: F401  (re-exported)
from app.domain.error_codes import DomainError


class FileNotFound(DomainError):
    """No such file in this workspace."""

    code = "file_not_found"


class StorageNotConfigured(DomainError):
    """AZURE_STORAGE_ACCOUNT_URL is not set, so files cannot be stored."""

    code = "storage_not_configured"


class UploadRejected(DomainError):
    """The upload breaks a rule (too large, wrong size, unknown kind). The message says which."""

    code = "upload_rejected"


class UploadIncomplete(DomainError):
    """The client has not uploaded the bytes yet."""

    code = "upload_incomplete"


class Unindexable(DomainError):
    """The file cannot be turned into searchable text. The message says why; it is not a failure."""

    code = "not_indexable"
