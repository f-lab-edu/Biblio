from __future__ import annotations

import pytest

from adapters.ai.google_stt_adapter import ExternalAIAdapterError
from tests.support import build_stt_adapter


@pytest.mark.asyncio
async def test_google_stt_adapter_returns_sorted_segments(tmp_path) -> None:
    audio = tmp_path / "audio.flac"
    audio.write_bytes(b"audio")
    adapter = build_stt_adapter()

    result = await adapter.transcribe(audio_path=str(audio), trace_id="trace-1")

    assert result.stt_model_version == "google-stt-v1"
    assert result.segments[0].start_ms == 0


@pytest.mark.asyncio
async def test_google_stt_adapter_retries_timeout(tmp_path) -> None:
    audio = tmp_path / "audio.flac"
    audio.write_bytes(b"audio")
    adapter = build_stt_adapter(fail_times=1)

    result = await adapter.transcribe(audio_path=str(audio), trace_id="trace-2")

    assert len(result.segments) == 2


@pytest.mark.asyncio
async def test_google_stt_adapter_requires_existing_file(tmp_path) -> None:
    adapter = build_stt_adapter()

    with pytest.raises(ExternalAIAdapterError):
        await adapter.transcribe(audio_path=str(tmp_path / "missing.flac"), trace_id="trace-3")
