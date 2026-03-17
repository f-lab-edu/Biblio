from __future__ import annotations

import pytest

from adapters.ai.google_stt_adapter import ExternalAIAdapterError
from tests.support import build_embedding_client


@pytest.mark.asyncio
async def test_embedding_client_returns_embeddings() -> None:
    client = build_embedding_client()

    result = await client.embed_texts(["alpha", "beta"], trace_id="trace-1")

    assert result.model_version == "v001"
    assert len(result.embeddings) == 2


@pytest.mark.asyncio
async def test_embedding_client_retries_503_and_succeeds() -> None:
    client = build_embedding_client(fail_embed_times=1)

    result = await client.embed_texts(["alpha"], trace_id="trace-2")

    assert result.embeddings[0][0] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_embedding_client_rejects_empty_input() -> None:
    client = build_embedding_client()

    with pytest.raises(ExternalAIAdapterError):
        await client.embed_texts([], trace_id="trace-3")
