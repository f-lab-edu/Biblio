from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


class ReleaseChangeCommitter(Protocol):
    async def commit(self) -> None: ...


class ServingTargetReloader(Protocol):
    async def reload(self, *, trace_id: UUID) -> None: ...


class NoopReleaseChangeCommitter:
    async def commit(self) -> None:
        return None


@dataclass(frozen=True)
class SqlAlchemyReleaseChangeCommitter:
    session: AsyncSession

    async def commit(self) -> None:
        await self.session.commit()


class NoopServingTargetReloader:
    async def reload(self, *, trace_id: UUID) -> None:
        _ = trace_id


class SearchServiceReloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchServiceServingTargetReloader:
    base_url: str
    timeout_sec: float = 5.0

    async def reload(self, *, trace_id: UUID) -> None:
        await asyncio.to_thread(self._post_reload, trace_id) # urlopen이 동기 함수라 별도 thread로 처리

    def _post_reload(self, trace_id: UUID) -> None:
        request = Request(
            f"{self.base_url.rstrip('/')}/internal/reload-serving-targets",
            data=json.dumps({"trace_id": str(trace_id)}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_sec) as response:
                response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise SearchServiceReloadError(
                "search-service serving target reload failed"
            ) from exc
