"""Admin API for the dashboard and CLI, split by area."""

from fastapi import APIRouter

from strixrun.api.admin import binary, hf, models, requests, session, settings

router = APIRouter(prefix="/admin")
router.include_router(session.router)
router.include_router(models.router)
router.include_router(requests.router)
router.include_router(binary.router)
router.include_router(hf.router)
router.include_router(settings.router)
