# Core API Server PLAN

**Meta**
- **Component ID:** core-api-server
- **Status:** Draft
- **Target SPEC:** `docs/Tech_Spec/Core_Api_Server_Spec.md`
- **SOT:** `docs/system-design.md`, `docs/Tech_Spec/Core_Api_Server_Spec.md`

---

## 1. Goals & Strategy

### 1.1 달성 목표

- **Local File 업로드:** 요청 시 201 응답과 Signed URL을 발급하고, `/complete`에서 최초 요청은 202, 중복 요청은 200으로 멱등 처리하며, `PREPROCESS_REQUEST` 메시지 발행까지의 E2E 흐름을 완성한다.
- **External URL 인입:** 요청 시 202 응답 후 즉시 `PREPROCESS_REQUEST`를 발행하여 Worker가 다운로드를 시작하도록 트리거한다.
- **Reconciler:** Background Task로 구현하여, 메시지 발행이 누락된 건을 주기적으로 자동 재발행한다.
- **관측성:** 모든 요청·발행 로그에 `trace_id`를 포함하고, MQ 발행 실패·재발행·커서 디코드 실패 등 핵심 메트릭을 노출한다.
- **테스트:** Postgres 기반 통합 테스트를 작성하여 SPEC DoD(Definition of Done) 시나리오 10건을 모두 충족한다.

### 1.2 제외 대상 (Non-Goals)

- DLQ(Dead Letter Queue) 재처리 워크플로우는 Worker 측 책임이므로 이번 범위에서 제외한다.
- Pipeline Controller의 연쇄 삭제 및 하드 삭제 실행은 제외한다.
- Admin 대시보드 및 모니터링 인프라 배포는 제외한다.
- 피드백 수집 API(`POST /api/v1/feedbacks`)는 Phase 2 항목으로, 이번 구현 범위에서 제외한다. (SPEC §2.1에 계약은 정의되어 있으나 WBS에는 포함하지 않음)

### 1.3 리스크 및 대응 방안 (Risk & Mitigation)

- **MQ 발행 실패로 인한 메시지 누락:** 인라인 재시도를 우선 수행하고, 최종 실패 시 Reconciler가 주기적으로 보정 발행한다.
- **Background Task 지연 또는 중단:** 헬스체크 엔드포인트와 메트릭 알림을 통해 Reconciler 상태를 감시한다.
- **GCS 업로드 크기 제한 우회 시도:** Signed URL 생성 시 `content-length-range` 조건을 설정하고, `/complete` 시점에서 `blob.size`를 재검증하여 이중으로 차단한다.

### 1.4 핵심 의존성 패키지

| 패키지 | 용도 | 최소 버전 |
| --- | --- | --- |
| `fastapi` | API 프레임워크 | 0.111+ |
| `uvicorn[standard]` | ASGI 서버 | 0.29+ |
| `pydantic-settings` | 환경 변수 로딩 (Settings 클래스) | 2.x |
| `sqlalchemy[asyncio]` | ORM / 쿼리 빌더 | 2.x |
| `asyncpg` | PostgreSQL 비동기 드라이버 | 0.29+ |
| `alembic` | DB 마이그레이션 | 1.x |
| `PyJWT` | JWT 검증 | 2.x |
| `google-cloud-storage` | GCS Signed URL 발급 | 2.x |
| `aio-pika` | RabbitMQ 비동기 클라이언트 | 9.x |
| `apscheduler` | Reconciler 스케줄러 | 3.x |
| `testcontainers[postgres]` | 통합 테스트용 Postgres 컨테이너 | 4.x |
| `pytest-asyncio` | 비동기 단위/통합 테스트 | 0.23+ |
| `freezegun` | Reconciler 시간 고정 테스트 | 1.x |

---

## 2. Implementation Phasing Strategy

- **Phase 1:** FastAPI 앱 팩토리, Settings, JWT 미들웨어, DTO/Schema, Router 스텁, Alembic 베이스라인.
- **Phase 2:** Signed URL 발급, `/complete` 멱등 처리, Repository(Keyset 페이지네이션), DELETE/PATCH 엔드포인트.
- **Phase 3:** RabbitMQ 퍼블리셔, Reconciler Background Task, 관측성(메트릭/로깅), 에러 미들웨어.
- **병합 게이트:** 각 단계 또는 자율적으로 나눈 PR 단위로 CI(단위·통합 테스트) 통과 + 1인 이상 리뷰.

---

## 3. Work Breakdown Structure (WBS)

### Phase 1: Skeleton & Contracts

- [ ] **Task 0: FastAPI 앱 팩토리 및 설정 로딩** *(다른 모든 Task의 전제)*
  - **Output:** `pydantic-settings` 기반 `Settings` 클래스(필수 환경 변수 타입 정의), FastAPI 앱 팩토리 함수, 미들웨어 등록 순서(`auth` → `error_handler`), 라우터 마운트, lifespan 이벤트(DB 풀 초기화, Reconciler 스케줄러 시작/종료).
  - **Files:** `src/main.py`, `src/core/config.py`, `.env.example`
  - **Verify:** `uvicorn src.main:app` 기동 후 `/docs` 접근 가능; 필수 환경 변수 누락 시 `ValidationError` 발생 확인.
  - **Linked AC:** 전 Task 공통 전제.

- [ ] **Task 1: JWT/테넌시 미들웨어 및 공통 응답 포맷**
  - **Output:** `requester_user_id` 추출(`Depends(get_current_user)`), 401/403 처리, 표준 에러 바디(`{"code", "message", "trace_id"}`).
  - **Files:** `src/middlewares/auth.py`, `src/middlewares/error_handler.py`
  - **Test Files:** `tests/unit/test_auth_middleware.py`
  - **Verify:** 단위 테스트로 JWT 미제공 시 401, 만료 토큰 시 401, 타인 video_id 포함 시 403 반환 확인.
  - **Linked AC:** DoD 5.1 — 테넌시 위반 차단

- [ ] **Task 2: DTO/Schema & Router 스텁**
  - **Output:** `input_type` 판별 유니온(Discriminated Union) 기반 Pydantic 요청/응답 스키마, cursor DTO, Router 경로 및 의존성 주입 뼈대 코드.
  - **Files:** `src/schemas/video_dto.py`, `src/api/v1/routers/videos.py`
  - **Test Files:** `tests/unit/test_video_dto.py` (미지원 확장자 → 400, 잘못된 input_type → 400, source_url 누락 → 400)
  - **Verify:** 잘못된 payload 전송 시 400, JWT 의존성 주입 정상 동작 단위 테스트 확인.
  - **Linked AC:** DoD 5.1 — 예외 흐름, 테넌시 위반 차단

- [ ] **Task 3: Alembic 베이스라인 + Video 모델**
  - **Output:** SPEC §2.5 DDL 기준 `Video` 테이블 Alembic 마이그레이션 스크립트와 SQLAlchemy 비동기 모델.
  - **Files:** `alembic/env.py`, `alembic/versions/0001_init_video.py`, `src/models/video.py`
  - **Test Files:** (마이그레이션은 `alembic upgrade head` 명령으로 수동 검증)
  - **Verify:** `alembic upgrade head` 성공; `idx_video_user_created`, `idx_video_user_status` 인덱스 존재 확인.
  - **Linked AC:** DoD 5.1 — 정상 흐름 초기 상태

### Phase 2: Core Logic & Persistence

> **구현 순서**: Task 6(Repository) → Task 4(Signed URL) → Task 5(/complete) → Task 7(DELETE/PATCH). Repository가 서비스 레이어의 기반이므로 먼저 구현.

- [ ] **Task 6: Repository 및 트랜잭션 경계 설정** *(Phase 2 선행 작업)*
  - **Output:** AsyncSession 기반 트랜잭션 처리; Opaque Cursor(`Base64URL("{created_at, id}")`) 인코드/디코드 유틸; Keyset 페이지네이션(`(created_at DESC, id DESC)`) 조회 로직; Video INSERT/UPDATE/SELECT(테넌시 필터 필수).
  - **Files:** `src/infra/db/video_repository.py`
  - **Test Files:** `tests/integration/test_video_repository.py` (Testcontainers; 트랜잭션 롤백, 연속 페이지 중복/누락 없음, cursor round-trip, 잘못된 토큰 → 400)
  - **Verify:** Testcontainers 통합 테스트 통과; cursor encode/decode round-trip 및 예외 케이스 확인.
  - **Linked AC:** DoD 5.1 — 커서 페이지네이션 정합성

- [ ] **Task 4: Signed URL 발급 및 업로드 준비 유스케이스** *(Task 6 완료 후)*
  - **Output:** `VideoService.initiate_upload()` — Local File 요청 시 Video INSERT → GCS Signed URL 발급 → 201 반환; External URL 요청 시 Video INSERT → `PREPROCESS_REQUEST` 발행 → 202 반환. GCS `content-length-range` 조건 설정 포함.
  - **Files:** `src/services/video_service.py`, `src/infra/gcs_client.py`
  - **Test Files:** `tests/unit/test_video_service_upload.py` (GCS/MQ AsyncMock; Local File 201+URL, External URL 202+발행 확인)
  - **Verify:** 통합 테스트로 Local File 준비 성공, External URL 준비 성공 시나리오 통과.
  - **Linked AC:** DoD 5.1 — 정상 흐름 1, External URL 준비 성공

- [ ] **Task 5: `/complete` 멱등 처리 및 상태 전이** *(Task 4 완료 후)*
  - **Output:** `VideoService.complete_upload()` — GCS `blob.exists()` + `blob.size <= 2GB` 검증; 최초 요청 시 `UPLOADED`로 UPDATE → `PREPROCESS_REQUEST` 발행 → 202; 중복 요청(`UPLOADED/PROCESSING/READY` 상태) 시 200 반환(DB 변경 없음, MQ 재발행 없음).
  - **Files:** `src/services/video_service.py`
  - **Test Files:** `tests/unit/test_video_service_complete.py` (멱등 호출 시 MQ 미발행, 파일 크기 초과 400, 상태 전이 확인)
  - **Verify:** 멱등성 테스트, 사이즈 초과 400, 상태 전이 정상 동작 확인.
  - **Linked AC:** DoD 5.1 — 멱등성 검증, 파일 크기 초과 차단

- [ ] **Task 7: DELETE/PATCH 엔드포인트** *(Task 6 완료 후)*
  - **Output:** `DELETE /api/v1/videos/{id}` — 테넌시 확인 후 202 반환(연쇄 삭제는 Pipeline Controller 위임); `PATCH /api/v1/videos/{id}` — `deleted=true` 또는 삭제 진행 중이면 409, 그 외 title/category UPDATE 후 200 반환. GET `/api/v1/videos`, GET `/api/v1/videos/{id}` 라우터도 이 Task에서 완성.
  - **Files:** `src/api/v1/routers/videos.py`, `src/services/video_service.py`
  - **Test Files:** `tests/api/v1/test_videos.py` (DELETE 202 위임, PATCH 409 조건, GET 페이지네이션)
  - **Verify:** DoD 5.1 — 삭제 요청 위임, 테넌시 위반 차단, 미존재 리소스 404 시나리오 통과.
  - **Linked AC:** DoD 5.1 — 삭제 요청 위임, 테넌시 위반, 404

### Phase 3: Integration & Ops

- [ ] **Task 8: RabbitMQ 퍼블리셔**
  - **Output:** `payload_version=v1`, `trace_id`, `attempt` 포함 MessageEnvelope 발행; 지수 백오프 재시도(최대 3회); 최종 실패 시 500 반환.
  - **Files:** `src/infra/rabbitmq_client.py`, `src/services/video_service.py`
  - **Test Files:** `tests/unit/test_rabbitmq_client.py` (페이로드 스키마 검증, 라우팅 키 확인, 재시도 후 500 반환)
  - **Verify:** 큐 도달 메시지 스키마·라우팅 키가 SPEC §2.1과 일치; 발행 실패 시 재시도 후 500 확인.
  - **Linked AC:** DoD 5.1 — 정상 흐름 2

- [ ] **Task 9: Reconciler Background Task**
  - **Output:** apscheduler 1분 주기 실행; 정체 기준: `updated_at <= NOW() - INTERVAL '5 minutes'`; 대상: ① `status=UPLOADED` ② `input_type=EXTERNAL_URL AND status=PENDING`; 재발행 시 `attempt+1`.
  - **Files:** `src/services/video_reconciler.py`, `src/main.py` (lifespan scheduler hook)
  - **Test Files:** `tests/services/test_video_reconciler.py` (freezegun으로 시간 고정; 5분 초과 건 재발행 확인, attempt 증가 확인, 5분 미만 건 미발행 확인)
  - **Verify:** freezegun 테스트로 재발행 조건 충족 시 메시지 발행 + attempt 증가; 미충족 시 미발행 확인.
  - **Linked AC:** DoD 5.1 — MQ 실패 보정

- [ ] **Task 10: Observability 및 Error Handling**
  - **Output:** `trace_id`, `user_id`, `video_id` 포함 구조화 로깅; 메트릭(`mq_publish_fail_count`, `reconciler_republish_count`, `cursor_decode_fail_count`, `complete_idempotent_hit_count`, `gcs_signed_url_latency_ms`) 노출; 공통 에러 응답 미들웨어(`{"code","message","trace_id"}`).
  - **Files:** `src/common/logging.py`, `src/common/metrics.py`, `src/middlewares/error_handler.py`
  - **Test Files:** `tests/unit/test_error_handler.py` (에러 경로에서 trace_id 포함 응답 확인)
  - **Verify:** 에러 및 성공 경로 모두 trace_id 일관성 확인; 각 메트릭이 해당 이벤트 발생 시 증가 확인.
  - **Linked AC:** DoD 4장 — 관측성, 테넌시

---

## 4. Integration Checklist & Done Criteria

### 4.1 통합 체크리스트
- [ ] API 응답 및 MQ 메시지 스키마가 SPEC §2.1에 정의된 구조와 일치하며, `payload_version=v1`을 사용한다.
- [ ] 모든 DB 조회·변경 및 큐 발행 시 `user_id` 기반 테넌시 필터가 적용되어 있다.
- [ ] GCS 및 MQ 호출에 타임아웃이 설정되어 있고, 인라인 재시도(최대 3회)와 Reconciler 폴백이 동작한다.
- [ ] Local File 업로드는 Signed URL `content-length-range` 조건과 `/complete` 시점 `blob.size` 이중 검증이 적용된다.
- [ ] Reconciler grace period는 5분이며, `attempt` 필드가 재발행마다 1씩 증가한다.

### 4.2 완료 조건 (Definition of Done)
- [ ] SPEC §5.1에 정의된 10개 시나리오 테스트가 모두 녹색이다.
- [ ] 단위·통합 테스트가 CI에서 통과한다.
- [ ] 핵심 메트릭 5종이 정상 노출된다.

---

## 5. Rollout & Rollback Plan

- **Rollout**
  - 환경변수: `DATABASE_URL`, `RABBITMQ_URL`, `GCS_VIDEO_BUCKET_NAME`, `GCP_PROJECT_ID`, `JWT_SECRET_KEY`를 배포 환경에 설정한다.
  - 마이그레이션: `alembic upgrade head`를 실행하여 Video 테이블과 인덱스를 생성한다.
  - Reconciler: FastAPI 스타트업 시 스케줄러를 활성화하고, 실행 주기는 기본 1분으로 설정한다.
- **Rollback**
  - 애플리케이션: 이전 버전의 컨테이너 이미지로 즉시 롤백한다.
  - DB: `alembic downgrade -1`로 Video 테이블을 제거한다. 단, 데이터 손실 영향을 사전 검토한 후 실행한다.
  - MQ: 롤백 시 큐에 신규 포맷 메시지가 남아 있으면 DLQ로 라우팅하거나 수동 폐기한다.
  - Reconciler: 장애 시 환경 변수 플래그(`RECONCILER_ENABLED=false`)를 통해 스케줄러를 즉시 비활성화한다.

---

## Assumptions (확정된 사항)

- Reconciler는 FastAPI lifespan 내 apscheduler로 주기 실행한다.
- DB 마이그레이션 도구는 Alembic을 사용한다.
- Postgres 통합 테스트는 Testcontainers 기반으로 수행한다.
- Worker의 멱등성은 system-design SOT를 준수하여, 중복 메시지를 안전하게 처리한다고 가정한다.
