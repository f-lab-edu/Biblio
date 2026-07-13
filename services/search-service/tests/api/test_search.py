"""API integration tests for POST /api/v1/search (Task 6).

Verifies: success response, no-videos 409, readiness 409, final-empty, 400/401/500/503
mappings, X-Trace-Id echo, req_id generation, chunks response structure,
answer/metadata separation, <ANSWER> block missing → 500.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import jwt
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.v1.router import api_v1_router
from src.core.config import Settings
from src.core.dependencies import (
    DependencyContainer,
    get_search_orchestrator,
    get_search_repository,
)
from src.infra.db.search_repository import (
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
    ServiceUnavailableError,
    register_exception_handlers,
)
from src.middlewares.trace import TraceIdMiddleware
from src.schemas.search_dto import EMPTY_ANSWER
from src.services.search_orchestrator import SearchOrchestrator
from src.services.serving_targets import ServingSearchTargetProvider

TEST_SECRET = "test-secret-key-for-search-service-32b"
TEST_USER_ID = uuid4()
TEST_PROJECT_ID = uuid4()
SEARCH_URL = "/api/v1/search"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    user_id: UUID | str | None = None,
    *,
    expired: bool = False,
    secret: str = TEST_SECRET,
) -> str:
    payload: dict = {}
    if user_id is not None:
        payload["requester_user_id"] = str(user_id)
    if expired:
        payload["exp"] = datetime(2020, 1, 1, tzinfo=timezone.utc)
    else:
        payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode(payload, secret, algorithm="HS256")


def _auth_headers(
    user_id: UUID | None = None,
    trace_id: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {
        "Authorization": f"Bearer {_make_token(user_id or TEST_USER_ID)}",
    }
    if trace_id is not None:
        headers["X-Trace-Id"] = trace_id
    return headers


def _auth_cookies(user_id: UUID | None = None) -> dict[str, str]:
    return {
        "biblio_access_token": _make_token(user_id or TEST_USER_ID),
        "biblio_csrf_token": "csrf-1",
    }


def _chunk_record(
    *,
    chunk_id: UUID | None = None,
    video_id: UUID | None = None,
    title: str = "Test Video",
    text: str = "original text",
    enriched_text: str = "enriched text",
    start_ms: int = 1000,
    end_ms: int = 5000,
) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=chunk_id or uuid4(),
        video_id=video_id or uuid4(),
        title=title,
        text=text,
        enriched_text=enriched_text,
        start_ms=start_ms,
        end_ms=end_ms,
    )


def _make_orchestrator(
    *,
    total_videos: int = 1,
    non_ready_count: int = 0,
    fts_results: list[FTSCandidate] | None = None,
    sot_records: list[ChunkRecord] | None = None,
    llm_text: str | None = None,
    llm_error: LLMAdapterError | None = None,
    embedding_error: Exception | None = None,
) -> SearchOrchestrator:
    repo = AsyncMock(spec=SearchRepository)
    repo.check_corpus_readiness.return_value = CorpusReadiness(
        total_videos=total_videos, non_ready_count=non_ready_count,
    )
    repo.fts_search.return_value = fts_results or []
    repo.ann_search.return_value = []
    repo.sot_gate.return_value = sot_records or []
    serving_targets = ServingSearchTargets(
        active=ServingSearchTarget(
            model_version="embedding-v1",
            index_name="active-index",
        )
    )
    repo.get_serving_search_targets.return_value = serving_targets

    embedding_client = AsyncMock(spec=EmbeddingClient)
    if embedding_error is not None:
        embedding_client.embed_query.side_effect = embedding_error
    else:
        embedding_client.embed_query.return_value = EmbeddingResult(
            embedding=[0.1, 0.2, 0.3]
        )

    llm_adapter = AsyncMock(spec=LLMAdapter)
    if llm_error is not None:
        llm_adapter.generate.side_effect = llm_error
    else:
        llm_adapter.generate.return_value = LLMGenerationResult(
            text=llm_text
            or '<ANSWER>Grounded answer [1]</ANSWER>\n'
            '<USED_REFS_JSON>{"used_refs":[1]}</USED_REFS_JSON>'
        )

    return SearchOrchestrator(
        repo=repo,
        serving_target_provider=ServingSearchTargetProvider(
            repo,
            loaded_targets=serving_targets,
        ),
        embedding_client=embedding_client,
        llm_adapter=llm_adapter,
    )


def _make_app(
    orchestrator: SearchOrchestrator,
    history_repo: AsyncMock | None = None,
) -> FastAPI:
    settings = Settings(
        JWT_SECRET_KEY=TEST_SECRET,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost/db",
        EMBEDDING_API_URL="https://localhost:8081/embed",
    )
    container = DependencyContainer(settings=settings)

    app = FastAPI()
    app.state.container = container
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(api_v1_router, prefix="/api/v1")
    app.dependency_overrides[get_search_orchestrator] = lambda: orchestrator
    if history_repo is not None:
        app.dependency_overrides[get_search_repository] = lambda: history_repo

    return app


async def _post(
    orchestrator: SearchOrchestrator,
    *,
    query: str = "test query",
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    json_body: dict | None = None,
):
    app = _make_app(orchestrator)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        if cookies is not None:
            client.cookies.update(cookies)
        return await client.post(
            SEARCH_URL,
            json=json_body
            if json_body is not None
            else {"query": query, "project_id": str(TEST_PROJECT_ID)},
            headers=headers if headers is not None else _auth_headers(),
        )


async def _get_history(
    history_repo: AsyncMock,
    *,
    user_id: UUID = TEST_USER_ID,
    project_id: UUID = TEST_PROJECT_ID,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
):
    app = _make_app(_make_orchestrator(), history_repo=history_repo)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        if cookies is not None:
            client.cookies.update(cookies)
        return await client.get(
            f"/api/v1/search/history?project_id={project_id}",
            headers=headers if headers is not None else _auth_headers(user_id),
        )


# ---------------------------------------------------------------------------
# Success scenarios
# ---------------------------------------------------------------------------


class TestSearchSuccess:
    async def test_returns_200_with_req_id_answer_chunks(self) -> None:
        record = _chunk_record()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=record.chunk_id, rank=1)],
            sot_records=[record],
        )
        resp = await _post(orch)

        assert resp.status_code == 200
        body = resp.json()
        UUID(body["req_id"])  # valid UUID
        assert body["answer"] == "Grounded answer [1]"
        assert len(body["chunks"]) == 1

    async def test_chunks_have_correct_structure(self) -> None:
        cid = uuid4()
        vid = uuid4()
        record = _chunk_record(
            chunk_id=cid, video_id=vid, title="My Video",
            text="raw text", enriched_text="enriched text",
            start_ms=1000, end_ms=5000,
        )
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=cid, rank=1)],
            sot_records=[record],
        )
        resp = await _post(orch)
        chunk = resp.json()["chunks"][0]

        assert chunk["ref"] == 1
        assert chunk["chunk_id"] == str(cid)
        assert chunk["video_id"] == str(vid)
        assert chunk["title"] == "My Video"
        assert chunk["start_ms"] == 1000
        assert chunk["end_ms"] == 5000
        assert chunk["text"] == "raw text"
        assert isinstance(chunk["used"], bool)

    async def test_used_true_for_referenced_chunks(self) -> None:
        record = _chunk_record()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=record.chunk_id, rank=1)],
            sot_records=[record],
            llm_text=(
                "<ANSWER>Answer citing [1]</ANSWER>\n"
                '<USED_REFS_JSON>{"used_refs":[1]}</USED_REFS_JSON>'
            ),
        )
        resp = await _post(orch)
        chunks = resp.json()["chunks"]

        assert chunks[0]["used"] is True

    async def test_answer_excludes_metadata_block(self) -> None:
        record = _chunk_record()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=record.chunk_id, rank=1)],
            sot_records=[record],
        )
        resp = await _post(orch)
        answer = resp.json()["answer"]

        assert "USED_REFS_JSON" not in answer
        assert "<ANSWER>" not in answer

    async def test_ref_asc_ordering(self) -> None:
        cid1 = uuid4()
        cid2 = uuid4()
        r1 = _chunk_record(chunk_id=cid1, title="V1")
        r2 = _chunk_record(chunk_id=cid2, title="V2")
        orch = _make_orchestrator(
            fts_results=[
                FTSCandidate(chunk_id=cid1, rank=1),
                FTSCandidate(chunk_id=cid2, rank=2),
            ],
            sot_records=[r1, r2],
            llm_text=(
                "<ANSWER>Answer [1][2]</ANSWER>\n"
                '<USED_REFS_JSON>{"used_refs":[1,2]}</USED_REFS_JSON>'
            ),
        )
        resp = await _post(orch)
        refs = [c["ref"] for c in resp.json()["chunks"]]

        assert refs == [1, 2]


class TestSearchHistory:
    async def test_get_history_uses_authenticated_user_and_maps_turns(
        self,
    ) -> None:
        req_id = uuid4()
        chunk_id = uuid4()
        video_id = uuid4()
        history_repo = AsyncMock()
        history_repo.list_conversations_for_project.return_value = [
            SimpleNamespace(
                query="예전 질문",
                req_id=req_id,
                answer="예전 답변",
                sources=[
                    {
                        "ref": 1,
                        "chunk_id": str(chunk_id),
                        "video_id": str(video_id),
                        "title": "강의1",
                        "start_ms": 1000,
                        "end_ms": 2000,
                        "used": True,
                    }
                ],
            )
        ]

        resp = await _get_history(history_repo)

        assert resp.status_code == 200
        history_repo.list_conversations_for_project.assert_awaited_once_with(
            TEST_USER_ID, TEST_PROJECT_ID
        )
        assert resp.json() == [
            {
                "query": "예전 질문",
                "reqId": str(req_id),
                "answer": "예전 답변",
                "chunks": [
                    {
                        "ref": 1,
                        "chunk_id": str(chunk_id),
                        "video_id": str(video_id),
                        "title": "강의1",
                        "start_ms": 1000,
                        "end_ms": 2000,
                        "used": True,
                    }
                ],
            }
        ]

    async def test_get_history_accepts_cookie_auth_without_csrf(
        self,
    ) -> None:
        history_repo = AsyncMock()
        history_repo.list_conversations_for_project.return_value = []

        resp = await _get_history(
            history_repo,
            headers={},
            cookies=_auth_cookies(TEST_USER_ID),
        )

        assert resp.status_code == 200
        history_repo.list_conversations_for_project.assert_awaited_once_with(
            TEST_USER_ID, TEST_PROJECT_ID
        )


# ---------------------------------------------------------------------------
# Empty paths
# ---------------------------------------------------------------------------


class TestSearchEmptyPaths:
    async def test_no_videos_uploaded_returns_409(self) -> None:
        orch = _make_orchestrator(total_videos=0)
        resp = await _post(orch)

        assert resp.status_code == 409
        assert resp.json()["code"] == "NO_VIDEOS_UPLOADED"

    async def test_final_empty_returns_empty_answer(self) -> None:
        cid = uuid4()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=cid, rank=1)],
            sot_records=[],  # SOT gate rejects all
        )
        resp = await _post(orch)

        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"] == EMPTY_ANSWER
        assert body["chunks"] == []


# ---------------------------------------------------------------------------
# 400 — Validation errors
# ---------------------------------------------------------------------------


class TestSearchValidation:
    async def test_short_query_returns_400(self) -> None:
        orch = _make_orchestrator()
        resp = await _post(orch, query=" a ")  # normalizes to "a" (1 char)

        assert resp.status_code == 400
        assert resp.json()["code"] == "INVALID_ARGUMENT"

    async def test_long_query_returns_400(self) -> None:
        orch = _make_orchestrator()
        resp = await _post(orch, query="a" * 1001)

        assert resp.status_code == 400
        assert resp.json()["code"] == "INVALID_ARGUMENT"

    async def test_extra_fields_returns_400(self) -> None:
        orch = _make_orchestrator()
        resp = await _post(
            orch,
            json_body={"query": "valid query", "scope": "all"},
        )

        assert resp.status_code == 400

    async def test_missing_query_returns_400(self) -> None:
        orch = _make_orchestrator()
        resp = await _post(orch, json_body={})

        assert resp.status_code == 400

    async def test_empty_string_query_returns_400(self) -> None:
        orch = _make_orchestrator()
        resp = await _post(orch, json_body={"query": ""})

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 401 — Authentication errors
# ---------------------------------------------------------------------------


class TestSearchAuth:
    async def test_no_token_returns_401(self) -> None:
        orch = _make_orchestrator()
        resp = await _post(orch, headers={})

        assert resp.status_code == 401
        assert resp.json()["code"] == "UNAUTHENTICATED"

    async def test_expired_token_returns_401(self) -> None:
        orch = _make_orchestrator()
        token = _make_token(TEST_USER_ID, expired=True)
        resp = await _post(orch, headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 401

    async def test_wrong_secret_returns_401(self) -> None:
        orch = _make_orchestrator()
        token = _make_token(TEST_USER_ID, secret="wrong-secret-key-32bytes!!!!!!!!")
        resp = await _post(orch, headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 401

    async def test_cookie_auth_with_matching_csrf_allows_search(self) -> None:
        record = _chunk_record()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=record.chunk_id, rank=1)],
            sot_records=[record],
        )

        resp = await _post(
            orch,
            headers={"X-CSRF-Token": "csrf-1"},
            cookies=_auth_cookies(TEST_USER_ID),
        )

        assert resp.status_code == 200

    async def test_cookie_auth_without_csrf_rejects_search(self) -> None:
        orch = _make_orchestrator()

        resp = await _post(
            orch,
            headers={},
            cookies=_auth_cookies(TEST_USER_ID),
        )

        assert resp.status_code == 403
        assert resp.json()["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# 409 — Search not ready
# ---------------------------------------------------------------------------


class TestSearchNotReady:
    async def test_non_ready_video_returns_409(self) -> None:
        orch = _make_orchestrator(non_ready_count=1)
        resp = await _post(orch)

        assert resp.status_code == 409
        assert resp.json()["code"] == "SEARCH_NOT_READY"


# ---------------------------------------------------------------------------
# 500 — Internal errors
# ---------------------------------------------------------------------------


class TestSearchInternalError:
    async def test_non_retryable_llm_error_returns_500(self) -> None:
        record = _chunk_record()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=record.chunk_id, rank=1)],
            sot_records=[record],
            llm_error=LLMAdapterError(
                code="AUTH_ERROR",
                message="Gemini auth failed",
                retryable=False,
            ),
        )
        resp = await _post(orch)

        assert resp.status_code == 500
        assert resp.json()["code"] == "INTERNAL_ERROR"

    async def test_missing_answer_block_returns_500(self) -> None:
        record = _chunk_record()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=record.chunk_id, rank=1)],
            sot_records=[record],
            llm_text='<USED_REFS_JSON>{"used_refs":[1]}</USED_REFS_JSON>',
        )
        resp = await _post(orch)

        assert resp.status_code == 500
        assert resp.json()["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# 503 — Service unavailable
# ---------------------------------------------------------------------------


class TestSearchServiceUnavailable:
    async def test_embedding_failure_returns_503(self) -> None:
        orch = _make_orchestrator(
            embedding_error=ServiceUnavailableError("Embedding endpoint timed out"),
        )
        resp = await _post(orch)

        assert resp.status_code == 503
        assert resp.json()["code"] == "SERVICE_UNAVAILABLE"

    async def test_retryable_llm_error_returns_503(self) -> None:
        record = _chunk_record()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=record.chunk_id, rank=1)],
            sot_records=[record],
            llm_error=LLMAdapterError(
                code="UNAVAILABLE",
                message="Gemini unavailable",
                retryable=True,
            ),
        )
        resp = await _post(orch)

        assert resp.status_code == 503
        assert resp.json()["code"] == "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# X-Trace-Id
# ---------------------------------------------------------------------------


class TestSearchTraceId:
    async def test_success_echoes_trace_id_header(self) -> None:
        sent_id = str(uuid4())
        record = _chunk_record()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=record.chunk_id, rank=1)],
            sot_records=[record],
        )
        resp = await _post(orch, headers=_auth_headers(trace_id=sent_id))

        assert resp.status_code == 200
        assert resp.headers["X-Trace-Id"] == sent_id

    async def test_error_response_includes_trace_id(self) -> None:
        orch = _make_orchestrator()
        resp = await _post(orch, headers={})  # 401

        assert "X-Trace-Id" in resp.headers
        body = resp.json()
        assert "trace_id" in body
        assert resp.headers["X-Trace-Id"] == body["trace_id"]

    async def test_invalid_trace_id_generates_new_uuid(self) -> None:
        record = _chunk_record()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=record.chunk_id, rank=1)],
            sot_records=[record],
        )
        resp = await _post(orch, headers=_auth_headers(trace_id="not-a-uuid"))

        trace_id = resp.headers["X-Trace-Id"]
        UUID(trace_id)  # must be valid UUID
        assert trace_id != "not-a-uuid"

    async def test_trace_id_in_error_body_matches_header(self) -> None:
        orch = _make_orchestrator()
        resp = await _post(orch, query=" a ")  # 400

        body = resp.json()
        assert resp.headers["X-Trace-Id"] == body["trace_id"]


# ---------------------------------------------------------------------------
# req_id
# ---------------------------------------------------------------------------


class TestSearchReqId:
    async def test_req_id_is_valid_uuid(self) -> None:
        record = _chunk_record()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=record.chunk_id, rank=1)],
            sot_records=[record],
        )
        resp = await _post(orch)

        UUID(resp.json()["req_id"])

    async def test_empty_result_has_req_id(self) -> None:
        cid = uuid4()
        orch = _make_orchestrator(
            fts_results=[FTSCandidate(chunk_id=cid, rank=1)],
            sot_records=[],
        )
        resp = await _post(orch)

        UUID(resp.json()["req_id"])
