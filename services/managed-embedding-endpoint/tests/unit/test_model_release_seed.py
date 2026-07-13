import sys
from types import SimpleNamespace

import pytest

from src.core.model_release_seed import derive_seed_model_release, seed_model_release


class TestDeriveSeedModelRelease:
    def test_derives_version_from_model_artifact_path_last_segment(self):
        seed = derive_seed_model_release(
            model_artifact_path="/home/artyom9/models/bge-m3-20260526T143000KST"
        )

        assert seed.active_model_version == "bge-m3-20260526T143000KST"
        assert seed.active_index_name == "vector-bge-m3-20260526T143000KST"

    def test_allows_explicit_active_index_name(self):
        seed = derive_seed_model_release(
            model_artifact_path="/home/artyom9/models/bge-m3-20260526T143000KST",
            active_index_name="custom-index",
        )

        assert seed.active_model_version == "bge-m3-20260526T143000KST"
        assert seed.active_index_name == "custom-index"


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.calls = []
        self.closed = False

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    async def execute(self, sql: str, *args):
        self.calls.append((sql, args))
        return "INSERT 0 1"

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_seed_model_release_also_seeds_active_snapshot_and_catalog(monkeypatch):
    connection = FakeConnection()

    async def connect(database_url: str):
        assert database_url == "postgresql://user:pass@db/app"
        return connection

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(connect=connect))

    inserted = await seed_model_release(
        database_url="postgresql+asyncpg://user:pass@db/app",
        model_artifact_path="/models/bge-m3-base",
    )

    assert inserted is True
    assert connection.closed is True
    assert len(connection.calls) == 3
    assert "INSERT INTO model_release" in connection.calls[0][0]
    assert connection.calls[0][1] == ("bge-m3-base", "vector-bge-m3-base")
    assert "INSERT INTO model_snapshot" in connection.calls[1][0]
    assert "WHERE status = 'ACTIVE'" in connection.calls[1][0]
    assert "INSERT INTO vector_index_catalog" in connection.calls[2][0]
    assert connection.calls[2][1] == (1024,)
