from fastapi import APIRouter

from src.api.v1.routers.embed import router as embed_router

router = APIRouter()
router.include_router(embed_router)

