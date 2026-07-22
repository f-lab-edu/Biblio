import asyncio

import pytest

from src.core.admission_control import AdmissionController, EmbeddingWorkload


def _controller(**overrides: object) -> AdmissionController:
    values = {
        "max_concurrency": 1,
        "search_request_limit": 2,
        "video_preprocess_request_limit": 2,
        "search_wait_timeout_sec": 1.0,
        "video_preprocess_wait_timeout_sec": 1.0,
    }
    values.update(overrides)
    return AdmissionController(**values)  # type: ignore[arg-type]


async def test_search_is_granted_before_waiting_video() -> None:
    controller = _controller()
    running_video = await controller.acquire_slot(
        EmbeddingWorkload.VIDEO_PREPROCESS
    )
    waiting_video = asyncio.create_task(
        controller.acquire_slot(EmbeddingWorkload.VIDEO_PREPROCESS)
    )
    await asyncio.sleep(0)
    waiting_search = asyncio.create_task(
        controller.acquire_slot(EmbeddingWorkload.SEARCH)
    )
    await asyncio.sleep(0)

    await running_video.release()
    search_slot = await asyncio.wait_for(waiting_search, timeout=0.2)
    assert not waiting_video.done()

    await search_slot.release()
    video_slot = await asyncio.wait_for(waiting_video, timeout=0.2)
    await video_slot.release()


async def test_timed_out_waiter_does_not_leak_slot() -> None:
    controller = _controller(search_wait_timeout_sec=0.01)
    running = await controller.acquire_slot(EmbeddingWorkload.VIDEO_PREPROCESS)

    with pytest.raises(TimeoutError):
        await controller.acquire_slot(EmbeddingWorkload.SEARCH)

    snapshot = await controller.snapshot()
    assert snapshot.running_count == 1
    assert snapshot.search_queue_depth == 0

    await running.release()
    next_slot = await controller.try_acquire_legacy()
    assert next_slot is not None
    await next_slot.release()


async def test_cancelled_waiter_does_not_leak_slot() -> None:
    controller = _controller()
    running = await controller.acquire_slot(EmbeddingWorkload.VIDEO_PREPROCESS)
    waiting = asyncio.create_task(
        controller.acquire_slot(EmbeddingWorkload.SEARCH)
    )
    await asyncio.sleep(0)

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    snapshot = await controller.snapshot()
    assert snapshot.running_count == 1
    assert snapshot.search_queue_depth == 0

    await running.release()
    next_slot = await controller.try_acquire_legacy()
    assert next_slot is not None
    await next_slot.release()


async def test_request_limits_are_separate_per_workload() -> None:
    controller = _controller(
        search_request_limit=1,
        video_preprocess_request_limit=1,
    )
    video_request = await controller.try_acquire_request(
        EmbeddingWorkload.VIDEO_PREPROCESS
    )

    assert video_request is not None
    assert (
        await controller.try_acquire_request(EmbeddingWorkload.VIDEO_PREPROCESS)
        is None
    )
    search_request = await controller.try_acquire_request(EmbeddingWorkload.SEARCH)
    assert search_request is not None

    await video_request.release()
    await search_request.release()
