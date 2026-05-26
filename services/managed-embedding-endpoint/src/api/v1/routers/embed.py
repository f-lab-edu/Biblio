import asyncio

from fastapi import APIRouter, Request

from src.core.admission_control import AdmissionController
from src.core.model_state import ModelState
from src.middlewares.error_handler import ServiceUnavailableError
from src.schemas.embed_dto import EmbedRequest, EmbedResponse, HealthResponse
from src.services.inference_service import InferenceService

router = APIRouter()


@router.post("/embed")
async def embed(request: Request, body: EmbedRequest) -> EmbedResponse:
    inference_service: InferenceService | None = getattr(
        request.app.state, "inference_service", None
    )
    if inference_service is None:
        raise ServiceUnavailableError("Model is not ready.")
    # admission_controller : 추론 슬롯 빈 자리 여부
    admission_controller: AdmissionController = request.app.state.admission_controller
    if not admission_controller.try_acquire():
        raise ServiceUnavailableError("Server is at maximum concurrency. Try again later.")
    try:
        raw_body = await request.body()
        payload_size = len(raw_body)
        trace_id = request.state.trace_id
        embeddings = await asyncio.to_thread(
            inference_service.embed,
            body.texts,
            payload_size,
            trace_id,
        )
    finally:
        admission_controller.release()
    return EmbedResponse(embeddings=embeddings)


@router.get("/health")
async def health(request: Request) -> HealthResponse:
    model_state: ModelState = request.app.state.model_state
    if not model_state.ready:
        raise ServiceUnavailableError("Model is not ready.")
    return HealthResponse(status="ok", model_version=model_state.model_version)
