from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeAlias
from uuid import UUID, uuid4

import jwt
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.config import Settings
from src.infra.db.video_repository import VideoRepository
from src.models.video import Video

TEST_JWT_SIGNING_KEY = "biblio-jwt-signing-test-value-32b"
SessionFactory: TypeAlias = async_sessionmaker[AsyncSession]


@dataclass(slots=True)
class AppContext:
    app: FastAPI
    settings: Settings
    session_factory: SessionFactory


def create_token(secret: str, requester_user_id: str) -> str:
    payload = {
        "requester_user_id": requester_user_id,
        "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def seed_video(session_factory: SessionFactory, video: Video) -> None:
    await seed_videos(session_factory, video)


async def seed_videos(session_factory: SessionFactory, *videos: Video) -> None:
    async with session_factory() as session:
        repository = VideoRepository(session)
        for video in videos:
            await repository.add(video)
        await session.commit()


def build_video(
    *,
    user_id: UUID,
    status: str = "PENDING",
    input_type: str = "LOCAL_FILE",
    title: str = "Local upload",
    category: str = "GENERAL",
    created_at: datetime | None = None,
    source_url: str | None = None,
    failed_stage: str | None = None,
) -> Video:
    video_id = uuid4()
    if source_url is None and input_type == "EXTERNAL_URL":
        source_url = "https://example.com/watch?v=1"
    if failed_stage is None and status == "FAILED":
        failed_stage = "STT"

    path_suffix = "original.mp4" if input_type == "LOCAL_FILE" else "original"
    kwargs = {}
    if created_at is not None:
        kwargs["created_at"] = created_at
        kwargs["updated_at"] = created_at

    return Video(
        id=video_id,
        user_id=user_id,
        title=title,
        category=category,
        input_type=input_type,
        source_url=source_url,
        storage_path=f"videos/{user_id}/{video_id}/{path_suffix}",
        status=status,
        failed_stage=failed_stage,
        **kwargs,
    )

