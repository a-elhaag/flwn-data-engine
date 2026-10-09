"""Shared route dependencies: the workspace's steward and the permission each route needs."""

from typing import Annotated

from fastapi import Depends, Request

from app.api import tokens
from app.api.auth import WorkspaceId, allow
from app.decisions.service import DecisionService
from app.memory.steward import MemorySteward
from app.storage.blobs import BlobStorage, get_storage
from app.storage.files import FileService


def _actor(request: Request) -> str | None:
    """The member acting, set by the permission check that must run before these dependencies.

    If the check has not run, fail loudly: building the service anyway would write records with
    no author and no error, which is the failure this exists to prevent.
    """
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise RuntimeError("route builds a service before its permission check has run")
    return principal.member_id


def steward(request: Request, workspace_id: WorkspaceId) -> MemorySteward:
    return MemorySteward(str(workspace_id), actor=_actor(request))


def files_service(
    request: Request,
    workspace_id: WorkspaceId,
    storage: Annotated[BlobStorage, Depends(get_storage)],
) -> FileService:
    return FileService(str(workspace_id), storage, actor=_actor(request))


def decision_ledger(request: Request, workspace_id: WorkspaceId) -> DecisionService:
    return DecisionService(str(workspace_id), actor=_actor(request))


Memory = Annotated[MemorySteward, Depends(steward)]
Files = Annotated[FileService, Depends(files_service)]
Decisions = Annotated[DecisionService, Depends(decision_ledger)]

# Agent tokens carry these scopes; ADMIN is for the trusted backend (service key) only.
READ = allow(tokens.SCOPE_READ)
WRITE = allow(tokens.SCOPE_WRITE)
DELETE = allow(tokens.SCOPE_DELETE)
FILES_READ = allow(tokens.SCOPE_FILES_READ)
FILES_WRITE = allow(tokens.SCOPE_FILES_WRITE)
FILES_DELETE = allow(tokens.SCOPE_FILES_DELETE)
DECISIONS_READ = allow(tokens.SCOPE_DECISIONS_READ)
DECISIONS_WRITE = allow(tokens.SCOPE_DECISIONS_WRITE)
ADMIN = allow(None)
