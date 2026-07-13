from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

IdTokenProvider = Callable[[str], str]


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


def fetch_google_id_token(audience: str) -> str:
    """배포 환경에서 메타데이터 서버로부터 audience용 ID 토큰을 발급받는다."""
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import id_token

    return id_token.fetch_id_token(GoogleAuthRequest(), audience)


@dataclass(frozen=True)
class SearchServiceServingTargetReloader:
    base_url: str
    timeout_sec: float = 5.0
    id_token_provider: IdTokenProvider = fetch_google_id_token

    async def reload(self, *, trace_id: UUID) -> None:
        await asyncio.to_thread(self._post_reload, trace_id) # urlopen이 동기 함수라 별도 thread로 처리

    def _post_reload(self, trace_id: UUID) -> None:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.id_token_provider(self._audience())}",
        }
        request = Request(
            f"{self.base_url.rstrip('/')}/internal/reload-serving-targets",
            data=json.dumps({"trace_id": str(trace_id)}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_sec) as response:
                response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise SearchServiceReloadError(
                "search-service serving target reload failed"
            ) from exc

    def _audience(self) -> str:
        parts = urlsplit(self.base_url)
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))
