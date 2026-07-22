from src.services.failure_classifier import classify_pipeline_failure
from src.services.pipeline_errors import AudioPreparationError, PipelineStageError


def test_unknown_unwrapped_error_uses_internal_processing_code() -> None:
    exception = RuntimeError("unexpected processing failure")

    failure = classify_pipeline_failure(exception)

    assert failure.failure_code == "INTERNAL_PROCESSING_ERROR"
    assert failure.exception is exception


def test_file_not_found_outside_download_keeps_actual_stage() -> None:
    exception = FileNotFoundError("keyframe missing")

    failure = classify_pipeline_failure(PipelineStageError("CHUNKING", exception))

    assert failure.failed_stage == "CHUNKING"
    assert failure.failure_code == "INTERNAL_PROCESSING_ERROR"


def test_audio_preparation_error_uses_audio_extraction_code() -> None:
    exception = AudioPreparationError("ffmpeg failed")

    failure = classify_pipeline_failure(PipelineStageError("EXTRACT", exception))

    assert failure.failed_stage == "EXTRACT"
    assert failure.failure_code == "AUDIO_EXTRACTION_FAILED"
