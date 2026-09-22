from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from infrastructure import LoadTestError
from video_pipeline.models import CompleteRequestRecord
from video_pipeline.observability import WorkerLogDatasets
from video_pipeline.timeline import StageInterval, stage_intervals


def build_schema_v2_pipeline_timings(
    datasets: WorkerLogDatasets,
    requests: tuple[CompleteRequestRecord, ...],
) -> tuple[dict[str, Any], ...]:
    completed_at, completed_batch = _video_completion_maps(datasets.events)
    batch_completed_at, batch_participants = _batch_completion_maps(datasets.events)
    intervals = stage_intervals(datasets.stage_events)
    intervals_by_video_stage = _video_stage_intervals(intervals)
    embedding_by_video = _embedding_intervals_by_video(
        intervals,
        batch_participants,
    )
    missing = sorted(
        request.video_id
        for request in requests
        if request.video_id not in completed_at
    )
    if missing:
        raise LoadTestError(
            "Schema v2 timing is missing pipeline.video.completed for: "
            + ", ".join(missing)
        )
    return tuple(
        _video_timing(
            request,
            completed_at=completed_at,
            completed_batch=completed_batch,
            batch_completed_at=batch_completed_at,
            intervals_by_video_stage=intervals_by_video_stage,
            embedding_by_video=embedding_by_video,
        )
        for request in requests
    )


def _video_completion_maps(
    events: tuple[dict[str, Any], ...],
) -> tuple[dict[str, datetime], dict[str, str]]:
    completed_at: dict[str, datetime] = {}
    completed_batch: dict[str, str] = {}
    for event in events:
        if event.get("event_type") != "pipeline.video.completed":
            continue
        video_id = str(event.get("video_id", ""))
        completed_at[video_id] = _timestamp(event)
        completed_batch[video_id] = str(event.get("batch_id", ""))
    return completed_at, completed_batch


def _batch_completion_maps(
    events: tuple[dict[str, Any], ...],
) -> tuple[dict[str, datetime], dict[str, tuple[str, ...]]]:
    completed_at: dict[str, datetime] = {}
    participants: dict[str, tuple[str, ...]] = {}
    for event in events:
        if event.get("event_type") != "embedding.batch.completed":
            continue
        batch_id = str(event.get("batch_id", ""))
        completed_at[batch_id] = _timestamp(event)
        participant_video_ids = event.get("participant_video_ids", [])
        participants[batch_id] = tuple(
            str(video_id)
            for video_id in participant_video_ids
            if video_id not in {None, "", "-"}
        )
    return completed_at, participants


def _video_stage_intervals(
    intervals: tuple[StageInterval, ...],
) -> dict[tuple[str, str], list[StageInterval]]:
    grouped: dict[tuple[str, str], list[StageInterval]] = defaultdict(list)
    for interval in intervals:
        if interval.stage != "embedding":
            grouped[(interval.video_id, interval.stage)].append(interval)
    return grouped


def _embedding_intervals_by_video(
    intervals: tuple[StageInterval, ...],
    batch_participants: dict[str, tuple[str, ...]],
) -> dict[str, list[StageInterval]]:
    grouped: dict[str, list[StageInterval]] = defaultdict(list)
    for interval in intervals:
        if interval.stage != "embedding":
            continue
        for video_id in batch_participants.get(interval.work_id, ()):
            grouped[video_id].append(interval)
    return grouped


def _video_timing(
    request: CompleteRequestRecord,
    *,
    completed_at: dict[str, datetime],
    completed_batch: dict[str, str],
    batch_completed_at: dict[str, datetime],
    intervals_by_video_stage: dict[tuple[str, str], list[StageInterval]],
    embedding_by_video: dict[str, list[StageInterval]],
) -> dict[str, Any]:
    video_id = request.video_id
    normalization = intervals_by_video_stage[(video_id, "normalization")]
    transcription = intervals_by_video_stage[(video_id, "transcription")]
    assembly = intervals_by_video_stage[(video_id, "assembly")]
    enrichment = intervals_by_video_stage[(video_id, "enrichment")]
    embedding = embedding_by_video[video_id]
    normalization_ms = _span_ms(normalization)
    transcription_ms = _span_ms(transcription)
    chunk_enrichment_ms = _span_ms([*assembly, *enrichment])
    embedding_span_ms = _span_ms(embedding)
    video_completed_at = completed_at[video_id]
    persist_ms = _persist_ms(
        video_completed_at,
        completed_batch.get(video_id, ""),
        batch_completed_at,
    )
    return {
        "timestamp_utc": video_completed_at.isoformat(),
        "trace_id": request.trace_id,
        "video_id": video_id,
        "status": "success",
        "download_ms": 0.0,
        "audio_ms": normalization_ms,
        "stt_ms": transcription_ms,
        "chunk_enrichment_ms": chunk_enrichment_ms,
        "embedding_ms": embedding_span_ms,
        "persist_ms": persist_ms,
        "total_ms": _duration_ms(request.started_at, video_completed_at),
        "normalization_ms": normalization_ms,
        "transcription_ms": transcription_ms,
        "assembly_ms": _sum_ms(assembly),
        "enrichment_ms": _span_ms(enrichment),
        "embedding_execution_ms": _sum_ms(embedding),
        "embedding_span_ms": embedding_span_ms,
        "timing_source": "schema-v2-lifecycle",
        "stages_overlap": True,
    }


def _persist_ms(
    video_completed_at: datetime,
    completed_batch_id: str,
    batch_completed_at: dict[str, datetime],
) -> float:
    embedding_completed_at = batch_completed_at.get(completed_batch_id)
    if embedding_completed_at is None:
        return 0.0
    return _duration_ms(embedding_completed_at, video_completed_at)


def _span_ms(intervals: list[StageInterval]) -> float:
    if not intervals:
        return 0.0
    return _duration_ms(
        min(interval.started_at for interval in intervals),
        max(interval.finished_at for interval in intervals),
    )


def _sum_ms(intervals: list[StageInterval]) -> float:
    return round(
        sum(
            _duration_ms(interval.started_at, interval.finished_at)
            for interval in intervals
        ),
        3,
    )


def _duration_ms(started_at: datetime, finished_at: datetime) -> float:
    return round((finished_at - started_at).total_seconds() * 1000, 3)


def _timestamp(event: dict[str, Any]) -> datetime:
    value = event.get("timestamp_utc")
    if not isinstance(value, str):
        raise LoadTestError(f"Event is missing timestamp_utc: {event!r}")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
