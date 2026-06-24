from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.dataset.raw_log import DOWNLOAD_BATCH_SIZE, RawFeedbackLogReader


def _make_event_line(event_id: str) -> str:
    return json.dumps({
        "event_id": event_id,
        "trace_id": "trace-1",
        "req_id": "req-1",
        "user_id": "user-1",
        "project_id": "proj-1",
        "query_text": "검색어",
        "rating": "like",
        "topk_ids": ["chunk-a", "chunk-b"],
        "used_ids": ["chunk-a"],
        "active_model_version": "model-v1",
        "active_index_name": "index-v1",
        "response_snapshot_ref": "gs://bucket/snap/1.json",
        "created_at": datetime(2026, 5, 1, tzinfo=UTC).isoformat(),
    })


class _FakeArtifactStore:
    def __init__(self, files: dict[str, str]) -> None:
        self._files = files
        self.downloaded: list[str] = []

    async def list_objects(self, prefix: str):
        return [path for path in self._files if path.startswith(prefix)]

    async def download_object(self, storage_path: str, destination: Path) -> None:
        self.downloaded.append(storage_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self._files[storage_path], encoding="utf-8")

    async def upload_object(self, source: Path, storage_path: str) -> None:
        pass

    def object_uri(self, storage_path: str) -> str:
        return f"gs://bucket/{storage_path}"


class TestRawFeedbackLogReaderSuccess:
    async def test_reads_single_file(self, tmp_path: Path) -> None:
        store = _FakeArtifactStore({
            "logs/2026-05-01.jsonl": _make_event_line("evt-1"),
        })
        events = await RawFeedbackLogReader(store).read_events("logs/", workspace_dir=tmp_path)

        assert len(events) == 1
        assert events[0].event_id == "evt-1"

    async def test_reads_multiple_files_and_merges_events(self, tmp_path: Path) -> None:
        store = _FakeArtifactStore({
            "logs/2026-05-01.jsonl": _make_event_line("evt-1"),
            "logs/2026-05-02.jsonl": _make_event_line("evt-2"),
            "logs/2026-05-03.jsonl": _make_event_line("evt-3"),
        })
        events = await RawFeedbackLogReader(store).read_events("logs/", workspace_dir=tmp_path)

        assert len(events) == 3
        assert {e.event_id for e in events} == {"evt-1", "evt-2", "evt-3"}

    async def test_downloads_in_parallel_chunks(self, tmp_path: Path) -> None:
        files = {f"logs/{i:02d}.jsonl": _make_event_line(f"evt-{i}") for i in range(25)}
        store = _FakeArtifactStore(files)

        events = await RawFeedbackLogReader(store, batch_size=10).read_events("logs/", workspace_dir=tmp_path)

        assert len(events) == 25
        assert len(store.downloaded) == 25

    async def test_skips_empty_lines(self, tmp_path: Path) -> None:
        content = _make_event_line("evt-1") + "\n\n" + _make_event_line("evt-2") + "\n"
        store = _FakeArtifactStore({"logs/file.jsonl": content})

        events = await RawFeedbackLogReader(store).read_events("logs/", workspace_dir=tmp_path)

        assert len(events) == 2

    async def test_returns_empty_when_no_files(self, tmp_path: Path) -> None:
        store = _FakeArtifactStore({})
        events = await RawFeedbackLogReader(store).read_events("logs/", workspace_dir=tmp_path)

        assert events == []

    async def test_reads_json_array_file(self, tmp_path: Path) -> None:
        # FIP(Vector)는 배치를 JSON 배열로 쓴다.
        content = "[" + _make_event_line("evt-1") + "," + _make_event_line("evt-2") + "]"
        store = _FakeArtifactStore({"logs/batch.jsonl": content})

        events = await RawFeedbackLogReader(store).read_events("logs/", workspace_dir=tmp_path)

        assert {e.event_id for e in events} == {"evt-1", "evt-2"}

    async def test_reads_vector_enriched_array_with_extra_fields(self, tmp_path: Path) -> None:
        # Vector가 이벤트 필드를 최상위에 merge하고 부가 필드를 덧붙인 형태.
        event = json.loads(_make_event_line("evt-1"))
        event["component"] = "feedback-ingestion-pipeline"
        event["message"] = "{...}"
        content = json.dumps([event])
        store = _FakeArtifactStore({"logs/enriched.jsonl": content})

        events = await RawFeedbackLogReader(store).read_events("logs/", workspace_dir=tmp_path)

        assert len(events) == 1
        assert events[0].event_id == "evt-1"


class TestRawFeedbackLogReaderValidation:
    async def test_raises_on_invalid_json_line(self, tmp_path: Path) -> None:
        store = _FakeArtifactStore({"logs/bad.jsonl": "not json"})

        with pytest.raises(ValueError, match="invalid raw feedback log line"):
            await RawFeedbackLogReader(store).read_events("logs/", workspace_dir=tmp_path)

    async def test_default_chunk_size_is_10(self) -> None:
        assert DOWNLOAD_BATCH_SIZE == 10
