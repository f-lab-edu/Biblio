import asyncio
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from loguru import logger


MAX_WORDS_PER_SEGMENT = 100
SENTENCE_ENDING_MARKS = (".", "!", "?")


@dataclass(slots=True)
class TranscriptSegmentDTO:
    text: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class TranscriptWordDTO:
    text: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class STTTranscriptionResult:
    segments: list[TranscriptSegmentDTO]
    stt_model_version: str
    words: list[TranscriptWordDTO] | None = None


@dataclass(slots=True)
class ExternalAIAdapterError(Exception):
    code: str
    message: str
    trace_id: str
    provider: str
    retryable: bool
    attempt_count: int = 1

    def __str__(self) -> str:
        return f"{self.provider}:{self.code}:{self.message}"


STTCallable = Callable[[str, str], Awaitable[dict[str, Any] | STTTranscriptionResult]]
SleepCallable = Callable[[float], Awaitable[None]]
JitterCallable = Callable[[], float]


def segments_from_words(words: list[TranscriptWordDTO]) -> list[TranscriptSegmentDTO]:
    segments: list[TranscriptSegmentDTO] = []
    buffered_words: list[TranscriptWordDTO] = []
    for word in words:
        if not word.text:
            continue
        buffered_words.append(word)
        if word.text.endswith(SENTENCE_ENDING_MARKS) or len(buffered_words) >= MAX_WORDS_PER_SEGMENT:
            segments.append(_segment_from_words(buffered_words))
            buffered_words = []
    if buffered_words:
        segments.append(_segment_from_words(buffered_words))
    return segments


def _segment_from_words(words: list[TranscriptWordDTO]) -> TranscriptSegmentDTO:
    return TranscriptSegmentDTO(
        text=" ".join(word.text for word in words),
        start_ms=words[0].start_ms,
        end_ms=words[-1].end_ms,
    )


class GoogleSTTAdapter:
    def __init__(
        self,
        client: STTCallable,
        *,
        max_retries: int,
        sleep: SleepCallable = asyncio.sleep,
        jitter: JitterCallable = random.random,
    ) -> None:
        self._client = client
        self._max_retries = max_retries
        self._sleep = sleep
        self._jitter = jitter

    async def transcribe(self, *, audio_uri: str, trace_id: str) -> STTTranscriptionResult:
        if not audio_uri.startswith("gs://"):
            raise ExternalAIAdapterError(
                code="INVALID_REQUEST",
                message=f"audio_uri must be a gs:// URI, got: {audio_uri}",
                trace_id=trace_id,
                provider="google-stt",
                retryable=False,
            )

        last_error: ExternalAIAdapterError | None = None
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
            assert last_error is not None
            last_error.attempt_count = attempt + 1
            if attempt >= self._max_retries:
                raise last_error
            delay_seconds = (2**attempt) * (1 + (self._jitter() * 0.25))
            logger.bind(trace_id=trace_id).warning(
                "STT retry uri={} attempt={} code={} delay_seconds={:.3f}",
                audio_uri,
                attempt + 1,
                last_error.code,
                delay_seconds,
            )
            await self._sleep(delay_seconds)

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
        raw_words = response.get("words") if "words" in response else None
        normalized_words = [
            TranscriptWordDTO(
                text=str(word["text"]),
                start_ms=int(word["start_ms"]),
                end_ms=int(word["end_ms"]),
            )
            for word in sorted(raw_words or [], key=lambda item: int(item["start_ms"]))
        ]
        normalized_segments = [
            TranscriptSegmentDTO(
                text=segment["text"],
                start_ms=int(segment["start_ms"]),
                end_ms=int(segment["end_ms"]),
            )
            for segment in sorted(segments, key=lambda item: int(item["start_ms"]))
        ]
        return STTTranscriptionResult(
            segments=normalized_segments,
            stt_model_version=str(model_version),
            words=normalized_words if raw_words is not None else None,
        )
