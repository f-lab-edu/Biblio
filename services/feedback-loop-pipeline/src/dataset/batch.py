from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, Protocol

from src.dataset.artifacts import DatasetArtifactRefs, DatasetArtifactWriter
from src.dataset.manifest import DatasetManifestSelector
from src.dataset.materializer import DatasetMaterializer, RawFeedbackEvent
from src.dataset.raw_log import RawFeedbackLogReader
from src.infra.storage.client import ArtifactStore
from src.utils.clock import Clock, SystemClock


#  chunk ID 목록을 받아 각 chunk의 텍스트를 반환
class ChunkTextSnapshotPort(Protocol):
    async def text_by_chunk_id(self, chunk_ids: set[str]) -> Mapping[str, str]: ...


class DatasetBatchService:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        chunk_text_snapshot: ChunkTextSnapshotPort,
        materializer: DatasetMaterializer | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._chunk_text_snapshot = chunk_text_snapshot
        self._materializer = materializer or DatasetMaterializer()
        self._clock = clock or SystemClock()

    async def materialize_latest(
        self,
        *,
        raw_feedback_log_prefix: str,
        dataset_artifact_prefix: str,
        workspace_dir: Path,
        source_window_start: datetime,
        source_window_end: datetime,
    ) -> DatasetArtifactRefs:
        created_at = self._clock.now()
        events = await RawFeedbackLogReader(self._artifact_store).read_events(
            raw_feedback_log_prefix,
            workspace_dir=workspace_dir,
        )
        dataset = self._materializer.materialize(
            events,
            chunk_text_by_id=await self._chunk_text_snapshot.text_by_chunk_id(_referenced_chunk_ids(events)),
            dataset_version=_dataset_version(created_at),
            created_at=created_at,
            source_window_start=source_window_start,
            source_window_end=source_window_end,
        )
        return await DatasetArtifactWriter(self._artifact_store).write_dataset(
            dataset,
            artifact_prefix=dataset_artifact_prefix,
            workspace_dir=workspace_dir,
        )

    @staticmethod
    def manifest_selector(artifact_store: ArtifactStore) -> DatasetManifestSelector:
        return DatasetManifestSelector(artifact_store)


def _referenced_chunk_ids(events: list[RawFeedbackEvent]) -> set[str]:
    chunk_ids: set[str] = set()
    for event in events:
        chunk_ids.update(event.topk_ids)
        chunk_ids.update(event.used_ids)
    return chunk_ids


def _dataset_version(created_at: datetime) -> str:
    return f"dataset-{created_at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"
