import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path


BGE_M3_EMBEDDING_DIMENSION = 1024


@dataclass(frozen=True, slots=True)
class SeedModelRelease:
    active_model_version: str
    active_index_name: str


def derive_seed_model_release(
    *,
    model_artifact_path: str,
    active_index_name: str | None = None,
) -> SeedModelRelease:
    model_version = Path(model_artifact_path).name
    return SeedModelRelease(
        active_model_version=model_version,
        active_index_name=active_index_name or f"vector-{model_version}",
    )


async def seed_model_release(
    *,
    database_url: str,
    model_artifact_path: str,
    active_index_name: str | None = None,
    embedding_dimension: int = BGE_M3_EMBEDDING_DIMENSION,
) -> bool:
    import asyncpg

    seed = derive_seed_model_release(
        model_artifact_path=model_artifact_path,
        active_index_name=active_index_name,
    )
    conn = await asyncpg.connect(_normalize_database_url(database_url))
    try:
        async with conn.transaction(): # seed 주입
            status = await _insert_model_release(conn, seed)
            await _ensure_active_snapshot(conn)
            await _ensure_active_index_catalog(conn, embedding_dimension)
    finally:
        await conn.close()
    return status == "INSERT 0 1"


def _normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


# --- seed 주입을 위한 helper 함수 ----
async def _insert_model_release(conn, seed: SeedModelRelease) -> str:
    return await conn.execute(
        """
        INSERT INTO model_release (
            singleton_key,
            active_model_version,
            active_index_name
        )
        VALUES (1, $1, $2)
        ON CONFLICT (singleton_key) DO NOTHING
        """,
        seed.active_model_version,
        seed.active_index_name,
    )


async def _ensure_active_snapshot(conn) -> str:
    return await conn.execute(
        """
        INSERT INTO model_snapshot (
            model_version,
            index_name,
            status,
            captured_at
        )
        SELECT
            active_model_version,
            active_index_name,
            'ACTIVE',
            NOW()
        FROM model_release
        WHERE singleton_key = 1
          AND NOT EXISTS (
              SELECT 1 FROM model_snapshot WHERE status = 'ACTIVE'
          )
        """
    )


async def _ensure_active_index_catalog(conn, embedding_dimension: int) -> str:
    return await conn.execute(
        """
        INSERT INTO vector_index_catalog (
            index_name,
            model_version,
            embedding_dimension,
            created_at
        )
        SELECT
            active_index_name,
            active_model_version,
            $1,
            NOW()
        FROM model_release
        WHERE singleton_key = 1
          AND NOT EXISTS (
              SELECT 1
              FROM vector_index_catalog
              WHERE index_name = model_release.active_index_name
          )
        """,
        embedding_dimension,
    )
# ----------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed the singleton ModelRelease row from MODEL_ARTIFACT_PATH.",
    )
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument(
        "--model-artifact-path",
        default=os.getenv("MODEL_ARTIFACT_PATH", ""),
    )
    parser.add_argument(
        "--active-index-name",
        default=os.getenv("ACTIVE_INDEX_NAME"),
    )
    parser.add_argument(
        "--embedding-dimension",
        type=int,
        default=int(os.getenv("EMBEDDING_DIMENSION", str(BGE_M3_EMBEDDING_DIMENSION))),
    )
    return parser.parse_args()


async def _run() -> None:
    args = _parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    if not args.model_artifact_path:
        raise SystemExit("MODEL_ARTIFACT_PATH is required")
    inserted = await seed_model_release(
        database_url=args.database_url,
        model_artifact_path=args.model_artifact_path,
        active_index_name=args.active_index_name,
        embedding_dimension=args.embedding_dimension,
    )
    print("inserted" if inserted else "already_exists")


if __name__ == "__main__":
    asyncio.run(_run())
