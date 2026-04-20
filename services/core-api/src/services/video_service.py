from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from src.common.logging import error as log_error
from src.common.logging import info as log_info
from src.common.logging import warning as log_warning
from src.infra.broker import BrokerClient, BrokerPublishError, build_message
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
            title=payload.title,
            category=payload.category,
            input_type=payload.input_type,
            source_url=source_url,
            storage_path=storage_path,
            status="PENDING",
        )

        async with self._db_session_factory() as session:
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
        await self._publish_message("PREPROCESS_REQUEST", video_id=video_id, trace_id=trace_id)
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
        await self._publish_message("PREPROCESS_REQUEST", video_id=video_id, trace_id=trace_id)
        return VideoActionResult(
            payload=VideoCompleteResponse(video_id=video_id, status="UPLOADED"),
            status_code=202,
        )

    async def list_videos(
        self,
        *,
        requester_user_id: UUID,
        limit: int = 20,
        cursor: str | None = None,
    ) -> VideoListResponse:
        self._ensure_db_session_factory()

        async with self._db_session_factory() as session:
            repository = VideoRepository(session)
            page = await repository.list_for_user(
                requester_user_id,
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
        self._ensure_db_session_factory()

        async with self._db_session_factory() as session:
            _, video = await self._get_video_for_requester(
                session,
                video_id=video_id,
                requester_user_id=requester_user_id,
            )
            if video.status != "DELETING":
                video.status = "DELETING"
                await session.commit()

        self._ensure_broker_client()
        await self._publish_message("DELETE_REQUEST", video_id=video_id, trace_id=trace_id)
        return VideoActionResult(
            payload=DeleteVideoResponse(video_id=video_id, delete_requested=True),
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
        await self._publish_message("PREPROCESS_REQUEST", video_id=video_id, trace_id=trace_id)
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
        if video.input_type != "LOCAL_FILE":
            raise InvalidArgumentError("Playback URL is only available for LOCAL_FILE videos.")

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
            )
            raise ApiError("Failed to generate a signed URL.") from exc

    async def _publish_message(
        self,
        message_type: str,
        *,
        video_id: UUID,
        trace_id: UUID,
    ) -> None:
        for attempt_number in range(1, 4):
            message = build_message(
                message_type,
                video_id=video_id,
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
                    video_id=str(video_id),
                )
                return
            except BrokerPublishError as exc:
                if attempt_number == 3:
                    log_error(
                        "mq.publish.failed",
                        message_type=message_type,
                        attempt=attempt_number,
                        trace_id=str(trace_id),
                        video_id=str(video_id),
                    )
                    raise ApiError("Message broker publish failed after retries.") from exc

                log_warning(
                    "mq.publish.retry",
                    message_type=message_type,
                    attempt=attempt_number,
                    trace_id=str(trace_id),
                    video_id=str(video_id),
                )

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
