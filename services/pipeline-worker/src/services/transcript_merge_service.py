from dataclasses import dataclass

from src.infra.ai.google_stt_adapter import (
    STTTranscriptionResult,
    TranscriptWordDTO,
    segments_from_words,
)


@dataclass(frozen=True, slots=True)
class AudioPart:
    index: int
    start_ms: int
    end_ms: int
    storage_path: str


class TranscriptMergeService:
    def merge(
        self,
        *,
        parts: list[AudioPart],
        results: list[STTTranscriptionResult],
        duration_ms: int,
    ) -> STTTranscriptionResult:
        if len(parts) != len(results):
            raise ValueError("Audio part and STT result counts must match")
        words: list[TranscriptWordDTO] = []
        for position, (part, result) in enumerate(zip(parts, results, strict=True)):
            if result.words is None:
                raise ValueError("Long audio STT response must include word time offsets")
            words.extend(
                self.owned_words_for_part(
                    part=part,
                    relative_words=result.words,
                    duration_ms=duration_ms,
                    previous_part=parts[position - 1] if position else None,
                    next_part=parts[position + 1] if position + 1 < len(parts) else None,
                )
            )
        words.sort(key=lambda word: (word.start_ms, word.end_ms, word.text))
        model_version = results[0].stt_model_version if results else ""
        return STTTranscriptionResult(
            segments=segments_from_words(words),
            stt_model_version=model_version,
            words=words,
        )

    def owned_words_for_part(
        self,
        *,
        part: AudioPart,
        relative_words: list[TranscriptWordDTO] | tuple[TranscriptWordDTO, ...],
        duration_ms: int,
        previous_part: AudioPart | None,
        next_part: AudioPart | None,
    ) -> list[TranscriptWordDTO]:
        lower_bound = (
            0
            if previous_part is None
            else (part.start_ms + previous_part.end_ms) / 2
        )
        upper_bound = (
            part.end_ms
            if next_part is None
            else (next_part.start_ms + part.end_ms) / 2
        )
        owned_words: list[TranscriptWordDTO] = []
        for word in relative_words:
            global_word = self._to_global_word(word, part.start_ms, duration_ms)
            midpoint = (global_word.start_ms + global_word.end_ms) / 2
            if midpoint < lower_bound or midpoint >= upper_bound:
                continue
            owned_words.append(global_word)
        return owned_words

    @staticmethod
    def _to_global_word(
        word: TranscriptWordDTO,
        offset_ms: int,
        duration_ms: int,
    ) -> TranscriptWordDTO:
        start_ms = min(max(word.start_ms + offset_ms, 0), duration_ms)
        end_ms = min(max(word.end_ms + offset_ms, start_ms), duration_ms)
        return TranscriptWordDTO(text=word.text, start_ms=start_ms, end_ms=end_ms)
