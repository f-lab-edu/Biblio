"""Factory for a production STTCallable using Google Cloud Speech-to-Text v2 BatchRecognize."""

import asyncio
from typing import Any

from loguru import logger

from src.infra.ai.google_stt_adapter import ExternalAIAdapterError, STTCallable
from google.api_core.client_options import ClientOptions

_MAX_WORDS_PER_SEGMENT = 100
_SENTENCE_ENDING_MARKS = (".", "!", "?")

# google stt: 초단위 -> biblio: 밀리초 단위 변환
def _duration_to_ms(duration: Any, trace_id: str) -> int:
    try:
        return int(duration.total_seconds() * 1000)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _stt_parse_error("STT word time offsets missing", trace_id) from exc


def _word_text(word: Any) -> str:
    return str(getattr(word, "word", "")).strip()


def _is_sentence_end(text: str) -> bool:
    return text.endswith(_SENTENCE_ENDING_MARKS)


def _stt_parse_error(message: str, trace_id: str) -> ExternalAIAdapterError:
    return ExternalAIAdapterError(
        code="INTERNAL_ERROR",
        message=message,
        trace_id=trace_id,
        provider="google-stt",
        retryable=False,
    )


def _build_segment(words: list[Any], trace_id: str) -> dict:
    text = " ".join(_word_text(word) for word in words).strip()
    if not text:
        raise _stt_parse_error("STT word text missing", trace_id)
    try:
        start_ms = _duration_to_ms(words[0].start_offset, trace_id)
        end_ms = _duration_to_ms(words[-1].end_offset, trace_id)
    except AttributeError as exc:
        raise _stt_parse_error("STT word time offsets missing", trace_id) from exc
    if end_ms < start_ms:
        raise _stt_parse_error("STT word time offsets are invalid", trace_id)
    return {"text": text, "start_ms": start_ms, "end_ms": end_ms}


def _segments_from_words(words: list[Any], trace_id: str) -> list[dict]:
    segments: list[dict] = []
    buffer: list[Any] = []
    for word in words:
        text = _word_text(word)
        if not text:
            continue
        buffer.append(word)
        if _is_sentence_end(text) or len(buffer) >= _MAX_WORDS_PER_SEGMENT:
            segments.append(_build_segment(buffer, trace_id))
            buffer = []
    if buffer:
        segments.append(_build_segment(buffer, trace_id))
    if words and not segments:
        raise _stt_parse_error("STT word text missing", trace_id)
    return segments


def _parse_batch_recognize_response(response: Any, stt_model_version: str, trace_id: str = "") -> dict:
    segments: list[dict] = []
    for _uri, file_result in response.results.items():
        transcript = getattr(getattr(file_result, "inline_result", None), "transcript", None)
        if transcript is None:
            transcript = getattr(file_result, "transcript", None)
        if transcript is None:
            continue
        for result in transcript.results:
            if not result.alternatives:
                continue
            alt = result.alternatives[0]
            text = alt.transcript.strip()
            words = list(getattr(alt, "words", []) or [])
            if text and not words:
                raise _stt_parse_error("STT word time offsets missing", trace_id)
            segments.extend(_segments_from_words(words, trace_id))
    return {"segments": segments, "stt_model_version": stt_model_version}


def build_stt_callable(
    *,
    project_id: str,
    location: str,
    recognizer: str,
    model: str,
    submit_timeout_sec: int,
    operation_timeout_sec: int,
) -> STTCallable:
    """Return an async callable(audio_uri, trace_id) -> dict compatible with STTCallable.

    Uses google.cloud.speech_v2 BatchRecognize with inline response.
    The actual SDK call is blocking, so it runs in asyncio.to_thread.
    """
    from google.api_core import exceptions as google_exceptions
    from google.cloud.speech_v2 import SpeechClient
    from google.cloud.speech_v2.types import cloud_speech

    recognizer_name = recognizer or f"projects/{project_id}/locations/{location}/recognizers/_"

    client = SpeechClient(client_options=ClientOptions(
        api_endpoint=f"{location}-speech.googleapis.com"
    ))

    def _map_google_error(exc: Exception, *, trace_id: str, stage: str) -> ExternalAIAdapterError:
        if isinstance(exc, google_exceptions.DeadlineExceeded):
            return ExternalAIAdapterError(
                code="TIMEOUT",
                message=f"STT {stage} timed out",
                trace_id=trace_id,
                provider="google-stt",
                retryable=True,
            )
        if isinstance(exc, google_exceptions.ServiceUnavailable):
            return ExternalAIAdapterError(
                code="UNAVAILABLE",
                message=f"STT {stage} unavailable",
                trace_id=trace_id,
                provider="google-stt",
                retryable=True,
            )
        if isinstance(exc, google_exceptions.ResourceExhausted):
            return ExternalAIAdapterError(
                code="RATE_LIMITED",
                message=f"STT {stage} rate limited",
                trace_id=trace_id,
                provider="google-stt",
                retryable=True,
            )
        if isinstance(exc, google_exceptions.InvalidArgument):
            return ExternalAIAdapterError(
                code="INVALID_REQUEST",
                message=f"STT {stage} invalid request",
                trace_id=trace_id,
                provider="google-stt",
                retryable=False,
            )
        return ExternalAIAdapterError(
            code="INTERNAL_ERROR",
            message=f"STT {stage} failed: {exc}",
            trace_id=trace_id,
            provider="google-stt",
            retryable=False,
        )

    def _sync_batch_recognize(audio_uri: str, trace_id: str) -> dict:
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=["ko-KR"],
            model=model,
            features=cloud_speech.RecognitionFeatures(
                enable_word_time_offsets=True,
            ),
        )
        file_metadata = cloud_speech.BatchRecognizeFileMetadata(uri=audio_uri)
        request = cloud_speech.BatchRecognizeRequest(
            recognizer=recognizer_name,
            config=config,
            files=[file_metadata],
            recognition_output_config=cloud_speech.RecognitionOutputConfig(
                inline_response_config=cloud_speech.InlineOutputConfig(),
            ),
        )
        try:
            operation = client.batch_recognize(request=request, timeout=submit_timeout_sec)
        except Exception as exc:
            raise _map_google_error(exc, trace_id=trace_id, stage="submit") from exc

        try:
            return operation.result(timeout=operation_timeout_sec)
        except Exception as exc:
            raise _map_google_error(exc, trace_id=trace_id, stage="operation") from exc

    async def stt_callable(audio_uri: str, trace_id: str) -> dict:
        logger.bind(trace_id=trace_id).info("STT BatchRecognize start uri={}", audio_uri)
        response = await asyncio.to_thread(_sync_batch_recognize, audio_uri, trace_id)
        result = _parse_batch_recognize_response(response, model, trace_id=trace_id)
        logger.bind(trace_id=trace_id).info("STT BatchRecognize done segments={}", len(result["segments"]))
        return result

    return stt_callable
