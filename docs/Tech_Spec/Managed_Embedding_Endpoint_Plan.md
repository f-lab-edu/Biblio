# Managed Embedding Endpoint PLAN

**Meta**
- **Component ID:** managed-embedding-endpoint
- **Target SPEC:** `docs/Tech_Spec/Managed_Embedding_Endpoint_Spec.md`
- **SOT:** `docs/system-design.md`, `docs/Tech_Spec/Managed_Embedding_Endpoint_Spec.md`, `docs/Tech_Spec/Pipeline_Worker_Spec.md`, `docs/Tech_Spec/Search_Service_Spec.md`, `docs/folder_structure.md`
- **Code Root:** `services/managed-embedding-endpoint`

---

> 이 PLAN은 Managed Embedding Endpoint를 MVP 범위에서 구현 가능한 작업 단위로 분해한 실행 계획 문서다.
> 목표는 구현자가 `SPEC + PLAN`만 읽고도 서비스 스캐폴딩, 모델 로드, `/health`, `/embed`, 테스트, 배포 스모크까지 추측 없이 진행할 수 있게 만드는 것이다.

## 1. Goals & Strategy

### 1.1 달성 목표 (Goals)

- **임베딩 HTTP 계약 완성:** `POST /embed`, `GET /health`, `X-Trace-Id`, 에러 응답 `code/message/trace_id`를 SPEC 그대로 구현한다.
- **실제 모델 서빙 완성:** 프로세스 시작 시 로컬 모델 artifact를 로드하고, `BAAI/bge-m3`의 dense embedding만 서빙한다.
- **guardrail 및 fail-fast 완성:** `texts` 개수, 개별 길이, payload size, 동시 처리량 제한을 서버 측에서 강제하고, admission control 초과 시 즉시 `503`을 반환한다.
- **테스트 및 운영 스모크 완성:** 단위/API 테스트는 model stub 기반으로 자동화하고, 배포 이후 실제 모델로 `/embed` smoke request 1건을 검증 절차에 포함한다.

### 1.2 제외 대상 (Non-Goals)

- metrics, alerts, 대시보드 연동
- model registry 자동 감지, hot reload, blue/green version coordination
- 서비스 내부 startup smoke inference, warm-up orchestration, init container/sidecar 설계
- 사용자 인증/인가, JWT, 테넌시 판정
- sparse retrieval, multi-vector, image/multimodal embedding
- DB, vector store, feedback 저장

### 1.3 리스크 및 대응 방안 (Risk & Mitigation)

- **실모델 로드 실패 위험:** artifact path 검증과 startup load 실패를 명확히 분리하고, ready 전에는 `/health`와 `/embed` 모두 `503`으로 고정한다.
- **대형 요청으로 인한 메모리/지연 급증:** guardrail 기본값(`32`, `4096`, `262144`, `1`)을 설정 기본값으로 강제하고, 미설정을 무제한으로 해석하지 않는다.
- **Search/Worker와 계약 드리프트:** `EMBEDDING_API_URL`, `X-Trace-Id`, 응답 shape, `model_version` 노출 규칙을 통합 체크리스트에 넣고 최종 검증한다.
- **실모델 테스트 비용 증가:** 자동화 테스트는 stub runtime으로 고정하고, 실제 `bge-m3` 추론 검증은 배포/운영 smoke step으로 분리한다.
- **모델 버전 문자열 혼선:** `model_version`은 실제 로드한 artifact path를 SOT로 두고, 응답 본문이 아닌 `/health`와 구조화 로그에서만 노출한다.

### 1.4 구현 전제 및 열려 있는 결정사항 (Preconditions & Open Decisions)

- **구현 전제:** `Managed_Embedding_Endpoint_Spec.md`의 HTTP 계약, readiness, `model_version`, guardrail 기본값, 에러 semantics는 현재 기준으로 닫혀 있다.
- **구현 전제:** 서비스 코드는 새 루트 `services/managed-embedding-endpoint/` 아래에 생성한다.
- **구현 전제:** 실제 운영 모델은 로컬 경로에서 로드되는 `BAAI/bge-m3` artifact이며, `model_version` SOT는 실제 로드한 artifact path다.
- **구현 전제:** startup readiness는 모델 로드 성공만을 의미하며, 실제 추론 가능 여부는 운영 smoke request로 검증한다.
- **구현 전제:** 로깅은 `loguru`, HTTP API는 `FastAPI`, 설정 로딩은 `pydantic-settings`, 비동기 테스트는 `pytest-asyncio`를 사용한다.
- **열려 있는 결정사항:** 현재 plan 작성 기준 구현을 막는 blocker는 없다.

### 1.5 핵심 의존성 패키지

| 패키지 | 용도 | 최소 버전 |
| --- | --- | --- |
| `fastapi` | HTTP API 프레임워크 | `>=0.111,<1.0` |
| `uvicorn[standard]` | ASGI 서버 | `>=0.29,<1.0` |
| `pydantic-settings` | 환경 변수 로딩 | `>=2.3,<3.0` |
| `httpx` | API 테스트 및 내부 HTTP 검증 | `>=0.27,<1.0` |
| `loguru` | 구조화 로깅 | `>=0.7,<1.0` |
| `FlagEmbedding` | `BGEM3FlagModel` 기반 임베딩 런타임 | `pyproject lock 시 결정` |
| `pytest` | 테스트 러너 | `>=8.0,<9.0` |
| `pytest-asyncio` | 비동기 테스트 | `>=0.23,<1.0` |
| `pytest-cov` | 커버리지 측정 | `>=5.0,<6.0` |
| `poethepoet` | 표준 검증 task runner | `>=0.32,<1.0` |

---

## 2. Implementation Phasing Strategy

- **Phase 1:** 서비스 스캐폴딩, 설정, model state, trace/error middleware, DTO, 라우터 스켈레톤을 먼저 닫는다.
- **Phase 2:** 모델 로더와 inference runtime 경계를 구현하고, `model_version`/readiness 계약을 고정한다.
- **Phase 3:** `/embed` 핵심 유스케이스, guardrail, admission control, 응답 검증을 붙인다.
- **Phase 4:** API 통합 테스트, 실제 모델 smoke 절차, Docker/README/rollout checklist를 마무리한다.
- **병합 게이트:** 각 Phase는 관련 테스트가 통과하고, Search/Worker와의 공유 계약 검토가 끝난 뒤 병합한다.

### 2.1 작업 분해 원칙 (Task Decomposition Rules)

- 각 Task는 하나의 명확한 산출물과 하나의 검증 가능한 완료 조건을 가진다.
- `core/settings|state`, `schemas|middlewares|api`, `infra/model_loader|runtime`, `services/inference_service`, `tests` 축으로 파일 경계를 분리한다.
- 실모델 의존 로직과 API 계약 로직을 분리하여, 자동화 테스트는 stub만으로 재현 가능하게 유지한다.
- 구현 순서는 레이어 나열보다 “ready health endpoint가 뜨고, `/embed`가 spec shape로 응답하는 기능 슬라이스”를 우선한다.
- 실제 모델 다운로드/배포 절차는 서비스 코드와 분리하되, smoke 검증 명령은 plan에 포함한다.

### 2.2 선행 경로 및 병렬 가능 범위 (Critical Path & Parallelism)

- **Critical Path:** Task 0(스캐폴딩) → Task 1(HTTP 계약/미들웨어) → Task 2(모델 로더/상태) → Task 4(`/embed` 유스케이스) → Task 5(API 통합) → Task 6(실모델 smoke/Docker)
- **Parallelizable Workstreams:** Task 1(API/DTO)와 Task 2(model loader/runtime abstraction)는 Task 0 이후 병렬 가능하다. Task 3(guardrail/admission control)은 Task 1 이후 병렬 가능하다.
- **Merge Owner / Integration Point:** 최종 통합은 `services/managed-embedding-endpoint/src/main.py`, `services/managed-embedding-endpoint/src/services/inference_service.py`, `services/managed-embedding-endpoint/tests/api/test_embed.py`에서 수행한다.

---

## 3. Work Breakdown Structure (WBS)

> 구현자가 그대로 실행할 수 있는 작업 지시서다.
> 모든 작업은 `Output / Files / Test Files / Commands / Verify / Linked AC / Depends On`를 포함한다.

### Phase 1: Skeleton & Contracts

- [x] **Task 0: 서비스 스캐폴딩 및 설정 로딩**
  - **Output:** 새 서비스 루트, `pyproject.toml`, 앱 엔트리포인트, 설정 로딩, `.env.example`, `model_state` 기본 구조
  - **Files:** `services/managed-embedding-endpoint/pyproject.toml`, `services/managed-embedding-endpoint/src/main.py`, `services/managed-embedding-endpoint/src/core/settings.py`, `services/managed-embedding-endpoint/src/core/model_state.py`, `services/managed-embedding-endpoint/.env.example`
  - **Test Files:** `services/managed-embedding-endpoint/tests/unit/test_settings.py`
  - **Commands:** `cd services/managed-embedding-endpoint && poetry run pytest tests/unit/test_settings.py`
  - **Verify:** 필수 환경 변수 누락 시 설정 로딩이 실패하고, 선택 설정 미지정 시 guardrail 기본값이 적용된다.
  - **Linked AC:** SPEC §1.2, §2.3, §5.1 공통 전제
  - **Depends On:** 없음
  - **병렬 가능:** N

- [x] **Task 1: DTO, trace middleware, error handler, 라우터 스켈레톤**
  - **Output:** `EmbedRequest`, `EmbedResponse`, `HealthResponse`, `X-Trace-Id` 미들웨어, 공통 에러 응답, `/embed`/`/health` 라우터 시그니처
  - **Files:** `services/managed-embedding-endpoint/src/schemas/embed_dto.py`, `services/managed-embedding-endpoint/src/middlewares/trace.py`, `services/managed-embedding-endpoint/src/middlewares/error_handler.py`, `services/managed-embedding-endpoint/src/api/v1/router.py`, `services/managed-embedding-endpoint/src/api/v1/routers/embed.py`
  - **Test Files:** `services/managed-embedding-endpoint/tests/unit/test_embed_dto.py`, `services/managed-embedding-endpoint/tests/unit/test_trace_middleware.py`, `services/managed-embedding-endpoint/tests/unit/test_error_handler.py`
  - **Commands:** `cd services/managed-embedding-endpoint && poetry run pytest tests/unit/test_embed_dto.py tests/unit/test_trace_middleware.py tests/unit/test_error_handler.py`
  - **Verify:** invalid `texts`, 빈 문자열, 잘못된 trace header가 SPEC의 `400`/trace 재발급 규칙대로 매핑되고, 성공/실패 응답에 동일한 `X-Trace-Id`가 유지된다.
  - **Linked AC:** SPEC §2.1, §2.4, §4, §5.1
  - **Depends On:** Task 0
  - **병렬 가능:** Y

### Phase 2: Model Load & Runtime Boundaries

- [x] **Task 2: 모델 로더와 runtime abstraction 구현**
  - **Output:** `model_version` 확보, 모델 로드/실패, ready state 승격을 담당하는 `ModelLoader`와 inference runtime abstraction
  - **Files:** `services/managed-embedding-endpoint/src/infra/model_loader.py`, `services/managed-embedding-endpoint/src/infra/runtime.py`, `services/managed-embedding-endpoint/src/core/model_state.py`
  - **Test Files:** `services/managed-embedding-endpoint/tests/unit/test_model_loader.py`, `services/managed-embedding-endpoint/tests/unit/test_model_state.py`
  - **Commands:** `cd services/managed-embedding-endpoint && poetry run pytest tests/unit/test_model_loader.py tests/unit/test_model_state.py`
  - **Verify:** 로드 성공 시 `model_state`가 ready로 전이되고 `model_version`은 실제 로드한 artifact path로 설정된다. 로드 실패 시 ready가 열리지 않는다.
  - **Linked AC:** SPEC §2.1 Local Model Files, §2.3, §3.1 모델 파일 로드, §3.2, §3.3
  - **Depends On:** Task 0
  - **병렬 가능:** Y

- [x] **Task 3: 실제 BGE-M3 runtime adapter 연결**
  - **Output:** `FlagEmbedding` 기반 `BGEM3FlagModel` runtime adapter, dense embedding 추출, 순서 보장용 runtime wrapper
  - **Files:** `services/managed-embedding-endpoint/src/infra/bge_runtime.py`, `services/managed-embedding-endpoint/src/infra/runtime.py`
  - **Test Files:** `services/managed-embedding-endpoint/tests/unit/test_runtime_stub.py`
  - **Commands:** `cd services/managed-embedding-endpoint && poetry run pytest tests/unit/test_runtime_stub.py`
  - **Verify:** 서비스 계층은 runtime interface만 호출하고, 테스트는 실모델 없이 stub으로 대체 가능하다. 실모델 adapter는 dense vector만 반환한다.
  - **Linked AC:** SPEC §1.2 V1 Reference Runtime, §2.3 텍스트 전용 추론
  - **Depends On:** Task 2
  - **병렬 가능:** Y

### Phase 3: Core Embed Flow

- [x] **Task 4: `/embed` 유스케이스와 guardrail/admission control 구현**
  - **Output:** 입력 검증, guardrail enforcement, concurrency gate, 모델 호출, 길이/순서/형식 검증을 포함한 `InferenceService`
  - **Files:** `services/managed-embedding-endpoint/src/services/inference_service.py`, `services/managed-embedding-endpoint/src/api/v1/routers/embed.py`
  - **Test Files:** `services/managed-embedding-endpoint/tests/unit/test_inference_service.py`
  - **Commands:** `cd services/managed-embedding-endpoint && poetry run pytest tests/unit/test_inference_service.py`
  - **Verify:** `texts` 개수/길이/payload 제한, ready 미존재 시 `503`, concurrency 초과 시 즉시 `503`, 비정상 결과 shape 시 `503`, 성공 시 `embeddings` 길이/순서 1:1 대응이 보장된다.
  - **Linked AC:** SPEC §2.1, §2.3, §2.4, §3.1 POST /embed, §5.1 POST /embed
  - **Depends On:** Task 1, Task 2, Task 3
  - **병렬 가능:** N

- [x] **Task 5: `/health`와 앱 startup 통합**
  - **Output:** startup 시 모델 로드, ready state 반영, `/health` 응답, 공통 bootstrap
  - **Files:** `services/managed-embedding-endpoint/src/main.py`, `services/managed-embedding-endpoint/src/core/bootstrap.py`, `services/managed-embedding-endpoint/src/api/v1/routers/embed.py`
  - **Test Files:** `services/managed-embedding-endpoint/tests/api/test_health.py`, `services/managed-embedding-endpoint/tests/api/test_embed.py`
  - **Commands:** `cd services/managed-embedding-endpoint && poetry run pytest tests/api/test_health.py tests/api/test_embed.py`
  - **Verify:** 로드 성공 시 `/health`가 `200 {"status":"ok","model_version":"..."}`, 로드 실패 시 `/health`와 `/embed`가 `503`을 반환한다. `/embed` 성공 응답 본문에는 `model_version`이 포함되지 않는다.
  - **Linked AC:** SPEC §2.4 헬스 체크 semantics, §3.1 GET /health, §5.1 GET /health
  - **Depends On:** Task 1, Task 2, Task 4
  - **병렬 가능:** N

### Phase 4: Verification & Release Readiness

- [ ] **Task 6: 최종 테스트 스위트, 운영 smoke 절차, Docker 패키징**
  - **Output:** 전체 테스트 스위트, 실제 모델 `/embed` smoke 절차, Dockerfile, README 또는 실행 지침
  - **Files:** `services/managed-embedding-endpoint/tests/...`, `services/managed-embedding-endpoint/Dockerfile`, `services/managed-embedding-endpoint/README.md`
  - **Test Files:** 전체 테스트 스위트
  - **Commands:** `cd services/managed-embedding-endpoint && poetry run pytest`, `cd services/managed-embedding-endpoint && poetry run pytest --cov=src --cov-report=term-missing --cov-fail-under=80`, `curl -sS -X POST \"$EMBEDDING_API_URL\" -H 'Content-Type: application/json' -d '{\"texts\":[\"smoke test\"]}'`
  - **Verify:** 자동화 테스트가 녹색이고, 운영 smoke request 1건이 실제 float vector 배열을 반환한다. Docker 이미지로 기동 후 `/health`와 `/embed`가 spec대로 응답한다.
  - **Linked AC:** SPEC §5.1, §5.2, §5.3
  - **Depends On:** Task 5
  - **병렬 가능:** N

---

## 4. Integration Checklist & Done Criteria

### 4.1 통합 체크리스트 (Integration Checklist)

- [ ] Search Service가 기대하는 `POST {EMBEDDING_API_URL}`와 `{"texts":[...]}` 요청 shape가 정확히 일치한다.
- [ ] Pipeline Worker가 기대하는 batch embedding 응답 shape `{"embeddings":[[float,...], ...]}`와 순서 보장 규칙이 일치한다.
- [ ] 성공/실패 응답 모두 `X-Trace-Id`와 에러 바디 `trace_id`가 일관되다.
- [ ] `model_version`은 `/health`와 구조화 로그에만 노출되고, `/embed` 성공 응답 본문에는 포함되지 않는다.
- [ ] `model_version` SOT가 실제 로드한 artifact path 의미와 충돌하지 않는다.
- [ ] `SERVICE_UNAVAILABLE`, `INVALID_ARGUMENT`, `PAYLOAD_TOO_LARGE` 의미가 Search Service의 하위 호출 기대와 충돌하지 않는다.
- [ ] 서비스 내부 startup smoke inference 없이도 readiness 계약과 운영 smoke test 절차가 문서대로 분리되어 있다.
- [ ] Docker 이미지 기동 환경에서 로컬 모델 경로 마운트/복사 방식이 실행 지침에 포함되어 있다.

### 4.2 완료 조건 (Definition of Done)

- [ ] SPEC §5.1에 정의된 시나리오 테스트가 모두 녹색이다.
- [ ] 단위/API 테스트 합산 커버리지가 80% 이상이다 (`pytest-cov` 기준).
- [ ] 로드 성공 시 `/health`, 정상 `/embed`, guardrail 예외, ready 부재 `503`가 자동화 테스트로 검증된다.
- [ ] 실제 모델 artifact를 사용한 운영 smoke request 1건이 성공한다.
- [ ] Docker 이미지로 서비스 기동이 가능하다.

---

## 5. Rollout & Rollback Plan

### 5.1 배포 계획 (Rollout)

- **서비스 추가:** `services/managed-embedding-endpoint`를 신규 배포 단위로 추가한다.
- **환경 변수:** `PORT`, `MODEL_ARTIFACT_PATH`, `MODEL_CACHE_DIR`, `MAX_TEXTS_PER_REQUEST`, `MAX_TEXT_LENGTH_CHARS`, `MAX_PAYLOAD_BYTES`, `MAX_CONCURRENCY`
- **런타임 준비:** 배포 환경에 `bge-m3` artifact가 로컬 경로로 존재해야 하며, 프로세스가 해당 경로를 읽을 수 있어야 한다.
- **기동 검증:** 서비스 시작 후 `/health`가 `200`이 되는지 확인한다.
- **운영 smoke:** `/embed`에 `{"texts":["smoke test"]}` 1건을 보내 실제 벡터 응답을 검증한다.
- **하위 서비스 연결:** Search Service와 Pipeline Worker의 `EMBEDDING_API_URL`을 새 endpoint의 `/embed` 경로로 설정한다.

### 5.2 롤백 계획 (Rollback)

- **애플리케이션 롤백:** 이전 이미지/아티팩트로 즉시 복귀한다.
- **모델 롤백:** 이전 artifact path를 다시 배포하고 프로세스를 재기동한다.
- **호환성 복구:** `/embed` 요청/응답 shape는 버전 간 유지해야 하므로, 계약 변경이 섞인 부분 배포는 허용하지 않는다.
- **부분 적용 복구:** 서비스는 올라왔지만 실모델 로드에 실패한 경우, readiness가 열리지 않아야 하며 이전 안정 버전으로 즉시 되돌린다.

---

## Assumptions (확정된 사항)

- 서비스 코드는 `services/managed-embedding-endpoint` 루트 아래에 새로 생성한다.
- readiness는 모델 로드 성공만을 의미하고, 실제 추론 가능 여부는 운영 smoke request로 검증한다.
- `model_version` SOT는 실제 로드한 artifact path다.
- `/embed`는 내부 서비스 전용이며 사용자 인증/테넌시를 소유하지 않는다.
- 자동화 테스트는 model stub 기반으로 수행하고, 실모델 추론 검증은 배포 후 smoke 절차로 분리한다.
