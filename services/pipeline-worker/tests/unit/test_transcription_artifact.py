import json
from uuid import uuid4

import pytest

from src.infra.ai.google_stt_adapter import (
    STTTranscriptionResult,
    TranscriptSegmentDTO,
    TranscriptWordDTO,
)
from src.services.transcription_artifact import (
    TranscriptionArtifact,
    transcription_result_path,
)


def test_transcription_artifact_round_trip_preserves_relative_timestamps() -> None:
    run_id = uuid4()
    part_id = uuid4()
    artifact = TranscriptionArtifact.from_result(
        pipeline_run_id=run_id,
        audio_part_id=part_id,
        part_index=2,
        start_ms=895_000,
        end_ms=1_800_000,
        result=STTTranscriptionResult(
            stt_model_version="chirp_3",
            segments=[TranscriptSegmentDTO("안녕.", 100, 600)],
            words=[TranscriptWordDTO("안녕.", 100, 600)],
        ),
    )

    restored = TranscriptionArtifact.from_bytes(artifact.to_bytes())

    assert restored == artifact
    assert restored.words[0].start_ms == 100


def test_transcription_result_path_is_deterministic_per_run_and_part() -> None:
    video_id = uuid4()
    run_id = uuid4()

    path = transcription_result_path(video_id, run_id, 7)

    assert path == (
        f"artifacts/{video_id}/pipeline-runs/{run_id}/"
        "transcription-parts/part-007.json"
    )


def test_transcription_artifact_rejects_invalid_relative_timestamp() -> None:
    payload = {
        "schema_version": 1,
        "pipeline_run_id": str(uuid4()),
        "audio_part_id": str(uuid4()),
        "part_index": 0,
        "start_ms": 0,
        "end_ms": 60_000,
        "stt_model_version": "chirp_3",
        "segments": [{"text": "잘못된 구간", "start_ms": 500, "end_ms": 100}],
        "words": [],
    }

    with pytest.raises(ValueError, match="timestamp is invalid"):
        TranscriptionArtifact.from_bytes(json.dumps(payload).encode())
