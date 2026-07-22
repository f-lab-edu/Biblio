from dataclasses import dataclass
from typing import Literal


PipelineStage = Literal[
    "DOWNLOAD",
    "EXTRACT",
    "STT",
    "CHUNKING",
    "EMBEDDING",
    "VECTOR_UPSERT",
]

FailureCode = Literal[
    "YOUTUBE_BLOCKED",
    "SOURCE_UNAVAILABLE",
    "SOURCE_LIMIT_EXCEEDED",
    "AUDIO_EXTRACTION_FAILED",
    "STT_FAILED",
    "EMBEDDING_FAILED",
    "INDEX_WRITE_FAILED",
    "INTERNAL_PROCESSING_ERROR",
]


class AudioPreparationError(Exception):
    """Raised when audio validation, splitting, or upload cannot complete."""


class SourceLimitExceededError(AudioPreparationError):
    """Raised when the source exceeds a configured size or duration limit."""


class DeleteRequested(Exception):
    """Raised when a processing worker observes a pending video deletion."""


class PipelineStageError(Exception):
    def __init__(self, failed_stage: PipelineStage, cause: Exception) -> None:
        super().__init__(str(cause))
        self.failed_stage = failed_stage
        self.cause = cause


@dataclass(frozen=True, slots=True)
class PipelineFailure:
    failed_stage: PipelineStage
    failure_code: FailureCode
    exception: Exception
    provider: str | None = None
