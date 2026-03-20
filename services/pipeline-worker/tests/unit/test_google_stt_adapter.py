import pytest

from adapters.ai.google_stt_adapter import ExternalAIAdapterError
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
