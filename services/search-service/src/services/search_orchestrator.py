import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from src.common.logging import warning as log_warning
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
    target: ServingSearchTarget
    embedding: list[float] # 질문쿼리


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
        req_id = uuid4()

        readiness = await self._repo.check_corpus_readiness(user_id, project_id)
        if readiness.total_videos == 0:
            raise NoVideosUploadedError()
        if readiness.non_ready_count > 0:
            raise SearchNotReadyError()

        targets, fts_results, ann_results = await self._retrieve_once(
            user_id=user_id,
            project_id=project_id,
            query=query,
            trace_id=trace_id,
        )

        merged = self._merge(fts_results, ann_results)
        if not merged:
            return await self._empty_result_with_conversation(
                req_id=req_id,
                user_id=user_id,
                project_id=project_id,
                query=query,
                trace_id=trace_id,
            )

        records = await self._sot_gate(user_id, project_id, merged)
        if not records:
            return await self._empty_result_with_conversation(
                req_id=req_id,
                user_id=user_id,
                project_id=project_id,
                query=query,
                trace_id=trace_id,
            )

        ordered_records = self._order_records(merged, records)
        chunks = self._build_chunks(ordered_records)

        answer, used_refs = await self._generate_answer(
            query=query,
            ordered_records=ordered_records,
            trace_id=trace_id,
        )
        chunks = self._apply_used_refs(chunks, used_refs)

        await self._save_snapshot(
            req_id=req_id,
            user_id=user_id,
            project_id=project_id,
            query=query,
            chunks=chunks,
            targets=targets,
            trace_id=trace_id,
        )
        await self._save_conversation(
            req_id=req_id,
            user_id=user_id,
            project_id=project_id,
            query=query,
            answer=answer,
            chunks=chunks,
            trace_id=trace_id,
        )

        return SearchResult(req_id=req_id, answer=answer, chunks=chunks)

    # ------------------------------------------------------------------
    # Private steps
    # ------------------------------------------------------------------

    async def _retrieve_once(
        self,
        *,
        user_id: UUID,
        project_id: UUID,
        query: str,
        trace_id: str,
    ) -> tuple[
        ServingSearchTargets,
        list[FTSCandidate],
        list[ANNCandidate],
    ]:
        targets = self._serving_target_provider.get()
        target_embeddings = await self._embed_query_targets(query, trace_id, targets)
        fts_results, ann_results = await self._retrieve(
            user_id, project_id, query, target_embeddings
        )
        return targets, fts_results, ann_results

    async def _embed_query_targets(
        self,
        query: str,
        trace_id: str,
        targets: ServingSearchTargets,
    ) -> list[_TargetQueryEmbedding]:
        embeddings: list[_TargetQueryEmbedding] = []
        for _, target in targets.target_entries:
            result = await self._embedding_client.embed_query(
                query,
                trace_id=trace_id,
                model_version=target.model_version,
            )
            embeddings.append(
                _TargetQueryEmbedding(
                    target=target,
                    embedding=result.embedding,
                )
            )
        return embeddings

    async def _retrieve(
        self,
        user_id: UUID,
        project_id: UUID,
        query: str,
        target_embeddings: list[_TargetQueryEmbedding],
    ) -> tuple[list[FTSCandidate], list[ANNCandidate]]:
        fts_task = self._repo.fts_search(
            user_id, project_id, query, top_k=self._search_top_k
        )
        ann_tasks = [
            self._repo.ann_search(
                user_id,
                project_id,
                target_embedding.embedding,
                target_embedding.target.index_name,
                top_k=self._search_top_k,
            )
            for target_embedding in target_embeddings
        ]
        results = await asyncio.gather(fts_task, *ann_tasks)
        ann_results: list[ANNCandidate] = []
        for result in results[1:]:
            ann_results.extend(result)
        return results[0], ann_results

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
        user_id: UUID,
        project_id: UUID,
        merged: list[RRFCandidate],
    ) -> list[ChunkRecord]:
        chunk_ids = [c.chunk_id for c in merged]
        return await self._repo.sot_gate(user_id, project_id, chunk_ids)

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

    async def _save_snapshot(
        self,
        *,
        req_id: UUID,
        user_id: UUID,
        project_id: UUID,
        query: str,
        chunks: list[ChunkResponse],
        targets: ServingSearchTargets,
        trace_id: str,
    ) -> None:
        if not chunks:
            return

        snapshot = SearchResponseSnapshotWrite(
            req_id=req_id,
            user_id=user_id,
            project_id=project_id,
            query_text=query,
            topk_chunk_ids=[str(chunk.chunk_id) for chunk in chunks],
            used_chunk_ids=[str(chunk.chunk_id) for chunk in chunks if chunk.used],
            active_model_version=targets.active.model_version,
            active_index_name=targets.active.index_name,
            served_vector_paths=targets.served_vector_paths,
            project_serving_state="SERVABLE",
            expires_at=datetime.now(UTC) + timedelta(hours=self._snapshot_ttl_hours),
        )
        try:
            await self._repo.save_search_response_snapshot(snapshot)
        except Exception as exc:
            log_warning(
                "search.snapshot_write_failed",
                trace_id=trace_id,
                req_id=str(req_id),
                error=str(exc),
            )

    async def _save_conversation(
        self,
        *,
        req_id: UUID,
        user_id: UUID,
        project_id: UUID,
        query: str,
        answer: str,
        chunks: list[ChunkResponse],
        trace_id: str,
    ) -> None:
        conversation = SearchConversationWrite(
            req_id=req_id,
            user_id=user_id,
            project_id=project_id,
            query=query,
            answer=answer,
            sources=self._conversation_sources(chunks),
        )
        try:
            await self._repo.save_conversation(conversation)
        except Exception as exc:
            log_warning(
                "search.conversation_write_failed",
                trace_id=trace_id,
                req_id=str(req_id),
                error=str(exc),
            )

    async def _empty_result_with_conversation(
        self,
        *,
        req_id: UUID,
        user_id: UUID,
        project_id: UUID,
        query: str,
        trace_id: str,
    ) -> SearchResult:
        result = _empty_result(req_id)
        await self._save_conversation(
            req_id=req_id,
            user_id=user_id,
            project_id=project_id,
            query=query,
            answer=result.answer,
            chunks=result.chunks,
            trace_id=trace_id,
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
