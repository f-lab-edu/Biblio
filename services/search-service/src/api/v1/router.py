from fastapi import APIRouter

from src.api.v1.routers.search import router as search_router

api_v1_router = APIRouter()
api_v1_router.include_router(search_router)
