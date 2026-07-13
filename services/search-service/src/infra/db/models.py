"""Read-only SQLAlchemy models for Search Service.

These mirror the DDL owned by Core API / Pipeline Worker.
Search Service never creates or modifies these tables.
"""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


SNAPSHOT_JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class ProjectModel(Base):
    __tablename__ = "project"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    search_serving_state: Mapped[str] = mapped_column(Text(), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default="ACTIVE"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VideoModel(Base):
    __tablename__ = "video"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChunkModel(Base):
    __tablename__ = "chunk"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer(), nullable=False)
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    enriched_text: Mapped[str] = mapped_column(Text(), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer(), nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer(), nullable=False)


class SearchResponseSnapshotModel(Base):
    __tablename__ = "search_response_snapshot"

    req_id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("project.id"), nullable=False)
    query_text: Mapped[str] = mapped_column(Text(), nullable=False)
    topk_chunk_ids: Mapped[list[str]] = mapped_column(SNAPSHOT_JSON_TYPE, nullable=False)
    used_chunk_ids: Mapped[list[str]] = mapped_column(SNAPSHOT_JSON_TYPE, nullable=False)
    active_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    active_index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    served_vector_paths: Mapped[list[dict[str, str]]] = mapped_column(
        SNAPSHOT_JSON_TYPE, nullable=False
    )
    project_serving_state: Mapped[str] = mapped_column(Text(), nullable=False)
    scope_notice: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SearchConversationModel(Base):
    __tablename__ = "search_conversation"

    req_id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("project.id"), nullable=False)
    query: Mapped[str] = mapped_column(Text(), nullable=False)
    answer: Mapped[str] = mapped_column(Text(), nullable=False)
    sources: Mapped[list[dict[str, object]]] = mapped_column(
        SNAPSHOT_JSON_TYPE, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelReleaseModel(Base):
    __tablename__ = "model_release"
    __table_args__ = (
        CheckConstraint("singleton_key = 1", name="ck_model_release_singleton_key"),
    )

    singleton_key: Mapped[int] = mapped_column(Integer(), primary_key=True, default=1)
    release_status: Mapped[str] = mapped_column(Text(), nullable=False)
    active_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    active_index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    previous_model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    previous_index_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate_model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate_index_name: Mapped[str | None] = mapped_column(String(128), nullable=True)


class VectorIndexEntryModel(Base):
    __tablename__ = "vector_index_entry"

    index_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("chunk.id"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project.id"), nullable=True
    )
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"), nullable=False)
    embedding_vector: Mapped[list[float]] = mapped_column(
        Vector(), nullable=False
    )
    embedding_model_version: Mapped[str] = mapped_column(String(64), nullable=False)
