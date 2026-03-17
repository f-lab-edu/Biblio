from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.infra.db.cursor import CursorDecodeError, KeysetCursor, decode_cursor, encode_cursor
from src.infra.db.video_repository import VideoRepository
from tests.support import SessionFactory, build_video


@pytest.mark.asyncio
async def test_alembic_creates_video_table_and_indexes(session_factory: SessionFactory) -> None:
    async with session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public' AND tablename = 'video'
                """
            )
        )
        index_names = {row[0] for row in result.fetchall()}

    assert "idx_video_user_created" in index_names
    assert "idx_video_user_status" in index_names


@pytest.mark.asyncio
async def test_video_repository_enforces_tenancy_and_cursor_pagination(
    session_factory: SessionFactory,
) -> None:
    owner_id = uuid4()
    other_user_id = uuid4()
    base_time = datetime(2026, 3, 12, 0, 0, tzinfo=UTC)

    owner_videos = [
        build_video(
            user_id=owner_id,
            title=f"Owner Video {index}",
            created_at=base_time + timedelta(minutes=index),
        )
        for index in range(3)
    ]
    foreign_video = build_video(
        user_id=other_user_id,
        title="Foreign Video",
        category="IT",
        input_type="EXTERNAL_URL",
        created_at=base_time + timedelta(minutes=10),
    )

    async with session_factory() as session:
        repository = VideoRepository(session)
        for video in [*owner_videos, foreign_video]:
            await repository.add(video)

        await session.commit()

        first_page = await repository.list_for_user(owner_id, limit=2)

        assert [video.title for video in first_page.items] == ["Owner Video 2", "Owner Video 1"]
        assert first_page.next_cursor is not None
        decoded_cursor = decode_cursor(first_page.next_cursor)
        assert decoded_cursor.id == owner_videos[1].id

        second_page = await repository.list_for_user(owner_id, limit=2, cursor=first_page.next_cursor)

        assert [video.title for video in second_page.items] == ["Owner Video 0"]
        assert second_page.next_cursor is None

        owner_visible_video = await repository.get_by_id_for_user(owner_videos[0].id, owner_id)
        foreign_video_lookup = await repository.get_by_id_for_user(owner_videos[0].id, other_user_id)

    assert owner_visible_video is not None
    assert foreign_video_lookup is None


def test_decode_cursor_rejects_invalid_token() -> None:
    with pytest.raises(CursorDecodeError):
        decode_cursor("not-a-valid-cursor")


def test_encode_decode_cursor_round_trip() -> None:
    cursor = KeysetCursor(
        created_at=datetime(2026, 3, 12, 0, 0, tzinfo=UTC),
        id=uuid4(),
    )

    encoded = encode_cursor(cursor)
    decoded = decode_cursor(encoded)

    assert decoded == cursor
