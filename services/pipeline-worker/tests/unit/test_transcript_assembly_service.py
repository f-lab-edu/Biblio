from uuid import uuid4

from src.infra.ai.google_stt_adapter import TranscriptWordDTO, drain_segments
from src.services.chunking_service import ChunkingService
from src.services.transcript_assembly_service import (
    AssemblyPart,
    TranscriptAssemblyService,
)
from src.services.transcript_merge_service import TranscriptMergeService
from src.services.transcription_artifact import TranscriptionArtifact


def _part(run_id, index: int, start_ms: int, end_ms: int) -> AssemblyPart:
    return AssemblyPart(
        pipeline_run_id=run_id,
        audio_part_id=uuid4(),
        part_index=index,
        start_ms=start_ms,
        end_ms=end_ms,
        audio_gcs_path=f"part-{index}.flac",
        stt_model_version="chirp_3",
        status="COMPLETED",
        result_ref=f"part-{index}.json",
    )


def _artifact(part: AssemblyPart, *words: TranscriptWordDTO) -> TranscriptionArtifact:
    return TranscriptionArtifact(
        pipeline_run_id=part.pipeline_run_id,
        audio_part_id=part.audio_part_id,
        part_index=part.part_index,
        start_ms=part.start_ms,
        end_ms=part.end_ms,
        stt_model_version=part.stt_model_version,
        segments=(),
        words=words,
    )


def _service(*, max_words: int = 300) -> TranscriptAssemblyService:
    return TranscriptAssemblyService(
        merge_service=TranscriptMergeService(),
        chunking_service=ChunkingService(
            max_tokens=max_words,
            overlap_sentences=1,
        ),
    )


def test_pending_sentence_crosses_part_boundary_and_flushes_at_end() -> None:
    run_id = uuid4()
    first = _part(run_id, 0, 0, 10_000)
    second = _part(run_id, 1, 5_000, 15_000)
    service = _service()

    first_progress = service.advance(
        all_parts=[first, second],
        artifacts=[_artifact(first, TranscriptWordDTO("hello", 6_000, 7_000))],
        duration_ms=15_000,
        next_part_index=0,
        next_chunk_index=0,
        pending_words=[],
        chunk_buffer=[],
        final_flush=False,
    )
    final_progress = service.advance(
        all_parts=[first, second],
        artifacts=[
            _artifact(
                second,
                TranscriptWordDTO("duplicate", 1_000, 2_000),
                TranscriptWordDTO("world.", 3_000, 4_000),
            )
        ],
        duration_ms=15_000,
        next_part_index=first_progress.next_part_index,
        next_chunk_index=first_progress.next_chunk_index,
        pending_words=first_progress.pending_words,
        chunk_buffer=first_progress.chunk_buffer,
        final_flush=True,
    )

    assert [word.text for word in first_progress.pending_words] == ["hello"]
    assert [segment.text for segment in final_progress.segments] == ["hello world."]
    assert [chunk.text for chunk in final_progress.chunks] == ["hello world."]
    assert final_progress.completed is True


def test_chunk_buffer_survives_incremental_assembly() -> None:
    run_id = uuid4()
    first = _part(run_id, 0, 0, 5_000)
    second = _part(run_id, 1, 5_000, 10_000)
    service = _service(max_words=2)

    first_progress = service.advance(
        all_parts=[first, second],
        artifacts=[_artifact(first, TranscriptWordDTO("one.", 0, 1_000))],
        duration_ms=10_000,
        next_part_index=0,
        next_chunk_index=0,
        pending_words=[],
        chunk_buffer=[],
        final_flush=False,
    )
    final_progress = service.advance(
        all_parts=[first, second],
        artifacts=[_artifact(second, TranscriptWordDTO("two three.", 0, 1_000))],
        duration_ms=10_000,
        next_part_index=1,
        next_chunk_index=0,
        pending_words=first_progress.pending_words,
        chunk_buffer=first_progress.chunk_buffer,
        final_flush=True,
    )

    assert [chunk.chunk_index for chunk in final_progress.chunks] == [0, 1]
    assert final_progress.chunks[1].text.startswith("one.")


def test_one_hundred_words_force_a_segment_without_punctuation() -> None:
    words = [TranscriptWordDTO(f"word-{index}", index, index + 1) for index in range(100)]

    result = drain_segments(words)

    assert len(result.segments) == 1
    assert len(result.segments[0].text.split()) == 100
    assert result.pending_words == []
