# [ComponentName] PLAN

**Meta**
- **Component ID:** (예: core-api-server / pipeline-worker / search-service)
- **Target SPEC:** `./spec.md` (이 PLAN이 구현할 스펙 문서의 경로)
- **SOT:** `docs/system-design.md`, `(Target SPEC 경로)`, `(필요 시 관련 ADR / 관련 Tech Spec)`

---

> 이 PLAN은 특정 구현자나 특정 에이전트 도구에 종속되지 않는 실행 계획 문서다.
> 목표는 구현자가 `SPEC + PLAN`만 읽고도 작업 순서, 검증 기준, 통합 경계를 오해 없이 이해할 수 있게 만드는 것이다.

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

### 1.4 구현 전제 및 열려 있는 결정사항 (Preconditions & Open Decisions)

- **구현 전제:** (예: Target SPEC의 상태 전이 / 메시지 계약 / DDL이 확정되어 있음)
- **선행 필요 사항:** (예: 공용 인증 모듈 선구현 필요, 공용 라이브러리 버전 확정 필요)
- **열려 있는 결정사항:** 구현을 막는 미확정 항목이 있다면 `Open Decision`, `Blocker`, `Pending Decision` 같은 일반적인 표기로 명시한다.

### 1.5 핵심 의존성 패키지

| 패키지 | 용도 | 최소 버전 |
| --- | --- | --- |
| `(패키지명)` | (용도 설명) | (x.x+) |
| ... | ... | ... |

---

## 2. Implementation Phasing Strategy

- **Phase 1:** (스캐폴딩 및 인터페이스) 앱 팩토리/엔트리포인트, 설정 로딩, DTO/스키마, 라우터/컨슈머 스켈레톤, 공통 미들웨어.
- **Phase 2:** (영속성 및 핵심 유스케이스) Repository/트랜잭션 경계, 상태 전이, 외부 의존성 어댑터, 멱등성/복구 로직.
- **Phase 3:** (통합 및 운영) 관측성(로그·메트릭), 통합 테스트, 마이그레이션/배포 검증, 성능 스모크.
- **병합 게이트:** 각 단계 또는 자율적으로 나눈 PR 단위로 CI(단위·통합 테스트) 통과 + 1인 이상 리뷰.

### 2.1 작업 분해 원칙 (Task Decomposition Rules)

- 각 Task는 하나의 명확한 출력물과 하나의 검증 가능한 완료 조건을 가져야 한다.
- 병렬 작업이 가능하도록 Task 간 파일 수정 범위와 계약 경계를 최대한 분리한다.
- 선행 작업이 있는 경우 `Depends On`으로 명시하고, 독립 수행 가능하면 `병렬 가능`으로 표시한다.
- 각 Phase는 중간 병합 가능한 상태여야 하며, 부분 구현만 남은 채 다음 Phase로 넘어가지 않는다.
- 구현 순서는 레이어 나열보다 “기능 슬라이스가 검증 가능한 순서”를 우선한다.

### 2.2 선행 경로 및 병렬 가능 범위 (Critical Path & Parallelism)

- **Critical Path:** 반드시 먼저 완료되어야 하는 작업 흐름을 bullet로 적는다.
- **Parallelizable Workstreams:** 계약이 고정된 뒤 병렬로 진행 가능한 작업 묶음을 적는다.
- **Merge Owner / Integration Point:** 병렬 작업 결과를 어디서 통합하고 어떤 검증을 거칠지 적는다.

---

## 3. Work Breakdown Structure (WBS)

> 구현자가 그대로 실행할 수 있는 구체 작업 지시서.
> 모든 작업은 반드시 `Output / Files / Test Files / Commands / Verify / Linked AC / Depends On`를 포함해야 한다.
> 병렬화 가능한 작업은 `병렬 가능: Y` 또는 `병렬 가능: N`으로 표시한다.

### Phase 1: Skeleton & Contracts

- [ ] **Task 0: 프로젝트 엔트리포인트 및 설정 스캐폴딩**
  - **Output:** 앱 엔트리포인트, 설정 로딩, 기본 디렉토리 구조, 공통 의존성 주입 뼈대
  - **Files:** `src/main.py`, `src/core/config.py`, `.env.example`
  - **Test Files:** `(필요 시) tests/unit/test_config.py`
  - **Commands:** `pytest tests/unit/test_config.py` / `(프로젝트 실행 확인 명령)`
  - **Verify:** 필수 환경 변수 누락 시 실패하고, 정상 설정으로 앱이 기동한다.
  - **Linked AC:** SPEC §(관련 계약 섹션), DoD §5.1의 공통 전제
  - **Depends On:** 없음
  - **병렬 가능:** N

- [ ] **Task 1: DTO/Validation 및 인터페이스 스켈레톤**
  - **Output:** 요청/응답 스키마, 기본 라우터/컨슈머 시그니처, 유효성 검증 규칙
  - **Files:** `src/schemas/...`, `src/api/...` 또는 `src/adapters/...`
  - **Test Files:** `tests/unit/test_*.py` (유효성 검사 및 기본 예외 흐름)
  - **Commands:** `pytest tests/unit/test_*.py`
  - **Verify:** 잘못된 입력이 SPEC의 Error Contract에 맞는 에러로 매핑된다.
  - **Linked AC:** SPEC §2.1, §2.4, DoD §5.1 해당 시나리오
  - **Depends On:** Task 0
  - **병렬 가능:** Y

### Phase 2: Core Logic & Persistence

- [ ] **Task 2: 저장소/트랜잭션 경계 구현**
  - **Output:** SOT 저장소 접근 로직, 트랜잭션 경계, 조회/수정 필터, 필요한 인덱스 사용 경로
  - **Files:** `src/infra/db/...`, `src/models/...`, `alembic/...`
  - **Test Files:** `tests/integration/test_*repository.py`
  - **Commands:** `alembic upgrade head`, `pytest tests/integration/test_*repository.py`
  - **Verify:** 정상 저장/조회/롤백 및 테넌시 필터가 통합 테스트로 재현된다.
  - **Linked AC:** SPEC §2.2, §2.5, DoD §5.1 해당 시나리오
  - **Depends On:** Task 0
  - **병렬 가능:** Y

- [ ] **Task 3: 핵심 유스케이스 및 상태 전이 구현**
  - **Output:** 핵심 비즈니스 로직, 상태 전이 가드, 멱등성/재시도/복구 규칙
  - **Files:** `src/services/...` 또는 `src/usecases/...`
  - **Test Files:** `tests/unit/test_*service.py`
  - **Commands:** `pytest tests/unit/test_*service.py`
  - **Verify:** 동일 입력 재시도 시 허용된 부수 효과만 발생하고, 상태 전이가 SPEC과 일치한다.
  - **Linked AC:** SPEC §3.2, §3.3, DoD §5.1 해당 시나리오
  - **Depends On:** Task 1, Task 2
  - **병렬 가능:** N

- [ ] **Task 4: 외부 의존성 어댑터 및 인프라 연동**
  - **Output:** Storage/Broker/외부 API 클라이언트 인터페이스 및 운영/테스트 구현체
  - **Files:** `src/infra/storage/...`, `src/infra/broker/...`, `src/infra/external/...`
  - **Test Files:** `tests/unit/test_*client.py`
  - **Commands:** `pytest tests/unit/test_*client.py`
  - **Verify:** 메시지/요청 페이로드, 타임아웃, 재시도 정책이 계약과 일치한다.
  - **Linked AC:** SPEC §2.1, §2.3, §2.4, DoD §5.1 해당 시나리오
  - **Depends On:** Task 1
  - **병렬 가능:** Y

### Phase 3: Integration & Ops

- [ ] **Task 5: 라우터/컨슈머와 서비스 통합**
  - **Output:** 인터페이스 계층과 서비스/저장소/어댑터가 실제 흐름으로 연결된 실행 가능한 기능
  - **Files:** `src/api/...`, `src/services/...`, `src/infra/...`
  - **Test Files:** `tests/api/test_*.py` 또는 `tests/integration/test_*flow.py`
  - **Commands:** `pytest tests/api/test_*.py` 또는 `pytest tests/integration/test_*flow.py`
  - **Verify:** 주요 정상/예외 흐름이 엔드투엔드 또는 통합 테스트로 통과한다.
  - **Linked AC:** DoD §5.1의 대상 시나리오 묶음
  - **Depends On:** Task 2, Task 3, Task 4
  - **병렬 가능:** N

- [ ] **Task 6: 관측성 및 전역 예외 처리 적용**
  - **Output:** 구조화 로깅(`trace_id` 포함), 핵심 메트릭, 공통 에러 응답/예외 매핑
  - **Files:** `src/common/...`, `src/middlewares/...`, `src/observability/...`
  - **Test Files:** `tests/unit/test_error_handler.py`, `(필요 시) tests/unit/test_metrics.py`
  - **Commands:** `pytest tests/unit/test_error_handler.py`
  - **Verify:** 성공/실패 경로 모두에서 추적 필드와 에러 응답 계약이 일관된다.
  - **Linked AC:** SPEC §4, SPEC §2.4, DoD §5.2
  - **Depends On:** Task 1, Task 3
  - **병렬 가능:** Y

- [ ] **Task 7: 최종 검증 및 릴리스 준비**
  - **Output:** 통합 체크리스트 완료, 배포/롤백 절차 검증, 커버리지 및 CI 기준 충족
  - **Files:** `.github/workflows/...`, `docs/...` 또는 배포 설정 파일
  - **Test Files:** 전체 테스트 스위트
  - **Commands:** `(lint 명령)`, `(type check 명령)`, `pytest`, `pytest --cov`, `alembic upgrade head`
  - **Verify:** 완료 조건과 병합 게이트를 모두 만족한다.
  - **Linked AC:** DoD §5.1, §5.2, §5.3
  - **Depends On:** Task 5, Task 6
  - **병렬 가능:** N

---

## 4. Integration Checklist & Done Criteria

> 컴포넌트 통합 및 최종 완료 판정을 위한 체크리스트

### 4.1 통합 체크리스트 (Integration Checklist)
- [ ] **SPEC 추적성:** 모든 구현 대상 엔드포인트/메시지/상태 전이가 SPEC의 명시된 계약과 연결되어 있는가?
- [ ] **계약 정합성:** API/MQ/스토리지/스키마 필드가 관련 컴포넌트 문서의 기대 버전과 일치하는가?
- [ ] **테넌시 및 권한:** 데이터 조회/조작 시 `user_id` 또는 동등한 권한 격리 규칙이 모든 레이어에 적용되었는가?
- [ ] **네트워크 복원력:** 외부 연동 포인트에 적절한 Timeout, Retry, Circuit Breaker 또는 대체 정책이 적용되었는가?
- [ ] **데이터 일관성:** SOT와 파생 데이터 간 부분 실패 시 보정/정리 로직이 동작하는가?
- [ ] **마이그레이션/초기화 재현성:** 새로운 환경에서 스키마/설정/필수 리소스를 재현 가능한가?
- [ ] **공유 계약 검토:** 관련 Tech Spec 문서를 확인했고, 남은 충돌이 없는가?

### 4.2 완료 조건 (Definition of Done)
- [ ] SPEC §5.1에 정의된 시나리오 테스트가 모두 녹색이다.
- [ ] 단위·통합 테스트 합산 커버리지가 (목표 %) 이상이다 (`pytest-cov` 기준).
- [ ] 단위·통합 테스트가 CI에서 통과한다.
- [ ] 핵심 메트릭이 정상 노출된다.

---

## 5. Rollout & Rollback Plan

> 배포 및 장애 발생 시 데이터/스키마 원복(되돌리기) 계획

### 5.1 배포 계획 (Rollout)
- **환경 변수 추가:** (필수 환경 변수 목록과 기본 검증 방식)
- **인프라/스키마 변경:** 배포 전 필요한 마이그레이션/리소스 생성 절차
- **호환성 확인:** 기존 메시지/스키마/클라이언트와의 호환성 영향 여부

### 5.2 롤백 계획 (Rollback)
- **애플리케이션 롤백:** 이전 버전 이미지/아티팩트로 즉시 복귀하는 절차
- **데이터베이스 스키마 원복:** 역방향 마이그레이션 또는 안전한 복원 전략
- **메시지/비동기 호환성:** 큐/토픽에 남아 있는 신규 포맷 메시지 처리 방안
- **부분 적용 복구:** 배포 도중 일부 단계만 반영된 경우의 복구 순서

---

## Assumptions (확정된 사항)

- (확정된 기술 선택, 도구, 외부 의존성 전제 조건을 기술한다)
- *예: DB 마이그레이션 도구는 Alembic을 사용한다.*
- *예: 통합 테스트는 Testcontainers 기반으로 수행한다.*
- *예: Worker의 멱등성은 system-design SOT를 준수하여, 중복 메시지를 안전하게 처리한다고 가정한다.*
