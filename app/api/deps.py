"""Shared route dependencies: the workspace's steward and the permission each route needs."""

from typing import Annotated

from fastapi import Depends

from app.api import tokens
from app.api.auth import WorkspaceId, allow
from app.memory.steward import MemorySteward
from app.storage.blobs import BlobStorage, get_storage
from app.storage.files import FileService


def steward(workspace_id: WorkspaceId) -> MemorySteward:
    return MemorySteward(str(workspace_id))


def files_service(
    workspace_id: WorkspaceId, storage: Annotated[BlobStorage, Depends(get_storage)]
) -> FileService:
    return FileService(str(workspace_id), storage)


Memory = Annotated[MemorySteward, Depends(steward)]
Files = Annotated[FileService, Depends(files_service)]

# Agent tokens carry these scopes; ADMIN is for the trusted backend (service key) only.
READ = allow(tokens.SCOPE_READ)
WRITE = allow(tokens.SCOPE_WRITE)
DELETE = allow(tokens.SCOPE_DELETE)
FILES_READ = allow(tokens.SCOPE_FILES_READ)
FILES_WRITE = allow(tokens.SCOPE_FILES_WRITE)
FILES_DELETE = allow(tokens.SCOPE_FILES_DELETE)
ADMIN = allow(None)
