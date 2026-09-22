"""Repair known Google STT word offset corruption patterns."""

from statistics import median
from typing import NoReturn

from loguru import logger

from src.infra.ai.google_stt_adapter import TranscriptWordDTO


BLOCK_BOUNDARY_THRESHOLD_MS = 30_000
BLOCK_REALIGNED_EVENT = "stt.word_offsets.block_realigned"
BLOCK_REJECTED_EVENT = "stt.word_offsets.block_rejected"

Violation = tuple[str, int]


class UnrepairableWordOffsetsError(ValueError):
    """Raised when reversed word offsets do not match a safe repair rule."""


class WordOffsetRepairer:
    """Apply narrowly-scoped repairs to reversed STT word offsets."""

    def __init__(self, trace_id: str, *, audio_duration_ms: int | None = None) -> None:
        self._trace_id = trace_id
        self._audio_duration_ms = audio_duration_ms

    def repair(self, words: list[TranscriptWordDTO]) -> list[TranscriptWordDTO]:
        block_repaired_words = self._repair_displaced_blocks(words)
        return self._repair_local_reversals(block_repaired_words)

    def _repair_local_reversals(
        self,
        words: list[TranscriptWordDTO],
    ) -> list[TranscriptWordDTO]:
        repaired_words: list[TranscriptWordDTO] = []
        word_index = 0
        while word_index < len(words):
            word = words[word_index]
            if not self._has_reversed_offsets(word):
                repaired_words.append(word)
                word_index += 1
                continue

            repaired_pair = self._try_repair_shared_boundary(words, word_index)
            if repaired_pair is not None:
                repaired_word, repaired_next_word = repaired_pair
                self._log_repair(
                    word_index,
                    word,
                    repaired_word.start_ms,
                    repaired_word.end_ms,
                )
                self._log_repair(
                    word_index + 1,
                    words[word_index + 1],
                    repaired_next_word.start_ms,
                    repaired_next_word.end_ms,
                )
                repaired_words.extend(repaired_pair)
                word_index += 2
                continue

            repaired_block = self._try_repair_short_displaced_block(words, word_index)
            if repaired_block is not None:
                for block_offset, repaired_word in enumerate(repaired_block):
                    raw_word = words[word_index + block_offset]
                    self._log_repair(
                        word_index + block_offset,
                        raw_word,
                        repaired_word.start_ms,
                        repaired_word.end_ms,
                    )
                repaired_words.extend(repaired_block)
                word_index += len(repaired_block)
                continue

            repaired_words.append(self._repair_single_reversed_word(words, word_index))
            word_index += 1
        return repaired_words

    def _repair_displaced_blocks(
        self,
        words: list[TranscriptWordDTO],
    ) -> list[TranscriptWordDTO]:
        boundary_indexes = self._boundary_indexes(words)
        if not boundary_indexes:
            return words

        estimated_duration_ms = self._estimated_word_duration_ms(words)
        candidate, shifts, shifted_count, final_shift = self._realign_blocks(
            words,
            estimated_duration_ms,
        )
        violations_before = self._violations(words)
        violations_after = self._violations(candidate)
        rejection_reason = self._block_rejection_reason(
            boundary_indexes=boundary_indexes,
            final_shift=final_shift,
            candidate=candidate,
            violations_before=violations_before,
            violations_after=violations_after,
        )
        if rejection_reason is not None:
            self._log_block_rejected(rejection_reason, boundary_indexes)
            return words

        self._log_block_realigned(
            boundary_indexes=boundary_indexes,
            shifts=shifts,
            shifted_word_count=shifted_count,
            estimated_duration_ms=estimated_duration_ms,
            violations_before=len(violations_before),
            violations_after=len(violations_after),
        )
        return candidate

    @staticmethod
    def _boundary_indexes(words: list[TranscriptWordDTO]) -> list[int]:
        return [
            index
            for index, word in enumerate(words)
            if abs(word.end_ms - word.start_ms) >= BLOCK_BOUNDARY_THRESHOLD_MS
        ]

    @staticmethod
    def _estimated_word_duration_ms(words: list[TranscriptWordDTO]) -> int:
        normal_durations = [
            word.end_ms - word.start_ms
            for word in words
            if 0 < word.end_ms - word.start_ms < BLOCK_BOUNDARY_THRESHOLD_MS
        ]
        return int(median(normal_durations)) if normal_durations else 0

    @staticmethod
    def _realign_blocks(
        words: list[TranscriptWordDTO],
        estimated_duration_ms: int,
    ) -> tuple[list[TranscriptWordDTO], list[int], int, int]:
        shift_ms = 0
        shifts: list[int] = []
        shifted_word_count = 0
        realigned_words: list[TranscriptWordDTO] = []
        for word in words:
            start_ms = word.start_ms + shift_ms
            end_ms = word.end_ms + shift_ms
            if abs(word.end_ms - word.start_ms) >= BLOCK_BOUNDARY_THRESHOLD_MS:
                if shift_ms == 0:
                    shift_ms = (word.start_ms + estimated_duration_ms) - word.end_ms
                    shifts.append(shift_ms)
                    end_ms = word.end_ms + shift_ms
                else:
                    end_ms = word.end_ms
                    shift_ms = 0
            if (start_ms, end_ms) != (word.start_ms, word.end_ms):
                shifted_word_count += 1
            realigned_words.append(
                TranscriptWordDTO(text=word.text, start_ms=start_ms, end_ms=end_ms)
            )
        return realigned_words, shifts, shifted_word_count, shift_ms

    @staticmethod
    def _violations(words: list[TranscriptWordDTO]) -> set[Violation]:
        violations: set[Violation] = set()
        for index, word in enumerate(words):
            if word.end_ms < word.start_ms:
                violations.add(("reversed", index))
            if index > 0 and word.start_ms < words[index - 1].start_ms:
                violations.add(("backward_start", index))
        return violations

    def _block_rejection_reason(
        self,
        *,
        boundary_indexes: list[int],
        final_shift: int,
        candidate: list[TranscriptWordDTO],
        violations_before: set[Violation],
        violations_after: set[Violation],
    ) -> str | None:
        if len(boundary_indexes) % 2 != 0:
            return "odd_boundary_count"
        if final_shift != 0:
            return "shift_not_zero"
        if not violations_after < violations_before:
            return "new_violation"
        if not self._exit_boundaries_are_ordered(candidate, boundary_indexes):
            return "new_violation"
        if self._exceeds_audio_duration(candidate):
            return "exceeds_duration"
        return None

    @staticmethod
    def _exit_boundaries_are_ordered(
        words: list[TranscriptWordDTO],
        boundary_indexes: list[int],
    ) -> bool:
        return all(
            exit_index == len(words) - 1
            or words[exit_index].end_ms <= words[exit_index + 1].start_ms
            for exit_index in boundary_indexes[1::2]
        )

    def _exceeds_audio_duration(self, words: list[TranscriptWordDTO]) -> bool:
        if self._audio_duration_ms is None:
            return False
        return max((word.end_ms for word in words), default=0) > self._audio_duration_ms

    def _try_repair_short_displaced_block(
        self,
        words: list[TranscriptWordDTO],
        entry_index: int,
    ) -> list[TranscriptWordDTO] | None:
        if entry_index == 0 or entry_index + 2 >= len(words):
            return None
        entry = words[entry_index]
        estimated_duration_ms = self._estimated_word_duration_ms(words)
        shift_ms = (entry.start_ms + estimated_duration_ms) - entry.end_ms
        if not self._is_short_block_entry(words, entry_index, shift_ms):
            return None

        exit_index = self._find_short_block_exit(words, entry_index, shift_ms)
        if exit_index is None:
            return None
        candidate = self._short_block_candidate(words, entry_index, exit_index, shift_ms)
        if not self._short_block_candidate_is_safe(words, candidate, entry_index, exit_index):
            return None
        return candidate[entry_index : exit_index + 1]

    def _is_short_block_entry(
        self,
        words: list[TranscriptWordDTO],
        entry_index: int,
        shift_ms: int,
    ) -> bool:
        previous_word = words[entry_index - 1]
        entry = words[entry_index]
        next_word = words[entry_index + 1]
        return (
            self._has_reversed_offsets(entry)
            and entry.end_ms == next_word.start_ms
            and previous_word.end_ms <= entry.start_ms
            and 0 < shift_ms < BLOCK_BOUNDARY_THRESHOLD_MS
        )

    @staticmethod
    def _find_short_block_exit(
        words: list[TranscriptWordDTO],
        entry_index: int,
        shift_ms: int,
    ) -> int | None:
        for candidate_index in range(entry_index + 1, len(words) - 1):
            candidate = words[candidate_index]
            following_word = words[candidate_index + 1]
            corrected_start_ms = candidate.start_ms + shift_ms
            if corrected_start_ms <= candidate.end_ms <= following_word.start_ms:
                return candidate_index
            if candidate.start_ms >= words[entry_index].start_ms:
                return None
        return None

    @staticmethod
    def _short_block_candidate(
        words: list[TranscriptWordDTO],
        entry_index: int,
        exit_index: int,
        shift_ms: int,
    ) -> list[TranscriptWordDTO]:
        candidate = list(words)
        for index in range(entry_index, exit_index + 1):
            word = words[index]
            start_ms = word.start_ms if index == entry_index else word.start_ms + shift_ms
            end_ms = word.end_ms if index == exit_index else word.end_ms + shift_ms
            candidate[index] = TranscriptWordDTO(
                text=word.text,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        return candidate

    def _short_block_candidate_is_safe(
        self,
        words: list[TranscriptWordDTO],
        candidate: list[TranscriptWordDTO],
        entry_index: int,
        exit_index: int,
    ) -> bool:
        window = candidate[entry_index - 1 : exit_index + 2]
        window_is_ordered = all(
            left.end_ms <= right.start_ms
            for left, right in zip(window, window[1:])
        )
        return (
            window_is_ordered
            and self._violations(candidate) < self._violations(words)
            and not self._exceeds_audio_duration(candidate)
        )

    def _log_block_realigned(
        self,
        *,
        boundary_indexes: list[int],
        shifts: list[int],
        shifted_word_count: int,
        estimated_duration_ms: int,
        violations_before: int,
        violations_after: int,
    ) -> None:
        logger.bind(
            log_schema_version=2,
            event_name=BLOCK_REALIGNED_EVENT,
            trace_id=self._trace_id,
            block_count=len(boundary_indexes) // 2,
            boundary_word_indexes=boundary_indexes,
            shift_ms_list=shifts,
            shifted_word_count=shifted_word_count,
            estimated_word_duration_ms=estimated_duration_ms,
            violations_before=violations_before,
            violations_after=violations_after,
        ).warning(BLOCK_REALIGNED_EVENT)

    def _log_block_rejected(self, reason: str, boundary_indexes: list[int]) -> None:
        logger.bind(
            log_schema_version=2,
            event_name=BLOCK_REJECTED_EVENT,
            trace_id=self._trace_id,
            reason=reason,
            boundary_word_indexes=boundary_indexes,
        ).warning(BLOCK_REJECTED_EVENT)

    @staticmethod
    def _has_reversed_offsets(word: TranscriptWordDTO) -> bool:
        return word.end_ms < word.start_ms

    @staticmethod
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

    def _try_repair_shared_boundary(
        self,
        words: list[TranscriptWordDTO],
        word_index: int,
    ) -> tuple[TranscriptWordDTO, TranscriptWordDTO] | None:
        # 오류 단어가 첫 단어이거나 두 칸 뒤에 following_word가 존재하지 않으면 None
        if word_index == 0 or word_index + 2 >= len(words):
            return None

        previous_word = words[word_index - 1]
        word = words[word_index]
        next_word = words[word_index + 1]
        following_word = words[word_index + 2]

        neighbors_have_valid_offsets = not any(
            self._has_reversed_offsets(neighbor)
            for neighbor in (previous_word, next_word, following_word)
        )
        # 발견한 오류가 맞는지 확인
        shared_boundary_is_corrupted = (
            word.end_ms == next_word.start_ms
        )
        # 잘못된 부분 제외하고 나머지는 정상인지
        outer_bounds_are_ordered = (
            previous_word.end_ms
            <= word.start_ms
            < next_word.end_ms
            <= following_word.start_ms
        )
        if not (
            self._has_reversed_offsets(word)
            and neighbors_have_valid_offsets
            and shared_boundary_is_corrupted
            and outer_bounds_are_ordered
        ):
            return None

        # 오류가 발생한 경계 timestamp(word.end_ms & next_word.start_ms)를 중간값으로 보정
        corrected_boundary_ms = (word.start_ms + next_word.end_ms) // 2
        return (
            TranscriptWordDTO(
                text=word.text,
                start_ms=word.start_ms,
                end_ms=corrected_boundary_ms,
            ),
            TranscriptWordDTO(
                text=next_word.text,
                start_ms=corrected_boundary_ms,
                end_ms=next_word.end_ms,
            ),
        )
    def _repair_single_reversed_word(
        self,
        words: list[TranscriptWordDTO],
        word_index: int,
    ) -> TranscriptWordDTO:
        word = words[word_index]
        if word_index == 0 or word_index == len(words) - 1:
            self._raise_unrepairable(words, word_index, "missing_neighbor")

        previous_word = words[word_index - 1]
        next_word = words[word_index + 1]
        if self._has_reversed_offsets(previous_word) or self._has_reversed_offsets(next_word):
            self._raise_unrepairable(words, word_index, "adjacent_reversal")
        if previous_word.end_ms > next_word.start_ms:
            self._raise_unrepairable(words, word_index, "overlapping_neighbors")

        corrected_start_ms, corrected_end_ms = self._corrected_word_bounds(
            word,
            previous_word.end_ms,
            next_word.start_ms,
        )
        self._log_repair(word_index, word, corrected_start_ms, corrected_end_ms)
        return TranscriptWordDTO(
            text=word.text,
            start_ms=corrected_start_ms,
            end_ms=corrected_end_ms,
        )

    def _log_repair(
        self,
        word_index: int,
        word: TranscriptWordDTO,
        corrected_start_ms: int,
        corrected_end_ms: int,
    ) -> None:
        logger.bind(
            log_schema_version=2,
            event_name="stt.word_offsets.corrected",
            trace_id=self._trace_id,
            word_index=word_index,
            raw_start_ms=word.start_ms,
            raw_end_ms=word.end_ms,
            corrected_start_ms=corrected_start_ms,
            corrected_end_ms=corrected_end_ms,
        ).warning("stt.word_offsets.corrected")

    def _raise_unrepairable(
        self,
        words: list[TranscriptWordDTO],
        word_index: int,
        reason: str,
    ) -> NoReturn:
        word = words[word_index]
        previous_end_ms = words[word_index - 1].end_ms if word_index > 0 else None
        next_start_ms = words[word_index + 1].start_ms if word_index < len(words) - 1 else None
        logger.bind(
            log_schema_version=2,
            event_name="stt.word_offsets.failed",
            trace_id=self._trace_id,
            word_index=word_index,
            raw_start_ms=word.start_ms,
            raw_end_ms=word.end_ms,
            previous_end_ms=previous_end_ms,
            next_start_ms=next_start_ms,
            reason=reason,
        ).error("stt.word_offsets.failed")
        raise UnrepairableWordOffsetsError(reason)
