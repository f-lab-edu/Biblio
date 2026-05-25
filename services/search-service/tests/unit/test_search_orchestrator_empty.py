"""Unit tests for SearchOrchestrator — readiness / empty / retrieval flow.

Tests: no-videos 409, readiness gate 409, final-empty return,
       RRF→SOT→chunks 조립, ref ASC ordering, FINAL_TOP_K 제한.
LLM 응답 파싱 및 used_refs는 Task 5 (test_search_orchestrator_answer.py).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src.infra.db.search_repository import (
    ANNCandidate,
    ChunkRecord,
    CorpusReadiness,
    FTSCandidate,
    SearchRepository,
)
from src.infra.embedding.client import EmbeddingClient, EmbeddingResult
from src.infra.llm.base import LLMAdapter, LLMGenerationResult
from src.middlewares.error_handler import NoVideosUploadedError, SearchNotReadyError
from src.schemas.search_dto import EMPTY_ANSWER
from src.services.search_orchestrator import SearchOrchestrator

USER_ID = uuid4()
PROJECT_ID = uuid4()
TRACE_ID = str(uuid4())


def _make_orchestrator(
    *,
    total_videos: int = 1,
    non_ready_count: int = 0,
    fts_results: list[FTSCandidate] | None = None,
    ann_results: list[ANNCandidate] | None = None,
    sot_records: list[ChunkRecord] | None = None,
    embedding: list[float] | None = None,
    llm_text: str = "<ANSWER>mock answer</ANSWER>\n<USED_REFS_JSON>{\"used_refs\":[]}</USED_REFS_JSON>",
    search_top_k: int = 20,
    final_top_k: int = 5,
    rrf_k: int = 60,
    snapshot_ttl_hours: int = 168,
) -> SearchOrchestrator:
    repo = AsyncMock(spec=SearchRepository)
    repo.check_corpus_readiness.return_value = CorpusReadiness(
        total_videos=total_videos, non_ready_count=non_ready_count,
    )
    repo.fts_search.return_value = fts_results or []
    repo.ann_search.return_value = ann_results or []
    repo.sot_gate.return_value = sot_records or []
    repo.get_active_search_target = AsyncMock(
        return_value=SimpleNamespace(
            model_version="embedding-v1",
            index_name="active-index",
        )
    )
    repo.save_search_response_snapshot = AsyncMock()

    embedding_client = AsyncMock(spec=EmbeddingClient)
    embedding_client.embed_query.return_value = EmbeddingResult(
        embedding=embedding or [0.1, 0.2, 0.3]
    )

    llm_adapter = AsyncMock(spec=LLMAdapter)
    llm_adapter.generate.return_value = LLMGenerationResult(text=llm_text)

    return SearchOrchestrator(
        repo=repo,
        embedding_client=embedding_client,
        llm_adapter=llm_adapter,
        search_top_k=search_top_k,
        final_top_k=final_top_k,
        rrf_k=rrf_k,
        snapshot_ttl_hours=snapshot_ttl_hours,
    )


def _chunk_record(
    chunk_id=None, video_id=None, title="Video", text="text",
    enriched="enriched", start_ms=0, end_ms=5000,
):
    return ChunkRecord(
        chunk_id=chunk_id or uuid4(),
        video_id=video_id or uuid4(),
        title=title,
        text=text,
        enriched_text=enriched,
        start_ms=start_ms,
        end_ms=end_ms,
    )


class TestReadinessGate:
    async def test_non_ready_videos_raise_409(self) -> None:
        orch = _make_orchestrator(non_ready_count=1)

        with pytest.raises(SearchNotReadyError):
            await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

    async def test_non_ready_skips_all_expensive_calls(self) -> None:
        orch = _make_orchestrator(non_ready_count=1)

        with pytest.raises(SearchNotReadyError):
            await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

        orch._embedding_client.embed_query.assert_not_called()
        orch._repo.fts_search.assert_not_called()
        orch._repo.ann_search.assert_not_called()
        orch._llm_adapter.generate.assert_not_called()


class TestCorpusEmpty:
    async def test_no_videos_uploaded_raises_409(self) -> None:
        orch = _make_orchestrator(total_videos=0)

        with pytest.raises(NoVideosUploadedError):
            await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

    async def test_no_videos_uploaded_skips_all_downstream_calls(self) -> None:
        orch = _make_orchestrator(total_videos=0)

        with pytest.raises(NoVideosUploadedError):
            await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

        orch._embedding_client.embed_query.assert_not_called()
        orch._repo.fts_search.assert_not_called()
        orch._repo.ann_search.assert_not_called()
        orch._llm_adapter.generate.assert_not_called()


class TestFinalEmpty:
    async def test_sot_gate_returns_nothing(self) -> None:
        """FTS/ANN find candidates but SOT gate filters all out."""
        cid = uuid4()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=cid, rank=1)],
            ann_results=[],
            sot_records=[],  # SOT gate rejects everything
        )
        result = await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

        assert result.answer == EMPTY_ANSWER
        assert result.chunks == []

    async def test_fts_ann_both_empty(self) -> None:
        orch = _make_orchestrator(
            fts_results=[],
            ann_results=[],
        )
        result = await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

        assert result.answer == EMPTY_ANSWER
        assert result.chunks == []

    async def test_final_empty_skips_llm(self) -> None:
        orch = _make_orchestrator(
            fts_results=[],
            ann_results=[],
        )
        await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

        orch._llm_adapter.generate.assert_not_called()


class TestRetrievalFlow:
    async def test_project_scope_is_passed_to_all_repository_reads(self) -> None:
        chunk_id = uuid4()
        orch = _make_orchestrator(
            search_top_k=15,
            fts_results=[FTSCandidate(chunk_id=chunk_id, rank=1)],
            sot_records=[],
        )
        await orch.execute(
            user_id=USER_ID,
            project_id=PROJECT_ID,
            query="my query",
            trace_id=TRACE_ID,
        )

        orch._repo.check_corpus_readiness.assert_called_once_with(
            USER_ID, PROJECT_ID
        )
        orch._repo.fts_search.assert_called_once_with(
            USER_ID, PROJECT_ID, "my query", top_k=15
        )
        orch._repo.ann_search.assert_called_once()
        ann_args = orch._repo.ann_search.call_args
        assert ann_args[0][0] == USER_ID
        assert ann_args[0][1] == PROJECT_ID
        orch._repo.sot_gate.assert_called_once()
        sot_args = orch._repo.sot_gate.call_args
        assert sot_args[0][0] == USER_ID
        assert sot_args[0][1] == PROJECT_ID

    async def test_fts_ann_called_with_correct_args(self) -> None:
        orch = _make_orchestrator(search_top_k=15)
        await orch.execute(
            user_id=USER_ID,
            project_id=PROJECT_ID,
            query="my query",
            trace_id=TRACE_ID,
        )

        orch._repo.fts_search.assert_called_once_with(
            USER_ID, PROJECT_ID, "my query", top_k=15
        )
        orch._repo.ann_search.assert_called_once()
        call_args = orch._repo.ann_search.call_args
        assert call_args[0][0] == USER_ID
        assert call_args[0][1] == PROJECT_ID
        assert call_args[0][3] == "active-index"
        assert call_args[1]["top_k"] == 15

    async def test_embedding_called_with_query(self) -> None:
        orch = _make_orchestrator()
        await orch.execute(
            user_id=USER_ID,
            project_id=PROJECT_ID,
            query="search term",
            trace_id=TRACE_ID,
        )

        orch._embedding_client.embed_query.assert_called_once_with(
            "search term", trace_id=TRACE_ID
        )

    async def test_active_target_is_loaded_before_retrieval(self) -> None:
        cid = uuid4()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=cid, rank=1)],
            sot_records=[_chunk_record(chunk_id=cid)],
        )

        await orch.execute(
            user_id=USER_ID,
            project_id=PROJECT_ID,
            query="search term",
            trace_id=TRACE_ID,
        )

        orch._repo.get_active_search_target.assert_called_once()

    async def test_sot_gate_receives_rrf_merged_ids(self) -> None:
        """SOT gate should receive chunk_ids from RRF merge output."""
        cid_fts = uuid4()
        cid_ann = uuid4()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=cid_fts, rank=1)],
            ann_results=[ANNCandidate(chunk_id=cid_ann, rank=1)],
            sot_records=[],
        )
        await orch.execute(
            user_id=USER_ID,
            project_id=PROJECT_ID,
            query="test",
            trace_id=TRACE_ID,
        )

        call_args = orch._repo.sot_gate.call_args
        assert call_args[0][0] == USER_ID
        assert call_args[0][1] == PROJECT_ID
        passed_ids = set(call_args[0][2])
        assert cid_fts in passed_ids
        assert cid_ann in passed_ids


class TestChunksAssembly:
    async def test_ref_starts_at_1(self) -> None:
        cid = uuid4()
        vid = uuid4()
        record = _chunk_record(chunk_id=cid, video_id=vid)
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=cid, rank=1)],
            sot_records=[record],
        )
        result = await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

        assert len(result.chunks) == 1
        assert result.chunks[0].ref == 1

    async def test_ref_asc_ordering(self) -> None:
        """Chunks should be ordered by ref ASC (which follows RRF rank)."""
        cid1 = uuid4()
        cid2 = uuid4()
        cid3 = uuid4()
        vid = uuid4()

        # cid1 has highest RRF score (appears in both FTS rank=1, ANN rank=1)
        # cid2 in FTS only rank=2
        # cid3 in ANN only rank=2
        orch = _make_orchestrator(
            fts_results=[
                FTSCandidate(chunk_id=cid1, rank=1),
                FTSCandidate(chunk_id=cid2, rank=2),
            ],
            ann_results=[
                ANNCandidate(chunk_id=cid1, rank=1),
                ANNCandidate(chunk_id=cid3, rank=2),
            ],
            sot_records=[
                _chunk_record(chunk_id=cid1, video_id=vid, title="V1"),
                _chunk_record(chunk_id=cid2, video_id=vid, title="V1"),
                _chunk_record(chunk_id=cid3, video_id=vid, title="V1"),
            ],
        )
        result = await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

        refs = [c.ref for c in result.chunks]
        assert refs == [1, 2, 3]
        # cid1 should be ref=1 (highest RRF score)
        assert result.chunks[0].chunk_id == cid1

    async def test_chunks_fields_from_sot_record(self) -> None:
        cid = uuid4()
        vid = uuid4()
        record = _chunk_record(
            chunk_id=cid, video_id=vid, title="My Video",
            text="original text", enriched="enriched text",
            start_ms=1000, end_ms=5000,
        )
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=cid, rank=1)],
            sot_records=[record],
        )
        result = await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

        chunk = result.chunks[0]
        assert chunk.chunk_id == cid
        assert chunk.video_id == vid
        assert chunk.title == "My Video"
        assert chunk.text == "original text"
        assert chunk.start_ms == 1000
        assert chunk.end_ms == 5000
        assert chunk.used is False

    async def test_used_defaults_to_false(self) -> None:
        """All chunks should have used=False initially (Task 5 sets used)."""
        cid = uuid4()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=cid, rank=1)],
            sot_records=[_chunk_record(chunk_id=cid)],
        )
        result = await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

        for chunk in result.chunks:
            assert chunk.used is False

    async def test_final_top_k_limits_chunks(self) -> None:
        """Chunks should not exceed FINAL_TOP_K."""
        records = []
        fts = []
        for i in range(10):
            cid = uuid4()
            fts.append(FTSCandidate(chunk_id=cid, rank=i + 1))
            records.append(_chunk_record(chunk_id=cid))

        orch = _make_orchestrator(
            fts_results=fts,
            sot_records=records,
            final_top_k=3,
        )
        result = await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

        assert len(result.chunks) <= 3

    async def test_sot_gate_filters_reduce_chunks(self) -> None:
        """If SOT gate rejects some candidates, only passed ones appear."""
        cid_pass = uuid4()
        cid_fail = uuid4()
        orch = _make_orchestrator(
            fts_results=[
                FTSCandidate(chunk_id=cid_pass, rank=1),
                FTSCandidate(chunk_id=cid_fail, rank=2),
            ],
            sot_records=[_chunk_record(chunk_id=cid_pass)],
        )
        result = await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)

        assert len(result.chunks) == 1
        assert result.chunks[0].chunk_id == cid_pass


class TestReqId:
    async def test_each_call_generates_unique_req_id(self) -> None:
        orch = _make_orchestrator()
        r1 = await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)
        r2 = await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)
        assert r1.req_id != r2.req_id

    async def test_empty_result_still_has_req_id(self) -> None:
        orch = _make_orchestrator()
        result = await orch.execute(user_id=USER_ID, project_id=PROJECT_ID, query="test", trace_id=TRACE_ID)
        assert isinstance(result.req_id, UUID)


class TestSnapshotPersistence:
    async def test_success_with_chunks_saves_snapshot_before_return(self) -> None:
        cid = uuid4()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=cid, rank=1)],
            sot_records=[_chunk_record(chunk_id=cid)],
            llm_text=(
                "<ANSWER>mock answer</ANSWER>\n"
                '<USED_REFS_JSON>{"used_refs":[1]}</USED_REFS_JSON>'
            ),
            snapshot_ttl_hours=12,
        )

        result = await orch.execute(
            user_id=USER_ID,
            project_id=PROJECT_ID,
            query="test",
            trace_id=TRACE_ID,
        )

        orch._repo.save_search_response_snapshot.assert_called_once()
        snapshot = orch._repo.save_search_response_snapshot.call_args.args[0]
        assert snapshot.req_id == result.req_id
        assert snapshot.user_id == USER_ID
        assert snapshot.project_id == PROJECT_ID
        assert snapshot.query_text == "test"
        assert snapshot.topk_chunk_ids == [str(cid)]
        assert snapshot.used_chunk_ids == [str(cid)]
        assert snapshot.active_model_version == "embedding-v1"
        assert snapshot.active_index_name == "active-index"
        assert snapshot.served_vector_paths == [
            {
                "role": "active",
                "model_version": "embedding-v1",
                "index_name": "active-index",
            }
        ]
        assert snapshot.project_serving_state == "SERVABLE"
        assert snapshot.expires_at > datetime.now(UTC) + timedelta(hours=11)

    async def test_empty_result_does_not_save_snapshot(self) -> None:
        orch = _make_orchestrator(fts_results=[], ann_results=[])

        await orch.execute(
            user_id=USER_ID,
            project_id=PROJECT_ID,
            query="test",
            trace_id=TRACE_ID,
        )

        orch._repo.save_search_response_snapshot.assert_not_called()

    async def test_snapshot_write_failure_does_not_block_search_response(self) -> None:
        cid = uuid4()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=cid, rank=1)],
            sot_records=[_chunk_record(chunk_id=cid)],
        )
        orch._repo.save_search_response_snapshot.side_effect = RuntimeError(
            "snapshot unavailable"
        )

        result = await orch.execute(
            user_id=USER_ID,
            project_id=PROJECT_ID,
            query="test",
            trace_id=TRACE_ID,
        )

        assert result.chunks
