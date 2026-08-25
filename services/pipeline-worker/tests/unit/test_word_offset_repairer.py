from collections.abc import Callable

import pytest
from loguru import logger

from src.infra.ai.google_stt_adapter import TranscriptWordDTO
from src.infra.ai.word_offset_repairer import WordOffsetRepairer


def _word(text: str, start_ms: int, end_ms: int) -> TranscriptWordDTO:
    return TranscriptWordDTO(text=text, start_ms=start_ms, end_ms=end_ms)


def _single_displaced_block(*, normal_duration_ms: int = 400) -> list[TranscriptWordDTO]:
    entry_start_ms = 100_000 + normal_duration_ms
    raw_block_start_ms = -63_440
    exit_end_ms = entry_start_ms + (normal_duration_ms * 3)
    return [
        _word("before", 100_000, 100_000 + normal_duration_ms),
        _word("entry", entry_start_ms, raw_block_start_ms),
        _word(
            "inside",
            raw_block_start_ms,
            raw_block_start_ms + normal_duration_ms,
        ),
        _word(
            "exit",
            raw_block_start_ms + normal_duration_ms,
            exit_end_ms,
        ),
        _word(
            "after",
            exit_end_ms + 200,
            exit_end_ms + 200 + normal_duration_ms,
        ),
    ]


def _capture_warning_records(action: Callable[[], object]) -> list[dict]:
    records: list[dict] = []
    sink_id = logger.add(
        lambda message: records.append(message.record),
        level="WARNING",
    )
    try:
        action()
    finally:
        logger.remove(sink_id)
    return records


class TestDisplacedBlockRepair:
    def test_realigns_one_block_and_logs_summary(self) -> None:
        words = _single_displaced_block()
        repaired: list[TranscriptWordDTO] = []

        records = _capture_warning_records(
            lambda: repaired.extend(WordOffsetRepairer("trace-one-block").repair(words))
        )

        assert [(word.start_ms, word.end_ms) for word in repaired[1:4]] == [
            (100_400, 100_800),
            (100_800, 101_200),
            (101_200, 101_600),
        ]
        record = next(
            item
            for item in records
            if item["extra"].get("event_name") == "stt.word_offsets.block_realigned"
        )
        assert record["extra"]["block_count"] == 1
        assert record["extra"]["boundary_word_indexes"] == [1, 3]
        assert record["extra"]["shift_ms_list"] == [164_240]
        assert record["extra"]["shifted_word_count"] == 3
        assert record["extra"]["estimated_word_duration_ms"] == 400
        assert record["extra"]["violations_before"] == 2
        assert record["extra"]["violations_after"] == 0
        assert record["extra"]["trace_id"] == "trace-one-block"
        assert "word" not in record["extra"]
        assert "stt_uri" not in record["extra"]

    def test_realigns_two_blocks_shifted_in_opposite_directions(self) -> None:
        words = _single_displaced_block() + [
            _word("before-two", 200_000, 200_400),
            _word("entry-two", 200_400, 364_640),
            _word("inside-two", 364_640, 365_040),
            _word("exit-two", 365_040, 201_600),
            _word("after-two", 201_800, 202_200),
        ]

        repaired = WordOffsetRepairer("trace-two-blocks").repair(words)

        assert [(word.start_ms, word.end_ms) for word in repaired[6:9]] == [
            (200_400, 200_800),
            (200_800, 201_200),
            (201_200, 201_600),
        ]

    def test_realigns_adjacent_entry_and_exit_boundaries(self) -> None:
        words = [
            _word("before", 100_000, 100_400),
            _word("entry", 100_400, -63_440),
            _word("exit", -63_440, 101_200),
            _word("after", 101_400, 101_800),
        ]

        repaired = WordOffsetRepairer("trace-no-inner-words").repair(words)

        assert [(word.start_ms, word.end_ms) for word in repaired[1:3]] == [
            (100_400, 100_800),
            (100_800, 101_200),
        ]

    def test_rejects_odd_boundary_count_without_changing_words(self) -> None:
        words = [
            _word("before", 0, 400),
            _word("long", 1_000, 50_000),
            _word("after", 50_000, 50_400),
        ]
        repaired: list[TranscriptWordDTO] = []

        records = _capture_warning_records(
            lambda: repaired.extend(WordOffsetRepairer("trace-odd").repair(words))
        )

        assert repaired == words
        record = next(
            item
            for item in records
            if item["extra"].get("event_name") == "stt.word_offsets.block_rejected"
        )
        assert record["extra"]["reason"] == "odd_boundary_count"
        assert record["extra"]["boundary_word_indexes"] == [1]

    def test_rejects_candidate_that_breaks_exit_ordering(self) -> None:
        words = _single_displaced_block()
        words[-1] = _word("after", 101_400, 101_800)
        repairer = WordOffsetRepairer("trace-new-violation")
        repaired: list[TranscriptWordDTO] = []

        records = _capture_warning_records(
            lambda: repaired.extend(repairer._repair_displaced_blocks(words))
        )

        assert repaired == words
        record = next(
            item
            for item in records
            if item["extra"].get("event_name") == "stt.word_offsets.block_rejected"
        )
        assert record["extra"]["reason"] == "new_violation"

    def test_rejects_candidate_that_exceeds_audio_duration(self) -> None:
        words = _single_displaced_block()
        repairer = WordOffsetRepairer(
            "trace-duration",
            audio_duration_ms=101_900,
        )
        repaired: list[TranscriptWordDTO] = []

        records = _capture_warning_records(
            lambda: repaired.extend(repairer._repair_displaced_blocks(words))
        )

        assert repaired == words
        record = next(
            item
            for item in records
            if item["extra"].get("event_name") == "stt.word_offsets.block_rejected"
        )
        assert record["extra"]["reason"] == "exceeds_duration"

    def test_keeps_normal_words_unchanged(self) -> None:
        words = [_word("one", 0, 400), _word("two", 400, 800)]

        repaired = WordOffsetRepairer("trace-normal").repair(words)

        assert repaired == words

    def test_runs_existing_local_repair_after_block_realigning(self) -> None:
        words = _single_displaced_block() + [
            _word("local-before", 103_000, 103_400),
            _word("local-reversed", 103_800, 103_500),
            _word("local-after", 104_200, 104_600),
        ]

        repaired = WordOffsetRepairer("trace-layered").repair(words)

        assert repaired[-2] == _word("local-reversed", 103_400, 104_200)

    def test_repairs_short_displaced_block_left_after_large_realigning(self) -> None:
        words = _single_displaced_block() + [
            _word("short-before", 647_800, 648_080),
            _word("short-entry", 648_080, 647_240),
            _word("short-inside-one", 647_240, 647_400),
            _word("short-inside-two", 647_400, 647_720),
            _word("short-exit", 647_720, 650_920),
            _word("short-after", 650_960, 651_360),
        ]

        repaired = WordOffsetRepairer("trace-short-block").repair(words)

        short_words = repaired[-6:]
        assert all(word.end_ms >= word.start_ms for word in short_words)
        assert all(
            left.end_ms <= right.start_ms
            for left, right in zip(short_words, short_words[1:])
        )

    def test_rejects_short_block_without_ordered_exit(self) -> None:
        words = [
            _word("before", 10_000, 10_400),
            _word("entry", 10_400, 9_600),
            _word("inside", 9_600, 9_800),
            _word("no-safe-exit", 9_800, 10_100),
            _word("after", 10_000, 10_500),
        ]

        with pytest.raises(ValueError, match="overlapping_neighbors"):
            WordOffsetRepairer("trace-unsafe-short-block").repair(words)

    @pytest.mark.parametrize("normal_duration_ms", [400, 600])
    def test_uses_estimated_duration_in_entry_and_block_shift(
        self,
        normal_duration_ms: int,
    ) -> None:
        words = _single_displaced_block(normal_duration_ms=normal_duration_ms)

        repaired = WordOffsetRepairer("trace-estimate").repair(words)

        assert repaired[1].end_ms == repaired[1].start_ms + normal_duration_ms
        assert repaired[2].start_ms == repaired[1].end_ms

    def test_uses_zero_estimate_when_no_normal_duration_exists(self) -> None:
        words = [
            _word("entry", 100_000, -60_000),
            _word("exit", -60_000, 100_500),
        ]

        repaired = WordOffsetRepairer("trace-zero-estimate").repair(words)

        assert repaired == [
            _word("entry", 100_000, 100_000),
            _word("exit", 100_000, 100_500),
        ]

    def test_excludes_boundary_words_from_duration_estimate(self) -> None:
        words = _single_displaced_block()
        repaired: list[TranscriptWordDTO] = []

        records = _capture_warning_records(
            lambda: repaired.extend(WordOffsetRepairer("trace-median").repair(words))
        )

        assert repaired
        record = next(
            item
            for item in records
            if item["extra"].get("event_name") == "stt.word_offsets.block_realigned"
        )
        assert record["extra"]["estimated_word_duration_ms"] == 400
