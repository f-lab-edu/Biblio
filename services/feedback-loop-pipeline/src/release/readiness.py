from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class CandidateReadinessError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManagedEmbeddingReadinessClient:
    base_url: str
    timeout_sec: float = 5.0

    async def is_candidate_ready(self, *, model_version: str) -> bool:
        payload = await asyncio.to_thread(self._fetch_health)
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
