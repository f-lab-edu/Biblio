import asyncio
import time
from collections.abc import Callable

from src.infra.db.search_repository import SearchRepository, ServingSearchTargets
from src.middlewares.error_handler import ServiceUnavailableError


class ServingSearchTargetProvider:
    def __init__(
        self,
        repo: SearchRepository,
        *,
        ttl_sec: int,
        now_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repo = repo
        self._ttl_sec = ttl_sec
        self._now_func = now_func
        self._lock = asyncio.Lock()
        self._cached_targets: ServingSearchTargets | None = None
        self._expires_at = 0.0

    async def get(self, *, force_refresh: bool = False) -> ServingSearchTargets:
        now = self._now_func()
        if not force_refresh and self._is_cache_valid(now):
            assert self._cached_targets is not None
            return self._cached_targets
        
        # 캐시 히트가 아닌경우 락 걸고 db 조회, 여러 요청이 db조회 못하게
        async with self._lock:
            now = self._now_func()
            if not force_refresh and self._is_cache_valid(now):
                assert self._cached_targets is not None
                return self._cached_targets

            targets = await self._repo.get_serving_search_targets()
            if targets is None:
                raise ServiceUnavailableError(
                    "ModelRelease active search target is missing."
                )

            self._cached_targets = targets
            self._expires_at = now + self._ttl_sec
            return targets

    def invalidate(self) -> None:
        self._cached_targets = None
        self._expires_at = 0.0

    def _is_cache_valid(self, now: float) -> bool:
        return self._cached_targets is not None and now < self._expires_at
