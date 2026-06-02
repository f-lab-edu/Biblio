"""Snapshot index restore 검증 adapter.

rollback은 pointer 모델이다. index 본체를 물리 복원하지 않는다. snapshot index는
자동 삭제되지 않으므로 평소 살아있다. 이 adapter는 snapshot index가 실제로 다시
서빙 가능한 상태인지 검증한다:

1. `vector_index_catalog`에 row가 살아있다(`deleted_at IS NULL`).
2. `vector_index_entry`에 해당 index_name 벡터 row가 1개 이상 존재한다.

둘 다 참일 때만 restore 성공으로 본다. `RollbackTransitionManager`의
`SnapshotIndexRestorePort`를 만족한다.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.models import VectorIndexCatalogModel, VectorIndexEntryModel


class CatalogSnapshotIndexRestore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def restore_snapshot(self, *, index_name: str) -> bool:
        catalog = await self._session.get(VectorIndexCatalogModel, index_name)
        if catalog is None or catalog.deleted_at is not None:
            return False
        entry_chunk_id = await self._session.scalar(
            select(VectorIndexEntryModel.chunk_id)
            .where(VectorIndexEntryModel.index_name == index_name)
            .limit(1)
        )
        return entry_chunk_id is not None
