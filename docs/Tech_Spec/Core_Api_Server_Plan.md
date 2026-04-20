# Core API Server PLAN

**Meta**
- **Component ID:** core-api-server
- **Target SPEC:** `docs/Tech_Spec/Core_Api_Server_Spec.md`
- **SOT:** `docs/system-design.md`, `docs/Tech_Spec/Core_Api_Server_Spec.md`, `docs/PRD.md`
- **Code Root:** `services/core-api`

---

> 이 PLAN은 Core API Server 구현을 작업 단위로 분해하고, 병렬 가능한 범위를 분명히 하여 여러 구현자 또는 코딩 에이전트가 같은 계약 위에서 동시에 작업할 수 있도록 만드는 실행 계획 문서다.

## 1. Goals & Strategy

### 1.1 달성 목표 (Goals)

- **Video ingest 접수 완성:** `POST /api/v1/videos`로 `LOCAL_FILE`과 `EXTERNAL_URL` 요청을 모두 접수하고, `PENDING` 저장 및 후속 처리 연결까지 완성한다.
- **업로드 완료 및 재처리 흐름 완성:** `/complete`, `/retry`, `DELETE`를 통해 `UPLOADED`, `PENDING`, `DELETING` 전이를 안전하게 처리하고 Worker에 필요한 메시지를 발행한다.
- **사용자별 메타데이터 관리 완성:** 목록 조회, 상세 조회, 제목/카테고리 수정, 재생 URL 재발급을 테넌시 보호와 함께 제공한다.
- **관측성:** 모든 요청·발행 로그에 `trace_id`를 포함하고, 큐 발행 실패·커서 디코드 실패·`/complete` 멱등 hit 등의 핵심 메트릭을 노출한다.
- **테스트:** Postgres 기반 통합 테스트를 포함하여 SPEC §5.1 시나리오를 모두 자동화하고, 단위·통합 테스트 합산 커버리지 80% 이상을 달성한다.

### 1.2 제외 대상 (Non-Goals)

- 미디어 다운로드, STT, 청킹, 임베딩, Vector Store 적재는 Pipeline Worker의 책임이며 이번 구현 범위에서 제외한다.
- 실제 연쇄 삭제(DB·Object Storage·Vector Store hard-delete)는 Pipeline Worker의 책임이며 Core API는 삭제 접수와 비동기 트리거만 수행한다.
- Search Service의 질의 처리, 검색 결과 생성, LLM 호출은 구현 범위에서 제외한다.
- 피드백 수집 API(`POST /api/v1/feedbacks`)는 계약은 유지하되 1차 구현 범위에서 제외한다.
- HLS/DASH 제어, 미디어 트랜스코딩, Admin 대시보드 배포는 이번 범위에서 제외한다.

### 1.3 리스크 및 대응 방안 (Risk & Mitigation)

- **MQ 발행 실패로 인한 후속 처리 누락:** 인라인 재시도를 우선 수행하고, 최종 실패는 500으로 반환하되 메시지 계약과 메트릭을 통해 운영자가 식별 가능하도록 만든다.
- **GCS 업로드 크기 제한 우회:** Signed URL 발급 시 `content-length-range` 조건을 주고, `/complete`에서 객체 존재 여부와 2GB 이하 조건을 다시 검증한다.
- **공유 계약 드리프트:** `PREPROCESS_REQUEST`, `DELETE_REQUEST`, `status`, `req_id`, `trace_id`는 Search Service/Worker spec과 교차 검증 후 구현한다.

### 1.4 구현 전제 및 열려 있는 결정사항 (Preconditions & Open Decisions)

- **구현 전제:** `docs/Tech_Spec/Core_Api_Server_Spec.md`가 Core API 계약의 기준이며, 상태 전이와 API/메시지 스키마는 현재 spec 기준으로 닫혀 있다.
- **선행 필요 사항:** Python/FastAPI 기반 프로젝트 골격, Alembic 마이그레이션, Postgres 테스트 환경(Testcontainers)이 준비되어야 한다.
- **열려 있는 결정사항:** 현재 spec만으로 Core API 구현을 시작하는 데 필수적으로 막히는 결정사항은 없다. 피드백 API는 별도 phase로 남긴다.

### 1.5 핵심 의존성 패키지

| 패키지 | 용도 | 최소 버전 |
| --- | --- | --- |
| `fastapi` | API 프레임워크 | 0.111+ |
| `uvicorn[standard]` | ASGI 서버 | 0.29+ |
| `pydantic-settings` | 환경 변수 로딩 | 2.x |
| `sqlalchemy[asyncio]` | ORM / 쿼리 빌더 | 2.x |
| `asyncpg` | PostgreSQL 비동기 드라이버 | 0.29+ |
| `alembic` | DB 마이그레이션 | 1.x |
| `PyJWT` | JWT 검증 | 2.x |
| `google-cloud-storage` | GCS Signed URL 발급 | 2.x |
| `httpx` | 비동기 API 테스트 및 외부 호출 클라이언트 | 0.27+ |
| `pytest-asyncio` | 비동기 테스트 | 0.23+ |
| `testcontainers[postgres]` | Postgres 통합 테스트 환경 | 4.x |

---

## 2. Implementation Phasing Strategy

- **Phase 1:** 앱 엔트리포인트, 설정 로딩, 인증/에러 처리, DTO/라우터 스켈레톤, 마이그레이션 베이스라인을 만든다.
- **Phase 2:** Video 모델, Repository, Cursor, Storage/Broker 어댑터를 구현하여 핵심 유스케이스가 의존할 영속성 및 외부 어댑터를 닫는다.
- **Phase 3:** 업로드/완료/조회/수정/삭제/재시도/재생 URL API를 구현하고 상태 전이 및 메시지 발행을 연결한다.
- **Phase 4:** 관측성, 테스트 보강, 통합 체크리스트, 배포/롤백 준비를 마무리한다.
- **병합 게이트:** 각 Phase 또는 하위 작업 단위마다 테스트 통과와 계약 검토를 선행하며, 공유 계약 변경은 Search/Worker spec과 교차 확인 후만 병합한다.

### 2.1 작업 분해 원칙 (Task Decomposition Rules)

- 각 Task는 하나의 명확한 출력물과 하나의 검증 가능한 완료 조건을 가진다.
- 병렬 작업은 파일 수정 범위가 겹치지 않도록 `services/core-api/src/core|middlewares|schemas|api`, `services/core-api/alembic|models|infra/db`, `services/core-api/src/infra/storage|services/core-api/src/infra/broker` 축으로 먼저 분리한다.
- `services/core-api/src/services/video_service.py`와 `services/core-api/src/api/v1/routers/videos.py`는 최종 통합 지점이므로, 선행 작업이 모두 닫힌 뒤 통합 담당자가 합치는 것을 원칙으로 한다.
- 각 Phase는 중간 병합 가능한 상태여야 하며, 다음 Phase가 이전 Phase의 미완성 계약을 추측하지 않도록 한다.
- 구현 순서는 레이어 나열보다 “업로드 접수 → 완료 처리 → 조회/수정/삭제/재시도”의 기능 슬라이스가 검증되는 순서를 우선한다.

### 2.2 선행 경로 및 병렬 가능 범위 (Critical Path & Parallelism)

- **Critical Path:** Task 0(앱/설정) → Task 2(Video 모델/Repository) → Task 4(업로드 준비) → Task 5(`/complete`) → Task 6(조회/수정/삭제/재시도/재생 URL) → Task 8(최종 검증)
- **Parallelizable Workstreams:** Task 1(auth/DTO/router skeleton), Task 2(model/repository), Task 3(storage/broker adapters)는 Task 0 이후 병렬 수행 가능하다.
- **Merge Owner / Integration Point:** 최종 통합은 `services/core-api/src/services/video_service.py`, `services/core-api/src/api/v1/routers/videos.py`, `services/core-api/tests/api/v1/test_videos.py`에서 수행한다. 이 지점에서 Shared Contract 검토와 E2E 테스트를 함께 닫는다.

---

## 3. Work Breakdown Structure (WBS)

> 구현자가 그대로 실행할 수 있는 작업 지시서다.
> 모든 작업은 `Output / Files / Test Files / Commands / Verify / Linked AC / Depends On`를 포함한다.
> 병렬화 가능한 작업은 `병렬 가능: Y` 또는 `병렬 가능: N`으로 표시한다.

### Phase 1: Skeleton & Contracts

- [x] **Task 0: 앱 엔트리포인트 및 설정 스캐폴딩**
  - **Output:** FastAPI 앱 팩토리, `Settings` 클래스, 라우터 마운트, 기본 DI 진입점, `.env.example`
  - **Files:** `services/core-api/src/main.py`, `services/core-api/src/core/config.py`, `services/core-api/src/core/dependencies.py`, `services/core-api/.env.example`
  - **Test Files:** `services/core-api/tests/unit/test_config.py`
  - **Commands:** `cd services/core-api && pytest tests/unit/test_config.py`
  - **Verify:** 필수 환경 변수 누락 시 기동이 실패하고, 정상 설정에서는 앱이 기동한다.
  - **Linked AC:** SPEC §1.2, SPEC §5.1 공통 전제
  - **Depends On:** 없음
  - **병렬 가능:** N

- [x] **Task 1: 인증/에러 계약/DTO/라우터 스켈레톤**
  - **Output:** JWT 검증, `requester_user_id` 추출, 공통 에러 바디(`code`, `message`, `trace_id`), video 요청/응답 DTO, video 라우터 스켈레톤
  - **Files:** `services/core-api/src/middlewares/auth.py`, `services/core-api/src/middlewares/error_handler.py`, `services/core-api/src/schemas/video_dto.py`, `services/core-api/src/api/v1/routers/videos.py`
  - **Test Files:** `services/core-api/tests/unit/test_auth_middleware.py`, `services/core-api/tests/unit/test_video_dto.py`
  - **Commands:** `cd services/core-api && pytest tests/unit/test_auth_middleware.py tests/unit/test_video_dto.py`
  - **Verify:** JWT 미제공/만료/권한 위반/잘못된 payload가 spec의 에러 계약대로 매핑된다.
  - **Linked AC:** SPEC §2.1, SPEC §2.4, SPEC §5.1 인증 및 입력 검증 시나리오
  - **Depends On:** Task 0
  - **병렬 가능:** Y

- [x] **Task 2: Video 모델, Alembic, Repository, Cursor 구현**
  - **Output:** `Video` ORM 모델, Alembic 마이그레이션, keyset pagination용 cursor 유틸, `VideoRepository`
  - **Files:** `services/core-api/alembic/env.py`, `services/core-api/alembic/versions/0001_init_video.py`, `services/core-api/src/models/video.py`, `services/core-api/src/infra/db/video_repository.py`, `services/core-api/src/infra/db/cursor.py`
  - **Test Files:** `services/core-api/tests/integration/test_video_repository.py`
  - **Commands:** `cd services/core-api && alembic upgrade head`, `cd services/core-api && pytest tests/integration/test_video_repository.py`
  - **Verify:** `Video` DDL과 인덱스가 생성되고, 테넌시 필터 및 cursor pagination이 통합 테스트로 검증된다.
  - **Linked AC:** SPEC §2.2, SPEC §2.5, SPEC §5.1 GET/목록 시나리오
  - **Depends On:** Task 0
  - **병렬 가능:** Y

### Phase 2: Persistence & Adapters

- [x] **Task 3: Storage/Broker 인터페이스 및 구현체**
  - **Output:** `StorageClient`, `BrokerClient`, GCS/PGMQ 운영 구현체, InMemory 테스트 구현체
  - **Files:** `services/core-api/src/infra/storage.py`, `services/core-api/src/infra/gcs_client.py`, `services/core-api/src/infra/inmemory_storage.py`, `services/core-api/src/infra/broker.py`, `services/core-api/src/infra/pgmq_client.py`, `services/core-api/src/infra/inmemory_broker.py`
  - **Test Files:** `services/core-api/tests/unit/test_storage_clients.py`, `services/core-api/tests/unit/test_broker.py`
  - **Commands:** `cd services/core-api && pytest tests/unit/test_storage_clients.py tests/unit/test_broker.py`
  - **Verify:** Signed URL 발급과 메시지 발행 payload가 spec 계약과 일치하고, 실패 재시도 경로가 테스트된다.
  - **Linked AC:** SPEC §2.1 Object Storage/Broker 계약, SPEC §2.3, SPEC §2.4
  - **Depends On:** Task 0
  - **병렬 가능:** Y

- [x] **Task 4: 업로드 준비 유스케이스 구현 (`POST /api/v1/videos`)**
  - **Output:** Local File/External URL 분기, `PENDING` 저장, `storage_path` 확정, Local File Signed URL 발급, External URL `PREPROCESS_REQUEST` 발행
  - **Files:** `services/core-api/src/services/video_service.py`, `services/core-api/src/api/v1/routers/videos.py`
  - **Test Files:** `services/core-api/tests/unit/test_video_service_upload.py`, `services/core-api/tests/api/v1/test_video_create.py`
  - **Commands:** `cd services/core-api && pytest tests/unit/test_video_service_upload.py tests/api/v1/test_video_create.py`
  - **Verify:** Local File은 201 + Signed URL, External URL은 202 + 즉시 `PREPROCESS_REQUEST` 발행이 검증된다.
  - **Linked AC:** SPEC §3.1 A/B, SPEC §5.1 `POST /api/v1/videos`
  - **Depends On:** Task 1, Task 2, Task 3
  - **병렬 가능:** N

- [x] **Task 5: 업로드 완료 유스케이스 구현 (`POST /api/v1/videos/{id}/complete`)**
  - **Output:** 객체 존재/크기 검증, `PENDING -> UPLOADED`, `/complete` 멱등성, `PREPROCESS_REQUEST` 발행
  - **Files:** `services/core-api/src/services/video_service.py`, `services/core-api/src/api/v1/routers/videos.py`
  - **Test Files:** `services/core-api/tests/unit/test_video_service_complete.py`, `services/core-api/tests/api/v1/test_video_complete.py`
  - **Commands:** `cd services/core-api && pytest tests/unit/test_video_service_complete.py tests/api/v1/test_video_complete.py`
  - **Verify:** 최초 요청 202, 중복 요청 200, 2GB 초과 400, 객체 미존재 400, MQ 중복 재발행 없음이 검증된다.
  - **Linked AC:** SPEC §2.4 `/complete` 멱등 규칙, SPEC §5.1 `POST /api/v1/videos/{id}/complete`
  - **Depends On:** Task 4
  - **병렬 가능:** N

### Phase 3: Application Flows

- [x] **Task 6: 조회/수정/삭제/재시도/재생 URL API 구현**
  - **Output:** 목록 조회, 상세 조회, 제목/카테고리 수정, 삭제 접수, 재시도, 재생 URL 재발급 API 구현
  - **Files:** `services/core-api/src/services/video_service.py`, `services/core-api/src/api/v1/routers/videos.py`
  - **Test Files:** `services/core-api/tests/api/v1/test_videos.py`
  - **Commands:** `cd services/core-api && pytest tests/api/v1/test_videos.py`
  - **Verify:** `DELETING` 수정 차단, `FAILED`만 retry 허용, `READY + LOCAL_FILE`만 playback-url 허용, 목록 조회에서 `DELETING` 제외가 검증된다.
  - **Linked AC:** SPEC §2.1 API 표, SPEC §3.1 C/D/E, SPEC §5.1 GET/PATCH/DELETE/retry/playback-url
  - **Depends On:** Task 2, Task 3, Task 5
  - **병렬 가능:** N

- [x] **Task 7: 관측성 및 공통 예외 처리 보강**
  - **Output:** 구조화 로깅, `trace_id` 전파, 핵심 메트릭, 공통 예외 매핑
  - **Files:** `services/core-api/src/common/logging.py`, `services/core-api/src/common/metrics.py`, `services/core-api/src/middlewares/error_handler.py`, `services/core-api/src/middlewares/trace.py`
  - **Test Files:** `services/core-api/tests/unit/test_error_handler.py`, `services/core-api/tests/unit/test_metrics.py`
  - **Commands:** `cd services/core-api && pytest tests/unit/test_error_handler.py tests/unit/test_metrics.py`
  - **Verify:** 성공/실패 경로 모두에 `trace_id`가 일관되게 남고, `mq_publish_fail_count`, `cursor_decode_fail_count`, `complete_idempotent_hit_count`, `gcs_signed_url_latency_ms`가 노출된다.
  - **Linked AC:** SPEC §4, SPEC §5.2
  - **Depends On:** Task 1, Task 4, Task 5, Task 6
  - **병렬 가능:** Y

### Phase 4: Final Integration & Release Readiness

- [x] **Task 8: 최종 통합 검증 및 릴리스 준비**
  - **Output:** 전체 API/Repository/Adapter 연결 검증, acceptance test sweep, CI 기준 충족, rollout/rollback 점검
  - **Files:** `.github/workflows/...`, `services/core-api/tests/...`
  - **Test Files:** 전체 테스트 스위트
  - **Commands:** `cd services/core-api && alembic upgrade head`, `cd services/core-api && pytest`, `cd services/core-api && pytest --cov`
  - **Verify:** SPEC §5.1 시나리오가 모두 녹색이고, 커버리지 목표와 통합 체크리스트가 충족된다.
  - **Linked AC:** SPEC §5.1, SPEC §5.2, SPEC §5.3
  - **Depends On:** Task 6, Task 7
  - **병렬 가능:** N

---

## 4. Integration Checklist & Done Criteria

### 4.1 통합 체크리스트 (Integration Checklist)
- [ ] `PREPROCESS_REQUEST`, `DELETE_REQUEST` 메시지 스키마가 Pipeline Worker spec과 일치하며 `payload_version=v1`을 사용한다.
- [ ] `status` 값(`PENDING`, `UPLOADED`, `PROCESSING`, `READY`, `FAILED`, `DELETING`)과 `failed_stage` 의미가 Search/Worker spec과 충돌하지 않는다.
- [ ] 모든 DB 조회·변경과 API 응답이 `requester_user_id` 기반 테넌시 규칙을 일관되게 적용한다.
- [ ] `req_id`는 Search Service가 생성하는 opaque 상관관계 ID라는 의미를 유지하고, Core API는 UUID 형식만 검증한다.
- [ ] Local File 업로드는 Signed URL `content-length-range`와 `/complete` 시점 `blob.size` 이중 검증을 적용한다.
- [ ] `DELETE` 시 즉시 `DELETING`으로 전이하고 검색 범위 제외 의미를 유지한다.
- [ ] `retry`는 `FAILED` 상태에서만 허용하고, 실제 Resume 판단은 Worker 책임으로 남긴다.
- [ ] 신규 환경에서 마이그레이션, 설정, 테스트가 재현 가능하다.

### 4.2 완료 조건 (Definition of Done)
- [ ] SPEC §5.1에 정의된 시나리오 테스트가 모두 녹색이다.
- [ ] 단위·통합 테스트 합산 커버리지가 80% 이상이다 (`pytest-cov` 기준).
- [ ] 단위·통합 테스트가 CI에서 통과한다.
- [ ] 핵심 메트릭 4종이 정상 노출된다.

---

## 5. Rollout & Rollback Plan

### 5.1 배포 계획 (Rollout)
- **환경 변수 추가:** `DATABASE_URL`, `BROKER_TYPE`, `GCS_VIDEO_BUCKET_NAME`, `GCP_PROJECT_ID`, `JWT_SECRET_KEY`
- **인프라/스키마 변경:** `cd services/core-api && alembic upgrade head`로 `video` 테이블과 인덱스를 생성한다.
- **호환성 확인:** Worker가 소비하는 `PREPROCESS_REQUEST`, `DELETE_REQUEST` 스키마와 큐 이름이 현행 spec과 일치하는지 확인한다.

### 5.2 롤백 계획 (Rollback)
- **애플리케이션 롤백:** 이전 버전의 컨테이너/아티팩트로 즉시 복귀한다.
- **데이터베이스 스키마 원복:** `cd services/core-api && alembic downgrade -1` 또는 이전 안정 버전 기준으로 복구한다. 단, 데이터 손실 영향은 사전 검토한다.
- **메시지/비동기 호환성:** 롤백 시 남아 있는 신규 포맷 메시지는 재처리하지 않도록 큐를 보관/아카이브하거나 수동 폐기한다.
- **부분 적용 복구:** 배포 도중 애플리케이션만 반영되고 스키마가 어긋난 경우, 우선 앱을 롤백한 뒤 스키마를 복구한다.

---

## Assumptions (확정된 사항)

- DB 마이그레이션 도구는 Alembic을 사용한다.
- Postgres 통합 테스트는 Testcontainers 기반으로 수행한다.
- Worker의 멱등성 및 Resume 로직은 system-design과 Pipeline Worker spec을 따른다.
- 피드백 수집 API는 계약 유지 대상이지만 1차 구현 범위에서 제외한다.
