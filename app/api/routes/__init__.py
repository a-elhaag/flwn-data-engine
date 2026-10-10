from fastapi import APIRouter

from app.api.routes import approvals, atoms, conflicts, files, meetings, memory, skills, system

router = APIRouter()
router.include_router(system.router)
router.include_router(memory.router)
router.include_router(files.router)
router.include_router(meetings.router)
router.include_router(conflicts.router)
router.include_router(atoms.router)
router.include_router(approvals.router)
router.include_router(skills.router)
