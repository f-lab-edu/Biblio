from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.dataset.materializer import RawFeedbackEvent
from src.infra.storage.client import ArtifactStore

DOWNLOAD_BATCH_SIZE = 10


class RawFeedbackLogReader:
    def __init__(self, artifact_store: ArtifactStore, batch_size: int = DOWNLOAD_BATCH_SIZE) -> None:
        self._artifact_store = artifact_store
        self._batch_size = batch_size

    async def read_events(self, raw_log_prefix: str, *, workspace_dir: Path) -> list[RawFeedbackEvent]:
        storage_paths = sorted(await self._artifact_store.list_objects(raw_log_prefix))
        log_dir = workspace_dir / "raw_feedback_logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        events: list[RawFeedbackEvent] = []
        for chunk_start in range(0, len(storage_paths), self._batch_size):
            chunk = list(enumerate(storage_paths[chunk_start : chunk_start + self._batch_size], start=chunk_start))
            batch = await asyncio.gather(*[
                self._download_one(index, path, log_dir)
                for index, path in chunk
            ])
            for batch_events in batch:
                events.extend(batch_events)
        return events

    async def _download_one(self, index: int, storage_path: str, log_dir: Path) -> list[RawFeedbackEvent]:
        destination = log_dir / f"{index}.jsonl"
        await self._artifact_store.download_object(storage_path, destination)
        return self._read_jsonl(destination)

    @staticmethod
    def _read_jsonl(path: Path) -> list[RawFeedbackEvent]:
        events: list[RawFeedbackEvent] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                events.append(RawFeedbackEvent.from_mapping(json.loads(line)))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid raw feedback log line {path}:{line_number}") from exc
        return events
