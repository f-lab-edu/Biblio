from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class VideoModel(Base):
    __tablename__ = "video"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(Text(), nullable=False)
    input_type: Mapped[str] = mapped_column(Text(), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[str] = mapped_column(Text(), nullable=False, default="PENDING")
    failed_stage: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )


class AssetModel(Base):
    __tablename__ = "asset"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"))
    asset_type: Mapped[str] = mapped_column(String(32))
    storage_path: Mapped[str] = mapped_column(Text())
    start_ms: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer(), nullable=True)


class TranscriptSegmentModel(Base):
    __tablename__ = "transcript_segment"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"))
    segment_index: Mapped[int] = mapped_column(Integer())
    text: Mapped[str] = mapped_column(Text())
    start_ms: Mapped[int] = mapped_column(Integer())
    end_ms: Mapped[int] = mapped_column(Integer())
    stt_model_version: Mapped[str] = mapped_column(String(64))


class ChunkModel(Base):
    __tablename__ = "chunk"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"))
    chunk_index: Mapped[int] = mapped_column(Integer())
    text: Mapped[str] = mapped_column(Text())
    enriched_text: Mapped[str] = mapped_column(Text())
    start_ms: Mapped[int] = mapped_column(Integer())
    end_ms: Mapped[int] = mapped_column(Integer())
    keyframe_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("asset.id"), nullable=True)
    chunking_version: Mapped[str] = mapped_column(String(32))
    stt_model_version: Mapped[str] = mapped_column(String(64))
    embedding_model_version: Mapped[str] = mapped_column(String(64))
    visual_caption: Mapped[str] = mapped_column(Text(), default="")
    ocr_text: Mapped[str] = mapped_column(Text(), default="")
    scene_tags: Mapped[str] = mapped_column(Text(), default="")


class VectorIndexEntryModel(Base):
    __tablename__ = "vector_index_entry"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("chunk.id"))
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"))
    embedding: Mapped[list[float]] = mapped_column(JSON())
    embedding_model_version: Mapped[str] = mapped_column(String(64))
