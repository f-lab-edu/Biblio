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
