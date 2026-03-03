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

### 1.3 리스크 및 대응 방안 (Risk & Mitigation)

- **MQ 발행 실패로 인한 메시지 누락:** 인라인 재시도를 우선 수행하고, 최종 실패 시 Reconciler가 주기적으로 보정 발행한다.
- **Background Task 지연 또는 중단:** 헬스체크 엔드포인트와 메트릭 알림을 통해 Reconciler 상태를 감시한다.
- **GCS 업로드 크기 제한 우회 시도:** Signed URL 생성 시 `content-length-range` 조건을 설정하고, `/complete` 시점에서 `blob.size`를 재검증하여 이중으로 차단한다.

---

## 2. PR & Branch Strategy

- **PR #1 — Skeleton & Contracts:** 프로젝트 디렉토리 구조, Settings, DTO/Schema, Router 스텁, Alembic 베이스라인, Postgres 테스트 픽스처를 구성한다.
- **PR #2 — Core Logic & Persistence:** 상태 전이와 멱등성 서비스, Signed URL 발급, `/complete` 가드 로직, Video 모델 및 리포지토리를 구현하고, 통합 테스트(Local/External URL, 멱등성, 사이즈 검증)를 작성한다.
- **PR #3 — MQ & Ops:** RabbitMQ 퍼블리셔, Reconciler Background Task, 관측성(메트릭/로깅), 에러 미들웨어, 커서 인코드/디코드 유틸을 구현하고, 큐 관련 통합 테스트를 작성한다.
- **Merge Gate:** CI(포맷 + lint + tests)를 통과하고 1인 이상의 리뷰 승인을 받은 후 병합한다. PR 간 의존성은 stacked rebase로 관리한다.

---

## 3. Work Breakdown Structure (WBS)

### Phase 1: Skeleton & Contracts (PR #1)

- [ ] **Task 1: Settings/Config 스캐폴딩**
  - **Output:** `.env.example` 파일과 환경 변수를 로드하는 `src/core/config.py`, 기본 로깅 설정을 작성한다.
  - **Files:** `src/core/config.py`, `.env.example`
  - **Verify:** 필수 환경 변수가 누락되었을 때 애플리케이션이 부팅에 실패하는지 테스트로 검증한다.
  - **Linked AC:** DoD 4.1 — 테넌시 및 계약 정합성

- [ ] **Task 2: DTO/Schema & Router 스텁**
  - **Output:** `input_type` 필드로 `LOCAL_FILE`과 `EXTERNAL_URL`을 분기하는 판별 유니온(Discriminated Union) 기반의 Pydantic 요청/응답 스키마와, Router 경로 및 의존성 주입 뼈대 코드를 작성한다.
  - **Files:** `src/schemas/video_dto.py`, `src/api/v1/routers/videos.py`
  - **Verify:** 잘못된 payload 전송 시 400 에러가 반환되는지, JWT 의존성 주입이 정상 동작하는지 단위 테스트로 검증한다.
  - **Linked AC:** DoD 5.1 — 예외 흐름, 테넌시 위반 차단

- [ ] **Task 3: Alembic 베이스라인 + Video 모델**
  - **Output:** `Video` 테이블의 DDL을 정의하는 Alembic 마이그레이션 스크립트와 SQLAlchemy 모델을 작성한다.
  - **Files:** `alembic/env.py`, `alembic/versions/20240303_init_video.py`, `src/models/video.py`
  - **Verify:** `alembic upgrade head` 명령이 성공하고, 필수 컬럼과 인덱스가 존재하는지 확인한다.
  - **Linked AC:** DoD 5.1 — 정상 흐름 초기 상태

### Phase 2: Core Logic & Persistence (PR #2)

- [ ] **Task 4: Signed URL 발급 및 업로드 준비 유스케이스**
  - **Output:** Local File 요청 시 201 응답과 Signed URL을 반환하고, External URL 요청 시 202 응답과 함께 즉시 메시지를 발행하는 서비스 로직을 구현한다.
  - **Files:** `src/services/video_service.py`, `src/infra/gcs_client.py`
  - **Verify:** DoD의 Local File 준비 성공 시나리오와 External URL 준비 성공 시나리오를 통합 테스트로 통과한다.
  - **Linked AC:** DoD 5.1 — 정상 흐름 1, External URL 준비 성공

- [ ] **Task 5: `/complete` 멱등 처리 및 상태 전이**
  - **Output:** GCS 오브젝트 존재 여부와 파일 크기(`blob.size <= 2GB`)를 검증하고, 최초 요청은 202 / 중복 요청은 200으로 응답하며, 상태를 `UPLOADED`로 전이하는 로직을 구현한다.
  - **Files:** `src/services/video_service.py`
  - **Verify:** 멱등성 테스트(중복 호출 시 큐 재발행 없음), 사이즈 초과 시 400 반환, 상태 전이 정상 동작을 단위/통합 테스트로 검증한다.
  - **Linked AC:** DoD 5.1 — 멱등성 검증, 파일 크기 초과 차단

- [ ] **Task 6: Repository 및 트랜잭션 경계 설정**
  - **Output:** AsyncSession 기반 트랜잭션 처리와, Opaque Cursor를 활용한 Keyset 페이지네이션 조회 로직을 구현한다.
  - **Files:** `src/infra/db/video_repository.py`
  - **Verify:** 예외 발생 시 트랜잭션이 롤백되는지, 연속 페이지 조회 시 결과에 중복이나 누락이 없는지 테스트로 검증한다.
  - **Linked AC:** DoD 5.1 — 커서 페이지네이션 정합성

### Phase 3: Integration & Ops (PR #3)

- [ ] **Task 7: RabbitMQ 퍼블리셔**
  - **Output:** `payload_version`, `trace_id`, `attempt` 필드를 포함하는 MessageEnvelope 형태로 메시지를 발행하고, 실패 시 지수 백오프 재시도 정책을 적용하는 퍼블리셔를 구현한다.
  - **Files:** `src/infra/rabbitmq_client.py`, `src/services/video_service.py`
  - **Verify:** 큐에 도달하는 메시지의 페이로드 스키마와 라우팅 키가 SPEC과 일치하는지, 발행 실패 시 재시도 후 500이 반환되는지 검증한다.
  - **Linked AC:** DoD 5.1 — 정상 흐름 2

- [ ] **Task 8: Reconciler Background Task**
  - **Output:** FastAPI 스타트업 훅에 스케줄러(apscheduler)를 등록하여, `UPLOADED` 또는 `PENDING(EXTERNAL_URL)` 상태에서 일정 시간 이상 정체된 건의 `PREPROCESS_REQUEST`를 재발행하는 Reconciler를 구현한다.
  - **Files:** `src/services/video_reconciler.py`, `src/main.py` (scheduler hook)
  - **Verify:** 시간을 고정(freezegun)한 테스트로, 재발행 조건 충족 시 메시지가 발행되고 `attempt`가 증가하는지 검증한다.
  - **Linked AC:** DoD 5.1 — MQ 실패 보정

- [ ] **Task 9: Observability 및 Error Handling**
  - **Output:** `trace_id`와 `video_id`를 포함하는 구조화 로깅, 핵심 메트릭(`mq_publish_fail_count`, `reconciler_republish_count`, `cursor_decode_fail_count`, `complete_idempotent_hit_count`) 노출, 공통 에러 응답 미들웨어를 구현한다.
  - **Files:** `src/common/logging.py`, `src/common/metrics.py`, `src/middlewares/error_handler.py`
  - **Verify:** 에러 및 성공 경로 모두에서 `trace_id`가 일관되게 노출되는지, 각 메트릭이 해당 이벤트 발생 시 정상적으로 증가하는지 단위 테스트로 검증한다.
  - **Linked AC:** DoD 4장 — 관측성, 테넌시

---

## 4. Integration Checklist & Done Criteria

- **계약 정합성:** API 응답 및 MQ 메시지의 스키마가 SPEC(SOT)에 정의된 구조와 일치하며, `payload_version`은 `v1`을 사용한다.
- **테넌시:** 모든 DB 조회·변경 및 큐 발행 시 `user_id` 기반 필터가 적용되어 있다.
- **네트워크 복원력:** GCS 및 MQ 호출에 타임아웃이 설정되어 있고, 인라인 재시도와 Reconciler 폴백이 동작한다.
- **데이터 일관성:** Local File 업로드는 Signed URL 조건과 `/complete` 시점에서 이중으로 크기를 검증한다. Reconciler 재발행으로 중복 메시지가 발생할 수 있으나, Worker가 멱등하게 처리한다고 가정한다.
- **DoD:** SPEC 5.1/5.2에 정의된 모든 시나리오 테스트를 통과하고, CI가 녹색이며, 핵심 메트릭이 정상적으로 노출된다.

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
  - Reconciler: 장애 시 환경 변수 플래그(env flag)를 통해 스케줄러를 즉시 비활성화한다.

---

## Assumptions (확정된 사항)

- Reconciler는 FastAPI 내부 Background Task(apscheduler 등)로 주기 실행한다.
- DB 마이그레이션 도구는 Alembic을 사용한다.
- Postgres 통합 테스트는 Testcontainers 또는 로컬 Docker 기반으로 수행한다.
- Worker의 멱등성은 system-design SOT를 준수하여, 중복 메시지를 안전하게 처리한다고 가정한다.
