"""Unit tests for SearchOrchestrator answer assembly (Task 5)."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.infra.db.search_repository import (
    CorpusReadiness,
    FTSCandidate,
    ChunkRecord,
    SearchRepository,
)
from src.infra.embedding.client import EmbeddingClient, EmbeddingResult
from src.infra.llm.base import LLMAdapter, LLMAdapterError, LLMGenerationResult
from src.middlewares.error_handler import ApiError, ServiceUnavailableError
from src.services.search_orchestrator import SearchOrchestrator

USER_ID = uuid4()
TRACE_ID = str(uuid4())


def _chunk_record(
    *,
    title: str = "Video",
    text: str = "raw text",
    enriched_text: str = "enriched text",
    start_ms: int = 0,
    end_ms: int = 5000,
) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=uuid4(),
        video_id=uuid4(),
        title=title,
        text=text,
        enriched_text=enriched_text,
        start_ms=start_ms,
        end_ms=end_ms,
    )


def _make_orchestrator(
    *,
    records: list[ChunkRecord],
    llm_text: str | None = None,
    llm_error: LLMAdapterError | None = None,
) -> SearchOrchestrator:
    repo = AsyncMock(spec=SearchRepository)
    repo.check_corpus_readiness.return_value = CorpusReadiness(
        total_videos=1, non_ready_count=0,
    )
    repo.fts_search.return_value = [
        FTSCandidate(chunk_id=record.chunk_id, rank=index + 1)
        for index, record in enumerate(records)
    ]
    repo.ann_search.return_value = []
    repo.sot_gate.return_value = records

    embedding_client = AsyncMock(spec=EmbeddingClient)
    embedding_client.embed_query.return_value = EmbeddingResult(
        embedding=[0.1, 0.2, 0.3]
    )

    llm_adapter = AsyncMock(spec=LLMAdapter)
    if llm_error is not None:
        llm_adapter.generate.side_effect = llm_error
    else:
        llm_adapter.generate.return_value = LLMGenerationResult(
            text=llm_text
            or "<ANSWER>Grounded answer [1]</ANSWER><USED_REFS_JSON>{\"used_refs\":[1]}</USED_REFS_JSON>"
        )

    return SearchOrchestrator(
        repo=repo,
        embedding_client=embedding_client,
        llm_adapter=llm_adapter,
    )


class TestSearchOrchestratorAnswer:
    async def test_returns_answer_and_marks_used_chunks(self) -> None:
        record = _chunk_record(text="raw transcript", enriched_text="better context")
        orchestrator = _make_orchestrator(records=[record])

        result = await orchestrator.execute(
            user_id=USER_ID,
            query="What happened?",
            trace_id=TRACE_ID,
        )

        assert result.answer == "Grounded answer [1]"
        assert len(result.chunks) == 1
        assert result.chunks[0].used is True

        user_prompt = orchestrator._llm_adapter.generate.await_args.args[1]
        assert "better context" in user_prompt
        assert "raw transcript" not in user_prompt

    async def test_malformed_used_refs_keeps_all_chunks_unused(self) -> None:
        record = _chunk_record()
        orchestrator = _make_orchestrator(
            records=[record],
            llm_text=(
                "<ANSWER>Grounded answer [1]</ANSWER>"
                "<USED_REFS_JSON>{bad json</USED_REFS_JSON>"
            ),
        )

        result = await orchestrator.execute(
            user_id=USER_ID,
            query="What happened?",
            trace_id=TRACE_ID,
        )

        assert result.answer == "Grounded answer [1]"
        assert [chunk.used for chunk in result.chunks] == [False]

    async def test_missing_answer_block_raises_internal_error(self) -> None:
        record = _chunk_record()
        orchestrator = _make_orchestrator(
            records=[record],
            llm_text='<USED_REFS_JSON>{"used_refs":[1]}</USED_REFS_JSON>',
        )

        with pytest.raises(ApiError, match="ANSWER"):
            await orchestrator.execute(
                user_id=USER_ID,
                query="What happened?",
                trace_id=TRACE_ID,
            )

    async def test_retryable_llm_error_maps_to_503(self) -> None:
        record = _chunk_record()
        orchestrator = _make_orchestrator(
            records=[record],
            llm_error=LLMAdapterError(
                code="UNAVAILABLE",
                message="Gemini unavailable",
                retryable=True,
            ),
        )

        with pytest.raises(ServiceUnavailableError):
            await orchestrator.execute(
                user_id=USER_ID,
                query="What happened?",
                trace_id=TRACE_ID,
            )

    async def test_non_retryable_llm_error_maps_to_500(self) -> None:
        record = _chunk_record()
        orchestrator = _make_orchestrator(
            records=[record],
            llm_error=LLMAdapterError(
                code="AUTH_ERROR",
                message="Gemini auth failed",
                retryable=False,
            ),
        )

        with pytest.raises(ApiError, match="Gemini auth failed"):
            await orchestrator.execute(
                user_id=USER_ID,
                query="What happened?",
                trace_id=TRACE_ID,
            )
