"""Allow work-unit pipeline stages in video failure metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0016_pipeline_failed_stages"
down_revision = "0015_pipeline_frame_candidate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_FAILED_STAGES = (
    "DOWNLOAD",
    "EXTRACT",
    "STT",
    "CHUNKING",
    "EMBEDDING",
    "VECTOR_UPSERT",
)
WORK_UNIT_FAILED_STAGES = (
    "NORMALIZE_VIDEO",
    "TRANSCRIBE_PART",
    "ASSEMBLE_CHUNKS",
    "ENRICH_CHUNK",
    "EMBED_BATCH",
)


def _constraint(stages: tuple[str, ...]) -> str:
    values = ",".join(f"'{stage}'" for stage in stages)
    return f"failed_stage IS NULL OR failed_stage IN ({values})"


def upgrade() -> None:
    op.drop_constraint("ck_video_failed_stage", "video", type_="check")
    op.create_check_constraint(
        "ck_video_failed_stage",
        "video",
        _constraint(LEGACY_FAILED_STAGES + WORK_UNIT_FAILED_STAGES),
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE video SET failed_stage = NULL "
            "WHERE failed_stage IN "
            "('NORMALIZE_VIDEO','TRANSCRIBE_PART','ASSEMBLE_CHUNKS',"
            "'ENRICH_CHUNK','EMBED_BATCH')"
        )
    )
    op.drop_constraint("ck_video_failed_stage", "video", type_="check")
    op.create_check_constraint(
        "ck_video_failed_stage",
        "video",
        _constraint(LEGACY_FAILED_STAGES),
    )
