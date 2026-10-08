from fastapi import APIRouter

from app.api.routes import files, memory, system

router = APIRouter()
router.include_router(system.router)
router.include_router(memory.router)
router.include_router(files.router)
