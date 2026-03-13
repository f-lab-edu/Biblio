from fastapi import APIRouter

from src.api.v1.routers.videos import videos_router

api_v1_router = APIRouter(tags=["system"])


@api_v1_router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


api_v1_router.include_router(videos_router)
