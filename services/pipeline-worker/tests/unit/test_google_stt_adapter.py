import asyncio

import pytest

from src.infra.ai.google_stt_adapter import ExternalAIAdapterError, GoogleSTTAdapter
from tests.support import build_stt_adapter


@pytest.mark.asyncio
async def test_google_stt_adapter_accepts_audio_uri() -> None:
    adapter = build_stt_adapter()

    result = await adapter.transcribe(audio_uri="gs://bucket/audio.flac", trace_id="trace-1")

    assert result.stt_model_version == "chirp_2"
    assert result.segments[0].start_ms == 0


@pytest.mark.asyncio
async def test_google_stt_adapter_retries_submit_failures_only() -> None:
    adapter = build_stt_adapter(fail_submit_times=1)

    result = await adapter.transcribe(audio_uri="gs://bucket/audio.flac", trace_id="trace-2")

    assert len(result.segments) == 2


@pytest.mark.asyncio
async def test_google_stt_adapter_rejects_non_gs_uri() -> None:
    adapter = build_stt_adapter()

    with pytest.raises(ExternalAIAdapterError):
        await adapter.transcribe(audio_uri="/local/path/audio.flac", trace_id="trace-3")


@pytest.mark.asyncio
async def test_google_stt_adapter_does_not_cap_long_running_batch_operation() -> None:
    async def slow_client(audio_uri: str, trace_id: str) -> dict:
        await asyncio.sleep(0.05)
        return {
            "segments": [{"text": "slow transcript", "start_ms": 0, "end_ms": 100}],
            "stt_model_version": "chirp_2",
        }

    adapter = GoogleSTTAdapter(client=slow_client, max_retries=0)

    result = await adapter.transcribe(audio_uri="gs://bucket/audio.flac", trace_id="trace-4")

    assert result.segments[0].text == "slow transcript"


@pytest.mark.asyncio
async def test_google_stt_adapter_uses_exponential_backoff_with_jitter() -> None:
    attempts = 0
    delays: list[float] = []

    async def retrying_client(audio_uri: str, trace_id: str) -> dict:
        nonlocal attempts
        del audio_uri, trace_id
        attempts += 1
        if attempts <= 3:
            raise ExternalAIAdapterError(
                code="UNAVAILABLE",
                message="temporary",
                trace_id="trace-backoff",
                provider="google-stt",
                retryable=True,
            )
        return {
            "segments": [{"text": "done", "start_ms": 0, "end_ms": 1}],
            "stt_model_version": "chirp_3",
        }

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    adapter = GoogleSTTAdapter(
        client=retrying_client,
        max_retries=3,
        sleep=record_sleep,
        jitter=lambda: 1.0,
    )

    await adapter.transcribe(audio_uri="gs://bucket/audio.flac", trace_id="trace-backoff")

    assert attempts == 4
    assert delays == pytest.approx([1.25, 2.5, 5.0])


@pytest.mark.asyncio
async def test_google_stt_adapter_does_not_retry_non_retryable_error() -> None:
    attempts = 0
    delays: list[float] = []

    async def invalid_client(audio_uri: str, trace_id: str) -> dict:
        nonlocal attempts
        del audio_uri, trace_id
        attempts += 1
        raise ExternalAIAdapterError(
            code="INVALID_REQUEST",
            message="invalid",
            trace_id="trace-invalid",
            provider="google-stt",
            retryable=False,
        )

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    adapter = GoogleSTTAdapter(
        client=invalid_client,
        max_retries=3,
        sleep=record_sleep,
        jitter=lambda: 0.0,
    )

    with pytest.raises(ExternalAIAdapterError, match="invalid"):
        await adapter.transcribe(
            audio_uri="gs://bucket/audio.flac",
            trace_id="trace-invalid",
        )

    assert attempts == 1
    assert delays == []


@pytest.mark.asyncio
async def test_google_stt_adapter_preserves_explicit_empty_words() -> None:
    async def silent_client(audio_uri: str, trace_id: str) -> dict:
        del audio_uri, trace_id
        return {
            "segments": [],
            "words": [],
            "stt_model_version": "chirp_3",
        }

    result = await GoogleSTTAdapter(
        client=silent_client,
        max_retries=0,
    ).transcribe(
        audio_uri="gs://bucket/silence.flac",
        trace_id="trace-silence",
    )

    assert result.words == []
    assert result.segments == []
