import asyncio
from types import SimpleNamespace

import google.cloud.speech_v2 as speech_v2
import pytest
from google.api_core import exceptions as google_exceptions
from google.rpc import code_pb2
from loguru import logger

from src.infra.ai.google_stt_adapter import ExternalAIAdapterError
from src.infra.ai.stt_batch_callable import (
    _parse_batch_recognize_response,
    build_poll_retry,
    build_stt_callable,
)


def _duration(seconds: float) -> SimpleNamespace:
    return SimpleNamespace(total_seconds=lambda: seconds)


def _word(text: str, start_seconds: float, end_seconds: float) -> SimpleNamespace:
    return SimpleNamespace(
        word=text,
        start_offset=_duration(start_seconds),
        end_offset=_duration(end_seconds),
    )


def _word_without_end_offset(text: str, start_seconds: float) -> SimpleNamespace:
    return SimpleNamespace(
        word=text,
        start_offset=_duration(start_seconds),
    )


def _word_with_invalid_start_offset(text: str, end_seconds: float) -> SimpleNamespace:
    return SimpleNamespace(
        word=text,
        start_offset=SimpleNamespace(total_seconds=lambda: None),
        end_offset=_duration(end_seconds),
    )


def _result(text: str, end_seconds: float, start_seconds: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        alternatives=[SimpleNamespace(transcript=text, words=[_word(text, start_seconds, end_seconds)])],
    )


def _result_with_words(text: str, words: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(
        alternatives=[SimpleNamespace(transcript=text, words=words)],
    )


def _response_with_words(text: str, words: list[SimpleNamespace]) -> SimpleNamespace:
    result = _result_with_words(text, words)
    return SimpleNamespace(
        results={
            "gs://bucket/audio.flac": SimpleNamespace(
                inline_result=SimpleNamespace(transcript=SimpleNamespace(results=[result]))
            )
        }
    )


def test_parse_batch_recognize_response_reads_inline_result_transcript() -> None:
    inline_transcript = SimpleNamespace(results=[_result("hello world", 1.25, 0.1)])
    file_result = SimpleNamespace(
        inline_result=SimpleNamespace(transcript=inline_transcript),
        transcript=SimpleNamespace(results=[]),
    )
    response = SimpleNamespace(results={"gs://bucket/audio.flac": file_result})

    parsed = _parse_batch_recognize_response(response, "chirp_2")

    assert parsed["stt_model_version"] == "chirp_2"
    assert parsed["segments"] == [
        {"text": "hello world", "start_ms": 100, "end_ms": 1250},
    ]


def test_parse_batch_recognize_response_preserves_file_error_details() -> None:
    response = SimpleNamespace(
        results={
            "gs://bucket/audio.flac": SimpleNamespace(
                error=SimpleNamespace(
                    code=code_pb2.INVALID_ARGUMENT,
                    message="Audio duration exceeds the allowed limit",
                )
            )
        }
    )

    with pytest.raises(ExternalAIAdapterError) as error_info:
        _parse_batch_recognize_response(response, "chirp_3", trace_id="trace-file-error")

    error = error_info.value
    assert error.code == "INVALID_REQUEST"
    assert error.message == (
        "STT BatchRecognize file failed uri=gs://bucket/audio.flac "
        "error_code=3 error_message=Audio duration exceeds the allowed limit"
    )
    assert error.trace_id == "trace-file-error"
    assert error.provider == "google-stt"
    assert error.retryable is False


@pytest.mark.parametrize(
    ("provider_code", "expected_code"),
    [
        (code_pb2.DEADLINE_EXCEEDED, "TIMEOUT"),
        (code_pb2.RESOURCE_EXHAUSTED, "RATE_LIMITED"),
        (code_pb2.UNAVAILABLE, "UNAVAILABLE"),
    ],
)
def test_parse_batch_recognize_response_keeps_retryable_file_error_policy(
    provider_code: int,
    expected_code: str,
) -> None:
    response = SimpleNamespace(
        results={
            "gs://bucket/audio.flac": SimpleNamespace(
                error=SimpleNamespace(code=provider_code, message="Temporary provider error")
            )
        }
    )

    with pytest.raises(ExternalAIAdapterError) as error_info:
        _parse_batch_recognize_response(response, "chirp_3", trace_id="trace-retryable-error")

    error = error_info.value
    assert error.code == expected_code
    assert error.retryable is True


def test_parse_batch_recognize_response_uses_word_offsets_for_segment_timestamps() -> None:
    words = [
        _word("Hello", 0.1, 0.4),
        _word("world.", 0.5, 1.25),
        _word("Again.", 1.5, 2.0),
    ]
    response = SimpleNamespace(
        results={
            "gs://bucket/audio.flac": SimpleNamespace(
                inline_result=SimpleNamespace(
                    transcript=SimpleNamespace(results=[_result_with_words("Hello world. Again.", words)])
                )
            )
        }
    )

    parsed = _parse_batch_recognize_response(response, "chirp_3", trace_id="trace-1")

    assert parsed["segments"] == [
        {"text": "Hello world.", "start_ms": 100, "end_ms": 1250},
        {"text": "Again.", "start_ms": 1500, "end_ms": 2000},
    ]
    assert parsed["words"] == [
        {"text": "Hello", "start_ms": 100, "end_ms": 400},
        {"text": "world.", "start_ms": 500, "end_ms": 1250},
        {"text": "Again.", "start_ms": 1500, "end_ms": 2000},
    ]


def test_parse_batch_recognize_response_splits_segment_after_one_hundred_words_without_punctuation() -> None:
    words = [_word(f"word{i}", i * 0.1, (i * 0.1) + 0.05) for i in range(101)]
    response = SimpleNamespace(
        results={
            "gs://bucket/audio.flac": SimpleNamespace(
                inline_result=SimpleNamespace(
                    transcript=SimpleNamespace(
                        results=[_result_with_words(" ".join(word.word for word in words), words)]
                    )
                )
            )
        }
    )

    parsed = _parse_batch_recognize_response(response, "chirp_3", trace_id="trace-2")

    assert len(parsed["segments"]) == 2
    assert parsed["segments"][0]["text"] == " ".join(f"word{i}" for i in range(100))
    assert parsed["segments"][0]["start_ms"] == 0
    assert parsed["segments"][0]["end_ms"] == 9950
    assert parsed["segments"][1] == {"text": "word100", "start_ms": 10000, "end_ms": 10050}


def test_parse_batch_recognize_response_returns_empty_segments_for_silence() -> None:
    response = SimpleNamespace(
        results={
            "gs://bucket/audio.flac": SimpleNamespace(
                inline_result=SimpleNamespace(transcript=SimpleNamespace(results=[SimpleNamespace(alternatives=[])]))
            )
        }
    )

    parsed = _parse_batch_recognize_response(response, "chirp_3", trace_id="trace-3")

    assert parsed["segments"] == []


def test_parse_batch_recognize_response_fails_when_transcript_has_no_word_offsets() -> None:
    result = SimpleNamespace(
        alternatives=[SimpleNamespace(transcript="Hello world.", words=[])],
    )
    response = SimpleNamespace(
        results={
            "gs://bucket/audio.flac": SimpleNamespace(
                inline_result=SimpleNamespace(transcript=SimpleNamespace(results=[result]))
            )
        }
    )

    with pytest.raises(ExternalAIAdapterError, match="word time offsets missing"):
        _parse_batch_recognize_response(response, "chirp_3", trace_id="trace-4")


def test_parse_batch_recognize_response_fails_when_word_end_offset_is_missing() -> None:
    result = _result_with_words("Hello.", [_word_without_end_offset("Hello.", 0.1)])
    response = SimpleNamespace(
        results={
            "gs://bucket/audio.flac": SimpleNamespace(
                inline_result=SimpleNamespace(transcript=SimpleNamespace(results=[result]))
            )
        }
    )

    with pytest.raises(ExternalAIAdapterError, match="word time offsets missing"):
        _parse_batch_recognize_response(response, "chirp_3", trace_id="trace-5")


class TestReversedWordOffsetRepair:
    def test_repairs_invalid_start_from_previous_word_end(self) -> None:
        words = [
            _word("보시면", 247.640, 248.120),
            _word("13으로부터", 249.160, 248.360),
            _word("왼쪽", 248.360, 251.240),
        ]
        messages: list[str] = []
        sink_id = logger.add(messages.append, format="{message}", level="WARNING")
        try:
            parsed = _parse_batch_recognize_response(
                _response_with_words("보시면 13으로부터 왼쪽", words),
                "chirp_3",
                trace_id="trace-repair-start",
            )
        finally:
            logger.remove(sink_id)

        assert parsed["words"][1] == {
            "text": "13으로부터",
            "start_ms": 248120,
            "end_ms": 248360,
        }
        assert any(
            "STT word time offsets corrected uri=gs://bucket/audio.flac word_index=1 "
            "word=13으로부터 raw_start_ms=249160 raw_end_ms=248360 "
            "corrected_start_ms=248120 corrected_end_ms=248360" in message
            for message in messages
        )

    def test_repairs_invalid_end_from_next_word_start(self) -> None:
        words = [
            _word("a", 1.0, 3.0),
            _word("b", 6.0, 2.0),
            _word("c", 7.0, 9.0),
        ]

        parsed = _parse_batch_recognize_response(
            _response_with_words("a b c", words),
            "chirp_3",
            trace_id="trace-repair-end",
        )

        assert parsed["words"][1] == {"text": "b", "start_ms": 6000, "end_ms": 7000}

    def test_uses_both_neighbor_boundaries_when_reversed_offsets_are_in_bounds(self) -> None:
        words = [
            _word("a", 1.0, 3.0),
            _word("b", 6.0, 4.0),
            _word("c", 7.0, 9.0),
        ]

        parsed = _parse_batch_recognize_response(
            _response_with_words("a b c", words),
            "chirp_3",
            trace_id="trace-repair-both",
        )

        assert parsed["words"][1] == {"text": "b", "start_ms": 3000, "end_ms": 7000}

    def test_repairs_multiple_isolated_words(self) -> None:
        words = [
            _word("a", 0.0, 1.0),
            _word("b", 3.0, 0.5),
            _word("c", 4.0, 5.0),
            _word("d", 7.0, 4.5),
            _word("e", 8.0, 9.0),
        ]

        parsed = _parse_batch_recognize_response(
            _response_with_words("a b c d e", words),
            "chirp_3",
            trace_id="trace-repair-multiple",
        )

        assert parsed["words"][1] == {"text": "b", "start_ms": 3000, "end_ms": 4000}
        assert parsed["words"][3] == {"text": "d", "start_ms": 7000, "end_ms": 8000}

    @pytest.mark.parametrize(
        "words",
        [
            [_word("only", 2.0, 1.0)],
            [
                _word("a", 0.0, 1.0),
                _word("b", 3.0, 0.5),
                _word("c", 4.0, 2.0),
                _word("d", 5.0, 6.0),
            ],
            [
                _word("a", 0.0, 5.0),
                _word("b", 6.0, 2.0),
                _word("c", 4.0, 7.0),
            ],
        ],
        ids=["missing-neighbors", "consecutive-reversals", "overlapping-neighbor-bounds"],
    )
    def test_fails_when_reversed_offsets_cannot_be_repaired(
        self,
        words: list[SimpleNamespace],
    ) -> None:
        with pytest.raises(ExternalAIAdapterError, match="word time offsets are invalid"):
            _parse_batch_recognize_response(
                _response_with_words("invalid words", words),
                "chirp_3",
                trace_id="trace-unrepairable",
            )


def test_parse_batch_recognize_response_wraps_invalid_duration_values() -> None:
    result = _result_with_words("Hello.", [_word_with_invalid_start_offset("Hello.", 1.0)])
    response = SimpleNamespace(
        results={
            "gs://bucket/audio.flac": SimpleNamespace(
                inline_result=SimpleNamespace(transcript=SimpleNamespace(results=[result]))
            )
        }
    )

    with pytest.raises(ExternalAIAdapterError, match="word time offsets missing"):
        _parse_batch_recognize_response(response, "chirp_3", trace_id="trace-7")


class _FlakyOperation:
    """operation.result()에 넘어온 retry로 조회 실패를 복구하는지 확인하는 테스트 대역."""

    def __init__(self, poll_errors: list[Exception], response: SimpleNamespace) -> None:
        self._poll_errors = list(poll_errors)
        self._response = response
        self.poll_count = 0
        self.result_timeout: int | None = None

    def _poll(self) -> SimpleNamespace:
        self.poll_count += 1
        if self._poll_errors:
            raise self._poll_errors.pop(0)
        return self._response

    def result(self, timeout: int | None = None, retry=None) -> SimpleNamespace:
        self.result_timeout = timeout
        if retry is None:
            return self._poll()
        return retry(self._poll)()


def _empty_response() -> SimpleNamespace:
    return SimpleNamespace(results={})


def _install_fake_speech_client(monkeypatch: pytest.MonkeyPatch, operation: _FlakyOperation) -> None:
    class _FakeSpeechClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def batch_recognize(self, request=None, timeout=None) -> _FlakyOperation:  # noqa: ARG002
            return operation

    monkeypatch.setattr(speech_v2, "SpeechClient", _FakeSpeechClient)


def _run_stt_callable(operation_timeout_sec: int = 900) -> None:
    stt_callable = build_stt_callable(
        project_id="test-project",
        location="us",
        recognizer="",
        model="chirp_3",
        submit_timeout_sec=30,
        operation_timeout_sec=operation_timeout_sec,
    )
    asyncio.run(stt_callable("gs://bucket/audio.flac", "trace-poll"))


class TestOperationPollRetry:
    """조회 RPC가 일시 오류로 실패해도 진행 중인 STT 작업을 버리지 않는지 확인한다."""

    def test_retries_resource_exhausted(self) -> None:
        retry = build_poll_retry()
        attempts: list[int] = []

        def flaky_poll() -> str:
            attempts.append(1)
            if len(attempts) == 1:
                raise google_exceptions.ResourceExhausted("429 Resource has been exhausted")
            return "done"

        assert retry(flaky_poll)() == "done"
        assert len(attempts) == 2

    @pytest.mark.parametrize(
        "error",
        [
            google_exceptions.ServiceUnavailable("503"),
            google_exceptions.DeadlineExceeded("504"),
        ],
    )
    def test_retries_other_transient_errors(self, error: Exception) -> None:
        retry = build_poll_retry()
        attempts: list[int] = []

        def flaky_poll() -> str:
            attempts.append(1)
            if len(attempts) == 1:
                raise error
            return "done"

        assert retry(flaky_poll)() == "done"
        assert len(attempts) == 2

    def test_does_not_retry_invalid_argument(self) -> None:
        retry = build_poll_retry()
        attempts: list[int] = []

        def always_invalid() -> str:
            attempts.append(1)
            raise google_exceptions.InvalidArgument("bad request")

        with pytest.raises(google_exceptions.InvalidArgument):
            retry(always_invalid)()
        assert len(attempts) == 1

    def test_stt_callable_survives_rate_limited_poll(self, monkeypatch: pytest.MonkeyPatch) -> None:
        operation = _FlakyOperation(
            [google_exceptions.ResourceExhausted("429 Resource has been exhausted")],
            _empty_response(),
        )
        _install_fake_speech_client(monkeypatch, operation)

        _run_stt_callable()

        assert operation.poll_count == 2

    def test_stt_callable_keeps_operation_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        operation = _FlakyOperation([], _empty_response())
        _install_fake_speech_client(monkeypatch, operation)

        _run_stt_callable(operation_timeout_sec=600)

        assert operation.result_timeout == 600
