# Admin Ops Foundation PLAN

> `docs/Tech_Spec/feedback_loop_&_admin_ops` 아래 컴포넌트 구현을 시작하기 전에 공유 DB schema, 상태값, 메시지 계약, API 골격을 먼저 고정하기 위한 실행 문서.
> 이 문서는 각 컴포넌트의 전체 구현 계획이 아니라 후속 브랜치가 재정의하지 않아야 하는 foundation 작업만 다룬다.

**메타 정보**
- Component ID: `admin-ops-foundation`
- SOT: `docs/system-design.md`
- Target SPEC:
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Feedback_Ingestion_Pipeline_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Admin_Control_Plane_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/ML_Pipeline_Execution_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Model_Release_and_Reindex_Spec.md`
- 관련 문서:
  - `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Pipeline_Worker_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Managed_Embedding_Endpoint_Spec.md`
- Plan 상태: Draft

---

## 1. 구현 의도

### 1.1 전달 목표
- 후속 구현 브랜치들이 같은 Metadata DB schema와 상태값을 공유한다.
- Feedback, Admin, ML Pipeline, Model Release 구현이 같은 message contract를 사용한다.
- Core API, Search Service, Pipeline Worker가 새 shared contract를 읽거나 발행할 수 있는 최소 골격을 가진다.
- 실제 feedback ingestion, ML training, release/cutover, rollback 실행은 후속 브랜치에서 구현한다.

### 1.2 이번 구현의 범위

이번 plan에 포함:
- Core API Alembic migration 추가
  - `Project.search_serving_state`
  - `SearchResponseSnapshot`
  - `MLPipelineRun`
  - `ModelEvaluation`
  - `ModelRelease`
- Core API ORM/read-write model 추가 또는 확장
- Search Service와 Pipeline Worker의 read model 정렬
- video-processing envelope와 분리된 control-message schema 추가
- feedback event schema 추가
- Core API `feedbacks` / `admin` router wiring 골격 추가
- feedback/admin/model-release repository 및 service 골격 추가
- foundation contract를 검증하는 최소 자동화 테스트 추가

명시적 제외 / 후속 phase:
- `POST /api/v1/feedbacks`의 전체 validation/publish flow 완성
- Feedback Ingestion Pipeline consumer와 raw log Object Storage sink 구현
- 학습 dataset generation, training, evaluation 실행 구현
- candidate reindex, cutover, rollback restore 구현
- Managed Embedding Endpoint readiness 연동 구현
- Admin API action의 전체 비즈니스 로직 구현

### 1.3 전제조건과 blocker
- 이미 고정된 spec contract:
  - `SearchResponseSnapshot`은 Search Service가 생성하고 Metadata DB에 TTL 기반으로 저장한다.
  - Feedback raw event는 Object Storage append-only log가 최종 원본이다.
  - `TRAINING_REQUEST`와 `ROLLBACK_REQUEST`는 `video_id` 없는 control-message schema를 사용한다.
  - Search serving gate는 `Project.search_serving_state=SERVABLE`이고 프로젝트 내부 모든 영상이 `READY`인 프로젝트만 검색 가능 대상으로 본다.
  - `ModelRelease`는 active/previous/candidate/rollback snapshot serving 상태의 SOT다.
- 필요한 upstream work / dependency:
  - 기존 Core API migration chain `0003_pgvector_vector_index_entry` 이후 새 migration을 추가한다.
  - 각 서비스의 SQLAlchemy model은 동일 테이블 계약을 서로 다르게 해석하지 않아야 한다.
- 구현을 막는 open question:
  - `ModelRelease`의 최초 row bootstrap을 migration seed로 둘지, service startup에서 lazy initialize할지 후속 구현 전에 결정해야 한다.
  - `SearchResponseSnapshot` TTL cleanup을 DB job, application cleanup, 운영 task 중 어디에 둘지는 후속 Search Service 구현에서 확정한다.
  - shared Python package가 없는 현재 구조에서 enum/message schema 중복을 어디까지 허용할지 결정이 필요하다. foundation에서는 서비스별 최소 중복을 작게 유지하고 이름/값을 테스트로 고정한다.

### 1.4 구현 전략
- 전체 접근:
  - 먼저 DB와 message contract를 고정하고, 그 다음 서비스별 read/write model과 router/service 골격을 맞춘다.
- 핵심 기술 작업 단위:
  - Core API가 Metadata DB schema owner 역할을 하므로 Alembic migration은 Core API에 둔다.
  - Search Service와 Pipeline Worker는 같은 DB table을 읽는 모델을 추가하되, schema 생성 책임은 갖지 않는다.
  - `TRAINING_REQUEST` / `ROLLBACK_REQUEST`는 기존 `BrokerMessage` 또는 `MessageEnvelope`에 억지로 끼우지 않고 별도 control message 타입으로 분리한다.
- 리스크 감소 전략:
  - 후속 구현 전에 migration, enum 값, message payload shape, router wiring을 작은 테스트로 먼저 잠근다.
  - 실제 domain transition은 skeleton에서 수행하지 않는다.
- 병합 전략:
  - 단일 foundation PR로 merge한다.
  - 후속 PR은 feedback ingestion, ML lifecycle/release, admin control 순서로 foundation 위에서 분기한다.
- Spec 추적 기준:
  - `docs/system-design.md` §3.1, §3.6, §3.7, §3.8, §3.11, §3.12, §3.13
  - Feedback Ingestion Pipeline Spec §2.1-2.3
  - Admin Control Plane Spec §2.1-2.3
  - ML Pipeline Execution Spec §2.1-2.3
  - Model Release and Reindex Spec §2.1-2.3

---

## 2. Workstream과 순서

### 2.1 권장 순서

| 순서 | Workstream | 연결 SPEC | 지금 먼저 하는 이유 | 의존성 |
| --- | --- | --- | --- | --- |
| 1 | Shared DB schema | system-design §3, all feedback/admin/ML specs §2.2 | 후속 구현의 SOT를 먼저 고정한다 | 기존 Core API migration chain |
| 2 | Shared constants and message schemas | system-design §3.13, Admin §2.1, ML §2.1, Release §2.1 | consumer/producer가 같은 payload shape를 보게 한다 | Workstream 1 naming decisions |
| 3 | Service read/write models | Search / Pipeline / Core API related specs | 각 서비스가 새 schema를 같은 의미로 읽게 한다 | Workstream 1 |
| 4 | Core API router and service skeletons | Feedback §2.1, Admin §2.1 | 후속 브랜치의 endpoint ownership과 wiring을 고정한다 | Workstream 2-3 |
| 5 | Contract verification | all acceptance criteria anchors | foundation이 후속 브랜치의 계약으로 쓸 수 있음을 증명한다 | Workstream 1-4 |

### 2.2 Workstream 상세

#### Workstream: Shared DB Schema
- 목표:
  - Feedback/Admin/ML 운영에 필요한 Metadata DB 구조를 Core API migration으로 추가한다.
- 연결 SPEC:
  - system-design §3.1, §3.7, §3.8, §3.11, §3.12
  - Admin Control Plane Spec §2.2
  - ML Pipeline Execution Spec §2.2
  - Model Release and Reindex Spec §2.2
- 주요 변경:
  - `project.search_serving_state` column 추가 또는 기존 `Project` schema와 정렬
  - `search_response_snapshot` table 추가
  - `ml_pipeline_run` table 추가
  - `model_evaluation` table 추가
  - `model_release` table 추가
  - 상태값 check constraint와 조회용 index 추가
- 영향 가능성이 높은 파일 / 영역:
  - `services/core-api/alembic/versions/0004_admin_ops_foundation.py`
  - `services/core-api/src/models/video.py`
  - `services/core-api/src/models/admin_ops.py`
- 의존성 / 연동 지점:
  - Search Service read-only models
  - Pipeline Worker DB models
  - Admin repository query paths
- 완료 조건:
  - 기존 schema에서 upgrade가 가능하다.
  - downgrade가 foundation schema를 제거하거나 원복할 수 있다.
  - `Project.search_serving_state` 기본값은 `SERVABLE`이다.
- 검증:
  - Alembic upgrade test 또는 schema integration test
  - SQLAlchemy model metadata smoke test

#### Workstream: Shared Constants and Message Schemas
- 목표:
  - 서비스들이 같은 상태값과 message payload shape를 사용하도록 최소 계약을 고정한다.
- 연결 SPEC:
  - system-design §3.13
  - Feedback Ingestion Pipeline Spec §2.1
  - Admin Control Plane Spec §2.1
  - ML Pipeline Execution Spec §2.1
  - Model Release and Reindex Spec §2.1
- 주요 변경:
  - Project serving state 값 정의: `SERVABLE`, `ROLLBACK_EXCLUDED`
  - Release status 값 정의: `STABLE`, `CANDIDATE_REINDEXING`, `ROLLBACK_PREPARING`
  - ML run status 값 정의: `PENDING`, `RUNNING`, `READY_FOR_RELEASE`, `FAILED`, `SUPERSEDED`
  - Control message schema 추가: `TRAINING_REQUEST`, `ROLLBACK_REQUEST`
  - Feedback event schema 추가: `schema_version=1`
- 영향 가능성이 높은 파일 / 영역:
  - `services/core-api/src/infra/broker.py`
  - `services/core-api/src/schemas/feedback_dto.py`
  - `services/core-api/src/schemas/admin_ops.py`
  - `services/pipeline-worker/src/schemas/messages.py`
- 의존성 / 연동 지점:
  - PGMQ publish path
  - In-memory broker test double
  - Pipeline Worker consumer dispatch
- 완료 조건:
  - Video-processing message는 계속 `video_id`를 요구한다.
  - Control message는 `video_id` 없이 직렬화/검증된다.
  - Feedback event는 spec의 필수 문맥 필드를 표현한다.
- 검증:
  - broker/message unit tests
  - Pydantic validation tests

#### Workstream: Service Read and Write Models
- 목표:
  - Core API, Search Service, Pipeline Worker가 새 shared schema를 같은 의미로 읽고 쓸 수 있게 한다.
- 연결 SPEC:
  - Search Service serving gate: Model Release and Reindex Spec §2.2-2.3
  - Pipeline Worker dual-write/rollback hooks: Model Release and Reindex Spec §2.1-2.3
  - Admin state read: Admin Control Plane Spec §2.1-2.2
- 주요 변경:
  - Search Service `ProjectModel.search_serving_state` 추가
  - Search Service `SearchResponseSnapshot` model 또는 repository skeleton 추가
  - Pipeline Worker `VideoModel.project_id`와 release 관련 read model 정렬
  - Pipeline Worker `MLPipelineRun`, `ModelEvaluation`, `ModelRelease` model 추가
  - Core API repository에서 admin 조회용 projection을 읽을 수 있게 준비
- 영향 가능성이 높은 파일 / 영역:
  - `services/search-service/src/infra/db/models.py`
  - `services/search-service/src/infra/db/search_repository.py`
  - `services/pipeline-worker/src/infra/db/models.py`
  - `services/core-api/src/infra/db/admin_repository.py`
  - `services/core-api/src/infra/db/model_release_repository.py`
- 의존성 / 연동 지점:
  - Existing `video`, `chunk`, `vector_index_entry` model fields
  - Search result snapshot write timing
  - Pipeline Worker process/delete flows
- 완료 조건:
  - Search Service query가 `Project.search_serving_state=SERVABLE`과 프로젝트 내부 all-or-nothing readiness를 기준으로 gate를 적용할 수 있다.
  - Pipeline Worker can parse control messages without requiring `video_id`.
  - Admin repository skeleton can query `Video`, `MLPipelineRun`, `ModelRelease`.
- 검증:
  - Search repository integration test for project-level `ROLLBACK_EXCLUDED` exclusion
  - Pipeline Worker message schema unit test
  - Core API repository smoke test

#### Workstream: Core API Router and Service Skeletons
- 목표:
  - Feedback/Admin endpoint ownership과 DI wiring을 고정한다.
- 연결 SPEC:
  - Feedback Ingestion Pipeline Spec §2.1
  - Admin Control Plane Spec §2.1-2.3
- 주요 변경:
  - `feedbacks` router 추가
  - `admin` router 추가
  - `api/v1/router.py` include wiring 추가
  - `FeedbackService`, `AdminService` skeleton 추가
  - `FeedbackRepository`, `AdminRepository`, `ModelReleaseRepository` skeleton 추가
- 영향 가능성이 높은 파일 / 영역:
  - `services/core-api/src/api/v1/routers/feedbacks.py`
  - `services/core-api/src/api/v1/routers/admin.py`
  - `services/core-api/src/api/v1/router.py`
  - `services/core-api/src/services/feedback_service.py`
  - `services/core-api/src/services/admin_service.py`
- 의존성 / 연동 지점:
  - `get_current_user`
  - admin role dependency to be completed in admin branch
  - broker client for control message publish
- 완료 조건:
  - API router wiring imports cleanly.
  - Skeletons do not claim unimplemented business behavior.
  - FastAPI dependencies use `Annotated[..., Depends(...)]`.
- 검증:
  - Core API route import/unit smoke test
  - Existing Core API tests still pass

#### Workstream: Contract Verification
- 목표:
  - foundation이 후속 브랜치의 stable base로 쓸 수 있음을 자동화된 최소 증거로 남긴다.
- 연결 SPEC:
  - All target specs §4 acceptance criteria에서 foundation이 선행해야 하는 contract pieces
- 주요 변경:
  - Migration/schema smoke tests
  - Message schema tests
  - Search gate readiness test
  - Router wiring test
- 영향 가능성이 높은 파일 / 영역:
  - `services/core-api/tests/integration/test_admin_ops_foundation_schema.py`
  - `services/core-api/tests/unit/test_broker.py`
  - `services/pipeline-worker/tests/unit/test_message_schemas.py`
  - `services/search-service/tests/integration/test_search_repository.py`
- 의존성 / 연동 지점:
  - Existing service `.venv` / Poetry setup
  - Test DB support in existing test fixtures
- 완료 조건:
  - Foundation-specific tests pass.
  - Existing affected service tests pass or documented blocker is recorded.
- 검증:
  - Service-level pytest commands listed in §3.5

### 2.3 병렬화와 병합 지점
- 안전하게 병렬화 가능한 작업:
  - Message schema tests can proceed in parallel with DB migration after enum names are fixed.
  - Core API router skeleton can proceed after repository interface names are fixed.
- 공유 연동 지점 / 충돌 가능 영역:
  - `services/core-api/src/infra/broker.py`
  - `services/pipeline-worker/src/schemas/messages.py`
  - `services/search-service/src/infra/db/search_repository.py`
  - `services/core-api/src/api/v1/router.py`
- 최종 통합 checkpoint:
  - Run migration/schema tests first.
  - Then run message schema tests.
  - Then run service route/repository tests.
  - Finally review all changed Python files against `AGENTS.md` Sonar rules.

---

## 3. 검증 및 테스트 전략

### 3.1 리스크 기반 테스트 초점

| Spec ref | 리스크 / 비즈니스 규칙 | 중요한 이유 | 권장 test level | 계획된 증명 |
| --- | --- | --- | --- | --- |
| system-design §3.1, Release §2.2 | `Project.search_serving_state` default/check constraint drift | Rollback 중 검색 제외 계약이 깨진다 | Integration | 새 column 기본값과 허용값을 schema test로 확인 |
| system-design §3.13 | control message가 `video_id` 필수 envelope에 섞임 | ML/rollback 요청이 spec과 다른 payload가 된다 | Unit | `TRAINING_REQUEST` / `ROLLBACK_REQUEST` validation에 `video_id`가 없음을 확인 |
| Release §2.3 | Search Service가 `ROLLBACK_EXCLUDED` 프로젝트를 검색 가능하게 취급 | rollback 복구 중 문제 데이터가 노출된다 | Integration | search repository gate가 project-level serving state와 project readiness를 적용함을 확인 |
| Admin §2.2 | Admin read path가 필요한 SOT fields를 조회하지 못함 | 대시보드와 후속 admin action precondition이 불가능하다 | Unit/Integration | repository projection에 required nullable fields가 존재함을 확인 |
| ML §2.3 | `MLPipelineRun` 상태값 drift | 활성 실행/대기 실행 규칙 구현이 흔들린다 | Integration | status check constraint 또는 model enum test로 허용값 고정 |

### 3.2 계획된 자동화 테스트

| Spec ref / acceptance criterion | 시나리오 / 규칙 | Test level | 이 level을 쓰는 이유 | 관찰 가능한 증명 |
| --- | --- | --- | --- | --- |
| system-design §3.1 | new `Project` row has `search_serving_state=SERVABLE` | Integration | DB default와 ORM mapping이 모두 관여한다 | inserted row의 field 값 확인 |
| system-design §3.13 | control messages serialize without `video_id` | Unit | 순수 schema 계약이다 | payload keys가 정확히 일치 |
| Release §2.3 | Search gate excludes `ROLLBACK_EXCLUDED` project | Integration | repository SQL이 계약을 강제해야 한다 | excluded project의 chunk가 반환되지 않음 |
| Feedback §2.1-2.2 | feedback event schema preserves snapshot context fields | Unit | Pydantic schema shape 검증이면 충분하다 | required fields validation |
| Admin §2.1 | admin router is included under `/api/v1/admin` | Unit/API smoke | FastAPI wiring 회귀를 막는다 | route list 또는 test client smoke |

### 3.3 자동화 테스트로 다루지 않는 항목

| Spec ref / rule | 자동화하지 않는 이유 | 수동 / 운영 증명 |
| --- | --- | --- |
| FIP raw Object Storage sink | foundation 범위가 아니며 consumer 구현 브랜치 소유 | 후속 FIP PR에서 Vector/Object Storage 설정과 함께 검증 |
| ML training/evaluation execution | foundation 범위가 아니며 ML lifecycle 브랜치 소유 | 후속 ML PR에서 artifact와 run transition 테스트 |
| ModelRelease cutover/rollback restore | foundation은 schema/contract만 제공 | 후속 release/reindex PR에서 transition integration test |
| Managed Embedding readiness | foundation에서 endpoint 연동을 추가하지 않음 | 후속 release/reindex PR에서 readiness gate test |

### 3.4 테스트 환경과 double
- DB / storage / broker 설정:
  - Core API migration/schema test는 기존 DB fixture 또는 Alembic test pattern을 재사용한다.
  - Broker publish는 existing in-memory broker 또는 unit-level payload builder test로 제한한다.
  - Object Storage는 foundation 테스트에서 double을 쓰지 않는다.
- 외부 의존성 격리 방식:
  - Search Service integration test는 DB repository boundary까지만 검증한다.
  - Managed Embedding Endpoint, Vector Store external runtime은 foundation 검증에서 제외한다.
- Time / async / retry 제어 방식:
  - Message schema tests는 explicit `issued_at` 값을 사용한다.
  - Retry/backoff behavior는 후속 구현에서 검증한다.
- 필요한 fixture 또는 seed data:
  - `SERVABLE` project와 그 하위 `READY` video
  - `ROLLBACK_EXCLUDED` project와 그 하위 `READY` video
  - minimal chunk/vector rows
  - minimal `ModelRelease` row

### 3.5 검증 명령과 quality gate
- 필수 명령:
  - `cd services/core-api && poetry run pytest tests/unit tests/integration`
  - `cd services/search-service && poetry run pytest tests/unit tests/integration`
  - `cd services/pipeline-worker && poetry run pytest tests/unit`
- 병합 전 최소 meaningful check:
  - New migration upgrade path succeeds.
  - Message schema tests prove control messages do not require `video_id`.
  - Search repository test proves project-level `ROLLBACK_EXCLUDED` is not served.
  - Core API router import/wiring test passes.
- 첨부할 증거:
  - pytest output for affected services
  - migration upgrade proof if run separately
  - short note for any skipped command with blocker reason

---

## 4. 전달 리스크와 안전장치

| 리스크 | 영향 | 완화책 | 검증 |
| --- | --- | --- | --- |
| Schema 또는 enum drift across services | 후속 브랜치가 서로 다른 상태값을 구현한다 | DB check constraint와 schema tests로 값 고정 | Migration/schema tests |
| Control message가 video envelope와 섞임 | `TRAINING_REQUEST` / `ROLLBACK_REQUEST`가 consumer에서 파싱 실패한다 | 별도 control schema와 unit test 추가 | Message schema tests |
| Search gate가 rollback exclusion을 누락 | 복구 중 데이터가 사용자 검색에 노출된다 | repository SQL을 project-level `SERVABLE`과 project readiness 기준으로 변경 | Search repository integration test |
| Foundation에서 domain behavior를 과하게 구현 | 후속 브랜치 책임 경계가 흐려진다 | skeleton은 계약과 wiring만 제공하고 side effect는 최소화 | Code review against this plan |
| Shared Python package 부재로 중복 증가 | 서비스별 enum/message 값이 나중에 갈라진다 | 중복은 작게 유지하고 테스트로 값 고정, 필요 시 후속 shared package ADR | Cross-service schema tests / review |
| Migration rollback ambiguity | 배포 중 partial failure 복구가 어려워진다 | downgrade 또는 safe-forward 절차를 migration에 명확히 둔다 | Alembic downgrade smoke where feasible |

---

## 5. Rollout and Rollback

### 5.1 Rollout 계획
- Migration / schema 단계:
  - Core API Alembic migration을 배포한다.
  - `Project.search_serving_state`는 기존 row에 `SERVABLE` 기본값을 부여한다.
  - 신규 tables는 비어 있는 상태로 배포 가능해야 한다.
- Config / secret / infra 변경:
  - foundation 단계에서는 새 secret을 추가하지 않는다.
  - 새 queue 이름이 필요하면 config placeholder만 추가하고 실제 consumer rollout은 후속 브랜치에서 수행한다.
- Backward / forward compatibility 고려사항:
  - 기존 video upload/search/delete flow는 새 column 추가 후에도 기존 behavior를 유지해야 한다.
  - `Project.search_serving_state` default가 있으므로 기존 project write path는 즉시 수정되지 않아도 row 생성이 가능해야 한다.
  - Control message schema 추가는 기존 `PREPROCESS_REQUEST` / `DELETE_REQUEST` payload를 변경하지 않는다.
- Rollout 중 볼 monitoring signal:
  - Core API DB migration failure
  - Search Service query failure
  - Pipeline Worker message validation failure
  - unexpected `ROLLBACK_EXCLUDED` row count greater than zero before rollback implementation
- 배포 후 점검:
  - Existing video creation still succeeds.
  - Existing search flow still returns expected results.
  - Existing pipeline worker video messages still parse.

### 5.2 Rollback 계획
- App rollback:
  - App code can roll back while additive DB columns/tables remain unused.
- Data rollback 또는 safe-forward plan:
  - Prefer safe-forward for additive schema if no bad data was written.
  - If rollback is required before use, downgrade can drop foundation-only tables and remove `Project.search_serving_state`.
- Async / message compatibility fallback:
  - Existing video-processing messages are unchanged.
  - If control-message publish skeleton causes issues, disable new admin/feedback routes or leave them uncalled until fixed.
- Partial deployment recovery:
  - If one service deploys before another, additive schema and default values should keep existing flows working.
  - If Search Service is not yet deployed with project-level `SERVABLE` filter, no `ROLLBACK_EXCLUDED` writes should occur before release/rollback implementation.

---

## 6. 완료 체크리스트

- [ ] Core API migration adds all foundation tables/columns and constraints.
- [ ] Core API ORM models match migration names and nullable/default rules.
- [ ] Search Service read model includes `Project.search_serving_state`.
- [ ] Pipeline Worker DB model includes `Video.project_id`, `MLPipelineRun`, `ModelEvaluation`, `ModelRelease`.
- [ ] Video-processing messages still require `video_id`.
- [ ] `TRAINING_REQUEST` and `ROLLBACK_REQUEST` use control-message schema without `video_id`.
- [ ] Feedback event schema preserves the required snapshot context fields.
- [ ] Core API includes `feedbacks` and `admin` router skeletons.
- [ ] Repository/service skeletons do not implement full downstream behavior prematurely.
- [ ] Foundation-specific tests are added and pass.
- [ ] Affected existing tests pass or blockers are documented.
- [ ] Remaining open questions are recorded before moving to implementation branches.

