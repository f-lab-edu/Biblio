import pytest

from src.infra.ai.google_stt_adapter import ExternalAIAdapterError
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


@pytest.mark.asyncio
async def test_embedding_client_reads_ready_model_versions_from_health() -> None:
    client = build_embedding_client(model_version="v001")

    versions = await client.get_ready_model_versions(trace_id="trace-4")

    assert versions == ["v001"]


@pytest.mark.asyncio
async def test_embedding_client_get_model_version_uses_configured_ready_version() -> None:
    client = build_embedding_client(
        model_version="v002",
        ready_model_versions=["v001", "v002"],
        embedding_model_version="v002",
    )

    version = await client.get_model_version(trace_id="trace-5")

    assert version == "v002"


@pytest.mark.asyncio
async def test_embedding_client_rejects_health_without_ready_model_versions() -> None:
    client = build_embedding_client(health_payload={"status": "ok", "model_version": "v001"})

    with pytest.raises(ExternalAIAdapterError, match="ready_model_versions"):
        await client.get_ready_model_versions(trace_id="trace-6")

