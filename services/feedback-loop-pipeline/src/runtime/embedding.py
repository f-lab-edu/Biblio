from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class EmbeddingBatchResult:
    embeddings: list[list[float]]
    model_version: str


class ManagedEmbeddingBatchClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: float,
        default_model_version: str,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec
        self._default_model_version = default_model_version

    async def embed_texts(
        self,
        texts: list[str],
        *,
        trace_id: str,
        model_version: str | None = None,
    ) -> EmbeddingBatchResult:
        payload = await asyncio.to_thread(
            self._post_embed,
            texts,
            trace_id,
            model_version or self._default_model_version,
        )
        embeddings = payload["embeddings"]
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError("embedding endpoint returned an invalid embedding count")
        return EmbeddingBatchResult(
            embeddings=[[float(value) for value in row] for row in embeddings],
            model_version=str(payload.get("model_version") or model_version or self._default_model_version),
        )

    def _post_embed(self, texts: list[str], trace_id: str, model_version: str) -> dict[str, Any]:
        body = json.dumps({"texts": texts, "model_version": model_version}).encode("utf-8")
        request = Request(
            f"{self._base_url}/embed",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Trace-Id": trace_id,
            },
            method="POST",
        )
        with urlopen(request, timeout=self._timeout_sec) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("embedding endpoint returned a non-object payload")
        return payload
