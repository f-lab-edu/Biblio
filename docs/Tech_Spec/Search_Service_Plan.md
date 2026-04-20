# Search Service PLAN

**Meta**
- **Component ID:** search-service
- **Target SPEC:** `docs/Tech_Spec/Search_Service_Spec.md`
- **SOT:** `docs/system-design.md`, `docs/Tech_Spec/Search_Service_Spec.md`, `docs/PRD.md`, `docs/Tech_Spec/Core_Api_Server_Spec.md`, `docs/Tech_Spec/Pipeline_Worker_Spec.md`, `docs/Tech_Spec/Managed_Embedding_Endpoint_Spec.md`
- **Code Root:** `services/search-service`

---

> 이 PLAN은 Search Service 구현을 작업 단위로 분해하고, 구현 순서와 통합 경계를 명확히 하여 코딩 에이전트가 `SPEC + PLAN`만으로도 추측 없이 구현을 진행할 수 있게 만드는 실행 계획 문서다.

## 1. Goals & Strategy

### 1.1 달성 목표 (Goals)

- **검색 API 완성:** `POST /api/v1/search`를 통해 JWT 인증, `X-Trace-Id` 처리, 질의 검증, `req_id` 생성, 에러 매핑을 포함한 HTTP 계약을 완성한다.
- **하이브리드 검색 파이프라인 완성:** 검색 범위 비어 있음 확인, 사용자 전체 영상 readiness gate, FTS/ANN 병렬 조회, RRF 병합, SOT 서빙 게이트를 포함한 읽기 전용 검색 오케스트레이션을 완성한다.
- **RAG 응답 조립 완성:** 프롬프트 빌더, 내부 `LLMAdapter`, `used_refs` 파싱, `chunks[].used` 반영까지 포함한 응답 생성 흐름을 완성한다.
- **추적성 및 테스트:** 성공/실패 응답 모두에서 `X-Trace-Id`를 일관되게 유지하고, SPEC §4.1 시나리오를 단위·API·통합 테스트로 자동화한다.

### 1.2 제외 대상 (Non-Goals)

- 피드백 저장 API 구현 및 `Feedback` 테이블 쓰기 로직
- 업로드/처리 상태 변경, 청킹/임베딩 적재, 파이프라인 오케스트레이션
- 카테고리 기반 검색 범위 제한, query rewrite, 번역, rerank 추가 알고리즘
- Search Service 내부 circuit breaker, 메트릭 대시보드, 운영 알람 체계
- 별도 검색 응답 저장소 구축 또는 `req_id` 영속 저장

### 1.3 리스크 및 대응 방안 (Risk & Mitigation)

- **공유 계약 드리프트:** `req_id`, `chunks`, `topk_ids/used_ids` 파생 규칙, `X-Trace-Id` 의미가 Core API/Client 기대와 어긋날 수 있다.
  - **대응:** API fixture와 통합 체크리스트에서 Search 응답과 Core feedback 계약을 함께 검증한다.
- **LLM 출력 파싱 취약성:** `used_refs` JSON 블록이 누락되거나 형식이 어긋나면 citation 해석이 흔들릴 수 있다.
  - **대응:** prompt builder에 출력 형식을 강제하고, parser 단위 테스트에서 malformed/duplicate/out-of-range 케이스를 고정한다.
- **검색 지연 위험:** Embedding, FTS, ANN, LLM이 직렬로 길어지면 PRD의 5초 SLA를 위반할 수 있다.
  - **대응:** 검색 범위 비어 있음 확인과 사용자 전체 영상 readiness gate를 선행하고, FTS/ANN 병렬 실행, 제한된 timeout/retry, `SEARCH_TOP_K`/`FINAL_TOP_K` 설정 기반으로 구현한다.
- **일부 영상만 준비된 상태에서 검색 허용 시 품질 신뢰 저하:** 일부 영상만 `READY`인 상태에서 검색을 허용하면 사용자는 전체 업로드 기준으로 불완전한 응답을 받게 된다.
  - **대응:** 검색 시작 전에 검색 범위 비어 있음 확인과 사용자 전체 영상 readiness gate를 두고, 영상이 0개면 `409 NO_VIDEOS_UPLOADED`, 미준비 영상이 1개라도 있으면 `409 SEARCH_NOT_READY`를 반환하도록 고정한다.
- **Projection 불일치로 인한 false empty:** Vector Store 후보는 존재하지만 SOT 게이트에서 모두 탈락하는 경우가 반복될 수 있다.
  - **대응:** SOT 게이트를 authoritative source로 유지하고, NO_VIDEOS_UPLOADED 분기와 final-empty 분기를 별도 테스트로 고정한다.

### 1.4 구현 전제 (Preconditions)

- **구현 전제:** `docs/Tech_Spec/Search_Service_Spec.md`가 Search Service 계약의 기준이며, HTTP/DB read path/error semantics는 현재 문서 기준으로 닫혀 있다.
- **선행 필요 사항:** `services/search-service`는 현재 빈 디렉토리이므로, 프로젝트 스캐폴딩부터 포함해 구현해야 한다.
- **테스트 전제:** Search Service는 DDL을 소유하지 않으므로, 통합 테스트에서는 Core API/Worker가 소유한 읽기 대상 스키마(`video`, `chunk`, `vector_index_entry`)를 fixture 또는 테스트 전용 bootstrap으로 준비해야 한다.
- **구현 범위 고정:** 현재 구현 범위의 운영 provider는 `gemini`로 고정하고, `mock`은 테스트 wiring 전용으로 사용한다.
- **입력 DTO 기준:** prompt builder 입력 `ContextBlock`은 `title`을 포함하는 형태로 구현한다. 멀티 비디오 검색 시 LLM 라벨링은 `title` 기준으로 고정한다.

### 1.5 핵심 의존성 패키지

| 패키지 | 용도 | 최소 버전 |
| --- | --- | --- |
| `fastapi` | HTTP API 프레임워크 | `>=0.111,<1.0` |
| `uvicorn[standard]` | ASGI 서버 | `>=0.29,<1.0` |
| `pydantic-settings` | 환경 변수 로딩 | `>=2.3,<3.0` |
| `sqlalchemy[asyncio]` | DB read query 구현 | `>=2.0,<3.0` |
| `asyncpg` | PostgreSQL 비동기 드라이버 | `>=0.29,<1.0` |
| `PyJWT` | JWT 검증 | `>=2.0,<3.0` |
| `httpx` | Embedding endpoint 호출 및 API 테스트 | `>=0.27,<1.0` |
| `pytest` | 테스트 러너 | `>=8.0,<9.0` |
| `pytest-asyncio` | 비동기 테스트 | `>=0.23,<1.0` |
| `pytest-cov` | 커버리지 확인 | `>=5.0,<6.0` |
| `testcontainers[postgres]` | Postgres 통합 테스트 환경 | `>=4.0,<5.0` |
| `google-genai` | Gemini 구현체 (Vertex AI backend) | `>=1.0,<2.0` |

---

## 2. Implementation Phasing Strategy

- **Phase 1:** 프로젝트 스캐폴딩, 설정 로딩, 미들웨어, DTO, 라우터 스켈레톤을 먼저 닫아 Search API 진입 계약을 고정한다.
- **Phase 2:** DB read repository, Embedding client, RRF/normalizer 등 읽기 경로 핵심 모듈을 구현하여 LLM 호출 전까지의 검색 파이프라인을 검증 가능하게 만든다.
- **Phase 3:** prompt builder, `LLMAdapter`, `used_refs` parser, 응답 조립을 붙여 최종 RAG 응답을 완성한다.
- **Phase 4:** API 통합 테스트, trace/error consistency, rollout checklist를 마무리하여 병합 가능한 상태로 닫는다.
- **병합 게이트:** 각 Phase 또는 하위 작업 단위마다 관련 테스트 통과와 SPEC §4.1 추적성을 확인한 뒤 병합한다.

### 2.1 작업 분해 원칙 (Task Decomposition Rules)

- 각 Task는 하나의 명확한 산출물과 하나의 검증 가능한 완료 조건을 가진다.
- 병렬 작업은 `core|middlewares|schemas|api`, `infra/db`, `infra/embedding|infra/llm`, `services` 축으로 먼저 분리한다.
- `search_orchestrator.py`, `api/v1/routers/search.py`, `tests/api/test_search.py`는 최종 통합 지점이므로 선행 Task가 닫힌 뒤 통합 담당자가 합친다.
- 구현 순서는 레이어 나열보다 “사용자 전체 영상 readiness gate가 있는 검색 API”를 먼저 성립시키고, 이후 LLM 응답 품질을 붙이는 기능 슬라이스 순서를 우선한다.
- 검색 서비스는 read-only 컴포넌트이므로, 쓰기 로직이나 스키마 소유권을 새로 추가하는 작업은 본 PLAN 범위에 넣지 않는다.

### 2.2 선행 경로 및 병렬 가능 범위 (Critical Path & Parallelism)

- **Critical Path:** Task 0(스캐폴딩) → Task 1(HTTP 계약/미들웨어) → Task 2(DB read path) → Task 4(오케스트레이터 기본 흐름) → Task 5(prompt/LLM/used_refs) → Task 6(API 통합) → Task 7(최종 검증)
- **Parallelizable Workstreams:** Task 2(DB read path), Task 3(Embedding/LLM infra), Task 4 일부(normalizer/RRF)는 Task 1 이후 병렬 수행 가능하다.
- **Merge Owner / Integration Point:** 최종 통합은 `services/search-service/src/services/search_orchestrator.py`, `services/search-service/src/api/v1/routers/search.py`, `services/search-service/tests/api/test_search.py`에서 수행한다.

---

## 3. Work Breakdown Structure (WBS)

> 구현자가 그대로 실행할 수 있는 작업 지시서다.
> 모든 작업은 `Output / Files / Test Files / Commands / Verify / Linked AC / Depends On`를 포함한다.
> 병렬화 가능한 작업은 `병렬 가능: Y` 또는 `병렬 가능: N`으로 표시한다.

### Phase 1: Skeleton & Contracts

- [x] **Task 0: 프로젝트 스캐폴딩 및 설정 로딩**
  - **Output:** Search Service용 `pyproject.toml`, 앱 팩토리, 설정 로딩, DI 진입점, `.env.example`
  - **Files:** `services/search-service/pyproject.toml`, `services/search-service/src/main.py`, `services/search-service/src/core/config.py`, `services/search-service/src/core/dependencies.py`, `services/search-service/.env.example`
  - **Test Files:** `services/search-service/tests/unit/test_config.py`
  - **Commands:** `cd services/search-service && pytest tests/unit/test_config.py`
  - **Verify:** 필수 환경 변수(`JWT_SECRET_KEY`, `DATABASE_URL`, `EMBEDDING_API_URL`) 누락 시 설정 로딩이 실패하고, 기본 선택 환경 변수는 SPEC 값으로 채워진다.
  - **Linked AC:** SPEC §1.2, SPEC §2.3
  - **Depends On:** 없음
  - **병렬 가능:** N

- [x] **Task 1: HTTP 계약, 인증/trace/에러 처리, DTO 스켈레톤**
  - **Output:** `POST /api/v1/search` 라우터 스켈레톤, 요청/응답 DTO, JWT 미들웨어, `X-Trace-Id` 미들웨어, 공통 에러 응답 매핑
  - **Files:** `services/search-service/src/api/v1/routers/search.py`, `services/search-service/src/schemas/search_dto.py`, `services/search-service/src/middlewares/auth.py`, `services/search-service/src/middlewares/trace.py`, `services/search-service/src/middlewares/error_handler.py`
  - **Test Files:** `services/search-service/tests/unit/test_search_dto.py`, `services/search-service/tests/unit/test_auth_middleware.py`, `services/search-service/tests/unit/test_trace_middleware.py`
  - **Commands:** `cd services/search-service && pytest tests/unit/test_search_dto.py tests/unit/test_auth_middleware.py tests/unit/test_trace_middleware.py`
  - **Verify:** 잘못된 `query` 또는 미지원 요청 필드가 SPEC의 400 규칙으로 매핑되고, invalid `X-Trace-Id`는 새 UUID4로 재발급되며, 성공/실패 응답 헤더에 동일한 `X-Trace-Id`가 유지된다.
  - **Linked AC:** SPEC §2.1, SPEC §2.4, SPEC §4.1 `POST /api/v1/search`
  - **Depends On:** Task 0
  - **병렬 가능:** Y

### Phase 2: Read Path & Retrieval Core

- [x] **Task 2: DB read repository 및 검색 쿼리 계층 구현**
  - **Output:** 검색 범위 비어 있음 확인, 사용자 전체 영상 readiness gate, FTS 조회, ANN 조회, SOT 게이트 조회를 담당하는 repository 인터페이스와 SQL 구현
  - **Files:** `services/search-service/src/infra/db/session.py`, `services/search-service/src/infra/db/search_repository.py`, `services/search-service/src/infra/db/sql_queries.py`
  - **Test Files:** `services/search-service/tests/integration/test_search_repository.py`
  - **Commands:** `cd services/search-service && pytest tests/integration/test_search_repository.py`
  - **Verify:** 검색 범위 비어 있음 분기(`409 NO_VIDEOS_UPLOADED`), 사용자 전체 영상 readiness gate(`READY`가 아닌 영상 존재 시 차단), `DELETING`/hard-delete 필터링, 후보 단계와 SOT 단계의 이중 테넌시 검증이 통합 테스트로 재현된다.
  - **Linked AC:** SPEC §2.2, SPEC §3.1 step 5/7/9, SPEC §3.5, SPEC §4.1 SOT 서빙 게이트
  - **Depends On:** Task 0
  - **병렬 가능:** Y

- [x] **Task 3: Embedding client 및 provider wiring 인프라 구현**
  - **Output:** Embedding HTTP client, `LLMAdapter` 추상 클래스, provider registry/bootstrap, 테스트용 mock wiring
  - **Files:** `services/search-service/src/infra/embedding/client.py`, `services/search-service/src/infra/llm/base.py`, `services/search-service/src/infra/llm/gemini_adapter.py`, `services/search-service/src/infra/llm/mock_adapter.py`, `services/search-service/src/bootstrap.py`, `services/search-service/src/core/dependencies.py`
  - **Test Files:** `services/search-service/tests/unit/test_embedding_client.py`, `services/search-service/tests/unit/test_llm_wiring.py`
  - **Commands:** `cd services/search-service && pytest tests/unit/test_embedding_client.py tests/unit/test_llm_wiring.py`
  - **Verify:** Embedding 요청/응답 shape 검증, timeout/retry, `503` 매핑이 SPEC과 일치한다. `LLM_PROVIDER=gemini|mock` 경로가 bootstrap에서 올바르게 조립된다.
  - **Linked AC:** SPEC §1.2, SPEC §2.1 Managed Embedding Endpoint, SPEC §2.1 LLMAdapter, SPEC §2.4
  - **Depends On:** Task 0
  - **병렬 가능:** Y

- [x] **Task 4: 검색 오케스트레이터 기본 흐름 구현**
  - **Output:** query normalizer, 검색 범위 비어 있음 확인, readiness gate 호출, RRF 병합기, 최종 컨텍스트 선정, `ref ASC` 기준 `chunks` 조립을 포함한 오케스트레이터 기본 흐름
  - **Files:** `services/search-service/src/services/query_normalizer.py`, `services/search-service/src/services/rrf.py`, `services/search-service/src/services/search_orchestrator.py`
  - **Test Files:** `services/search-service/tests/unit/test_query_normalizer.py`, `services/search-service/tests/unit/test_rrf.py`, `services/search-service/tests/unit/test_search_orchestrator_empty.py`
  - **Commands:** `cd services/search-service && pytest tests/unit/test_query_normalizer.py tests/unit/test_rrf.py tests/unit/test_search_orchestrator_empty.py`
  - **Verify:** 영상 0개면 `409 NO_VIDEOS_UPLOADED`, 미준비 영상 존재 시 `409 SEARCH_NOT_READY`, final-empty return, RRF 중복 병합, `FINAL_TOP_K` 제한, `chunks`의 `ref ASC` 순서가 단위 테스트로 고정된다.
  - **Linked AC:** SPEC §3.1 step 3~12, SPEC §3.3 RRF 병합, SPEC §3.4, SPEC §4.1 정상/예외
  - **Depends On:** Task 1, Task 2, Task 3
  - **병렬 가능:** N

### Phase 3: Prompting, LLM, and Response Finalization

- [x] **Task 5: prompt builder, `used_refs` parser, LLM 응답 반영 구현**
  - **Output:** `ContextBlock` 직렬화, 시스템/사용자 프롬프트 조립, `<ANSWER>` / `<USED_REFS_JSON>` 출력 계약 구현, `used_refs` JSON 추출/정제, `chunks[].used` 갱신, 근거 부족 응답 처리
  - **Files:** `services/search-service/src/services/prompt_builder.py`, `services/search-service/src/services/used_refs_parser.py`, `services/search-service/src/services/search_orchestrator.py`, `services/search-service/src/infra/llm/gemini_adapter.py`
  - **Test Files:** `services/search-service/tests/unit/test_prompt_builder.py`, `services/search-service/tests/unit/test_used_refs_parser.py`, `services/search-service/tests/unit/test_search_orchestrator_answer.py`
  - **Commands:** `cd services/search-service && pytest tests/unit/test_prompt_builder.py tests/unit/test_used_refs_parser.py tests/unit/test_search_orchestrator_answer.py`
  - **Verify:** `enriched_text` 우선 규칙, `ContextBlock.title` 기반 멀티 비디오 라벨링 직렬화, 모든 사실 주장에 대한 `[n]` 인라인 인용 강제, `<ANSWER>`와 `<USED_REFS_JSON>` 분리, malformed/duplicate/out-of-range `used_refs` 정제, 일반 JSON 유사 문자열 오인 파싱 방지, 파싱 실패 시 전부 `used=false`가 보장된다.
  - **Linked AC:** SPEC §2.1 `chunks`, SPEC §3.1 step 13~15, SPEC §3.3 prompt builder / citation 해석, SPEC §4.1 `used_refs` 파싱 및 프롬프트 조립
  - **Depends On:** Task 3, Task 4
  - **병렬 가능:** N

### Phase 4: API Integration & Final Verification

- [x] **Task 6: 라우터와 오케스트레이터 통합**
  - **Output:** HTTP 라우터, 미들웨어, 오케스트레이터, repository, embedding client, LLM adapter가 실제 실행 흐름으로 연결된 Search API
  - **Files:** `services/search-service/src/api/v1/routers/search.py`, `services/search-service/src/main.py`, `services/search-service/src/core/dependencies.py`, `services/search-service/src/services/search_orchestrator.py`
  - **Test Files:** `services/search-service/tests/api/test_search.py`
  - **Commands:** `cd services/search-service && pytest tests/api/test_search.py`
  - **Verify:** 성공 응답, `409 NO_VIDEOS_UPLOADED`, `409 SEARCH_NOT_READY`, final-empty, 400/401/500/503 매핑, `X-Trace-Id` echo, `req_id` 생성과 `chunks` 응답 구조, `answer`와 metadata 분리, `<ANSWER>` 블록 누락 시 500이 API 테스트로 검증된다.
  - **Linked AC:** SPEC §2.1, SPEC §2.4, SPEC §3.1 전체, SPEC §4.1 `POST /api/v1/search`
  - **Depends On:** Task 4, Task 5
  - **병렬 가능:** N

- [ ] **Task 7: 최종 통합 검증 및 릴리스 준비**
  - **Output:** SPEC §4.1 전체 시나리오 테스트, 통합 체크리스트, 배포/롤백 점검, Search 전용 README 또는 실행 지침
  - **Files:** `services/search-service/tests/...`, `services/search-service/README.md`, 필요 시 `.github/workflows/...`
  - **Test Files:** 전체 테스트 스위트
  - **Commands:** `cd services/search-service && pytest`, `cd services/search-service && pytest --cov`
  - **Verify:** SPEC §4.1 시나리오가 녹색이고, Search 응답 계약이 Core feedback 파생 규칙과 충돌하지 않으며, read-only 배포/롤백 절차가 재현 가능하다.
  - **Linked AC:** SPEC §4.1, §4.2, §4.3
  - **Depends On:** Task 6
  - **병렬 가능:** N

---

## 4. Integration Checklist & Done Criteria

### 4.1 통합 체크리스트 (Integration Checklist)

- [ ] Search 응답이 `req_id`, `answer`, `chunks[{ref, chunk_id, video_id, title, start_ms, end_ms, text, used}]` 계약을 정확히 따른다.
- [ ] Core API feedback 계약의 `topk_ids`, `used_ids`는 Search 응답 `chunks`에서 파생 가능하다.
- [ ] 모든 성공/실패 응답이 `X-Trace-Id`와 `trace_id` 의미를 Core API/Worker 패턴과 일치하게 유지한다.
- [ ] FTS 조회, ANN 조회, SOT 게이트 전 단계에서 `requester_user_id` 기준 테넌시가 일관되게 적용된다.
- [ ] 검색 범위 비어 있음 분기, 사용자 전체 영상 readiness gate, final-empty 분기가 Embedding/LLM 호출 skip 여부까지 포함해 구분된다.
- [ ] Search Service가 DB write나 별도 응답 저장 없이 read-only 경계를 유지한다.
- [ ] `Managed Embedding Endpoint` 요청/응답 contract(`POST /embed`, `{"texts": [...]}`, `{"embeddings": [...]}`)와 shape validation이 일치한다.
- [ ] Search 서비스 배포가 기존 Core API/Worker 스키마 소유권을 침범하지 않는다.

### 4.2 완료 조건 (Definition of Done)

- [ ] SPEC §4.1에 정의된 시나리오 테스트가 모두 녹색이다.
- [ ] 단위·API·통합 테스트가 모두 통과한다.
- [ ] 성공/실패 응답에서 `X-Trace-Id`와 에러 바디 `trace_id`가 일관되다.
- [ ] Search Service는 read-only 배포로 동작하며 스키마 마이그레이션 없이 기동 가능하다.

---

## 5. Rollout & Rollback Plan

### 5.1 배포 계획 (Rollout)

- **환경 변수 추가:** `JWT_SECRET_KEY`, `DATABASE_URL`, `EMBEDDING_API_URL`, `LLM_PROVIDER`, `GCP_PROJECT_ID`, `GCP_LOCATION`, `GEMINI_MODEL_NAME`, `SEARCH_TOP_K`, `FINAL_TOP_K`, `RRF_K`, `EMBEDDING_TIMEOUT_SEC`, `EMBEDDING_MAX_RETRIES`, `LLM_TIMEOUT_SEC`, `LLM_MAX_RETRIES`
- **인프라/스키마 변경:** Search Service는 read-only이므로 자체 DB 마이그레이션은 없다. 단, 대상 환경에 `video`, `chunk`, `vector_index_entry` 읽기 스키마와 임베딩 endpoint가 준비되어 있어야 한다.
- **호환성 확인:** Reverse Proxy가 `/api/v1/search`를 Search Service로 라우팅하고 `Authorization`, `X-Trace-Id` 헤더를 보존하는지 확인한다.
- **기동 전 점검:** `services/search-service` 설정 로딩, DB 연결, Embedding endpoint 도달 가능 여부, 기본 provider(`gemini`)의 Vertex AI ADC/service account credential과 `GCP_PROJECT_ID`/`GCP_LOCATION` 설정을 확인한다.

### 5.2 롤백 계획 (Rollback)

- **애플리케이션 롤백:** 이전 Search Service 아티팩트 또는 컨테이너 이미지로 즉시 복귀한다.
- **데이터베이스 스키마 원복:** Search Service는 DDL을 소유하지 않으므로 별도 스키마 롤백 절차가 없다.
- **호환성 복구:** 롤백 시에도 Core API feedback 계약(`req_id`, 파생 `topk_ids`/`used_ids`)은 유지되어야 하므로, 응답 필드 변경이 섞인 부분 배포는 허용하지 않는다.
- **부분 적용 복구:** 앱만 배포되고 Embedding endpoint 또는 provider credential이 준비되지 않은 경우, Search Service를 즉시 이전 버전으로 내리고 트래픽을 차단한다.

---

## Assumptions (확정된 사항)

- Search Service는 `video`, `chunk`, `vector_index_entry`를 읽기 전용으로 조회하며 자체 DDL을 소유하지 않는다.
- Search 응답은 `chunks`를 기준 데이터로 사용하고, feedback용 `topk_ids`/`used_ids`는 클라이언트가 파생한다.
- 현재 구현 범위의 운영 provider는 `gemini`이며, `mock`은 테스트 wiring에 사용한다.
- Search Service는 circuit breaker 없이 timeout/retry와 명확한 에러 반환만 소유한다.
