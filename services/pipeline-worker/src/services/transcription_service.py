from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from loguru import logger

from src.infra.ai.google_stt_adapter import ExternalAIAdapterError, GoogleSTTAdapter
from src.infra.db.transcription_repository import (
    TranscriptionInput,
    TranscriptionRepository,
)
from src.infra.queue.consumer import StageDispatchContext, StageHandlerResult
from src.infra.storage.client import StorageClient
from src.schemas.messages import TranscribePartMessage
from src.services.transcription_artifact import (
    TranscriptionArtifact,
    transcription_result_path,
)
from src.telemetry.pipeline_events import work_log_context_from_message


class TranscriptionService:
    def __init__(
        self,
        *,
        repository: TranscriptionRepository,
        storage: StorageClient,
        stt: GoogleSTTAdapter,
        max_delivery_attempts: int,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._stt = stt
        self._max_delivery_attempts = max_delivery_attempts

    async def execute(self, context: StageDispatchContext) -> StageHandlerResult:
        message = context.message
        if not isinstance(message, TranscribePartMessage):
            raise TypeError("TRANSCRIBE_PART handler received a different message type")
        log = logger.bind(
            log_schema_version=2,
            **work_log_context_from_message(
                message,
                message_id=context.message_id,
                read_ct=context.read_count,
            ).fields(),
        )
        input_record = await self._repository.load_input(
            message,
            message_id=context.message_id,
        )
        if input_record is None:
            log.info("transcription.skipped reason=stale_after_claim")
            return StageHandlerResult("SKIPPED", reason="stale_after_claim")

        result_ref = transcription_result_path(
            message.video_id,
            message.pipeline_run_id,
            message.part_index,
        )
        with TemporaryDirectory(prefix="biblio-transcription-") as directory:
            artifact_path = Path(directory) / "result.json"
            try:
                artifact, reused = await self._load_or_transcribe(
                    message,
                    input_record=input_record,
                    result_ref=result_ref,
                    artifact_path=artifact_path,
                    log=log,
                )
            except Exception as error:
                return await self._handle_failure(
                    context,
                    error=error,
                    log=log,
                )
            decision = await self._repository.complete(
                message,
                message_id=context.message_id,
                result_ref=result_ref,
                artifact=artifact,
            )
        if decision.accepted:
            log.bind(
                event_name="transcription.completed",
                result_json_bytes=len(artifact.to_bytes()),
                word_count=len(artifact.words),
                segment_count=len(artifact.segments),
                reused=reused,
            ).info("transcription.completed")
            return StageHandlerResult("SUCCEEDED", reused=reused)

        if decision.reason in {
            "video_not_found",
            "video_deleting",
            "inactive_pipeline_run",
            "work_not_found",
        }:
            await self._storage.delete_object(result_ref)
        if decision.reason == "video_deleting":
            await self._storage.delete_object(input_record.audio_gcs_path)
        log.bind(
            event_name="transcription.discarded",
            discard_reason=decision.reason,
            reused=reused,
        ).info("transcription.discarded")
        return StageHandlerResult("SKIPPED", reason=decision.reason)

    async def _load_or_transcribe(
        self,
        message: TranscribePartMessage,
        *,
        input_record: TranscriptionInput,
        result_ref: str,
        artifact_path: Path,
        log: Any,
    ) -> tuple[TranscriptionArtifact, bool]:
        if await self._storage.object_exists(result_ref):
            return await self._load_existing_artifact(
                message,
                input_record=input_record,
                result_ref=result_ref,
                artifact_path=artifact_path,
                log=log,
            )

        started_at = perf_counter()
        result = await self._stt.transcribe(
            audio_uri=self._storage.object_uri(input_record.audio_gcs_path),
            trace_id=str(message.trace_id),
        )
        artifact = TranscriptionArtifact.from_result(
            pipeline_run_id=message.pipeline_run_id,
            audio_part_id=message.audio_part_id,
            part_index=message.part_index,
            start_ms=input_record.start_ms,
            end_ms=input_record.end_ms,
            result=result,
        )
        artifact_path.write_bytes(artifact.to_bytes())
        created = await self._storage.upload_object_if_absent(
            artifact_path,
            result_ref,
        )
        if not created:
            return await self._load_existing_artifact(
                message,
                input_record=input_record,
                result_ref=result_ref,
                artifact_path=artifact_path,
                log=log,
            )
        log.bind(
            event_name="transcription.result.uploaded",
            stt_handler_ms=(perf_counter() - started_at) * 1000,
            result_json_bytes=artifact_path.stat().st_size,
            word_count=len(artifact.words),
            segment_count=len(artifact.segments),
        ).info("transcription.result.uploaded")
        return artifact, False

    async def _load_existing_artifact(
        self,
        message: TranscribePartMessage,
        *,
        input_record: TranscriptionInput,
        result_ref: str,
        artifact_path: Path,
        log: Any,
    ) -> tuple[TranscriptionArtifact, bool]:
        await self._storage.download_object(result_ref, artifact_path)
        artifact = TranscriptionArtifact.from_bytes(artifact_path.read_bytes())
        if not artifact.matches(
            pipeline_run_id=message.pipeline_run_id,
            audio_part_id=message.audio_part_id,
            part_index=message.part_index,
            start_ms=input_record.start_ms,
            end_ms=input_record.end_ms,
            stt_model_version=message.stt_model_version,
        ):
            raise ValueError("Existing transcription artifact identity mismatch")
        log.bind(
            event_name="transcription.result.reused",
            result_json_bytes=artifact_path.stat().st_size,
        ).info("transcription.result.reused")
        return artifact, True

    async def _handle_failure(
        self,
        context: StageDispatchContext,
        *,
        error: Exception,
        log: Any,
    ) -> StageHandlerResult:
        message = context.message
        assert isinstance(message, TranscribePartMessage)
        retryable = not isinstance(error, ExternalAIAdapterError) or error.retryable
        if retryable and context.read_count < self._max_delivery_attempts:
            raise error
        failure_code = (
            error.code
            if isinstance(error, ExternalAIAdapterError)
            else type(error).__name__
        )
        failed = await self._repository.fail(
            message,
            message_id=context.message_id,
            failure_code=failure_code,
        )
        if not failed:
            log.bind(
                event_name="transcription.discarded",
                discard_reason="stale_during_failure",
            ).info("transcription.discarded")
            return StageHandlerResult("SKIPPED", reason="stale_during_failure")
        return StageHandlerResult("FAILED", failure_code=failure_code)
