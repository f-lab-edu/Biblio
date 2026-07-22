from src.infra.ai.google_stt_adapter import (
    STTTranscriptionResult,
    TranscriptWordDTO,
)
from src.services.transcript_merge_service import AudioPart, TranscriptMergeService


def _result(*words: TranscriptWordDTO) -> STTTranscriptionResult:
    return STTTranscriptionResult(
        segments=[],
        stt_model_version="chirp_3",
        words=list(words),
    )


def test_merge_assigns_overlap_words_by_midpoint_and_global_time() -> None:
    parts = [
        AudioPart(index=0, start_ms=0, end_ms=900_000, storage_path="part-0"),
        AudioPart(index=1, start_ms=895_000, end_ms=1_200_000, storage_path="part-1"),
    ]
    first_result = _result(
        TranscriptWordDTO("before", 896_000, 897_000),
        TranscriptWordDTO("drop-first", 899_000, 900_000),
    )
    second_result = _result(
        TranscriptWordDTO("drop-second", 1_000, 2_000),
        TranscriptWordDTO("after.", 4_000, 5_000),
    )

    merged = TranscriptMergeService().merge(
        parts=parts,
        results=[first_result, second_result],
        duration_ms=1_200_000,
    )

    assert [word.text for word in merged.words or []] == ["before", "after."]
    assert [(word.start_ms, word.end_ms) for word in merged.words or []] == [
        (896_000, 897_000),
        (899_000, 900_000),
    ]
    assert merged.segments[0].text == "before after."


def test_merge_sorts_results_and_keeps_segment_time_invariants() -> None:
    parts = [
        AudioPart(index=0, start_ms=0, end_ms=900_000, storage_path="part-0"),
        AudioPart(index=1, start_ms=895_000, end_ms=1_200_000, storage_path="part-1"),
    ]
    results = [
        _result(TranscriptWordDTO("first.", 100, 200)),
        _result(TranscriptWordDTO("second.", 10_000, 10_100)),
    ]

    merged = TranscriptMergeService().merge(
        parts=parts,
        results=results,
        duration_ms=1_200_000,
    )

    assert [segment.text for segment in merged.segments] == ["first.", "second."]
    assert all(
        0 <= segment.start_ms <= segment.end_ms <= 1_200_000
        for segment in merged.segments
    )
    assert [segment.start_ms for segment in merged.segments] == sorted(
        segment.start_ms for segment in merged.segments
    )
