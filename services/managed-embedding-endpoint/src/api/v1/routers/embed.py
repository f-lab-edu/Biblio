import asyncio
import time

from fastapi import APIRouter, Request

from src.core.admission_control import (
    AdmissionController,
    EmbeddingWorkload,
    SlotLease,
)
from src.core.model_state import ModelState
from src.middlewares.error_handler import (
    InvalidArgumentError,
    ServiceUnavailableError,
)
from src.observability.logging import info, warning
from src.schemas.embed_dto import EmbedRequest, EmbedResponse, HealthResponse
from src.services.inference_service import InferenceService

router = APIRouter()
WORKLOAD_HEADER = "X-Embedding-Workload"


@router.post("/embed")
async def embed(request: Request, body: EmbedRequest) -> EmbedResponse:
    inference_service = _inference_service(request)
    workload = _parse_workload(request.headers.get(WORKLOAD_HEADER))
    _validate_workload_request(workload, body)

    payload_size = len(await request.body())
    trace_id = request.state.trace_id
    inference_service.validate_request(body.texts, payload_size, body.model_version)
    controller: AdmissionController = request.app.state.admission_controller

    if workload is EmbeddingWorkload.LEGACY:
        embeddings = await _execute_legacy(
            controller, inference_service, body, payload_size, trace_id
        )
    else:
        embeddings = await _execute_managed(
            controller, workload, inference_service, body, payload_size, trace_id
        )
    return EmbedResponse(embeddings=embeddings)


def _inference_service(request: Request) -> InferenceService:
    inference_service: InferenceService | None = getattr(
        request.app.state, "inference_service", None
    )
    if inference_service is None:
        raise ServiceUnavailableError("Model is not ready.")
    return inference_service


def _parse_workload(header_value: str | None) -> EmbeddingWorkload:
    if header_value is None:
        return EmbeddingWorkload.LEGACY
    try:
        return EmbeddingWorkload(header_value)
    except ValueError as exc:
        raise InvalidArgumentError(
            f"Unsupported {WORKLOAD_HEADER}: {header_value}."
        ) from exc


def _validate_workload_request(
    workload: EmbeddingWorkload,
    body: EmbedRequest,
) -> None:
    if workload is EmbeddingWorkload.SEARCH and len(body.texts) != 1:
        raise InvalidArgumentError("Search embedding requests require exactly one text.")


async def _execute_legacy(
    controller: AdmissionController,
    inference_service: InferenceService,
    body: EmbedRequest,
    payload_size: int,
    trace_id: str,
) -> list[list[float]]:
    slot_lease = await controller.try_acquire_legacy()
    if slot_lease is None:
        await _log_admission(
            controller,
            EmbeddingWorkload.LEGACY,
            "slot_busy",
            trace_id,
            body=body,
        )
        raise ServiceUnavailableError(
            "Server is at maximum concurrency. Try again later."
        )
    return await _execute_with_slot(
        controller,
        slot_lease,
        EmbeddingWorkload.LEGACY,
        inference_service,
        body,
        payload_size,
        trace_id,
    )


async def _execute_managed(
    controller: AdmissionController,
    workload: EmbeddingWorkload,
    inference_service: InferenceService,
    body: EmbedRequest,
    payload_size: int,
    trace_id: str,
) -> list[list[float]]:
    request_lease = await controller.try_acquire_request(workload)
    if request_lease is None:
        await _log_admission(controller, workload, "queue_full", trace_id, body=body)
        raise ServiceUnavailableError(
            "Embedding request queue is full. Try again later."
        )
    async with request_lease:
        slot_wait_started_at = time.monotonic()
        try:
            slot_lease = await controller.acquire_slot(workload)
        except TimeoutError as exc:
            await _log_admission(
                controller,
                workload,
                "queue_timeout",
                trace_id,
                queue_wait_ms=(time.monotonic() - slot_wait_started_at) * 1000,
                body=body,
            )
            raise ServiceUnavailableError(
                "Timed out waiting for an embedding slot. Try again later."
            ) from exc
        return await _execute_with_slot(
            controller,
            slot_lease,
            workload,
            inference_service,
            body,
            payload_size,
            trace_id,
        )


async def _execute_with_slot(
    controller: AdmissionController,
    slot_lease: SlotLease,
    workload: EmbeddingWorkload,
    inference_service: InferenceService,
    body: EmbedRequest,
    payload_size: int,
    trace_id: str,
) -> list[list[float]]:
    started_at = time.monotonic()
    async with slot_lease:
        embeddings = await _run_inference(
            inference_service, body, payload_size, trace_id
        )
    inference_duration_ms = (time.monotonic() - started_at) * 1000
    await _log_admission(
        controller,
        workload,
        "granted",
        trace_id,
        queue_wait_ms=slot_lease.queue_wait_ms,
        body=body,
        inference_duration_ms=inference_duration_ms,
    )
    return embeddings


async def _run_inference(
    inference_service: InferenceService,
    body: EmbedRequest,
    payload_size: int,
    trace_id: str,
) -> list[list[float]]:
    task = asyncio.create_task(
        asyncio.to_thread(
            inference_service.embed,
            body.texts,
            payload_size,
            body.model_version,
            trace_id,
        )
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception as exc:
            warning(
                "embedding.cancelled_inference_failed",
                error=str(exc),
                trace_id=trace_id,
            )
        raise


async def _log_admission(
    controller: AdmissionController,
    workload: EmbeddingWorkload,
    admission_result: str,
    trace_id: str,
    *,
    queue_wait_ms: float = 0.0,
    body: EmbedRequest | None = None,
    inference_duration_ms: float | None = None,
) -> None:
    snapshot = await controller.snapshot()
    fields = {
        "lane": workload.value,
        "queue_wait_ms": round(queue_wait_ms, 1),
        "search_queue_depth": snapshot.search_queue_depth,
        "video_preprocess_queue_depth": snapshot.video_preprocess_queue_depth,
        "admission_result": admission_result,
        "batch_size": len(body.texts) if body else None,
        "max_text_chars": max(map(len, body.texts), default=0) if body else None,
        "inference_duration_ms": (
            round(inference_duration_ms, 1)
            if inference_duration_ms is not None
            else None
        ),
        "model_version": body.model_version if body else None,
        "trace_id": trace_id,
    }
    if admission_result == "granted":
        info("embedding.admission", **fields)
    else:
        warning("embedding.admission", **fields)


@router.get("/health")
async def health(request: Request) -> HealthResponse:
    model_state: ModelState = request.app.state.model_state
    if not model_state.ready:
        raise ServiceUnavailableError("Model is not ready.")
    return HealthResponse(
        status="ok",
        ready_model_versions=model_state.ready_model_versions,
    )
