from fastapi import APIRouter

from src.api.v1.routers.embed import router as embed_router
from src.api.v1.routers.internal import router as internal_router

router = APIRouter()
router.include_router(embed_router)
router.include_router(internal_router)
