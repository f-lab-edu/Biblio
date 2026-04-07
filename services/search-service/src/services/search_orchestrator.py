"""Search Orchestrator — hybrid search pipeline.

Implements SPEC §3.1 steps 5-16:
  5. Whole-corpus readiness gate
  6. Searchable corpus existence check
  7. Query embedding
  8. FTS/ANN parallel retrieval
  9. RRF merge
 10. SOT serving gate
 11. Empty result check
 12. Citation ref assignment
 13. chunks assembly
 14. prompt builder + LLM call
 15. response parsing + used_refs interpretation
 16. final answer return
"""

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

from src.common.logging import warning as log_warning
from src.infra.db.search_repository import (
    ANNCandidate,
    ChunkRecord,
    FTSCandidate,
    SearchRepository,
)
from src.infra.embedding.client import EmbeddingClient
from src.infra.llm.base import LLMAdapter, LLMAdapterError
from src.middlewares.error_handler import (
    ApiError,
    NoVideosUploadedError,
    SearchNotReadyError,
    ServiceUnavailableError,
)
from src.schemas.search_dto import EMPTY_ANSWER, ChunkResponse
from src.services.prompt_builder import (
    build_context_blocks,
    build_system_prompt,
    build_user_prompt,
)
from src.services.rrf import RRFCandidate, rrf_merge
from src.services.used_refs_parser import (
    count_answer_blocks,
    count_used_refs_blocks,
    extract_answer,
    parse_used_refs,
)


@dataclass(slots=True)
class SearchResult:
    req_id: UUID
    answer: str
    chunks: list[ChunkResponse]


def _empty_result(req_id: UUID) -> SearchResult:
    return SearchResult(req_id=req_id, answer=EMPTY_ANSWER, chunks=[])


class SearchOrchestrator:
    def __init__(
        self,
        *,
        repo: SearchRepository,
        embedding_client: EmbeddingClient,
        llm_adapter: LLMAdapter,
        search_top_k: int = 20,
        final_top_k: int = 5,
        rrf_k: int = 60,
    ) -> None:
        self._repo = repo
        self._embedding_client = embedding_client
        self._llm_adapter = llm_adapter
        self._search_top_k = search_top_k
        self._final_top_k = final_top_k
        self._rrf_k = rrf_k

    async def execute(
        self,
        *,
        user_id: UUID,
        query: str,
        trace_id: str,
    ) -> SearchResult:
        req_id = uuid4()

        # Steps 5-6: Single-query corpus readiness gate
        readiness = await self._repo.check_corpus_readiness(user_id)
        if readiness.total_videos == 0:
            raise NoVideosUploadedError()
        if readiness.non_ready_count > 0:
            raise SearchNotReadyError()

        # Steps 7-8
        query_embedding = await self._embed_query(query, trace_id)
        fts_results, ann_results = await self._retrieve(
            user_id, query, query_embedding
        )

        # Steps 9-10
        merged = self._merge(fts_results, ann_results)
        if not merged:
            return _empty_result(req_id)

        records = await self._sot_gate(user_id, merged)
        if not records:
            return _empty_result(req_id)

        # Steps 12-13
        ordered_records = self._order_records(merged, records)
        chunks = self._build_chunks(ordered_records)

        # Steps 14-16
        answer, used_refs = await self._generate_answer(
            query=query,
            ordered_records=ordered_records,
            trace_id=trace_id,
        )
        chunks = self._apply_used_refs(chunks, used_refs)

        return SearchResult(req_id=req_id, answer=answer, chunks=chunks)

    # ------------------------------------------------------------------
    # Private steps
    # ------------------------------------------------------------------

    async def _embed_query(self, query: str, trace_id: str) -> list[float]:
        """Step 6: Query embedding via Managed Embedding Endpoint."""
        result = await self._embedding_client.embed_query(query, trace_id=trace_id)
        return result.embedding

    async def _retrieve(
        self,
        user_id: UUID,
        query: str,
        query_embedding: list[float],
    ) -> tuple[list[FTSCandidate], list[ANNCandidate]]:
        """Step 7: FTS/ANN parallel retrieval."""
        return await asyncio.gather(
            self._repo.fts_search(user_id, query, top_k=self._search_top_k),
            self._repo.ann_search(
                user_id, query_embedding, top_k=self._search_top_k
            ),
        )

    def _merge(
        self,
        fts_results: list[FTSCandidate],
        ann_results: list[ANNCandidate],
    ) -> list[RRFCandidate]:
        """Step 8: RRF merge, limited to FINAL_TOP_K."""
        return rrf_merge(
            fts_results, ann_results, k=self._rrf_k, top_k=self._final_top_k
        )

    async def _sot_gate(
        self,
        user_id: UUID,
        merged: list[RRFCandidate],
    ) -> list[ChunkRecord]:
        """Step 9: SOT serving gate — verify ownership and READY status."""
        chunk_ids = [c.chunk_id for c in merged]
        return await self._repo.sot_gate(user_id, chunk_ids)

    @staticmethod
    def _order_records(
        merged: list[RRFCandidate],
        records: list[ChunkRecord],
    ) -> list[ChunkRecord]:
        rank_map = {c.chunk_id: i for i, c in enumerate(merged)}
        matched = [r for r in records if r.chunk_id in rank_map]
        return sorted(matched, key=lambda r: rank_map[r.chunk_id])

    @staticmethod
    def _build_chunks(
        ordered_records: list[ChunkRecord],
    ) -> list[ChunkResponse]:
        """Steps 11-12: Assign ref by RRF rank, build chunks in ref ASC order."""
        return [
            ChunkResponse(
                ref=i + 1,
                chunk_id=r.chunk_id,
                video_id=r.video_id,
                title=r.title,
                start_ms=r.start_ms,
                end_ms=r.end_ms,
                text=r.text,
                used=False,
            )
            for i, r in enumerate(ordered_records)
        ]

    async def _generate_answer(
        self,
        *,
        query: str,
        ordered_records: list[ChunkRecord],
        trace_id: str,
    ) -> tuple[str, list[int]]:
        contexts = build_context_blocks(ordered_records)
        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(query=query, contexts=contexts)

        try:
            llm_result = await self._llm_adapter.generate(
                system_prompt, user_prompt, trace_id=trace_id
            )
        except LLMAdapterError as exc:
            if exc.retryable:
                raise ServiceUnavailableError(exc.message) from exc
            raise ApiError(exc.message) from exc

        self._log_answer_fallback_if_needed(llm_result.text, trace_id)

        try:
            answer = extract_answer(llm_result.text)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc

        used_refs = parse_used_refs(llm_result.text, max_ref=len(contexts))
        return answer, used_refs

    @staticmethod
    def _log_answer_fallback_if_needed(llm_text: str, trace_id: str) -> None:
        answer_block_count = count_answer_blocks(llm_text)
        if answer_block_count != 0:
            return

        log_warning(
            "search.llm_answer_fallback",
            trace_id=trace_id,
            answer_block_count=answer_block_count,
            used_refs_block_count=count_used_refs_blocks(llm_text),
            response_chars=len(llm_text),
            response_preview=llm_text[:200],
        )

    @staticmethod
    def _apply_used_refs(
        chunks: list[ChunkResponse],
        used_refs: list[int],
    ) -> list[ChunkResponse]:
        used_ref_set = set(used_refs)
        return [
            chunk.model_copy(update={"used": chunk.ref in used_ref_set})
            for chunk in chunks
        ]
