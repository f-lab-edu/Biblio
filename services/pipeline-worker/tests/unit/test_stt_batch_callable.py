from types import SimpleNamespace

from src.infra.ai.stt_batch_callable import _parse_batch_recognize_response


def _word(start_seconds: float) -> SimpleNamespace:
    return SimpleNamespace(start_offset=SimpleNamespace(total_seconds=lambda: start_seconds))


def _result(text: str, end_seconds: float, start_seconds: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        alternatives=[SimpleNamespace(transcript=text, words=[_word(start_seconds)])],
        result_end_offset=SimpleNamespace(total_seconds=lambda: end_seconds),
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
