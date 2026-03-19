from dataclasses import dataclass

from adapters.ai.google_stt_adapter import ExternalAIAdapterError
from adapters.db.video_repository import VideoRepository
from services.pipeline_orchestrator import DeleteRequested, PipelineOrchestrator
from usecases.delete_video import DeleteVideoUseCase


FAILED_STAGE_BY_CODE = {
    "TIMEOUT": "EMBEDDING",
    "UNAVAILABLE": "EMBEDDING",
    "RATE_LIMITED": "EMBEDDING",
    "INTERNAL_ERROR": "VECTOR_UPSERT",
}


@dataclass(slots=True)
class ProcessVideoResult:
    action: str
    failed_stage: str | None = None


class ProcessVideoUseCase:
    def __init__(
        self,
        *,
        video_repository: VideoRepository,
        orchestrator: PipelineOrchestrator,
        delete_video_use_case: DeleteVideoUseCase,
    ) -> None:
        self._video_repository = video_repository
        self._orchestrator = orchestrator
        self._delete_video_use_case = delete_video_use_case

    async def execute(
        self,
        *,
        video_id: str,
        trace_id: str,
        stt_model_version: str,
        embedding_model_version: str,
    ) -> ProcessVideoResult:
        state = self._video_repository.load_pipeline_state(
            video_id,
            stt_model_version=stt_model_version,
            embedding_model_version=embedding_model_version,
        )
        video = state.video
        if video is None:
            return ProcessVideoResult(action="skip")
        if video.status == "DELETING":
            await self._delete_video_use_case.execute(video_id=video_id, trace_id=trace_id)
            return ProcessVideoResult(action="deleted")
        if video.status == "READY" and state.has_current_outputs:
            return ProcessVideoResult(action="skip")

        keep_ready_status = video.status == "READY"
        if not keep_ready_status:
            claimed = self._video_repository.claim_processing(video_id)
            if not claimed:
                refreshed = self._video_repository.get_video(video_id)
                if refreshed is not None and refreshed.status == "DELETING":
                    await self._delete_video_use_case.execute(video_id=video_id, trace_id=trace_id)
                    return ProcessVideoResult(action="deleted")
                return ProcessVideoResult(action="skip")

        try:
            await self._orchestrator.run(
                video=video,
                trace_id=trace_id,
                state=state,
                target_stt_model_version=stt_model_version,
                keep_ready_status=keep_ready_status,
            )
            return ProcessVideoResult(action="processed")
        except DeleteRequested:
            await self._delete_video_use_case.execute(video_id=video_id, trace_id=trace_id)
            return ProcessVideoResult(action="deleted")
        except FileNotFoundError as exc:
            self._video_repository.set_failed(video_id, failed_stage="DOWNLOAD", error_message=str(exc))
            return ProcessVideoResult(action="failed", failed_stage="DOWNLOAD")
        except ExternalAIAdapterError as exc:
            if exc.provider == "google-stt":
                failed_stage = "STT"
            else:
                failed_stage = FAILED_STAGE_BY_CODE.get(exc.code, "VECTOR_UPSERT")
            self._video_repository.set_failed(video_id, failed_stage=failed_stage, error_message=exc.message)
            return ProcessVideoResult(action="failed", failed_stage=failed_stage)
        except Exception as exc:
            self._video_repository.set_failed(video_id, failed_stage="VECTOR_UPSERT", error_message=str(exc))
            return ProcessVideoResult(action="failed", failed_stage="VECTOR_UPSERT")
