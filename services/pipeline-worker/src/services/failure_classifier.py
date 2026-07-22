from src.infra.ai.google_stt_adapter import ExternalAIAdapterError
from src.infra.media.youtube_downloader import DownloadError
from src.services.pipeline_errors import (
    AudioPreparationError,
    FailureCode,
    PipelineFailure,
    PipelineStage,
    PipelineStageError,
    SourceLimitExceededError,
)


_DOWNLOAD_FAILURE_CODE_BY_CATEGORY: dict[str, FailureCode] = {
    "youtube_block": "YOUTUBE_BLOCKED",
    "source_limit_exceeded": "SOURCE_LIMIT_EXCEEDED",
    "proxy_error": "SOURCE_UNAVAILABLE",
    "video_unavailable": "SOURCE_UNAVAILABLE",
    "unknown": "SOURCE_UNAVAILABLE",
}


def classify_pipeline_failure(exception: Exception) -> PipelineFailure:
    failed_stage, cause = _unwrap_stage_error(exception)

    if failed_stage == "DOWNLOAD" and isinstance(cause, DownloadError):
        return PipelineFailure(
            failed_stage="DOWNLOAD",
            failure_code=_DOWNLOAD_FAILURE_CODE_BY_CATEGORY[cause.category],
            provider="youtube",
            exception=cause,
        )
    if failed_stage == "DOWNLOAD" and isinstance(cause, FileNotFoundError):
        return PipelineFailure(
            failed_stage="DOWNLOAD",
            failure_code="SOURCE_UNAVAILABLE",
            exception=cause,
        )
    if isinstance(cause, SourceLimitExceededError):
        return PipelineFailure(
            failed_stage=failed_stage,
            failure_code="SOURCE_LIMIT_EXCEEDED",
            exception=cause,
        )
    if isinstance(cause, AudioPreparationError):
        return PipelineFailure(
            failed_stage=failed_stage,
            failure_code="AUDIO_EXTRACTION_FAILED",
            exception=cause,
        )
    if isinstance(cause, ExternalAIAdapterError):
        return _classify_provider_failure(cause, failed_stage)
    if isinstance(exception, PipelineStageError) and failed_stage == "VECTOR_UPSERT":
        return PipelineFailure(
            failed_stage=failed_stage,
            failure_code="INDEX_WRITE_FAILED",
            exception=cause,
        )
    return PipelineFailure(
        failed_stage=failed_stage,
        failure_code="INTERNAL_PROCESSING_ERROR",
        exception=cause,
    )


def _unwrap_stage_error(exception: Exception) -> tuple[PipelineStage, Exception]:
    if isinstance(exception, PipelineStageError):
        return exception.failed_stage, exception.cause
    if isinstance(exception, (DownloadError, FileNotFoundError)):
        return "DOWNLOAD", exception
    if isinstance(exception, AudioPreparationError):
        return "EXTRACT", exception
    if isinstance(exception, ExternalAIAdapterError):
        if exception.provider == "google-stt":
            return "STT", exception
        if exception.provider == "embedding-endpoint":
            return "EMBEDDING", exception
    return "VECTOR_UPSERT", exception


def _classify_provider_failure(
    exception: ExternalAIAdapterError,
    failed_stage: PipelineStage,
) -> PipelineFailure:
    if exception.provider == "google-stt":
        return PipelineFailure(
            failed_stage="STT",
            failure_code="STT_FAILED",
            provider=exception.provider,
            exception=exception,
        )
    if exception.provider == "embedding-endpoint":
        return PipelineFailure(
            failed_stage="EMBEDDING",
            failure_code="EMBEDDING_FAILED",
            provider=exception.provider,
            exception=exception,
        )
    return PipelineFailure(
        failed_stage=failed_stage,
        failure_code="INTERNAL_PROCESSING_ERROR",
        provider=exception.provider,
        exception=exception,
    )
