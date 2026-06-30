from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.video import Base


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint("role IN ('USER','ADMIN')", name="ck_app_user_role"),
        CheckConstraint("status IN ('ACTIVE','DISABLED')", name="ck_app_user_status"),
        Index("uq_app_user_email", "email", unique=True),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="USER",
        server_default=text("'USER'"),
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
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
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
