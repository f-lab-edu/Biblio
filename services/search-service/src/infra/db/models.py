"""Read-only SQLAlchemy models for Search Service.

These mirror the DDL owned by Core API / Pipeline Worker.
Search Service never creates or modifies these tables.
"""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class VideoModel(Base):
    __tablename__ = "video"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
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


class VectorIndexEntryModel(Base):
    __tablename__ = "vector_index_entry"

    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("chunk.id"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("video.id"), nullable=False)
    embedding_vector: Mapped[list[float]] = mapped_column(
        Vector(), nullable=False
    )
    embedding_model_version: Mapped[str] = mapped_column(String(64), nullable=False)
