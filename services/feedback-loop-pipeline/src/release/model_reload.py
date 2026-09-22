from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID


class ModelReloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelReloadResult:
    ready_model_versions: frozenset[str]


class ManagedEmbeddingModelReloadClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: float = 10.0,
        urlopen_func: Callable[..., Any] = urlopen,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec
        self._urlopen = urlopen_func

    async def reload(self, *, trace_id: UUID) -> ModelReloadResult:
        payload = await asyncio.to_thread(self._post_reload, trace_id)
        ready_versions = payload.get("ready_model_versions", [])
        if not isinstance(ready_versions, list):
            raise ModelReloadError("reload-models returned invalid ready_model_versions")
        return ModelReloadResult(ready_model_versions=frozenset(str(version) for version in ready_versions))

    def _post_reload(self, trace_id: UUID) -> dict[str, Any]:
        body = json.dumps({"trace_id": str(trace_id)}).encode("utf-8")
        request = Request(
            f"{self._base_url}/internal/reload-models",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=self._timeout_sec) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ModelReloadError("managed embedding model reload failed") from exc
        if not isinstance(payload, dict):
            raise ModelReloadError("reload-models returned a non-object payload")
        return payload

    @property
    def base_url(self) -> str:
        return self._base_url


@dataclass(frozen=True)
class ManagedEmbeddingModelReloadFanout:
    batch_client: ManagedEmbeddingModelReloadClient
    search_client: ManagedEmbeddingModelReloadClient

    async def reload(self, *, trace_id: UUID) -> ModelReloadResult:
        clients = self._unique_clients()
        results = await asyncio.gather(
            *(client.reload(trace_id=trace_id) for client in clients),
            return_exceptions=True,
        )
        ready_version_sets = self._ready_version_sets(clients, results)
        intersection = ready_version_sets[0]
        for ready_versions in ready_version_sets[1:]:
            intersection = intersection.intersection(ready_versions)
        return ModelReloadResult(ready_model_versions=intersection)

    def _unique_clients(self) -> tuple[ManagedEmbeddingModelReloadClient, ...]:
        if self.batch_client.base_url == self.search_client.base_url:
            return (self.batch_client,)
        return self.batch_client, self.search_client

    @staticmethod
    def _ready_version_sets(
        clients: tuple[ManagedEmbeddingModelReloadClient, ...],
        results: list[ModelReloadResult | BaseException],
    ) -> list[frozenset[str]]:
        ready_version_sets: list[frozenset[str]] = []
        for client, result in zip(clients, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                raise ModelReloadError(
                    f"managed embedding model reload failed: {client.base_url}"
                ) from result
            ready_version_sets.append(result.ready_model_versions)
        return ready_version_sets
