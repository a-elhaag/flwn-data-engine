from fastapi import APIRouter

from app.api import generic_crud
from app.api.routes import (
    approvals,
    atoms,
    collaboration,
    conflicts,
    dev,
    files,
    identity,
    meetings,
    memory,
    planning,
    skills,
    system,
    users,
    workspaces,
)

router = APIRouter()
router.include_router(system.router, tags=["System"])
router.include_router(memory.router, tags=["Memory"])
router.include_router(files.router, tags=["Files"])
router.include_router(meetings.router, tags=["Meetings"])
router.include_router(conflicts.router, tags=["Decision conflicts"])
router.include_router(atoms.router, tags=["Atoms"])
router.include_router(approvals.router, tags=["Approvals"])
router.include_router(skills.router, tags=["Skills"])
router.include_router(users.router, tags=["Users"])
router.include_router(workspaces.router, tags=["Workspaces"])
router.include_router(identity.router, tags=["Identity"])
router.include_router(dev.router)
router.include_router(planning.router, tags=["Planning"])
router.include_router(collaboration.router, tags=["Collaboration"])
router.include_router(generic_crud.router, tags=["Tables"])
