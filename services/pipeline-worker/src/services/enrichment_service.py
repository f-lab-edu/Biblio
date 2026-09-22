from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID

from loguru import logger

from src.infra.ai.google_stt_adapter import ExternalAIAdapterError
from src.infra.ai.vision_adapter import VisionAdapter, VisionResult, extract_with_fallback
from src.infra.queue.consumer import StageDispatchContext, StageHandlerResult
from src.infra.storage.client import StorageClient
from src.schemas.messages import EnrichChunkMessage
from src.services.text_normalizer import normalize_enriched_text
from src.telemetry.pipeline_events import work_log_context_from_message


@dataclass(frozen=True, slots=True)
class EnrichmentInput:
    text: str
    start_ms: int
    end_ms: int
    frame_ref: str


@dataclass(frozen=True, slots=True)
class EnrichmentCommitDecision:
    accepted: bool
    reason: str


class EnrichmentRepository(Protocol):
    async def load_input(
        self,
        message: EnrichChunkMessage,
        *,
        message_id: int,
    ) -> EnrichmentInput | None: ...

    async def complete(
        self,
        message: EnrichChunkMessage,
        *,
        message_id: int,
        keyframe_ref: str,
        vision_result: VisionResult,
        enriched_text: str,
    ) -> EnrichmentCommitDecision: ...

    async def fail(
        self,
        message: EnrichChunkMessage,
        *,
        message_id: int,
        failure_code: str,
    ) -> bool: ...


def keyframe_storage_path(
    video_id: UUID,
    pipeline_run_id: UUID,
    chunk_index: int,
) -> str:
    return (
        f"artifacts/{video_id}/pipeline-runs/{pipeline_run_id}/"
        f"keyframes/chunk-{chunk_index:06d}.jpg"
    )


class EnrichmentService:
    def __init__(
        self,
        *,
        repository: EnrichmentRepository,
        storage: StorageClient,
        vision: VisionAdapter,
        vision_max_retries: int,
        max_delivery_attempts: int,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._vision = vision
        self._vision_max_retries = vision_max_retries
        self._max_delivery_attempts = max_delivery_attempts

    async def execute(self, context: StageDispatchContext) -> StageHandlerResult:
        message = context.message
        if not isinstance(message, EnrichChunkMessage):
            raise TypeError("ENRICH_CHUNK handler received a different message type")
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
            log.info("enrichment.skipped reason=stale_after_claim")
            return StageHandlerResult("SKIPPED", reason="stale_after_claim")

        keyframe_ref = keyframe_storage_path(
            message.video_id,
            message.pipeline_run_id,
            message.chunk_index,
        )
        with TemporaryDirectory(prefix="biblio-enrichment-") as directory:
            candidate_path = Path(directory) / "candidate.jpg"
            try:
                reused = await self._prepare_keyframe(
                    input_record,
                    candidate_path=candidate_path,
                    keyframe_ref=keyframe_ref,
                    log=log,
                )
                vision_result = await self._extract_vision(
                    candidate_path,
                    trace_id=str(message.trace_id),
                    log=log,
                )
                enriched_text = self._build_enriched_text(
                    input_record.text,
                    vision_result,
                )
            except Exception as error:
                return await self._handle_failure(context, error=error, log=log)

            decision = await self._repository.complete(
                message,
                message_id=context.message_id,
                keyframe_ref=keyframe_ref,
                vision_result=vision_result,
                enriched_text=enriched_text,
            )

        if decision.accepted:
            log.bind(
                event_name="enrichment.completed",
                keyframe_reused=reused,
            ).info("enrichment.completed")
            return StageHandlerResult("SUCCEEDED", reused=reused)

        if not reused and decision.reason in {
            "video_not_found",
            "video_deleting",
            "inactive_pipeline_run",
            "work_not_found",
            "identity_mismatch",
            "terminal_or_not_running",
        }:
            await self._storage.delete_object(keyframe_ref)
        log.bind(
            event_name="enrichment.discarded",
            discard_reason=decision.reason,
            keyframe_reused=reused,
        ).info("enrichment.discarded")
        return StageHandlerResult("SKIPPED", reason=decision.reason)

    async def _prepare_keyframe(
        self,
        input_record: EnrichmentInput,
        *,
        candidate_path: Path,
        keyframe_ref: str,
        log: Any,
    ) -> bool:
        started_at = perf_counter()
        await self._storage.download_object(input_record.frame_ref, candidate_path)
        download_ms = (perf_counter() - started_at) * 1000
        started_at = perf_counter()
        created = await self._storage.upload_object_if_absent(
            candidate_path,
            keyframe_ref,
        )
        log.bind(
            event_name="enrichment.keyframe.prepared",
            gcs_download_ms=download_ms,
            gcs_upload_ms=(perf_counter() - started_at) * 1000,
            keyframe_reused=not created,
        ).info("enrichment.keyframe.prepared")
        return not created

    async def _extract_vision(
        self,
        candidate_path: Path,
        *,
        trace_id: str,
        log: Any,
    ) -> VisionResult:
        started_at = perf_counter()
        try:
            result = await extract_with_fallback(
                self._vision,
                keyframe_path=str(candidate_path),
                trace_id=trace_id,
                max_retries=self._vision_max_retries,
                raise_on_exhaustion=True,
            )
        except Exception:
            log.bind(
                event_name="vision.request.failed",
                vision_request_ms=(perf_counter() - started_at) * 1000,
            ).warning("vision.request.failed")
            raise
        log.bind(
            event_name="vision.request.succeeded",
            vision_request_ms=(perf_counter() - started_at) * 1000,
        ).info("vision.request.succeeded")
        return result

    @staticmethod
    def _build_enriched_text(text: str, result: VisionResult) -> str:
        return normalize_enriched_text(
            " ".join(
                part
                for part in (
                    text,
                    result.visual_caption,
                    result.ocr_text,
                    result.scene_tags,
                )
                if part
            )
        )

    async def _handle_failure(
        self,
        context: StageDispatchContext,
        *,
        error: Exception,
        log: Any,
    ) -> StageHandlerResult:
        message = context.message
        assert isinstance(message, EnrichChunkMessage)
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
                event_name="enrichment.discarded",
                discard_reason="stale_during_failure",
            ).info("enrichment.discarded")
            return StageHandlerResult("SKIPPED", reason="stale_during_failure")
        return StageHandlerResult("FAILED", failure_code=failure_code)
