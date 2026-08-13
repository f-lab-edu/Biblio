"""Repair known Google STT word offset corruption patterns."""

from typing import NoReturn

from loguru import logger

from src.infra.ai.google_stt_adapter import TranscriptWordDTO


class UnrepairableWordOffsetsError(ValueError):
    """Raised when reversed word offsets do not match a safe repair rule."""


class WordOffsetRepairer:
    """Apply narrowly-scoped repairs to reversed STT word offsets."""

    def __init__(self, trace_id: str, uri: str) -> None:
        self._trace_id = trace_id
        self._uri = uri

    def repair(self, words: list[TranscriptWordDTO]) -> list[TranscriptWordDTO]:
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

            repaired_words.append(self._repair_single_reversed_word(words, word_index))
            word_index += 1
        return repaired_words

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
            trace_id=self._trace_id,
            stt_uri=self._uri,
            word_index=word_index,
            word=word.text,
            raw_start_ms=word.start_ms,
            raw_end_ms=word.end_ms,
            corrected_start_ms=corrected_start_ms,
            corrected_end_ms=corrected_end_ms,
        ).warning(
            "STT word time offsets corrected uri={} word_index={} word={} "
            "raw_start_ms={} raw_end_ms={} corrected_start_ms={} corrected_end_ms={}",
            self._uri,
            word_index,
            word.text,
            word.start_ms,
            word.end_ms,
            corrected_start_ms,
            corrected_end_ms,
        )

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
            trace_id=self._trace_id,
            stt_uri=self._uri,
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
            self._uri,
            word_index,
            word.text,
            word.start_ms,
            word.end_ms,
            previous_end_ms,
            next_start_ms,
            reason,
        )
        raise UnrepairableWordOffsetsError(reason)
