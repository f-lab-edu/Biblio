from dataclasses import dataclass

from loguru import logger

from src.infra.db.video_repository import VideoRepository
from src.services.failure_classifier import classify_pipeline_failure
from src.services.pipeline_errors import DeleteRequested, PipelineFailure
from src.services.pipeline_orchestrator import PipelineOrchestrator
from src.usecases.delete_video import DeleteVideoUseCase


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
        stt_model_version: str,
        embedding_model_version: str,
    ) -> None:
        self._video_repository = video_repository
        self._orchestrator = orchestrator
        self._delete_video_use_case = delete_video_use_case
        self._stt_model_version = stt_model_version
        self._embedding_model_version = embedding_model_version

    async def execute(
        self,
        *,
        video_id: str,
        trace_id: str,
    ) -> ProcessVideoResult:
        state = await self._video_repository.load_pipeline_state(
            video_id,
            stt_model_version=self._stt_model_version,
            embedding_model_version=self._embedding_model_version,
        )
        video = state.video
        if video is None:
            return ProcessVideoResult(action="skip")
        if video.status == "DELETING":
            await self._delete_video_use_case.execute(video_ids=[video_id], trace_id=trace_id)
            return ProcessVideoResult(action="deleted")
        if video.status == "READY" and state.has_current_outputs:
            return ProcessVideoResult(action="skip")

        # 처리권한 확보
        keep_ready_status = video.status == "READY"
        # READY 재처리는 상태를 유지한 채 처리 권한만 확보
        claimed = await self._video_repository.claim_processing(
            video_id,
            keep_ready_status=keep_ready_status,
        )
        if not claimed:
            refreshed = await self._video_repository.get_video(video_id)
            if refreshed is not None and refreshed.status == "DELETING": # 삭제
                await self._delete_video_use_case.execute(video_ids=[video_id], trace_id=trace_id)
                return ProcessVideoResult(action="deleted")
            return ProcessVideoResult(action="skip")
            
        # 오케스트레이터 호출
        try:
            await self._orchestrator.run(
                video=video,
                trace_id=trace_id,
                state=state,
                keep_ready_status=keep_ready_status,
            )
            return ProcessVideoResult(action="processed")
        
        # 예외 발생시 failed stage 분류
        except DeleteRequested:
            await self._video_repository.release_processing_claim(video_id)
            await self._delete_video_use_case.execute(video_ids=[video_id], trace_id=trace_id)
            return ProcessVideoResult(action="deleted")
        except Exception as exc:
            return await self._fail_or_delete(
                video_id=video_id,
                trace_id=trace_id,
                failure=classify_pipeline_failure(exc),
            )

    async def _fail_or_delete(
        self,
        *,
        video_id: str,
        trace_id: str,
        failure: PipelineFailure,
    ) -> ProcessVideoResult:
        marked_failed = await self._video_repository.set_failed(
            video_id,
            failed_stage=failure.failed_stage,
            failure_code=failure.failure_code,
            failure_trace_id=trace_id,
        )
        if marked_failed:
            self._log_terminal_failure(
                video_id=video_id,
                trace_id=trace_id,
                failure=failure,
            )
            return ProcessVideoResult(action="failed", failed_stage=failure.failed_stage)
        await self._delete_video_use_case.execute(video_ids=[video_id], trace_id=trace_id)
        return ProcessVideoResult(action="deleted")

    @staticmethod
    def _log_terminal_failure(
        *,
        video_id: str,
        trace_id: str,
        failure: PipelineFailure,
    ) -> None:
        failure_logger = logger.bind(
            video_id=video_id,
            trace_id=trace_id,
            failed_stage=failure.failed_stage,
            failure_code=failure.failure_code,
        )
        if failure.provider is not None:
            failure_logger = failure_logger.bind(provider=failure.provider)
            failure_logger.opt(exception=failure.exception).error(
                "video.processing.failed failed_stage={} failure_code={} provider={}",
                failure.failed_stage,
                failure.failure_code,
                failure.provider,
            )
            return
        failure_logger.opt(exception=failure.exception).error(
            "video.processing.failed failed_stage={} failure_code={}",
            failure.failed_stage,
            failure.failure_code,
        )
