"""Factory for a production STTCallable using Google Cloud Speech-to-Text v2 BatchRecognize."""

import asyncio
from typing import Any, NoReturn

from google.api_core.client_options import ClientOptions
from google.rpc import code_pb2
from loguru import logger

from src.infra.ai.google_stt_adapter import (
    ExternalAIAdapterError,
    STTCallable,
    TranscriptWordDTO,
    segments_from_words,
)

_FILE_ERROR_POLICY = {
    code_pb2.DEADLINE_EXCEEDED: ("TIMEOUT", True),
    code_pb2.RESOURCE_EXHAUSTED: ("RATE_LIMITED", True),
    code_pb2.UNAVAILABLE: ("UNAVAILABLE", True),
    code_pb2.INVALID_ARGUMENT: ("INVALID_REQUEST", False),
}
_INVALID_WORD_OFFSETS_MESSAGE = "STT word time offsets are invalid"

# google stt: 초단위 -> biblio: 밀리초 단위 변환
def _duration_to_ms(duration: Any, trace_id: str) -> int:
    try:
        return int(duration.total_seconds() * 1000)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _stt_parse_error("STT word time offsets missing", trace_id) from exc


def _word_text(word: Any) -> str:
    return str(getattr(word, "word", "")).strip()


def _stt_parse_error(message: str, trace_id: str) -> ExternalAIAdapterError:
    return ExternalAIAdapterError(
        code="INTERNAL_ERROR",
        message=message,
        trace_id=trace_id,
        provider="google-stt",
        retryable=False,
    )


def _raise_for_file_result_error(uri: str, file_result: Any, trace_id: str) -> None:
    error = getattr(file_result, "error", None)
    error_code = int(getattr(error, "code", code_pb2.OK))
    if error_code == code_pb2.OK:
        return

    error_message = str(getattr(error, "message", "")).strip() or "No message provided"
    #  Google 오류 코드를 Biblio 오류 코드와 재시도 여부로 변환
    app_code, retryable = _FILE_ERROR_POLICY.get(
        error_code,
        ("INTERNAL_ERROR", False),
    )
    detail = (
        f"STT BatchRecognize file failed uri={uri} "
        f"error_code={error_code} error_message={error_message}"
    )
    logger.bind(trace_id=trace_id).error(detail)
    raise ExternalAIAdapterError(
        code=app_code,
        message=detail,
        trace_id=trace_id,
        provider="google-stt",
        retryable=retryable,
    )


def _normalize_word(word: Any, trace_id: str) -> TranscriptWordDTO:
    text = _word_text(word)
    if not text:
        raise _stt_parse_error("STT word text missing", trace_id)
    try:
        start_ms = _duration_to_ms(word.start_offset, trace_id)
        end_ms = _duration_to_ms(word.end_offset, trace_id)
    except AttributeError as exc:
        raise _stt_parse_error("STT word time offsets missing", trace_id) from exc
    return TranscriptWordDTO(text=text, start_ms=start_ms, end_ms=end_ms)


def _has_reversed_offsets(word: TranscriptWordDTO) -> bool:
    return word.end_ms < word.start_ms


def _corrected_word_bounds(
    word: TranscriptWordDTO,
    previous_end_ms: int,
    next_start_ms: int,
) -> tuple[int, int]:
    start_is_usable = previous_end_ms <= word.start_ms <= next_start_ms
    end_is_usable = previous_end_ms <= word.end_ms <= next_start_ms
    if start_is_usable and not end_is_usable:
        return word.start_ms, next_start_ms
    if end_is_usable and not start_is_usable:
        return previous_end_ms, word.end_ms
    return previous_end_ms, next_start_ms


def _raise_unrepairable_word_offsets(
    words: list[TranscriptWordDTO],
    word_index: int,
    trace_id: str,
    uri: str,
    reason: str,
) -> NoReturn:
    word = words[word_index]
    previous_end_ms = words[word_index - 1].end_ms if word_index > 0 else None
    next_start_ms = words[word_index + 1].start_ms if word_index < len(words) - 1 else None
    logger.bind(
        trace_id=trace_id,
        stt_uri=uri,
        word_index=word_index,
        word=word.text,
        raw_start_ms=word.start_ms,
        raw_end_ms=word.end_ms,
        previous_end_ms=previous_end_ms,
        next_start_ms=next_start_ms,
        reason=reason,
    ).error(
        "STT word time offsets cannot be corrected uri={} word_index={} word={} "
        "raw_start_ms={} raw_end_ms={} previous_end_ms={} next_start_ms={} reason={}",
        uri,
        word_index,
        word.text,
        word.start_ms,
        word.end_ms,
        previous_end_ms,
        next_start_ms,
        reason,
    )
    raise _stt_parse_error(_INVALID_WORD_OFFSETS_MESSAGE, trace_id)


def _repair_reversed_word(
    words: list[TranscriptWordDTO],
    word_index: int,
    trace_id: str,
    uri: str,
) -> TranscriptWordDTO:
    word = words[word_index]
    if word_index == 0 or word_index == len(words) - 1:
        _raise_unrepairable_word_offsets(words, word_index, trace_id, uri, "missing_neighbor")

    previous_word = words[word_index - 1]
    next_word = words[word_index + 1]
    if _has_reversed_offsets(previous_word) or _has_reversed_offsets(next_word):
        _raise_unrepairable_word_offsets(words, word_index, trace_id, uri, "adjacent_reversal")
    if previous_word.end_ms > next_word.start_ms:
        _raise_unrepairable_word_offsets(words, word_index, trace_id, uri, "overlapping_neighbors")

    corrected_start_ms, corrected_end_ms = _corrected_word_bounds(
        word,
        previous_word.end_ms,
        next_word.start_ms,
    )
    logger.bind(
        trace_id=trace_id,
        stt_uri=uri,
        word_index=word_index,
        word=word.text,
        raw_start_ms=word.start_ms,
        raw_end_ms=word.end_ms,
        corrected_start_ms=corrected_start_ms,
        corrected_end_ms=corrected_end_ms,
    ).warning(
        "STT word time offsets corrected uri={} word_index={} word={} "
        "raw_start_ms={} raw_end_ms={} corrected_start_ms={} corrected_end_ms={}",
        uri,
        word_index,
        word.text,
        word.start_ms,
        word.end_ms,
        corrected_start_ms,
        corrected_end_ms,
    )
    return TranscriptWordDTO(
        text=word.text,
        start_ms=corrected_start_ms,
        end_ms=corrected_end_ms,
    )


def _repair_reversed_word_offsets(
    words: list[TranscriptWordDTO],
    trace_id: str,
    uri: str,
) -> list[TranscriptWordDTO]:
    repaired_words: list[TranscriptWordDTO] = []
    for word_index, word in enumerate(words):
        repaired_word = (
            _repair_reversed_word(words, word_index, trace_id, uri)
            if _has_reversed_offsets(word)
            else word
        )
        repaired_words.append(repaired_word)
    return repaired_words


def _parse_batch_recognize_response(response: Any, stt_model_version: str, trace_id: str = "") -> dict:
    normalized_words: list[TranscriptWordDTO] = []
    for uri, file_result in response.results.items():
        _raise_for_file_result_error(uri, file_result, trace_id)
        transcript = getattr(getattr(file_result, "inline_result", None), "transcript", None)
        if transcript is None:
            transcript = getattr(file_result, "transcript", None)
        if transcript is None:
            continue
        file_words: list[TranscriptWordDTO] = []
        for result in transcript.results:
            if not result.alternatives:
                continue
            alt = result.alternatives[0]
            text = alt.transcript.strip()
            words = list(getattr(alt, "words", []) or [])
            if text and not words:
                raise _stt_parse_error("STT word time offsets missing", trace_id)
            file_words.extend(_normalize_word(word, trace_id) for word in words)
        normalized_words.extend(_repair_reversed_word_offsets(file_words, trace_id, str(uri)))
    normalized_words.sort(key=lambda word: word.start_ms)
    segments = segments_from_words(normalized_words)
    return {
        "segments": [
            {"text": segment.text, "start_ms": segment.start_ms, "end_ms": segment.end_ms}
            for segment in segments
        ],
        "words": [
            {"text": word.text, "start_ms": word.start_ms, "end_ms": word.end_ms}
            for word in normalized_words
        ],
        "stt_model_version": stt_model_version,
    }


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
