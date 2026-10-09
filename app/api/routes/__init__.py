from fastapi import APIRouter

from app.api.routes import conflicts, files, meetings, memory, system

router = APIRouter()
router.include_router(system.router)
router.include_router(memory.router)
router.include_router(files.router)
router.include_router(meetings.router)
router.include_router(conflicts.router)
