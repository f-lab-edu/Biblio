import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from src.common.logging import warning as log_warning
from src.common.observability import SearchRequestContext
from src.infra.db.search_repository import (
    ANNCandidate,
    ChunkRecord,
    FTSCandidate,
    SearchRepository,
    SearchConversationWrite,
    SearchResponseSnapshotWrite,
    ServingSearchTarget,
    ServingSearchTargets,
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
from src.services.search_observability import (
    QUERY_EMBEDDING_STAGES,
    VECTOR_SEARCH_STAGES,
    SearchTimingRecorder,
)
from src.services.serving_targets import ServingSearchTargetProvider
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

# Query embedding이 검색해야 할 model/index target
@dataclass(frozen=True, slots=True)
class _TargetQueryEmbedding:
    role: str
    target: ServingSearchTarget
    embedding: list[float] # 질문쿼리


# LLM 호출 입력과 ref 상한 계산에 쓰이는 context 개수
@dataclass(frozen=True, slots=True)
class _LLMPrompt:
    system: str
    user: str
    context_count: int


def _empty_result(req_id: UUID) -> SearchResult:
    return SearchResult(req_id=req_id, answer=EMPTY_ANSWER, chunks=[])


class SearchOrchestrator:
    def __init__(
        self,
        *,
        repo: SearchRepository,
        embedding_client: EmbeddingClient,
        llm_adapter: LLMAdapter,
        serving_target_provider: ServingSearchTargetProvider,
        search_top_k: int = 20,
        final_top_k: int = 5,
        rrf_k: int = 60,
        snapshot_ttl_hours: int = 168,
    ) -> None:
        self._repo = repo
        self._serving_target_provider = serving_target_provider
        self._embedding_client = embedding_client
        self._llm_adapter = llm_adapter
        self._search_top_k = search_top_k
        self._final_top_k = final_top_k
        self._rrf_k = rrf_k
        self._snapshot_ttl_hours = snapshot_ttl_hours

    async def execute(
        self,
        *,
        user_id: UUID,
        project_id: UUID,
        query: str,
        trace_id: str,
    ) -> SearchResult:
        context = SearchRequestContext(
            trace_id=trace_id,
            req_id=uuid4(),
            user_id=user_id,
            project_id=project_id,
        )
        timings = SearchTimingRecorder(context)
        try:
            result = await self._execute_measured(
                context=context, query=query, timings=timings
            )
        except BaseException as exc:
            timings.log_failure(exc)
            raise

        if result.chunks:
            timings.log_success()
        else:
            timings.log_empty()
        return result

    # ------------------------------------------------------------------
    # Private steps
    # ------------------------------------------------------------------

    async def _execute_measured(
        self,
        *,
        context: SearchRequestContext,
        query: str,
        timings: SearchTimingRecorder,
    ) -> SearchResult:
        await self._check_readiness(context)

        targets, fts_results, ann_results = await self._retrieve_once(
            context=context, query=query, timings=timings
        )

        merged = self._merge(fts_results, ann_results)
        if not merged:
            return await self._empty_result_with_conversation(context, query)

        records = await self._sot_gate(context, merged, timings)
        if not records:
            return await self._empty_result_with_conversation(context, query)

        ordered_records = self._order_records(merged, records)
        chunks = self._build_chunks(ordered_records)

        answer, used_refs = await self._generate_answer(
            context=context,
            query=query,
            ordered_records=ordered_records,
            timings=timings,
        )
        chunks = self._apply_used_refs(chunks, used_refs)

        await self._save_snapshot(
            context=context,
            query=query,
            chunks=chunks,
            targets=targets,
            timings=timings,
        )
        await self._save_conversation(
            context=context,
            query=query,
            answer=answer,
            chunks=chunks,
        )

        return SearchResult(req_id=context.req_id, answer=answer, chunks=chunks)

    async def _check_readiness(self, context: SearchRequestContext) -> None:
        readiness = await self._repo.check_corpus_readiness(
            context.user_id,
            context.project_id,
            request_context=context,
        )
        if readiness.total_videos == 0:
            raise NoVideosUploadedError()
        if readiness.non_ready_count > 0:
            raise SearchNotReadyError()

    async def _retrieve_once(
        self,
        *,
        context: SearchRequestContext,
        query: str,
        timings: SearchTimingRecorder,
    ) -> tuple[
        ServingSearchTargets,
        list[FTSCandidate],
        list[ANNCandidate],
    ]:
        targets = self._serving_target_provider.get()
        timings.set_target_count(len(targets.target_entries))
        target_embeddings = await self._embed_query_targets(
            context, query, targets, timings
        )
        fts_results, ann_results = await self._retrieve(
            context, query, target_embeddings, timings
        )
        return targets, fts_results, ann_results

    async def _embed_query_targets(
        self,
        context: SearchRequestContext,
        query: str,
        targets: ServingSearchTargets,
        timings: SearchTimingRecorder,
    ) -> list[_TargetQueryEmbedding]:
        with timings.measure("query_embedding"):
            return await asyncio.gather(
                *(
                    self._embed_one_target(context, query, role, target, timings)
                    for role, target in targets.target_entries
                )
            )

    async def _embed_one_target(
        self,
        context: SearchRequestContext,
        query: str,
        role: str,
        target: ServingSearchTarget,
        timings: SearchTimingRecorder,
    ) -> _TargetQueryEmbedding:
        with timings.measure(QUERY_EMBEDDING_STAGES[role]):
            result = await self._embedding_client.embed_query(
                query,
                trace_id=context.trace_id,
                model_version=target.model_version,
            )
        return _TargetQueryEmbedding(
            role=role, target=target, embedding=result.embedding
        )

    async def _retrieve(
        self,
        context: SearchRequestContext,
        query: str,
        target_embeddings: list[_TargetQueryEmbedding],
        timings: SearchTimingRecorder,
    ) -> tuple[list[FTSCandidate], list[ANNCandidate]]:
        return await asyncio.gather(
            self._fts_search(context, query, timings),
            self._vector_search(context, target_embeddings, timings),
        )

    async def _fts_search(
        self,
        context: SearchRequestContext,
        query: str,
        timings: SearchTimingRecorder,
    ) -> list[FTSCandidate]:
        with timings.measure("fts"):
            return await self._repo.fts_search(
                context.user_id,
                context.project_id,
                query,
                top_k=self._search_top_k,
                request_context=context,
            )

    async def _vector_search(
        self,
        context: SearchRequestContext,
        target_embeddings: list[_TargetQueryEmbedding],
        timings: SearchTimingRecorder,
    ) -> list[ANNCandidate]:
        with timings.measure("vector_search"):
            per_target_results = await asyncio.gather(
                *(
                    self._ann_search_one_target(context, target_embedding, timings)
                    for target_embedding in target_embeddings
                )
            )
        ann_results: list[ANNCandidate] = []
        for result in per_target_results:
            ann_results.extend(result)
        return ann_results

    async def _ann_search_one_target(
        self,
        context: SearchRequestContext,
        target_embedding: _TargetQueryEmbedding,
        timings: SearchTimingRecorder,
    ) -> list[ANNCandidate]:
        target = target_embedding.target
        with timings.measure(VECTOR_SEARCH_STAGES[target_embedding.role]):
            return await self._repo.ann_search(
                context.user_id,
                context.project_id,
                target_embedding.embedding,
                target.index_name,
                top_k=self._search_top_k,
                request_context=context,
                target_role=target_embedding.role,
                model_version=target.model_version,
            )

    def _merge(
        self,
        fts_results: list[FTSCandidate],
        ann_results: list[ANNCandidate],
    ) -> list[RRFCandidate]:
        return rrf_merge(
            fts_results, ann_results, k=self._rrf_k, top_k=self._final_top_k
        )

    async def _sot_gate(
        self,
        context: SearchRequestContext,
        merged: list[RRFCandidate],
        timings: SearchTimingRecorder,
    ) -> list[ChunkRecord]:
        chunk_ids = [c.chunk_id for c in merged]
        with timings.measure("sot_gate"):
            return await self._repo.sot_gate(
                context.user_id,
                context.project_id,
                chunk_ids,
                request_context=context,
            )

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
        context: SearchRequestContext,
        query: str,
        ordered_records: list[ChunkRecord],
        timings: SearchTimingRecorder,
    ) -> tuple[str, list[int]]:
        with timings.measure("prompt_build"):
            prompt = self._build_prompt(query, ordered_records)

        llm_text = await self._call_llm(prompt, context.trace_id, timings)
        return self._parse_answer(llm_text, prompt.context_count, context.trace_id)

    @staticmethod
    def _build_prompt(
        query: str,
        ordered_records: list[ChunkRecord],
    ) -> _LLMPrompt:
        contexts = build_context_blocks(ordered_records)
        return _LLMPrompt(
            system=build_system_prompt(),
            user=build_user_prompt(query=query, contexts=contexts),
            context_count=len(contexts),
        )

    async def _call_llm(
        self,
        prompt: _LLMPrompt,
        trace_id: str,
        timings: SearchTimingRecorder,
    ) -> str:
        try:
            with timings.measure("llm"):
                llm_result = await self._llm_adapter.generate(
                    prompt.system, prompt.user, trace_id=trace_id
                )
        except LLMAdapterError as exc:
            if exc.retryable:
                raise ServiceUnavailableError(exc.message) from exc
            raise ApiError(exc.message) from exc
        return llm_result.text

    def _parse_answer(
        self,
        llm_text: str,
        context_count: int,
        trace_id: str,
    ) -> tuple[str, list[int]]:
        self._log_answer_fallback_if_needed(llm_text, trace_id)

        try:
            answer = extract_answer(llm_text)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc

        used_refs = parse_used_refs(llm_text, max_ref=context_count)
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

    async def _save_snapshot(
        self,
        *,
        context: SearchRequestContext,
        query: str,
        chunks: list[ChunkResponse],
        targets: ServingSearchTargets,
        timings: SearchTimingRecorder,
    ) -> None:
        if not chunks:
            return

        with timings.measure("snapshot_save"):
            snapshot = self._build_snapshot(context, query, chunks, targets)
            try:
                await self._repo.save_search_response_snapshot(
                    snapshot, request_context=context
                )
            except Exception as exc:
                log_warning(
                    "search.snapshot_write_failed",
                    trace_id=context.trace_id,
                    req_id=str(context.req_id),
                    error=str(exc),
                )

    def _build_snapshot(
        self,
        context: SearchRequestContext,
        query: str,
        chunks: list[ChunkResponse],
        targets: ServingSearchTargets,
    ) -> SearchResponseSnapshotWrite:
        return SearchResponseSnapshotWrite(
            req_id=context.req_id,
            user_id=context.user_id,
            project_id=context.project_id,
            query_text=query,
            topk_chunk_ids=[str(chunk.chunk_id) for chunk in chunks],
            used_chunk_ids=[str(chunk.chunk_id) for chunk in chunks if chunk.used],
            active_model_version=targets.active.model_version,
            active_index_name=targets.active.index_name,
            served_vector_paths=targets.served_vector_paths,
            project_serving_state="SERVABLE",
            expires_at=datetime.now(UTC) + timedelta(hours=self._snapshot_ttl_hours),
        )

    async def _save_conversation(
        self,
        *,
        context: SearchRequestContext,
        query: str,
        answer: str,
        chunks: list[ChunkResponse],
    ) -> None:
        conversation = SearchConversationWrite(
            req_id=context.req_id,
            user_id=context.user_id,
            project_id=context.project_id,
            query=query,
            answer=answer,
            sources=self._conversation_sources(chunks),
        )
        try:
            await self._repo.save_conversation(conversation, request_context=context)
        except Exception as exc:
            log_warning(
                "search.conversation_write_failed",
                trace_id=context.trace_id,
                req_id=str(context.req_id),
                error=str(exc),
            )

    async def _empty_result_with_conversation(
        self,
        context: SearchRequestContext,
        query: str,
    ) -> SearchResult:
        result = _empty_result(context.req_id)
        await self._save_conversation(
            context=context,
            query=query,
            answer=result.answer,
            chunks=result.chunks,
        )
        return result

    @staticmethod
    def _conversation_sources(
        chunks: list[ChunkResponse],
    ) -> list[dict[str, object]]:
        return [
            {
                "ref": chunk.ref,
                "chunk_id": str(chunk.chunk_id),
                "video_id": str(chunk.video_id),
                "title": chunk.title,
                "start_ms": chunk.start_ms,
                "end_ms": chunk.end_ms,
                "used": chunk.used,
            }
            for chunk in chunks
        ]
