from dataclasses import dataclass
from uuid import UUID

from src.infra.ai.google_stt_adapter import (
    TranscriptSegmentDTO,
    TranscriptWordDTO,
    drain_segments,
)
from src.services.chunking_service import (
    ChunkDraft,
    ChunkingService,
    SentenceFragment,
)
from src.services.transcript_merge_service import AudioPart, TranscriptMergeService
from src.services.transcription_artifact import TranscriptionArtifact


@dataclass(frozen=True, slots=True)
class AssemblyPart:
    pipeline_run_id: UUID
    audio_part_id: UUID
    part_index: int
    start_ms: int
    end_ms: int
    audio_gcs_path: str
    stt_model_version: str
    status: str
    result_ref: str | None

    def as_audio_part(self) -> AudioPart:
        return AudioPart(
            index=self.part_index,
            start_ms=self.start_ms,
            end_ms=self.end_ms,
            storage_path=self.audio_gcs_path,
        )


@dataclass(slots=True)
class AssemblyProgress:
    segments: list[TranscriptSegmentDTO]
    chunks: list[ChunkDraft]
    pending_words: list[TranscriptWordDTO]
    chunk_buffer: list[SentenceFragment]
    next_part_index: int
    next_chunk_index: int
    completed: bool


class TranscriptAssemblyService:
    def __init__(
        self,
        *,
        merge_service: TranscriptMergeService,
        chunking_service: ChunkingService,
    ) -> None:
        self._merge_service = merge_service
        self._chunking_service = chunking_service

    @property
    def chunking_version(self) -> str:
        return self._chunking_service.chunking_version

    def advance(
        self,
        *,
        all_parts: list[AssemblyPart],
        artifacts: list[TranscriptionArtifact],
        duration_ms: int,
        next_part_index: int,
        next_chunk_index: int,
        pending_words: list[TranscriptWordDTO],
        chunk_buffer: list[SentenceFragment],
        final_flush: bool,
    ) -> AssemblyProgress:
        parts_by_index = {part.part_index: part for part in all_parts}
        owned_words: list[TranscriptWordDTO] = []
        for artifact in artifacts:
            part = parts_by_index[artifact.part_index]
            self._validate_artifact(part, artifact)
            owned_words.extend(
                self._merge_service.owned_words_for_part(
                    part=part.as_audio_part(),
                    relative_words=artifact.words,
                    duration_ms=duration_ms,
                    previous_part=self._neighbor(parts_by_index, artifact.part_index - 1),
                    next_part=self._neighbor(parts_by_index, artifact.part_index + 1),
                )
            )

        drained = drain_segments(
            owned_words,
            pending_words=pending_words,
            flush=final_flush,
        )
        chunking = self._chunking_service.append_segments(
            drained.segments,
            buffer=chunk_buffer,
            next_chunk_index=next_chunk_index,
            flush=final_flush,
        )
        return AssemblyProgress(
            segments=drained.segments,
            chunks=chunking.chunks,
            pending_words=drained.pending_words,
            chunk_buffer=chunking.buffer,
            next_part_index=next_part_index + len(artifacts),
            next_chunk_index=chunking.next_chunk_index,
            completed=final_flush,
        )

    @staticmethod
    def _neighbor(
        parts_by_index: dict[int, AssemblyPart],
        part_index: int,
    ) -> AudioPart | None:
        part = parts_by_index.get(part_index)
        return part.as_audio_part() if part is not None else None

    @staticmethod
    def _validate_artifact(
        part: AssemblyPart,
        artifact: TranscriptionArtifact,
    ) -> None:
        if not artifact.matches(
            pipeline_run_id=part.pipeline_run_id,
            audio_part_id=part.audio_part_id,
            part_index=part.part_index,
            start_ms=part.start_ms,
            end_ms=part.end_ms,
            stt_model_version=part.stt_model_version,
        ):
            raise ValueError("Transcription artifact identity mismatch during assembly")
