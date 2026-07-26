"""Unit tests for SearchOrchestrator observability wiring.

Covers: exactly one `search.execute.timing` per attempt, stage fields for
success / empty / failure, active vs previous split, correlation reuse across
every DB call, and preserved parallelism of FTS/ANN.
"""

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.common.observability import SearchRequestContext
from src.infra.db.search_repository import (
    ANNCandidate,
    ChunkRecord,
    CorpusReadiness,
    FTSCandidate,
    SearchRepository,
    ServingSearchTarget,
    ServingSearchTargets,
)
from src.infra.embedding.client import EmbeddingClient, EmbeddingResult
from src.infra.llm.base import LLMAdapter, LLMAdapterError, LLMGenerationResult
from src.middlewares.error_handler import (
    ApiError,
    SearchNotReadyError,
    ServiceUnavailableError,
)
from src.services.search_observability import EXECUTE_TIMING_LOG
from src.services.search_orchestrator import SearchOrchestrator
from src.services.serving_targets import ServingSearchTargetProvider

USER_ID = uuid4()
PROJECT_ID = uuid4()
TRACE_ID = str(uuid4())

ANSWER_TEXT = (
    "<ANSWER>mock answer</ANSWER>"
    '<USED_REFS_JSON>{"used_refs":[1]}</USED_REFS_JSON>'
)

ACTIVE_ONLY = ServingSearchTargets(
    active=ServingSearchTarget(
        model_version="embedding-v2", index_name="active-index-v2"
    )
)

ACTIVE_AND_PREVIOUS = ServingSearchTargets(
    active=ServingSearchTarget(
        model_version="embedding-v2", index_name="active-index-v2"
    ),
    previous=ServingSearchTarget(
        model_version="embedding-v1", index_name="previous-index-v1"
    ),
)


def _chunk_record(chunk_id=None) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=chunk_id or uuid4(),
        video_id=uuid4(),
        title="Video",
        text="text",
        enriched_text="enriched",
        start_ms=0,
        end_ms=5000,
    )


def _make_orchestrator(
    *,
    total_videos: int = 1,
    non_ready_count: int = 0,
    fts_results: list[FTSCandidate] | None = None,
    ann_results: list[ANNCandidate] | None = None,
    sot_records: list[ChunkRecord] | None = None,
    llm_text: str = ANSWER_TEXT,
    llm_error: LLMAdapterError | None = None,
    targets: ServingSearchTargets = ACTIVE_ONLY,
) -> SearchOrchestrator:
    repo = AsyncMock(spec=SearchRepository)
    repo.check_corpus_readiness.return_value = CorpusReadiness(
        total_videos=total_videos, non_ready_count=non_ready_count
    )
    repo.fts_search.return_value = fts_results or []
    repo.ann_search.return_value = ann_results or []
    repo.sot_gate.return_value = sot_records or []
    repo.save_search_response_snapshot = AsyncMock()
    repo.save_conversation = AsyncMock()

    embedding_client = AsyncMock(spec=EmbeddingClient)
    embedding_client.embed_query.return_value = EmbeddingResult(
        embedding=[0.1, 0.2, 0.3]
    )

    llm_adapter = AsyncMock(spec=LLMAdapter)
    if llm_error is not None:
        llm_adapter.generate.side_effect = llm_error
    else:
        llm_adapter.generate.return_value = LLMGenerationResult(text=llm_text)

    return SearchOrchestrator(
        repo=repo,
        serving_target_provider=ServingSearchTargetProvider(
            repo, loaded_targets=targets
        ),
        embedding_client=embedding_client,
        llm_adapter=llm_adapter,
    )


def _with_one_hit(**kwargs) -> SearchOrchestrator:
    """Orchestrator whose pipeline reaches the LLM with a single chunk."""
    chunk_id = uuid4()
    return _make_orchestrator(
        fts_results=[FTSCandidate(chunk_id=chunk_id, rank=1)],
        sot_records=[_chunk_record(chunk_id=chunk_id)],
        **kwargs,
    )


async def _run_and_capture(orch: SearchOrchestrator, *, expected_error=None) -> dict:
    """Execute one search and return the `search.execute.timing` fields."""
    with patch("src.services.search_observability.log_info") as log_info:
        if expected_error is None:
            await orch.execute(
                user_id=USER_ID,
                project_id=PROJECT_ID,
                query="my query",
                trace_id=TRACE_ID,
            )
        else:
            with pytest.raises(expected_error):
                await orch.execute(
                    user_id=USER_ID,
                    project_id=PROJECT_ID,
                    query="my query",
                    trace_id=TRACE_ID,
                )

    log_info.assert_called_once()
    assert log_info.call_args.args[0] == EXECUTE_TIMING_LOG
    return log_info.call_args.kwargs


class TestSuccessTiming:
    async def test_records_every_stage_of_a_successful_search(self) -> None:
        fields = await _run_and_capture(_with_one_hit())

        assert fields["status"] == "success"
        assert fields["target_count"] == 1
        for stage in (
            "query_embedding_ms",
            "query_embedding_active_ms",
            "fts_ms",
            "vector_search_ms",
            "vector_search_active_ms",
            "sot_gate_ms",
            "prompt_build_ms",
            "llm_ms",
            "snapshot_save_ms",
            "total_ms",
        ):
            assert isinstance(fields[stage], float), stage

    async def test_active_only_omits_previous_fields(self) -> None:
        fields = await _run_and_capture(_with_one_hit())

        assert "query_embedding_previous_ms" not in fields
        assert "vector_search_previous_ms" not in fields

    async def test_previous_target_gets_its_own_fields(self) -> None:
        fields = await _run_and_capture(_with_one_hit(targets=ACTIVE_AND_PREVIOUS))

        assert fields["target_count"] == 2
        assert isinstance(fields["query_embedding_previous_ms"], float)
        assert isinstance(fields["vector_search_previous_ms"], float)


class TestEmptyTiming:
    async def test_no_candidates_logs_empty_without_llm_stages(self) -> None:
        fields = await _run_and_capture(_make_orchestrator())

        assert fields["status"] == "empty"
        assert "fts_ms" in fields
        assert "sot_gate_ms" not in fields
        assert "llm_ms" not in fields


class TestFailureTiming:
    async def test_readiness_conflict_logs_failure_without_stage_fields(self) -> None:
        orch = _make_orchestrator(non_ready_count=1)

        fields = await _run_and_capture(orch, expected_error=SearchNotReadyError)

        assert fields["status"] == "failed"
        assert fields["error_code"] == "SEARCH_NOT_READY"
        assert "failed_stage" not in fields
        assert "query_embedding_ms" not in fields
        assert isinstance(fields["total_ms"], float)

    async def test_active_embedding_503_keeps_partial_timing(self) -> None:
        orch = _make_orchestrator()
        orch._embedding_client.embed_query.side_effect = ServiceUnavailableError(
            "embedding endpoint down"
        )

        fields = await _run_and_capture(orch, expected_error=ServiceUnavailableError)

        assert fields["status"] == "failed"
        assert fields["error_code"] == "SERVICE_UNAVAILABLE"
        assert fields["failed_stage"] == "query_embedding_active"
        assert isinstance(fields["query_embedding_active_ms"], float)
        assert isinstance(fields["query_embedding_ms"], float)
        assert "fts_ms" not in fields

    async def test_fts_failure_names_the_fts_stage(self) -> None:
        orch = _make_orchestrator()
        orch._repo.fts_search.side_effect = RuntimeError("fts down")

        fields = await _run_and_capture(orch, expected_error=RuntimeError)

        assert fields["status"] == "failed"
        assert fields["error_type"] == "RuntimeError"
        assert fields["failed_stage"] == "fts"

    async def test_retryable_llm_failure_keeps_stages_before_llm(self) -> None:
        orch = _with_one_hit(
            llm_error=LLMAdapterError(
                code="UNAVAILABLE", message="Gemini unavailable", retryable=True
            )
        )

        fields = await _run_and_capture(orch, expected_error=ServiceUnavailableError)

        assert fields["status"] == "failed"
        assert fields["error_code"] == "SERVICE_UNAVAILABLE"
        assert fields["failed_stage"] == "llm"
        assert isinstance(fields["prompt_build_ms"], float)
        assert isinstance(fields["llm_ms"], float)
        assert "snapshot_save_ms" not in fields

    async def test_missing_answer_block_logs_failure_after_llm(self) -> None:
        orch = _with_one_hit(
            llm_text='<USED_REFS_JSON>{"used_refs":[1]}</USED_REFS_JSON>'
        )

        fields = await _run_and_capture(orch, expected_error=ApiError)

        assert fields["status"] == "failed"
        assert fields["error_code"] == "INTERNAL_ERROR"
        assert "failed_stage" not in fields
        assert isinstance(fields["llm_ms"], float)


class TestCorrelation:
    async def test_every_db_call_shares_one_request_context(self) -> None:
        orch = _with_one_hit()

        result = await orch.execute(
            user_id=USER_ID,
            project_id=PROJECT_ID,
            query="my query",
            trace_id=TRACE_ID,
        )

        contexts = {
            call.kwargs["request_context"]
            for call in (
                orch._repo.check_corpus_readiness.call_args,
                orch._repo.fts_search.call_args,
                orch._repo.ann_search.call_args,
                orch._repo.sot_gate.call_args,
                orch._repo.save_search_response_snapshot.call_args,
                orch._repo.save_conversation.call_args,
            )
        }

        assert contexts == {
            SearchRequestContext(
                trace_id=TRACE_ID,
                req_id=result.req_id,
                user_id=USER_ID,
                project_id=PROJECT_ID,
            )
        }

    async def test_ann_call_carries_target_role_and_model_version(self) -> None:
        orch = _with_one_hit(targets=ACTIVE_AND_PREVIOUS)

        await orch.execute(
            user_id=USER_ID,
            project_id=PROJECT_ID,
            query="my query",
            trace_id=TRACE_ID,
        )

        roles = [
            (call.kwargs["target_role"], call.kwargs["model_version"])
            for call in orch._repo.ann_search.await_args_list
        ]
        assert roles == [("active", "embedding-v2"), ("previous", "embedding-v1")]


class TestParallelismPreserved:
    async def test_fts_and_ann_still_overlap(self) -> None:
        orch = _make_orchestrator()
        both_started = asyncio.Event()
        started = 0

        async def wait_for_sibling(*args, **kwargs):
            del args, kwargs
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.5)
            return []

        orch._repo.fts_search.side_effect = wait_for_sibling
        orch._repo.ann_search.side_effect = wait_for_sibling

        await orch.execute(
            user_id=USER_ID,
            project_id=PROJECT_ID,
            query="my query",
            trace_id=TRACE_ID,
        )

        assert both_started.is_set()

    async def test_active_and_previous_ann_still_overlap(self) -> None:
        orch = _make_orchestrator(targets=ACTIVE_AND_PREVIOUS)
        both_started = asyncio.Event()
        started = 0

        async def wait_for_sibling(*args, **kwargs):
            del args, kwargs
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.5)
            return []

        orch._repo.ann_search.side_effect = wait_for_sibling

        await orch.execute(
            user_id=USER_ID,
            project_id=PROJECT_ID,
            query="my query",
            trace_id=TRACE_ID,
        )

        assert both_started.is_set()


class TestNoSensitiveContent:
    async def test_timing_log_excludes_query_prompt_answer_and_chunk_text(
        self,
    ) -> None:
        chunk_id = uuid4()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=chunk_id, rank=1)],
            sot_records=[_chunk_record(chunk_id=chunk_id)],
            llm_text=(
                "<ANSWER>secret answer body</ANSWER>"
                '<USED_REFS_JSON>{"used_refs":[1]}</USED_REFS_JSON>'
            ),
        )
        orch._embedding_client.embed_query.return_value = EmbeddingResult(
            embedding=[8.675309]
        )

        with patch("src.services.search_observability.log_info") as log_info:
            await orch.execute(
                user_id=USER_ID,
                project_id=PROJECT_ID,
                query="a very private question",
                trace_id=TRACE_ID,
            )

        logged = str(log_info.call_args.kwargs)
        assert "a very private question" not in logged
        assert "secret answer body" not in logged
        assert "enriched" not in logged
        assert "8.675309" not in logged
