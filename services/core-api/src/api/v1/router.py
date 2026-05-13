from fastapi import APIRouter

from src.api.v1.routers.admin import admin_router
from src.api.v1.routers.feedbacks import feedbacks_router
from src.api.v1.routers.videos import project_videos_router, videos_router

api_v1_router = APIRouter(tags=["system"])


api_v1_router.include_router(videos_router)
api_v1_router.include_router(project_videos_router)
api_v1_router.include_router(feedbacks_router)
api_v1_router.include_router(admin_router)
