from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, desc, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Video(Base):
    __tablename__ = "video"
    __table_args__ = (
        CheckConstraint(
            "category IN ('GENERAL','IT','MEDICAL','LEGAL')",
            name="ck_video_category",
        ),
        CheckConstraint(
            "input_type IN ('LOCAL_FILE','EXTERNAL_URL')",
            name="ck_video_input_type",
        ),
        CheckConstraint(
            "status IN ('PENDING','UPLOADED','PROCESSING','READY','FAILED','DELETING')",
            name="ck_video_status",
        ),
        CheckConstraint(
            "failed_stage IS NULL OR failed_stage IN "
            "('DOWNLOAD','EXTRACT','STT','CHUNKING','EMBEDDING','VECTOR_UPSERT',"
            "'NORMALIZE_VIDEO','TRANSCRIBE_PART','ASSEMBLE_CHUNKS',"
            "'ENRICH_CHUNK','EMBED_BATCH')",
            name="ck_video_failed_stage",
        ),
        Index("idx_video_user_created", "user_id", desc("created_at"), desc("id")),
        Index("idx_video_user_status", "user_id", "status"),
        Index("idx_video_project_id", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project.id"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    input_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="PENDING",
        server_default=text("'PENDING'"),
    )
    failed_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_trace_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
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
