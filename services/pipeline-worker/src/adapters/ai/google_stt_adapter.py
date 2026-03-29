import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(slots=True)
class TranscriptSegmentDTO:
    text: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class STTTranscriptionResult:
    segments: list[TranscriptSegmentDTO]
    stt_model_version: str


@dataclass(slots=True)
class ExternalAIAdapterError(Exception):
    code: str
    message: str
    trace_id: str
    provider: str
    retryable: bool

    def __str__(self) -> str:
        return f"{self.provider}:{self.code}:{self.message}"


STTCallable = Callable[[str, str], Awaitable[dict[str, Any] | STTTranscriptionResult]]


class GoogleSTTAdapter:
    def __init__(
        self,
        client: STTCallable,
        *,
        max_retries: int,
    ) -> None:
        self._client = client
        self._max_retries = max_retries

    async def transcribe(self, *, audio_uri: str, trace_id: str) -> STTTranscriptionResult:
        if not audio_uri.startswith("gs://"):
            raise ExternalAIAdapterError(
                code="INVALID_REQUEST",
                message=f"audio_uri must be a gs:// URI, got: {audio_uri}",
                trace_id=trace_id,
                provider="google-stt",
                retryable=False,
            )

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client(audio_uri, trace_id)
                return self._normalize(response, trace_id)
            except (asyncio.TimeoutError, TimeoutError):
                last_error = ExternalAIAdapterError(
                    code="TIMEOUT",
                    message="STT request timed out",
                    trace_id=trace_id,
                    provider="google-stt",
                    retryable=True,
                )
            except ExternalAIAdapterError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
            if attempt >= self._max_retries:
                assert last_error is not None
                raise last_error
            await asyncio.sleep(0)

        assert last_error is not None
        raise last_error

    def _normalize(self, response: dict[str, Any] | STTTranscriptionResult, trace_id: str) -> STTTranscriptionResult:
        if isinstance(response, STTTranscriptionResult):
            return response

        model_version = response.get("stt_model_version")
        segments = response.get("segments", [])
        if not model_version:
            raise ExternalAIAdapterError(
                code="INTERNAL_ERROR",
                message="STT model version missing",
                trace_id=trace_id,
                provider="google-stt",
                retryable=False,
            )
        normalized_segments = [
            TranscriptSegmentDTO(
                text=segment["text"],
                start_ms=int(segment["start_ms"]),
                end_ms=int(segment["end_ms"]),
            )
            for segment in sorted(segments, key=lambda item: int(item["start_ms"]))
        ]
        return STTTranscriptionResult(segments=normalized_segments, stt_model_version=str(model_version))
