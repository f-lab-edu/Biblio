# [Media & AI Pipeline Worker] PLAN

**메타 정보**
- Component ID: `pipeline-worker`
- SOT: `docs/system-design.md`
- Target SPEC: `docs/Tech_Spec/upload_search_Service/Pipeline_Worker_Spec.md`
- 관련 문서:
  - `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Managed_Embedding_Endpoint_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Model_Release_and_Reindex_Spec.md`
  - `docs/ADR/ADR-003-chunking-strategy.md`
  - `docs/ADR/ADR-004-video-search-retrieval-strategy.md`
- Plan 상태: Draft

---

## 1. 구현 의도

### 1.1 전달 목표
- 이 plan이 끝났을 때 실제로 동작해야 하는 것:
  - Core API가 발행한 project 하위 video-processing message를 Worker가 소비한다.
  - Worker가 `Video.project_id`를 읽고 transcript/chunk/vector artifacts를 생성한다.
  - Vector projection이 Search Service의 `user_id + project_id` FTS/ANN/SOT scope에 필요한 metadata를 가진다.
  - 삭제, 중복 수신, 실패 재시도가 검색 노출 정합성을 깨지 않는다.
- 검증 가능한 형태로 입증되어야 하는 것:
  - `PREPROCESS_REQUEST` 성공 후 `VectorIndexEntry.project_id`가 채워진다.
  - `DELETE_REQUEST` 후 해당 video artifacts가 검색 후보로 남지 않는다.
  - Worker가 `Project.search_serving_state`를 변경하지 않는다.

### 1.2 이번 구현의 범위
- 이번 plan에 포함:
  - message envelope parsing and dispatch
  - `Video` lookup with `project_id` and processing context restoration
  - media/STT/chunk/vision/embedding pipeline integration
  - artifact persistence and vector projection metadata
  - delete cascade and duplicate-safe ack behavior
  - ModelRelease active target index handling for online ingest
  - failure state, retry/resume, observability, tests
- 명시적 제외 / 후속 phase:
  - project CRUD and project 하위 upload API implementation
  - Search Service project readiness gate and retrieval implementation
  - admin rollback request API and rollback project exclusion state transition
  - full immediate reindex orchestration and activity-based priority reindexing
  - managed model training/evaluation pipeline

### 1.3 전제조건과 blocker
- 이미 고정된 spec contract:
  - video-processing message는 `video_id`를 담고, Worker는 Metadata DB에서 context를 복원한다.
  - 검색 가능한 산출물에는 `Video.project_id`가 필요하다.
  - project readiness는 선택된 project 내부 all-or-nothing이며 Search Service가 평가한다.
  - rollback exclusion은 project serving scope로 표현되며 Worker가 변경하지 않는다.
- 필요한 upstream work / dependency:
  - Core API는 message 발행 전에 올바른 membership을 가진 `Project`와 `Video`를 생성해야 한다.
  - Metadata schema는 `Project`, `Video.project_id`, `Chunk`, `VectorIndexEntry.project_id`, `ModelRelease`를 지원해야 한다.
  - Managed Embedding Endpoint는 현재 active model version을 처리할 수 있어야 한다.
- 구현을 막는 open question:
  - 현재 계획을 막는 blocker는 없다.
  - admin-ops 문서 중 video-level rollback exclusion 표현이 남아 있으면 project-level exclusion으로 별도 정합성 패치가 필요하다.

### 1.4 구현 전략
- 전체 접근:
  - 먼저 message/DB/artifact contracts를 닫고, 그 다음 processing pipeline과 deletion flow를 붙인다.
- 핵심 기술 작업 단위:
  - `Video` load path에서 `project_id`를 필수 processing context로 승격한다.
  - final persistence boundary에서 `Chunk`, `VectorIndexEntry`, `Video.status`를 함께 검증한다.
  - active target selection은 `ModelRelease` read model로 캡슐화한다.
- 리스크 감소 전략:
  - 최고 위험 invariant인 "vector metadata에는 project scope가 포함되어야 한다"를 integration test로 먼저 고정한다.
  - Search Service와 맞물리는 readiness는 Worker unit test가 아니라 cross-component contract test로 검증한다.
- 병합 전략:
  - phased PR을 선호한다: contracts/schema, processing pipeline, delete/resume, release-readiness tests.
- Spec 추적 기준:
  - SPEC 2.1 message contract
  - SPEC 2.2 data contract
  - SPEC 2.3 state and business rules
  - SPEC 4.1 acceptance criteria

---

## 2. Workstream과 순서

### 2.1 권장 순서
| 순서 | Workstream | 연결 SPEC | 지금 먼저 하는 이유 | 의존성 |
| --- | --- | --- | --- | --- |
| 1 | Contracts and persistence context | 2.1, 2.2, 2.3 | pipeline logic 전에 project scope metadata가 안정적이어야 함 | Core API schema decisions |
| 2 | Processing and vector materialization | 2.2, 2.4, 4.1 | 성공한 ingest는 검색의 핵심 user-visible 의존성임 | Workstream 1 |
| 3 | Deletion, idempotency, and recovery | 2.3, 2.5, 4.1 | async duplicate/failure path가 검색 정합성을 보호함 | Workstream 1, partial Workstream 2 |
| 4 | Release context and observability | 2.1, 3, 4.1 | candidate/rollback operation은 operator-visible proof가 필요함 | Workstreams 1-3 |

### 2.2 Workstream 상세

#### Workstream: Contracts and persistence context
- 목표:
  - `video_id -> Video -> project/user/storage/model context` 복원을 명시적이고 테스트 가능하게 만든다.
- 연결 SPEC:
  - 2.1 message contract, 2.2 data contract, 2.3 metadata invariants
- 주요 변경:
  - `PREPROCESS_REQUEST`와 `DELETE_REQUEST`의 shared message envelope parsing을 구현한다.
  - `Video.project_id`, `user_id`, status, source/storage field, related artifact를 읽는 repository method를 추가한다.
  - `VectorIndexEntry`가 `project_id`를 저장하도록 schema/migration을 추가하거나 갱신한다.
  - `Video.project_id`가 없으면 preprocessing을 거부하거나 실패 처리한다.
- 영향 가능성이 높은 파일 / 영역:
  - `services/pipeline-worker`
  - shared DB models or migrations under the existing metadata DB owner
  - test fixtures for `Project`, `Video`, `Chunk`, `VectorIndexEntry`
- 의존성 / 연동 지점:
  - Core API project/video schema
  - Search Service vector filtering expectations
- 완료 조건:
  - Worker repository가 project-scoped video를 읽고 같은 `project_id`로 vector metadata를 저장할 수 있다.
- 검증:
  - DB integration test가 생성된 모든 `VectorIndexEntry`에 예상 `user_id`, `project_id`, `video_id`, `chunk_id`가 있음을 검증한다.

#### Workstream: Processing and vector materialization
- 목표:
  - Deliver the normal `PREPROCESS_REQUEST` path from source media to `Video.status=READY`.
- 연결 SPEC:
  - 2.2 owned data, 2.4 operating constraints, 4.1 preprocess acceptance
- 주요 변경:
  - local storage object와 external URL input에 대한 media acquisition을 구현한다.
  - FFmpeg, STT, chunking, keyframe/vision fallback, embedding adapter를 연결한다.
  - transcript/chunk/vector artifact를 transactionally coherent boundary 안에서 저장한다.
  - citation용 canonical chunk text를 보존하면서 caller-side에서 embedding input을 정규화한다.
- 영향 가능성이 높은 파일 / 영역:
  - Worker application service/usecase layer
  - media, storage, STT, vision, embedding adapters
  - artifact repository
- 의존성 / 연동 지점:
  - Object Storage
  - Managed Embedding Endpoint
  - Metadata DB artifact schema
- 완료 조건:
  - 유효하게 업로드된 video가 `READY`에 도달하고, Search Service가 scoped chunk/vector metadata를 읽을 수 있다.
- 검증:
  - Integration test with test doubles proves artifact creation, vector metadata, and final status transition.

#### Workstream: Deletion, idempotency, and recovery
- 목표:
  - Prevent duplicate async messages, partial failures, and deletes from leaking stale search artifacts.
- 연결 SPEC:
  - 2.3 state rules, 2.5 error contract, 3 cleanup requirement, 4.1 duplicate/delete/failure acceptance
- 주요 변경:
  - 처리 가능한 status에 대한 conditional processing claim을 구현한다.
  - target artifact가 이미 있으면 중복 `READY` message를 skip한다.
  - terminal failure를 `failed_stage`와 함께 기록하고 안전하게 Ack한다.
  - pipeline stage 사이에서 `DELETE_REQUEST` cascade와 `DELETING` 감지를 구현한다.
  - delete 대상이 없으면 duplicate success로 처리한다.
- 영향 가능성이 높은 파일 / 영역:
  - process usecase
  - delete usecase
  - repository transaction helpers
  - consumer ack handling
- 의존성 / 연동 지점:
  - Core API delete/retry state transitions
  - Search Service SOT gate after delete
- 완료 조건:
  - Duplicate messages and delete races close without duplicate artifacts or stale searchable rows.
- 검증:
  - Unit tests cover branching.
  - DB integration tests prove delete cascade removes vector/chunk/transcript/asset/video rows.

#### Workstream: Release context and observability
- 목표:
  - online ingest가 현재 active model release state와 호환되고 rollback recovery 중에도 운영 가능하게 만든다.
- 연결 SPEC:
  - 2.1 external dependency contract, 3 observability, 4.1 candidate/rollback acceptance
- 주요 변경:
  - `ModelRelease`를 읽어 active target model/index를 선택한다.
  - candidate 준비 중에도 online ingest output은 기존 active target 한 곳에만 기록한다.
  - candidate index는 end-user search scope 밖에 두며, Search Service가 `ModelRelease`에서 serving path를 결정한다.
  - `trace_id`, `project_id`, `video_id`, model version, index name, stage를 포함하는 log와 metric을 추가한다.
- 영향 가능성이 높은 파일 / 영역:
  - release context repository
  - embedding/vector writer
  - logging/metrics setup
- 의존성 / 연동 지점:
  - Model Release and Reindex
  - Managed Embedding Endpoint readiness
  - Search Service active/previous serving path
  - Managed Embedding Endpoint request payload에는 target `model_version`이 포함된다.
- 완료 조건:
  - Online ingest가 stable 및 candidate-reindex state에 맞는 target projection을 쓰고 drift debug에 충분한 telemetry를 노출한다.
- 검증:
  - Integration 또는 contract test가 `ModelRelease=CANDIDATE_REINDEXING` seed를 만들고 두 target projection이 project metadata와 함께 생성되는지 검증한다.
  - Embedding client test는 request payload 안의 target `model_version`을 검증한다.

### 2.3 병렬화와 병합 지점
- 안전하게 병렬화 가능한 작업:
  - settings와 interface contract가 안정화된 뒤 Media/STT/Vision/Embedding adapter는 병렬 진행할 수 있다.
  - repository delete contract가 안정화된 뒤 delete usecase는 preprocess orchestration과 병렬 진행할 수 있다.
  - core log field가 정의되면 observability wiring을 진행할 수 있다.
- 공유 연동 지점 / 충돌 가능 영역:
  - Metadata DB models and migrations
  - artifact repository
  - consumer Ack/error handling
  - test fixtures shared with Core/Search specs
- 최종 통합 checkpoint:
  - Worker integration test와 Search Service project-scope test를 함께 실행해 vector metadata, readiness gate input, delete cleanup이 맞물리는지 증명한다.

---

## 3. 검증 및 테스트 전략

### 3.1 리스크 기반 테스트 초점
| Spec ref | 리스크 / 비즈니스 규칙 | 중요한 이유 | 권장 test level | 계획된 증명 |
| --- | --- | --- | --- | --- |
| SPEC 2.2, 2.3 | vector row에 `project_id` 누락 | Search가 project-scoped ANN을 안전하게 강제할 수 없음 | Integration | preprocess artifact 저장 후 vector metadata 검증 |
| SPEC 2.3 | Worker가 project serving state를 변경 | rollback exclusion ownership이 컴포넌트 사이에 분산됨 | Unit / integration | process/delete flow가 `Project.search_serving_state`를 변경하지 않음 |
| SPEC 2.5, 4.1 | partial artifact write가 stale search projection 생성 | 사용자가 삭제되었거나 유효하지 않은 chunk를 볼 수 있음 | Integration | transaction rollback과 delete cascade 후 vector/chunk orphan row가 없음 |
| SPEC 2.1, 4.1 | candidate 준비 중 online ingest가 candidate projection을 미리 기록 | 배포 전 모델이 신규 데이터를 받아 active 슬롯과 경합함 | Integration / contract | seeded `ModelRelease`가 active vector entry 하나만 생성 |

### 3.2 계획된 자동화 테스트
| Spec ref / acceptance criterion | 시나리오 / 규칙 | Test level | 이 level을 쓰는 이유 | 관찰 가능한 증명 |
| --- | --- | --- | --- | --- |
| AC 1 | preprocess project video가 scoped artifact 저장 | Integration | DB projection shape가 중요 | `VectorIndexEntry` row가 예상 `project_id`를 포함 |
| AC 2 | 성공한 processing이 `Video.status`만 갱신 | Integration | state ownership이 여러 table에 걸침 | `Video.status=READY`, project serving state는 변경 없음 |
| AC 3 | candidate 준비 중 active-only ingest | Integration / contract | release context와 vector writer가 필요 | active entry만 존재하고 candidate entry는 없음 |
| AC 4 | terminal failure가 failed stage 기록 | Unit + integration | branch logic과 persistence가 모두 중요 | `FAILED`, `failed_stage`, Ack outcome이 관찰 가능 |
| AC 5 | duplicate preprocess/delete safe close | Unit + integration | at-least-once delivery가 duplicate를 만들 수 있음 | 중복 artifact 없음; missing delete target Ack 성공 |
| AC 6 | delete가 searchable artifact 제거 | Integration | search leakage는 data-level risk | chunk/vector/transcript/asset/video row가 제거됨 |

### 3.3 자동화 테스트로 다루지 않는 항목
| Spec ref / rule | 자동화하지 않는 이유 | 수동 / 운영 증명 |
| --- | --- | --- |
| STT provider transcription quality | provider quality는 외부 요인이며 비결정적이다 | recorded/fake response를 쓰는 adapter contract test와 staging provider smoke |
| FFmpeg codec coverage across all supported formats | exhaustive media corpus는 무겁다 | 대표 fixture set과 staging media smoke |
| Object Storage orphan batch cleanup | 주 delete 완료 지점은 DB hard-delete이며 cleanup은 재시도 가능 | 운영 metric과 주기적 reconciliation report |

### 3.4 테스트 환경과 double
- DB / storage / broker 설정:
  - PostgreSQL test database with pgvector-compatible schema.
  - In-memory or fake broker for unit tests; PGMQ-backed integration where available.
  - In-memory storage for unit tests; local test bucket or fake GCS adapter for integration.
- 외부 의존성 격리 방식:
  - STT, Vision, Embedding은 normal/failure path에서 deterministic test double을 사용한다.
  - Managed Embedding contract test는 `model_version` request mapping을 별도로 검증한다.
- Time / async / retry 제어 방식:
  - inject clock/sleep/backoff controls to avoid slow retry tests.
  - force timeout branches with fake adapters instead of real sleeping.
- 필요한 fixture 또는 seed data:
  - user-owned `Project`
  - `PENDING`, `UPLOADED`, `PROCESSING`, `READY`, `FAILED`, `DELETING` 상태의 project 하위 `Video`
  - stable 및 candidate-reindex state의 `ModelRelease`
  - 대표 transcript/chunk/vector artifact row

### 3.5 검증 명령과 quality gate
- 필수 명령:
  - Worker test layout이 도입되면 관련 service에서 `poetry run pytest`
  - metadata DB owner service의 DB migration upgrade command
  - 병합 전 Worker + Search project scope에 대한 targeted contract test
- 병합 전 최소 meaningful check:
  - message schema tests
  - repository integration tests
  - preprocess happy path test
  - delete cascade test
  - duplicate message test
  - candidate 준비 중 active-only ingest test
- 첨부할 증거:
  - test command output
  - migration upgrade output
  - 성공한 preprocess와 실패한 preprocess의 structured log 예시

---

## 4. 전달 리스크와 안전장치

| 리스크 | 영향 | 완화책 | 검증 |
| --- | --- | --- | --- |
| Core, Worker, Search 사이 schema drift | project-scoped search가 누출되거나 false empty를 반환할 수 있음 | `project_id`를 필수 artifact metadata로 취급하고 fixture 공유 | DB integration과 Search contract test |
| embedding 성공 후 partial persistence | chunk/vector mismatch | 최종 DB write는 하나의 transaction 또는 보상 cleanup 사용 | transaction failure test |
| Duplicate at-least-once message | 중복 artifact 또는 반복 status 변경 | target artifact 존재 확인과 conditional status claim | duplicate preprocess test |
| Worker가 실수로 rollback state 소유 | project exclusion 의미가 컴포넌트 사이에 분산됨 | Worker repository에서 project serving state를 read-only로 취급 | test가 project state update 없음 검증 |
| Active target mismatch | 잘못된 model/index에 신규 데이터가 기록됨 | processing 시점에 `ModelRelease`를 읽고 active target metadata 기록 | candidate 준비 중 active-only ingest test |
| Observability에 project context 누락 | production drift debug가 어려움 | log/metric에 `trace_id`, `project_id`, `video_id`, stage 요구 | log assertion 또는 structured logging test |

---

## 5. Rollout and Rollback

### 5.1 Rollout 계획
- Migration / schema 단계:
  - Add or verify `Video.project_id`, `Chunk` references, and `VectorIndexEntry.project_id`.
  - Add indexes needed by Search Service for `user_id + project_id + video_id/chunk_id` filtering.
  - Backfill vector metadata for existing artifacts before enabling project-scoped search over migrated data.
- Config / secret / infra 변경:
  - Worker DB, broker, storage, STT, Vision, Embedding endpoint, and model release read credentials.
  - concurrency, timeout, retry, and embedding batch settings.
- Backward / forward compatibility 고려사항:
  - Message envelope는 `video_id`만 유지한다.
  - 참조된 `Video`에 `project_id`가 있으면 Worker는 중복 queued message를 허용해야 한다.
  - vector metadata backfill이 완료된 뒤에만 Search project-scope filtering을 활성화한다.
- Rollout 중 볼 monitoring signal:
  - processing success/failure by stage
  - `pipeline_project_id_missing_count`
  - vector upsert count by index
  - duplicate skip count
  - Search Service SOT-gate filtered count
- 배포 후 점검:
  - upload one video under a project and verify `READY` plus scoped vector metadata.
  - project search를 실행해 선택한 project chunk만 반환되는지 확인한다.
  - delete that video and confirm DB hard-delete removes searchable artifacts.

### 5.2 Rollback 계획
- App rollback:
  - DB schema addition은 유지한 채 Worker binary/config를 rollback한다. transition 중 추가 metadata column이 nullable이면 backward-compatible하다.
- Data rollback 또는 safe-forward plan:
  - bad projection은 영향받은 `VectorIndexEntry` row 삭제/재구축 방식의 safe-forward cleanup을 우선한다.
  - Search Service가 의존하기 시작한 뒤에는 `project_id` metadata를 제거하지 않는다.
- Async / message compatibility fallback:
  - projection write가 안전하지 않으면 Worker consumer를 pause한다.
  - fix 이후 queued message를 replay할 수 있도록 Core API message format은 변경하지 않는다.
- Partial deployment recovery:
  - Worker rollback으로 신규 projection에 `project_id`가 빠지면 metadata backfill 또는 reprocessing이 끝날 때까지 영향받은 project search를 비활성화한다.
  - active target 기록이 실패하면 Worker를 수정한 뒤 queued message를 재처리한다.

---

## 6. 완료 체크리스트

- [ ] 모든 계획된 workstream이 target SPEC에 매핑된다.
- [ ] 계획된 테스트가 SPEC section 또는 acceptance criteria에 매핑된다.
- [ ] 최고 위험도의 business rule에 대해 명시적 자동화 검증 또는 문서화된 예외 사유가 있다.
- [ ] 저가치 또는 중복 테스트를 의도적으로 피했다.
- [ ] 필요한 observability와 failure-path 점검이 포함되어 있다.
- [ ] rollout / rollback 단계에 compatibility 가정과 monitoring signal이 포함되어 있다.
- [ ] 남아 있는 open question 또는 deferred item이 기록되어 있다.
