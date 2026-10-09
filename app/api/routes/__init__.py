from fastapi import APIRouter

from app.api.routes import decisions, files, meetings, memory, system

router = APIRouter()
router.include_router(system.router)
router.include_router(memory.router)
router.include_router(files.router)
router.include_router(decisions.router)
router.include_router(meetings.router)
