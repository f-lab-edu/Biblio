import json
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from src.infra.ai.google_stt_adapter import (
    STTTranscriptionResult,
    TranscriptSegmentDTO,
    TranscriptWordDTO,
)


TRANSCRIPTION_ARTIFACT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class TranscriptionArtifact:
    pipeline_run_id: UUID
    audio_part_id: UUID
    part_index: int
    start_ms: int
    end_ms: int
    stt_model_version: str
    segments: tuple[TranscriptSegmentDTO, ...]
    words: tuple[TranscriptWordDTO, ...]

    def __post_init__(self) -> None:
        if self.part_index < 0:
            raise ValueError("part_index must be non-negative")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("Transcription artifact time range is invalid")
        if not self.stt_model_version:
            raise ValueError("stt_model_version must not be empty")
        for timed_text in (*self.segments, *self.words):
            if timed_text.start_ms < 0 or timed_text.end_ms < timed_text.start_ms:
                raise ValueError("Transcription artifact timestamp is invalid")

    @classmethod
    def from_result(
        cls,
        *,
        pipeline_run_id: UUID,
        audio_part_id: UUID,
        part_index: int,
        start_ms: int,
        end_ms: int,
        result: STTTranscriptionResult,
    ) -> "TranscriptionArtifact":
        return cls(
            pipeline_run_id=pipeline_run_id,
            audio_part_id=audio_part_id,
            part_index=part_index,
            start_ms=start_ms,
            end_ms=end_ms,
            stt_model_version=result.stt_model_version,
            segments=tuple(result.segments),
            words=tuple(result.words or ()),
        )

    def to_bytes(self) -> bytes:
        payload = {
            "schema_version": TRANSCRIPTION_ARTIFACT_SCHEMA_VERSION,
            "pipeline_run_id": str(self.pipeline_run_id),
            "audio_part_id": str(self.audio_part_id),
            "part_index": self.part_index,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "stt_model_version": self.stt_model_version,
            "segments": [asdict(segment) for segment in self.segments],
            "words": [asdict(word) for word in self.words],
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "TranscriptionArtifact":
        payload: dict[str, Any] = json.loads(raw.decode("utf-8"))
        if payload.get("schema_version") != TRANSCRIPTION_ARTIFACT_SCHEMA_VERSION:
            raise ValueError("Unsupported transcription artifact schema")
        return cls(
            pipeline_run_id=UUID(str(payload["pipeline_run_id"])),
            audio_part_id=UUID(str(payload["audio_part_id"])),
            part_index=int(payload["part_index"]),
            start_ms=int(payload["start_ms"]),
            end_ms=int(payload["end_ms"]),
            stt_model_version=str(payload["stt_model_version"]),
            segments=tuple(
                TranscriptSegmentDTO(
                    text=str(segment["text"]),
                    start_ms=int(segment["start_ms"]),
                    end_ms=int(segment["end_ms"]),
                )
                for segment in payload.get("segments", [])
            ),
            words=tuple(
                TranscriptWordDTO(
                    text=str(word["text"]),
                    start_ms=int(word["start_ms"]),
                    end_ms=int(word["end_ms"]),
                )
                for word in payload.get("words", [])
            ),
        )

    def matches(
        self,
        *,
        pipeline_run_id: UUID,
        audio_part_id: UUID,
        part_index: int,
        start_ms: int,
        end_ms: int,
        stt_model_version: str,
    ) -> bool:
        return (
            self.pipeline_run_id == pipeline_run_id
            and self.audio_part_id == audio_part_id
            and self.part_index == part_index
            and self.start_ms == start_ms
            and self.end_ms == end_ms
            and self.stt_model_version == stt_model_version
        )


def transcription_result_path(video_id: UUID, run_id: UUID, part_index: int) -> str:
    return (
        f"artifacts/{video_id}/pipeline-runs/{run_id}/"
        f"transcription-parts/part-{part_index:03d}.json"
    )
