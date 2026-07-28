'''
배포나 롤백을 실제로 실행하기 직전에, "새 모델이 진짜 임베딩 서버에 올라와 있나"를 확인
'''
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class CandidateReadinessError(RuntimeError):
    """준비 확인(readiness check) 과정에서 생긴 실패를 나타내는 전용 예외."""


@dataclass(frozen=True)
class ManagedEmbeddingReadinessClient:
    """임베딩 VM 한 대에 /health를 호출해, 특정 모델 버전이 준비됐는지 확인한다."""

    base_url: str
    timeout_sec: float = 5.0

    async def is_candidate_ready(self, *, model_version: str) -> bool:
        payload = await asyncio.to_thread(self._fetch_health) #_fetch_health 메서드에서 쓰이는 urlopen() 함수가 동기함수라  asyncio.to_thread로 처리
        ready_versions = payload.get("ready_model_versions")
        if isinstance(ready_versions, list):
            return model_version in {str(version) for version in ready_versions}
        return str(payload.get("model_version", "")) == model_version and payload.get("status") == "ok"

    async def is_ready(self, *, model_version: str) -> bool:
        return await self.is_candidate_ready(model_version=model_version)

    def _fetch_health(self) -> dict[str, Any]:
        request = Request(f"{self.base_url.rstrip('/')}/health", method="GET")
        try:
            with urlopen(request, timeout=self.timeout_sec) as response:
                body = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise CandidateReadinessError("managed embedding endpoint health check failed") from exc
        return json.loads(body)


@dataclass(frozen=True)
class ManagedEmbeddingReadinessFanout:
    """ManagedEmbeddingReadinessClient 두 개(배치·검색 VM)를 묶어, 둘 다 준비됐을 때만 True를 돌려준다."""

    batch_client: ManagedEmbeddingReadinessClient
    search_client: ManagedEmbeddingReadinessClient
    # 배포시 체크
    async def is_candidate_ready(self, *, model_version: str) -> bool:
        clients = self._unique_clients()
        results = await asyncio.gather(
            *(
                client.is_candidate_ready(model_version=model_version)
                for client in clients
            ),
            return_exceptions=True,
        )
        return self._all_ready(clients, results)

    # 롤백시 체크
    async def is_ready(self, *, model_version: str) -> bool:
        clients = self._unique_clients()
        results = await asyncio.gather(
            *(client.is_ready(model_version=model_version) for client in clients),
            return_exceptions=True,
        )
        return self._all_ready(clients, results)

    def _unique_clients(self) -> tuple[ManagedEmbeddingReadinessClient, ...]:
        if self.batch_client.base_url.rstrip("/") == self.search_client.base_url.rstrip(
            "/"
        ):
            return (self.batch_client,)
        return self.batch_client, self.search_client

    @staticmethod
    def _all_ready(
        clients: tuple[ManagedEmbeddingReadinessClient, ...],
        results: list[bool | BaseException],
    ) -> bool:
        ready_results: list[bool] = []
        for client, result in zip(clients, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                raise CandidateReadinessError(
                    f"managed embedding endpoint health check failed: {client.base_url}"
                ) from result
            ready_results.append(result)
        return all(ready_results)
