from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from src.common.logging import error as log_error
from src.common.logging import info as log_info
from src.common.logging import warning as log_warning
from src.infra.broker import BrokerClient, BrokerPublishError, build_message
from src.infra.db.admin_repository import AdminRepository, ProjectProjection
from src.infra.db.video_repository import VideoRepository
from src.infra.storage import MAX_UPLOAD_SIZE_BYTES, SignedUrlRequest, StorageClient
from src.middlewares.error_handler import (
    ApiError,
    ConflictError,
    ForbiddenError,
    InvalidArgumentError,
    NotFoundError,
)
from src.models.video import Video
from src.schemas.video_dto import (
    BatchDeleteVideosResponse,
    DeleteVideoResponse,
    ExternalUrlVideoCreateResponse,
    LocalFileVideoCreateRequest,
    LocalFileVideoCreateResponse,
    PlaybackUrlResponse,
    RetryVideoResponse,
    VideoCompleteRequest,
    VideoCompleteResponse,
    VideoCreateRequest,
    VideoListResponse,
    VideoMutationRequest,
    VideoResponse,
)


@dataclass(frozen=True, slots=True)
class VideoActionResult:
    payload: Any
    status_code: int


class VideoService:
    def __init__(
        self,
        *,
        db_session_factory: Any,
        storage_client: StorageClient | None,
        broker_client: BrokerClient | None,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._storage_client = storage_client
        self._broker_client = broker_client

    async def create_video(
        self,
        payload: VideoCreateRequest,
        *,
        requester_user_id: UUID,
        trace_id: UUID,
        project_id: UUID | None = None,
    ) -> VideoActionResult:
        self._ensure_db_session_factory()
        self._ensure_storage_client()

        video_id = uuid4()
        storage_path = self._build_storage_path(
            requester_user_id=requester_user_id,
            video_id=video_id,
            payload=payload,
        )
        source_url = str(payload.source_url) if hasattr(payload, "source_url") else None

        video = Video(
            id=video_id,
            user_id=requester_user_id,
            project_id=project_id,
            title=payload.title,
            category=payload.category,
            input_type=payload.input_type,
            source_url=source_url,
            storage_path=storage_path,
            status="PENDING",
        )

        async with self._db_session_factory() as session:
            if project_id is not None:
                await self._ensure_project_accepts_ingest(
                    session,
                    project_id=project_id,
                    requester_user_id=requester_user_id,
                )
            repository = VideoRepository(session)
            await repository.add(video)
            await session.commit()

        if isinstance(payload, LocalFileVideoCreateRequest):
            signed_url = self._generate_signed_url(
                SignedUrlRequest(
                    object_name=storage_path,
                    operation="upload",
                    max_size_bytes=MAX_UPLOAD_SIZE_BYTES,
                )
            )
            return VideoActionResult(
                payload=LocalFileVideoCreateResponse(
                    video_id=video_id,
                    status="PENDING",
                    signed_url=signed_url.url,
                    expires_at=signed_url.expires_at,
                ),
                status_code=201,
            )

        self._ensure_broker_client()
        await self._publish_message("PREPROCESS_REQUEST", video_ids=[video_id], trace_id=trace_id)
        return VideoActionResult(
            payload=ExternalUrlVideoCreateResponse(
                video_id=video_id,
                status="PENDING",
            ),
            status_code=202,
        )

    async def complete_video(
        self,
        video_id: UUID,
        payload: VideoCompleteRequest,
        *,
        requester_user_id: UUID,
        trace_id: UUID,
    ) -> VideoActionResult:
        del payload
        self._ensure_db_session_factory()
        self._ensure_storage_client()

        async with self._db_session_factory() as session:
            _, video = await self._get_video_for_requester(
                session,
                video_id=video_id,
                requester_user_id=requester_user_id,
            )

            if video.input_type != "LOCAL_FILE":
                raise InvalidArgumentError("Upload completion is only supported for LOCAL_FILE videos.")
            if video.status in {"UPLOADED", "PROCESSING", "READY"}:
                return VideoActionResult(
                    payload=VideoCompleteResponse(video_id=video.id, status=video.status),
                    status_code=200,
                )
            if video.status != "PENDING":
                raise ConflictError("Video upload cannot be completed from the current state.")

            object_name = self._require_storage_path(video)
            metadata = self._storage_client.get_blob_metadata(object_name)
            if not metadata.exists:
                raise InvalidArgumentError("Uploaded object was not found in storage.")
            if metadata.size_bytes is None:
                raise ApiError("Uploaded object metadata is incomplete.")
            if metadata.size_bytes > MAX_UPLOAD_SIZE_BYTES:
                raise InvalidArgumentError("Uploaded object exceeds the 2GB size limit.")

            video.status = "UPLOADED"
            await session.commit()

        self._ensure_broker_client()
        await self._publish_message("PREPROCESS_REQUEST", video_ids=[video_id], trace_id=trace_id)
        return VideoActionResult(
            payload=VideoCompleteResponse(video_id=video_id, status="UPLOADED"),
            status_code=202,
        )

    async def list_videos(
        self,
        *,
        requester_user_id: UUID,
        project_id: UUID | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> VideoListResponse:
        self._ensure_db_session_factory()

        async with self._db_session_factory() as session:
            if project_id is not None:
                await self._ensure_project_owned(
                    session,
                    project_id=project_id,
                    requester_user_id=requester_user_id,
                )
            repository = VideoRepository(session)
            page = await repository.list_for_user(
                requester_user_id,
                project_id=project_id,
                limit=limit,
                cursor=cursor,
            )

        return VideoListResponse(
            items=[self._to_video_response(video) for video in page.items],
            next_cursor=page.next_cursor,
        )

    async def get_video(
        self,
        video_id: UUID,
        *,
        requester_user_id: UUID,
    ) -> VideoResponse:
        self._ensure_db_session_factory()

        async with self._db_session_factory() as session:
            _, video = await self._get_video_for_requester(
                session,
                video_id=video_id,
                requester_user_id=requester_user_id,
            )
            return self._to_video_response(video)

    async def update_video(
        self,
        video_id: UUID,
        payload: VideoMutationRequest,
        *,
        requester_user_id: UUID,
    ) -> VideoResponse:
        self._ensure_db_session_factory()

        async with self._db_session_factory() as session:
            _, video = await self._get_video_for_requester(
                session,
                video_id=video_id,
                requester_user_id=requester_user_id,
            )
            if video.status == "DELETING":
                raise ConflictError("Deleting videos cannot be modified.")

            if payload.title is not None:
                video.title = payload.title
            if payload.category is not None:
                video.category = payload.category

            await session.commit()
            await session.refresh(video)
            return self._to_video_response(video)

    async def delete_video(
        self,
        video_id: UUID,
        *,
        requester_user_id: UUID,
        trace_id: UUID,
    ) -> VideoActionResult:
        result = await self.delete_videos(
            [video_id],
            requester_user_id=requester_user_id,
            trace_id=trace_id,
        )
        return VideoActionResult(
            payload=DeleteVideoResponse(video_id=video_id, delete_requested=True),
            status_code=result.status_code,
        )

    async def delete_videos(
        self,
        video_ids: list[UUID],
        *,
        requester_user_id: UUID,
        trace_id: UUID,
    ) -> VideoActionResult:
        self._ensure_db_session_factory()
        self._ensure_broker_client()

        unique_video_ids = list(dict.fromkeys(video_ids))
        async with self._db_session_factory() as session:
            repository = VideoRepository(session)
            videos = await repository.list_by_ids_for_user(unique_video_ids, requester_user_id)
            if len(videos) != len(unique_video_ids):
                raise NotFoundError("Video was not found.")
            previous_statuses = {video.id: video.status for video in videos}
            for video in videos:
                if video.status != "DELETING":
                    video.status = "DELETING"
            await session.commit()

        try:
            await self._publish_message("DELETE_REQUEST", video_ids=unique_video_ids, trace_id=trace_id)
        except ApiError:
            await self._restore_video_statuses(previous_statuses)
            raise
        return VideoActionResult(
            payload=BatchDeleteVideosResponse(video_ids=unique_video_ids, delete_requested=True),
            status_code=202,
        )

    async def retry_video(
        self,
        video_id: UUID,
        *,
        requester_user_id: UUID,
        trace_id: UUID,
    ) -> VideoActionResult:
        self._ensure_db_session_factory()

        async with self._db_session_factory() as session:
            _, video = await self._get_video_for_requester(
                session,
                video_id=video_id,
                requester_user_id=requester_user_id,
            )
            if video.status != "FAILED":
                raise ConflictError("Only failed videos can be retried.")

            video.status = "PENDING"
            await session.commit()

        self._ensure_broker_client()
        await self._publish_message("PREPROCESS_REQUEST", video_ids=[video_id], trace_id=trace_id)
        return VideoActionResult(
            payload=RetryVideoResponse(video_id=video_id, status="PENDING"),
            status_code=202,
        )

    async def issue_playback_url(
        self,
        video_id: UUID,
        *,
        requester_user_id: UUID,
    ) -> PlaybackUrlResponse:
        self._ensure_db_session_factory()
        self._ensure_storage_client()

        async with self._db_session_factory() as session:
            _, video = await self._get_video_for_requester(
                session,
                video_id=video_id,
                requester_user_id=requester_user_id,
            )

        if video.status != "READY":
            raise ConflictError("Playback URL can only be issued for READY videos.")

        signed_url = self._generate_signed_url(
            SignedUrlRequest(
                object_name=self._require_storage_path(video),
                operation="download",
            )
        )
        return PlaybackUrlResponse(
            signed_url=signed_url.url,
            expires_at=signed_url.expires_at,
        )

    @staticmethod
    def _build_storage_path(
        *,
        requester_user_id: UUID,
        video_id: UUID,
        payload: VideoCreateRequest,
    ) -> str:
        base = f"videos/{requester_user_id}/{video_id}/original"
        if isinstance(payload, LocalFileVideoCreateRequest):
            return f"{base}{payload.extension}"
        return base

    def _ensure_db_session_factory(self) -> None:
        if self._db_session_factory is None:
            raise ApiError("Database session factory is not configured.")

    def _ensure_storage_client(self) -> None:
        if self._storage_client is None:
            raise ApiError("Storage client is not configured.")

    def _ensure_broker_client(self) -> None:
        if self._broker_client is None:
            raise ApiError("Broker client is not configured.")

    async def _get_video_for_requester(
        self,
        session: Any,
        *,
        video_id: UUID,
        requester_user_id: UUID,
    ) -> tuple[VideoRepository, Video]:
        repository = VideoRepository(session)
        video = await repository.get_by_id(video_id)
        if video is None:
            raise NotFoundError("Video was not found.")
        if video.user_id != requester_user_id:
            raise ForbiddenError("You do not have access to this video.")
        return repository, video

    @staticmethod
    async def _ensure_project_accepts_ingest(
        session: Any,
        *,
        project_id: UUID,
        requester_user_id: UUID,
    ) -> None:
        project = await VideoService._ensure_project_owned(
            session,
            project_id=project_id,
            requester_user_id=requester_user_id,
        )
        if project.search_serving_state == "ROLLBACK_EXCLUDED":
            raise ConflictError("Project is in rollback recovery; new video ingest is temporarily blocked.")
        if project.lifecycle_state == "DELETING":
            raise ConflictError("Project is being deleted; new video ingest is blocked.")

    @staticmethod
    async def _ensure_project_owned(
        session: Any,
        *,
        project_id: UUID,
        requester_user_id: UUID,
    ) -> ProjectProjection:
        project = await AdminRepository(session).get_project(project_id)
        if project is None or project.user_id != requester_user_id:
            raise NotFoundError("Project not found.")
        return project

    def _generate_signed_url(self, request: SignedUrlRequest):
        try:
            result = self._storage_client.generate_signed_url(request)
            log_info(
                "storage.signed_url.generated",
                object_name=request.object_name,
                operation=request.operation,
            )
            return result
        except Exception as exc:
            log_error(
                "storage.signed_url.failed",
                object_name=request.object_name,
                operation=request.operation,
                error_type=type(exc).__name__,
                # 임시 디버깅용: 서명 URL 실패 원인을 추적한다.
                # repr(exc)에는 버킷 이름·경로·계정 같은 내부 정보가 섞일 수 있다.
                # 배포 안정화 후에는 error_type만 남기고 이 줄을 제거한다.
                error_detail=repr(exc),
            )
            raise ApiError("Failed to generate a signed URL.") from exc

    async def _publish_message(
        self,
        message_type: str,
        *,
        video_ids: list[UUID],
        trace_id: UUID,
    ) -> None:
        video_id_log = ",".join(str(video_id) for video_id in video_ids)
        for attempt_number in range(1, 4):
            message = build_message(
                message_type,
                video_ids=video_ids,
                trace_id=trace_id,
                attempt=attempt_number,
            )
            try:
                await self._broker_client.publish(message)
                log_info(
                    "mq.publish.succeeded",
                    message_type=message_type,
                    attempt=attempt_number,
                    trace_id=str(trace_id),
                    video_id=video_id_log,
                )
                return
            except BrokerPublishError as exc:
                if attempt_number == 3:
                    log_error(
                        "mq.publish.failed",
                        message_type=message_type,
                        attempt=attempt_number,
                        trace_id=str(trace_id),
                        video_id=video_id_log,
                    )
                    raise ApiError("Message broker publish failed after retries.") from exc

                log_warning(
                    "mq.publish.retry",
                    message_type=message_type,
                    attempt=attempt_number,
                    trace_id=str(trace_id),
                    video_id=video_id_log,
                )

    async def _restore_video_statuses(self, statuses: dict[UUID, str]) -> None:
        async with self._db_session_factory() as session:
            repository = VideoRepository(session)
            for video_id, status in statuses.items():
                video = await repository.get_by_id(video_id)
                if video is not None:
                    video.status = status
            await session.commit()

    @staticmethod
    def _require_storage_path(video: Video) -> str:
        if video.storage_path is None:
            raise ApiError("Video storage path is not configured.")
        return video.storage_path

    @staticmethod
    def _to_video_response(video: Video) -> VideoResponse:
        return VideoResponse(
            video_id=video.id,
            status=video.status,
            title=video.title,
            category=video.category,
            input_type=video.input_type,
            source_url=video.source_url,
            failed_stage=video.failed_stage,
            storage_path=video.storage_path,
            created_at=video.created_at,
            updated_at=video.updated_at,
        )
