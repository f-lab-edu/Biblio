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
