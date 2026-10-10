from fastapi import APIRouter

from app.api.routes import (
    approvals,
    atoms,
    collaboration,
    conflicts,
    files,
    identity,
    meetings,
    memory,
    planning,
    skills,
    system,
    workspaces,
)

router = APIRouter()
router.include_router(system.router)
router.include_router(memory.router)
router.include_router(files.router)
router.include_router(meetings.router)
router.include_router(conflicts.router)
router.include_router(atoms.router)
router.include_router(approvals.router)
router.include_router(skills.router)
router.include_router(workspaces.router)
router.include_router(identity.router)
router.include_router(planning.router)
router.include_router(collaboration.router)
