# [ComponentName] PLAN

**Meta**
- **Component ID:** (예: core-api / pipeline-worker / search-service)
- **Target SPEC:** `./spec.md` (이 PLAN이 구현할 스펙 문서의 경로)

---

## 1. Goals & Strategy

### 1.1 달성 목표 (Goals)

- **(핵심 기능 A):** (E2E 흐름 완성 목표 — 정상/예외 경로 포함하여 명시)
- **(핵심 기능 B):** (추가 주요 흐름이 있으면 bullet 추가)
- **관측성:** 모든 요청·발행 로그에 `trace_id`를 포함하고, 핵심 메트릭을 노출한다.
- **테스트:** (통합 테스트 환경 — 예: Postgres/Testcontainers)을 기반으로 SPEC §5.1에 정의된 시나리오를 모두 충족하고, 단위·통합 테스트 합산 커버리지 (목표 %) 이상을 달성한다.

### 1.2 제외 대상 (Non-Goals)
- (이번 PLAN에서 의도적으로 배제하는 작업)
  - *예: 실패 시 DLQ(Dead Letter Queue) 재처리 로직은 다음 페이즈에서 진행함.*

### 1.3 리스크 및 대응 방안 (Risk & Mitigation)
- **위험 요소:** (예: 외부 STT API 응답 지연으로 인한 워커 스레드 고갈)
- **대응 방안:** (예: HTTP Timeout을 30초로 강제하고, 서킷 브레이커 패턴 적용)

### 1.4 핵심 의존성 패키지

| 패키지 | 용도 | 최소 버전 |
| --- | --- | --- |
| `(패키지명)` | (용도 설명) | (x.x+) |
| ... | ... | ... |

---

## 2. Implementation Phasing Strategy

- **Phase 1:** (스캐폴딩 및 인터페이스) DTO/스키마 정의, Mock 기반 API 라우터/컨슈머 스켈레톤, 공통 미들웨어.
- **Phase 2:** (비즈니스 로직) 핵심 유스케이스 구현, DB/MQ 연동, 상태 전이 및 복구 로직.
- **Phase 3:** (통합 및 운영) 관측성(로그·메트릭), 통합 테스트, 성능 스모크.
- **병합 게이트:** 각 단계 또는 자율적으로 나눈 PR 단위로 CI(단위·통합 테스트) 통과 + 1인 이상 리뷰.

---

## 3. Work Breakdown Structure (WBS)

> 개발자 및 AI 에이전트가 수행할 구체적인 작업 지시서
> 모든 작업은 반드시 `Output / Files / Test Files / Verify / Linked AC`를 포함해야 한다.

### Phase 1: Skeleton & Contracts

- [ ] **Task 1: 프로젝트 스캐폴딩 및 DTO/Validation 정의**
  - **Output:** 기본 디렉토리 구조 생성 및 API Request/Response 스키마 유효성 검사 로직
  - **Files:** `src/contracts/video.dto.ts`, `src/routes/video.router.ts`
  - **Test Files:** `tests/unit/test_video_dto.py` (유효성 검사 엣지 케이스)
  - **Verify:** 로컬 빌드 성공 및 잘못된 페이로드 입력 시 400 에러 반환(단위 테스트)
  - **Linked AC:** SPEC DoD 5.1 (예외 흐름)

### Phase 2: Core Logic & Persistence

- [ ] **Task 2: 비즈니스 로직 및 상태 전이 구현**
  - **Output:** 핵심 유스케이스 구현 및 상태 전이 가드 로직
  - **Files:** `src/usecases/upload_video.ts`, `src/domain/video_state.ts`
  - **Test Files:** `tests/unit/test_upload_video.py` (멱등성 단위 테스트)
  - **Verify:** 동일 ID 요청 시 부수 효과가 1번만 발생하는지 멱등성 검증 단위 테스트
  - **Linked AC:** SPEC DoD 5.1 (멱등성 및 보정 검증)

- [ ] **Task 3: Repository (DB) 연동 및 트랜잭션 경계 설정**
  - **Output:** 트랜잭션이 적용된 SOT(Metadata DB) 쿼리 로직 구현
  - **Files:** `src/infra/db/video.repo.ts`
  - **Test Files:** `tests/integration/test_video_repo.py` (Testcontainers; 정상 Insert 및 Rollback 확인)
  - **Verify:** 정상 데이터 Insert 성공 및 DB 예외 발생 시 Rollback 여부 검증 (통합 테스트)
  - **Linked AC:** SPEC DoD 5.1 (정상 흐름)

### Phase 3: Integration & Ops

- [ ] **Task 4: 비동기 메시지 발행 (Message Broker 연동)**
  - **Output:** 상태 변경 후 즉시 큐에 이벤트를 발행하는 어댑터 로직
  - **Files:** `src/infra/mq/producer.ts`
  - **Test Files:** `tests/unit/test_mq_producer.py` (AsyncMock으로 페이로드 스키마 검증)
  - **Verify:** 지정된 토픽/큐에 정확한 메시지 페이로드 스키마가 도달하는지 검증
  - **Linked AC:** SPEC DoD 5.1 (정상 흐름)

- [ ] **Task 5: 관측성 및 전역 예외 처리 적용**
  - **Output:** 구조화된 로깅(`trace_id` 포함) 및 공통 에러 응답 미들웨어
  - **Files:** `src/common/logger.ts`, `src/middlewares/error_handler.ts`
  - **Test Files:** (로그 출력은 샘플 요청 후 수동 확인)
  - **Verify:** 에러 발생 시 로그와 API 응답에 동일한 `trace_id`가 노출되는지 확인
  - **Linked AC:** SPEC DoD 5.2 (산출물 - 에러 로그 확인)

---

## 4. Integration Checklist & Done Criteria

> 컴포넌트 통합 및 최종 완료 판정을 위한 체크리스트

### 4.1 통합 체크리스트 (Integration Checklist)
- [ ] **계약 정합성:** API/MQ 페이로드 스키마가 타 컴포넌트(상/하위)의 기대 버전과 일치하는가?
- [ ] **테넌시 (Tenancy):** 데이터 조회/조작 시 `user_id` 기반 권한 격리 필터가 모든 레이어에 적용되었는가?
- [ ] **네트워크 복원력:** 외부 연동 포인트에 적절한 Timeout 설정과 Retry/DLQ 정책이 적용되었는가?
- [ ] **데이터 일관성:** SOT(DB)와 파생 데이터(Vector/Storage) 간의 부분 실패 시 보정/정리 로직이 동작하는가?

### 4.2 완료 조건 (Definition of Done)
- [ ] SPEC §5.1에 정의된 시나리오 테스트가 모두 녹색이다.
- [ ] 단위·통합 테스트 합산 커버리지가 (목표 %) 이상이다 (`pytest-cov` 기준).
- [ ] 단위·통합 테스트가 CI에서 통과한다.
- [ ] 핵심 메트릭이 정상 노출된다.

---

## 5. Rollout & Rollback Plan

> 배포 및 장애 발생 시 데이터/스키마 원복(되돌리기) 계획

### 5.1 배포 계획 (Rollout)
- **환경 변수 추가:** `DB_URL`, `MQ_CONNECTION_STRING`
- **인프라/스키마 변경:** 배포 전 `migrations/001_init_video_table.sql` 스크립트 실행 필수 (정방향 마이그레이션)

### 5.2 롤백 계획 (Rollback)
- **애플리케이션 롤백:** 이전 버전 이미지로 즉시 롤백 (컨테이너 오케스트레이션 활용)
- **데이터베이스 스키마 원복:** `migrations/001_init_video_table_down.sql` (역방향 마이그레이션) 스크립트 준비 및 실행 계획
- **메시지 호환성:** 롤백 시 큐에 남아있는 신규 버전의 메시지(Poison Message) 처리 방안 (예: DLQ로 라우팅 후 폐기 또는 스크립트로 구버전 포맷 변환)

---

## Assumptions (확정된 사항)

- (확정된 기술 선택, 도구, 외부 의존성 전제 조건을 기술한다)
- *예: DB 마이그레이션 도구는 Alembic을 사용한다.*
- *예: 통합 테스트는 Testcontainers 기반으로 수행한다.*
- *예: Worker의 멱등성은 system-design SOT를 준수하여, 중복 메시지를 안전하게 처리한다고 가정한다.*