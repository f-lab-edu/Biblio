from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from infrastructure import LoadTestError
from video_pipeline.models import (
    CompleteRequestRecord,
    DispatchPhase,
    FixtureKind,
    FixtureSpec,
    PreparedVideo,
    ScenarioPlan,
    TerminalStatusRecord,
)


TERMINAL_VIDEO_STATUSES = frozenset({"READY", "FAILED"})


@dataclass
class ScenarioProgress:
    requests: list[CompleteRequestRecord] = field(default_factory=list)
    terminal_statuses: list[TerminalStatusRecord] = field(default_factory=list)


class VideoSessionClient(Protocol):
    def create_local_video(
        self,
        *,
        project_id: str,
        title: str,
        category: str,
        extension: str,
    ) -> dict[str, Any]: ...

    def upload_local_video(
        self,
        create_response: Mapping[str, Any],
        payload: bytes,
    ) -> None: ...

    def complete_video(
        self,
        video_id: str,
        size_bytes: int,
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]: ...

    def get_video(self, video_id: str) -> dict[str, Any]: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_trace_id() -> str:
    return str(uuid4())


def complete_prepared_video(
    client: VideoSessionClient,
    video: PreparedVideo,
    *,
    trace_id: str,
    now: Callable[[], datetime] = utc_now,
    record_sink: Callable[[CompleteRequestRecord], None] | None = None,
) -> CompleteRequestRecord:
    started_at = now()
    try:
        response = client.complete_video(
            video.video_id,
            video.size_bytes,
            trace_id=trace_id,
        )
    except Exception as error:
        record = CompleteRequestRecord(
            video_id=video.video_id,
            fixture=video.fixture,
            trace_id=trace_id,
            started_at=started_at,
            responded_at=now(),
            response_status="ERROR",
            error=str(error),
        )
        if record_sink is not None:
            record_sink(record)
        raise
    responded_at = now()
    response_status = _required_status(response, video.video_id)
    record = CompleteRequestRecord(
        video_id=video.video_id,
        fixture=video.fixture,
        trace_id=trace_id,
        started_at=started_at,
        responded_at=responded_at,
        response_status=response_status,
    )
    if record_sink is not None:
        record_sink(record)
    return record


def dispatch_complete_batch(
    client: VideoSessionClient,
    videos: Sequence[PreparedVideo],
    *,
    concurrency: int,
    trace_id_factory: Callable[[], str] = new_trace_id,
    now: Callable[[], datetime] = utc_now,
    record_sink: Callable[[CompleteRequestRecord], None] | None = None,
) -> tuple[CompleteRequestRecord, ...]:
    if concurrency <= 0:
        raise LoadTestError("concurrency must be greater than zero.")
    if not videos:
        return ()

    worker_count = min(concurrency, len(videos))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = tuple(
            executor.submit(
                complete_prepared_video,
                client,
                video,
                trace_id=trace_id_factory(),
                now=now,
                record_sink=record_sink,
            )
            for video in videos
        )
        return tuple(future.result() for future in futures)


def execute_scenario(
    client: VideoSessionClient,
    plan: ScenarioPlan,
    fixtures: dict[FixtureKind, FixtureSpec],
    *,
    project_id: str,
    run_label: str,
    terminal_timeout_seconds: float,
    poll_interval_seconds: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    progress: ScenarioProgress | None = None,
) -> tuple[tuple[CompleteRequestRecord, ...], tuple[TerminalStatusRecord, ...]]:
    current_progress = progress or ScenarioProgress()
    for repeat_index in range(plan.repeat_count):
        prepared_phases = tuple(
            _prepare_phase(
                client,
                phase,
                fixtures[phase.fixture],
                project_id=project_id,
                title_prefix=f"{run_label}-r{repeat_index + 1}-p{phase_index + 1}",
            )
            for phase_index, phase in enumerate(plan.phases)
        )
        pending_videos: list[PreparedVideo] = []
        for phase, videos in zip(plan.phases, prepared_phases, strict=True):
            if phase.wait_for_previous_terminal and pending_videos:
                current_progress.terminal_statuses.extend(
                    _observe_terminal_batch(
                        client,
                        pending_videos,
                        timeout_seconds=terminal_timeout_seconds,
                        poll_interval_seconds=poll_interval_seconds,
                    )
                )
                pending_videos.clear()
            if phase.delay_before_seconds > 0:
                sleep(phase.delay_before_seconds)
            dispatch_complete_batch(
                client,
                videos,
                concurrency=phase.concurrency,
                record_sink=current_progress.requests.append,
            )
            pending_videos.extend(videos)
        current_progress.terminal_statuses.extend(
            _observe_terminal_batch(
                client,
                pending_videos,
                timeout_seconds=terminal_timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
        )
    return tuple(current_progress.requests), tuple(current_progress.terminal_statuses)


def _prepare_phase(
    client: VideoSessionClient,
    phase: DispatchPhase,
    fixture: FixtureSpec,
    *,
    project_id: str,
    title_prefix: str,
) -> tuple[PreparedVideo, ...]:
    prepared: list[PreparedVideo] = []
    for request_index in range(phase.request_count):
        created = client.create_local_video(
            project_id=project_id,
            title=f"{title_prefix}-v{request_index + 1}",
            category="GENERAL",
            extension=fixture.path.suffix,
        )
        payload = fixture.path.read_bytes()
        client.upload_local_video(created, payload)
        prepared.append(
            PreparedVideo(
                video_id=str(created["video_id"]),
                fixture=fixture.kind,
                size_bytes=fixture.size_bytes,
            )
        )
    return tuple(prepared)


def _observe_terminal_batch(
    client: VideoSessionClient,
    videos: Sequence[PreparedVideo],
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[TerminalStatusRecord, ...]:
    if not videos:
        return ()
    with ThreadPoolExecutor(max_workers=len(videos)) as executor:
        futures = tuple(
            executor.submit(
                wait_for_terminal_status,
                client,
                video.video_id,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            for video in videos
        )
        return tuple(future.result() for future in futures)


def wait_for_terminal_status(
    client: VideoSessionClient,
    video_id: str,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = 5.0,
    now: Callable[[], datetime] = utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> TerminalStatusRecord:
    _validate_observation_timing(timeout_seconds, poll_interval_seconds)
    deadline = monotonic() + timeout_seconds
    while True:
        response = client.get_video(video_id)
        observed_at = now()
        status = _required_status(response, video_id)
        if status in TERMINAL_VIDEO_STATUSES:
            return TerminalStatusRecord(
                video_id=video_id,
                status=status,
                observed_at=observed_at,
            )

        remaining_seconds = deadline - monotonic()
        if remaining_seconds <= 0:
            raise LoadTestError(
                f"Video {video_id} did not reach a terminal status within "
                f"{timeout_seconds} seconds; last status was {status}."
            )
        sleep(min(poll_interval_seconds, remaining_seconds))


def _required_status(response: dict[str, Any], video_id: str) -> str:
    status = response.get("status")
    if not isinstance(status, str) or not status:
        raise LoadTestError(
            f"Video {video_id} response has an invalid status: {response!r}"
        )
    return status


def _validate_observation_timing(
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> None:
    if timeout_seconds <= 0:
        raise LoadTestError("timeout_seconds must be greater than zero.")
    if poll_interval_seconds <= 0:
        raise LoadTestError("poll_interval_seconds must be greater than zero.")
