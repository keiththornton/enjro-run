"""Admin API for the dashboard and CLI, split by area."""

from fastapi import APIRouter

from strixrun.api.admin import binary, models, requests, session

router = APIRouter(prefix="/admin")
router.include_router(session.router)
router.include_router(models.router)
router.include_router(requests.router)
router.include_router(binary.router)
