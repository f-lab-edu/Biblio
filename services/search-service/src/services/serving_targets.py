import asyncio

from src.infra.db.search_repository import SearchRepository, ServingSearchTargets
from src.middlewares.error_handler import ServiceUnavailableError


class ServingSearchTargetProvider:
    def __init__(
        self,
        repo: SearchRepository,
        *,
        loaded_targets: ServingSearchTargets | None = None,
    ) -> None:
        self._repo = repo
        self._lock = asyncio.Lock()
        self._targets = loaded_targets

    async def load(self) -> None:
        if self._targets is not None:
            return

        async with self._lock:
            if self._targets is not None:
                return

            self._targets = await self._read_targets()

    async def reload(self) -> ServingSearchTargets:
        async with self._lock:
            targets = await self._read_targets()
            self._targets = targets
            return targets

    async def _read_targets(self) -> ServingSearchTargets:
        targets = await self._repo.get_serving_search_targets()
        if targets is None:
            raise ServiceUnavailableError(
                "ModelRelease active search target is missing."
            )
        return targets

    def get(self) -> ServingSearchTargets:
        if self._targets is None:
            raise ServiceUnavailableError("Serving search targets are not loaded.")
        return self._targets
