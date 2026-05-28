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


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class TestServingSearchTargetProvider:
    
    async def test_cache_hit_reuses_serving_targets_without_repo_round_trip(self) -> None:
        repo = AsyncMock(spec=SearchRepository)
        repo.get_serving_search_targets.return_value = _targets()
        provider = ServingSearchTargetProvider(repo, ttl_sec=60)

        first = await provider.get()
        second = await provider.get()

        assert first is second
        repo.get_serving_search_targets.assert_awaited_once()

    async def test_expired_cache_reloads_targets(self) -> None:
        clock = _Clock()
        repo = AsyncMock(spec=SearchRepository)
        repo.get_serving_search_targets.side_effect = [
            _targets("embedding-v1"),
            _targets("embedding-v2"),
        ]
        provider = ServingSearchTargetProvider(
            repo,
            ttl_sec=60,
            now_func=clock.now,
        )

        first = await provider.get()
        clock.advance(61)
        second = await provider.get()

        assert first.active.model_version == "embedding-v1"
        assert second.active.model_version == "embedding-v2"
        assert repo.get_serving_search_targets.await_count == 2
    # invalidate()를 호출 테스트
    async def test_invalidate_forces_next_read_to_reload(self) -> None:
        repo = AsyncMock(spec=SearchRepository)
        repo.get_serving_search_targets.side_effect = [
            _targets("embedding-v1"),
            _targets("embedding-v2"),
        ]
        provider = ServingSearchTargetProvider(repo, ttl_sec=60)

        first = await provider.get()
        provider.invalidate()
        second = await provider.get()

        assert first.active.model_version == "embedding-v1"
        assert second.active.model_version == "embedding-v2"
        assert repo.get_serving_search_targets.await_count == 2

    async def test_missing_model_release_row_raises_service_unavailable(self) -> None:
        repo = AsyncMock(spec=SearchRepository)
        repo.get_serving_search_targets.return_value = None
        provider = ServingSearchTargetProvider(repo, ttl_sec=60)

        with pytest.raises(ServiceUnavailableError, match="ModelRelease"):
            await provider.get()
    
    async def test_concurrent_cache_miss_uses_single_repo_read(self) -> None:
        repo = AsyncMock(spec=SearchRepository)
        repo.get_serving_search_targets.return_value = _targets()
        provider = ServingSearchTargetProvider(repo, ttl_sec=60)

        results = await asyncio.gather(
            provider.get(),
            provider.get(),
            provider.get(),
        )

        first_result, second_result, third_result = results
        assert first_result is second_result
        assert second_result is third_result
        repo.get_serving_search_targets.assert_awaited_once()
