import asyncio
from unittest.mock import AsyncMock

import pytest

from src.infra.db.search_repository import (
    SearchRepository,
    ServingSearchTarget,
    ServingSearchTargets,
)
from src.middlewares.error_handler import ServiceUnavailableError
from src.services.serving_targets import ServingSearchTargetProvider


def _targets(version: str = "embedding-v1") -> ServingSearchTargets:
    return ServingSearchTargets(
        active=ServingSearchTarget(
            model_version=version,
            index_name=f"{version}-index",
        )
    )


class TestServingSearchTargetProvider:
    
    async def test_load_reads_targets_once_and_get_reuses_loaded_targets(self) -> None:
        repo = AsyncMock(spec=SearchRepository)
        repo.get_serving_search_targets.return_value = _targets()
        provider = ServingSearchTargetProvider(repo)

        await provider.load()
        first = provider.get()
        second = provider.get()

        assert first is second
        repo.get_serving_search_targets.assert_awaited_once()

    async def test_second_load_is_noop(self) -> None:
        repo = AsyncMock(spec=SearchRepository)
        repo.get_serving_search_targets.return_value = _targets("embedding-v1")
        provider = ServingSearchTargetProvider(repo)

        await provider.load()
        await provider.load()

        assert provider.get().active.model_version == "embedding-v1"
        repo.get_serving_search_targets.assert_awaited_once()

    async def test_reload_replaces_loaded_targets(self) -> None:
        repo = AsyncMock(spec=SearchRepository)
        repo.get_serving_search_targets.return_value = _targets("embedding-v2")
        provider = ServingSearchTargetProvider(
            repo,
            loaded_targets=_targets("embedding-v1"),
        )

        targets = await provider.reload()

        assert targets.active.model_version == "embedding-v2"
        assert provider.get().active.model_version == "embedding-v2"
        repo.get_serving_search_targets.assert_awaited_once()

    async def test_failed_reload_keeps_existing_targets(self) -> None:
        repo = AsyncMock(spec=SearchRepository)
        repo.get_serving_search_targets.return_value = None
        provider = ServingSearchTargetProvider(
            repo,
            loaded_targets=_targets("embedding-v1"),
        )

        with pytest.raises(ServiceUnavailableError, match="ModelRelease"):
            await provider.reload()

        assert provider.get().active.model_version == "embedding-v1"

    async def test_concurrent_load_uses_single_repo_read(self) -> None:
        repo = AsyncMock(spec=SearchRepository)
        repo.get_serving_search_targets.return_value = _targets()
        provider = ServingSearchTargetProvider(repo)

        await asyncio.gather(
            provider.load(),
            provider.load(),
            provider.load(),
        )

        assert provider.get().active.model_version == "embedding-v1"
        repo.get_serving_search_targets.assert_awaited_once()

    async def test_missing_model_release_row_raises_service_unavailable(self) -> None:
        repo = AsyncMock(spec=SearchRepository)
        repo.get_serving_search_targets.return_value = None
        provider = ServingSearchTargetProvider(repo)

        with pytest.raises(ServiceUnavailableError, match="ModelRelease"):
            await provider.load()
    
    async def test_get_before_load_raises_service_unavailable(self) -> None:
        repo = AsyncMock(spec=SearchRepository)
        provider = ServingSearchTargetProvider(repo)

        with pytest.raises(ServiceUnavailableError, match="not loaded"):
            provider.get()
        repo.get_serving_search_targets.assert_not_called()
