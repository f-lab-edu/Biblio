# [Search Service] PLAN

**메타 정보**
- Component ID: `search-service`
- SOT: `docs/system-design.md`
- Target SPEC: `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
- 관련 문서:
  - `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Pipeline_Worker_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Managed_Embedding_Endpoint_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Feedback_Ingestion_Pipeline_Spec.md`
- Plan 상태: Draft

---

## 1. 구현 의도

### 1.1 전달 목표
- 이 plan이 끝났을 때 실제로 동작해야 하는 것:
  - 사용자는 본인이 소유한 단일 프로젝트 안에서만 검색할 수 있다.
  - Search Service는 프로젝트 readiness와 rollback exclusion 상태를 확인한 뒤 retrieval을 시작한다.
  - FTS/ANN/SOT gate는 모두 `user_id + project_id` scope를 강제한다.
  - 성공 응답은 Core API feedback 검증에 필요한 `SearchResponseSnapshot`을 저장한다.
- 검증 가능한 형태로 입증되어야 하는 것:
  - project-scoped API integration tests
  - FTS/ANN/SOT gate scope tests
  - snapshot write and field mapping tests
  - embedding/LLM failure contract tests

### 1.2 이번 구현의 범위
- 이번 plan에 포함:
  - `/api/v1/projects/{project_id}/search` route, DTO, auth, trace, error mapping
  - Project ownership/readiness gate
  - FTS/ANN candidate read path with project filters
  - RRF merge and SOT gate
  - LLM prompt/answer parsing and `used_refs` mapping
  - `SearchResponseSnapshot` persistence
  - observability and release checks
- 명시적 제외 / 후속 phase:
  - project/video mutation APIs
  - Pipeline Worker vector upsert implementation
  - Core API feedback endpoint implementation
  - rollback exclusion state transition and re-embedding workflow
  - multi-project search, query rewrite, translation, reranking model

### 1.3 전제조건과 blocker
- 이미 고정된 spec contract:
  - 검색 스코프는 `requester_user_id + owned project_id`다.
  - project 내부 all-or-nothing readiness gate를 통과해야 retrieval을 시작한다.
  - rollback-excluded project는 검색하지 않고 사용자에게 고지한다.
  - 성공한 search는 `SearchResponseSnapshot`을 생성한다.
- 필요한 upstream work / dependency:
  - Core API-managed `Project` and `Video` schema
  - Pipeline Worker-generated `Chunk` and `VectorIndexEntry` with `project_id`
  - Managed Embedding Endpoint and LLM provider wiring
- 구현을 막는 open question:
  - 없음. Snapshot TTL의 구체 값은 배포 설정으로 두되, feedback 허용 시간 창보다 짧지 않아야 한다.

### 1.4 구현 전략
- 전체 접근:
  - project gate와 scoped retrieval을 먼저 고정하고, 이후 LLM answer와 snapshot write를 붙인다.
- 핵심 기술 작업 단위:
  - route/auth/trace foundation
  - repository scope filter and readiness gate
  - retrieval/RRF/SOT gate
  - LLM response finalization
  - snapshot persistence and observability
- 리스크 감소 전략:
  - 테넌시와 readiness gate를 integration tests로 먼저 고정한다.
  - snapshot persistence failure는 성공 응답 전 실패로 처리해 feedback 검증 불능 응답을 만들지 않는다.
- 병합 전략:
  - 단일 PR 안에서 workstream별 commit을 분리한다.
- Spec 추적 기준:
  - Search Service SPEC §2.1, §2.2, §2.3, §4.1

---

## 2. Workstream과 순서

### 2.1 권장 순서
| 순서 | Workstream | 연결 SPEC | 지금 먼저 하는 이유 | 의존성 |
| --- | --- | --- | --- | --- |
| 1 | API foundation and project gate | §2.1, §2.3 | 검색 시작 전 auth/scope/readiness를 먼저 닫는다 | Core project/video schema |
| 2 | Scoped retrieval and SOT gate | §2.2, §2.3 | 검색 품질보다 테넌시/정합성이 먼저다 | Workstream 1, vector metadata |
| 3 | LLM answer and snapshot persistence | §2.1, §2.2, §4.1 | feedback 검증 가능한 성공 응답을 완성한다 | Workstream 2 |
| 4 | Observability and release readiness | §2.5, §3, §4.1 | 운영 가능성과 배포 검증을 닫는다 | Workstream 3 |

### 2.2 Workstream 상세

#### Workstream: API foundation and project gate
- 목표:
  - project-scoped search route가 검색 시작 전 ownership, serving state, project readiness를 판단하게 한다.
- 연결 SPEC:
  - SPEC §2.1 외부 인터페이스, §2.3 검색 시작 전 gate
- 주요 변경:
  - `/api/v1/projects/{project_id}/search` route 구성
  - JWT validation and requester extraction
  - query validation and trace id handling
  - Project ownership lookup
  - `SERVABLE` and all-project-videos-READY gate
- 영향 가능성이 높은 파일 / 영역:
  - `services/search-service/src/api/v1/routers`
  - `services/search-service/src/schemas`
  - `services/search-service/src/middlewares`
  - `services/search-service/src/infra/db`
- 의존성 / 연동 지점:
  - Core API's `Project`/`Video` schema
  - Reverse Proxy route mapping
- 완료 조건:
  - non-owner, missing project, empty project, non-ready project, rollback-excluded project는 embedding/LLM 호출 전에 거부된다.
- 검증:
  - API integration tests with mocked embedding/LLM clients that assert no downstream calls on gate failure.

#### Workstream: Scoped retrieval and SOT gate
- 목표:
  - FTS/ANN 후보 조회와 최종 SOT gate가 같은 project scope를 강제하게 한다.
- 연결 SPEC:
  - SPEC §2.2 참조 데이터, §2.3 scope invariant, §4.1 scoped retrieval criteria
- 주요 변경:
  - FTS query joins `Chunk -> Video -> Project` and filters `Project.user_id`, `Project.id`
  - ANN query filters `VectorIndexEntry.user_id`, `project_id`, serving index/model metadata
  - active 및 선택적 previous vector path는 serving state에 따라 조회한다.
  - RRF merge keeps final relevance order
  - SOT gate reloads final chunks from Metadata DB and rechecks ownership, project, video status, serving state
- 영향 가능성이 높은 파일 / 영역:
  - `services/search-service/src/infra/db`
  - `services/search-service/src/infra/vector`
  - `services/search-service/src/services/rrf.py`
  - `services/search-service/src/services/search_orchestrator.py`
- 의존성 / 연동 지점:
  - Pipeline Worker는 `VectorIndexEntry.project_id`를 기록해야 한다.
  - ModelRelease/embedding serving state는 active/previous model/index context를 노출해야 한다.
  - Managed Embedding Endpoint request payload에는 target `model_version`이 포함되어야 한다.
- 완료 조건:
  - mixed-user and mixed-project fixtures cannot leak through FTS, ANN, or SOT gate.
- 검증:
  - repository integration tests
  - RRF unit tests
  - SOT gate tests with stale/deleted/non-ready candidates

#### Workstream: LLM answer and snapshot persistence
- 목표:
  - 성공한 search response와 snapshot은 같은 final retrieval context를 포함한다.
- 연결 SPEC:
  - SPEC §2.1 response contract, §2.2 snapshot ownership, §4.1 snapshot acceptance criteria
- 주요 변경:
  - ContextBlock construction from final chunks
  - prompt builder and internal LLM adapter wiring
  - `<ANSWER>` extraction and `used_refs` parsing
  - `chunks[].used` mapping
  - `SearchResponseSnapshot` insert with final chunk ids, used chunk ids, model/index context, served vector paths, project serving state, expiry
- 영향 가능성이 높은 파일 / 영역:
  - `services/search-service/src/services/prompt_builder.py`
  - `services/search-service/src/services/used_refs_parser.py`
  - `services/search-service/src/infra/llm`
  - `services/search-service/src/infra/db/search_snapshot_repository.py`
  - `services/search-service/src/services/search_orchestrator.py`
- 의존성 / 연동 지점:
  - Core API feedback validation reads snapshot fields.
  - FIP receives `topk_ids`/`used_ids` mapped from snapshot by Core API.
- 완료 조건:
  - 모든 200 response에는 대응하는 snapshot row가 있고, Core API는 `req_id`로 feedback context를 복원할 수 있다.
- 검증:
  - orchestrator integration tests
  - snapshot fixture assertions
  - malformed LLM output tests

#### Workstream: Observability and release readiness
- 목표:
  - search failure, readiness block, snapshot write failure, projection drift는 rollout 전에 관측 가능해야 한다.
- 연결 SPEC:
  - SPEC §2.5 에러 계약, §3 관측성, §4.1 acceptance criteria
- 주요 변경:
  - structured logs with trace/user/project/req identifiers
  - metrics for latency, not-ready, snapshot write failure, embedding/LLM failure, SOT gate filtered count
  - final contract grep against Core/Pipeline/FIP docs
  - rollout smoke checklist
- 영향 가능성이 높은 파일 / 영역:
  - `services/search-service/src/common`
  - `services/search-service/src/middlewares`
  - tests and README/runbook docs
- 의존성 / 연동 지점:
  - Core API feedback path
  - Reverse Proxy route preservation of Authorization and trace headers
- 완료 조건:
  - quality gates pass and rollout checks prove project-scoped search plus snapshot write.
- 검증:
  - full test suite
  - route smoke
  - captured snapshot and response example

### 2.3 병렬화와 병합 지점
- 안전하게 병렬화 가능한 작업:
  - endpoint shape가 고정된 뒤 route/DTO/trace 작업과 repository query 작업은 병렬 진행할 수 있다.
  - LLM parser/prompt test는 fake context fixture로 병렬 진행할 수 있다.
- 공유 연동 지점 / 충돌 가능 영역:
  - search orchestrator
  - DB repository interfaces
  - snapshot write transaction boundary
- 최종 통합 checkpoint:
  - ownership, readiness, retrieval, LLM answer, snapshot persistence를 한 경로에서 검증하는 route-level test를 실행한다.

---

## 3. 검증 및 테스트 전략

### 3.1 리스크 기반 테스트 초점
| Spec ref | 리스크 / 비즈니스 규칙 | 중요한 이유 | 권장 test level | 계획된 증명 |
| --- | --- | --- | --- | --- |
| SPEC §2.3 | project ownership search scope | cross-project leakage는 데이터 노출이다 | Integration | foreign project request가 403을 반환하고 retrieval call이 없음 |
| SPEC §2.3 | project readiness gate | 부분 준비 또는 rollback-excluded project 검색은 품질 해석을 모호하게 만든다 | Integration | non-ready 또는 `ROLLBACK_EXCLUDED` project는 retrieval을 건너뜀 |
| SPEC §2.3 | FTS/ANN/SOT가 모두 project filter 적용 | filter 하나만 빠져도 candidate가 누출될 수 있다 | Integration | mixed project/user fixture가 final chunk에 나타나지 않음 |
| SPEC §2.2 | snapshot persistence | feedback validation은 저장된 context에 의존한다 | Integration | 200 response에 일치하는 snapshot field가 있음 |

### 3.2 계획된 자동화 테스트
| Spec ref / acceptance criterion | 시나리오 / 규칙 | Test level | 이 level을 쓰는 이유 | 관찰 가능한 증명 |
| --- | --- | --- | --- | --- |
| AC 1 | requester가 소유한 project만 검색 | API integration | auth, DB, route path가 함께 동작 | owner는 200, non-owner는 403 |
| AC 2 | empty/non-ready/excluded project gate | API integration | downstream call skip을 증명해야 함 | embedding/LLM mock이 호출되지 않음 |
| AC 3 | FTS/ANN/SOT project filter | repository integration | SQL/vector filter 정합성 | scoped chunk id만 반환 |
| AC 4 | canonical response chunks와 citations | unit + integration | parser와 response assembler가 함께 동작 | `ref`, `used`, `answer`가 fixture와 일치 |
| AC 5 | snapshot write | integration | DB write와 response가 같은 context를 공유해야 함 | snapshot row가 response context와 일치 |
| AC 7 | error contract | API integration | middleware/service mapping이 중요 | status/code/trace header가 일치 |

### 3.3 자동화 테스트로 다루지 않는 항목
| Spec ref / rule | 자동화하지 않는 이유 | 수동 / 운영 증명 |
| --- | --- | --- |
| Real LLM answer quality | provider output이 비결정적이다 | 고정 fixture query를 쓰는 staging prompt smoke |
| Production vector index latency | 배포된 vector store 크기에 의존한다 | rollout latency dashboard와 p95 확인 |
| Snapshot TTL cleanup job | DB/storage policy로 구현될 수 있다 | TTL cleanup에 대한 migration/runbook 증명 |

### 3.4 테스트 환경과 double
- DB / vector / provider 설정:
  - PostgreSQL integration DB with project/video/chunk/snapshot fixtures
  - in-memory or fake vector repository for scoped ANN tests unless production vector test harness exists
- 외부 의존성 격리 방식:
  - Embedding/LLM client는 adapter 기반이며 API test에서는 mock 처리한다.
- Time / async / retry 제어 방식:
  - injectable clock for snapshot expiry
  - bounded retry config for embedding/LLM failure tests
- 필요한 fixture 또는 seed data:
  - two users, multiple projects, mixed ready/non-ready videos
  - chunks and vectors across users/projects
  - active and optional previous vector path context

### 3.5 검증 명령과 quality gate
- 필수 명령:
  - `cd services/search-service && pytest`
  - `git diff --check -- docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md docs/Tech_Spec/upload_search_Service/Search_Service_Plan.md`
- 병합 전 최소 meaningful check:
  - project-scope route tests pass
  - readiness/exclusion gate tests pass
  - FTS/ANN/SOT scope tests pass
  - snapshot write tests pass
  - no Sonar-sensitive FastAPI/test patterns introduced
- 첨부할 증거:
  - test output
  - 성공 response 예시
  - matching snapshot row
  - examples of gate failures that skip retrieval

---

## 4. 전달 리스크와 안전장치

| 리스크 | 영향 | 완화책 | 검증 |
| --- | --- | --- | --- |
| Search route가 unscoped global query를 여전히 허용 | cross-project retrieval 범위가 모호해짐 | project path route만 노출 | route test가 global search를 거부하거나 등록하지 않음 |
| Vector metadata에 `project_id` 누락 | ANN이 SOT gate 전에 scope를 강제할 수 없음 | Worker/vector projection이 project metadata를 포함할 때까지 rollout 차단 | vector fixture와 contract grep |
| Answer 생성 후 snapshot write 실패 | Core가 검증할 수 없는 `req_id`를 client가 받음 | 200 전 snapshot write failure를 request failure로 처리 | 강제 snapshot write failure test |
| Rollback-excluded project가 검색 가능 | 사용자가 복구 중인 데이터를 봄 | retrieval 전 project serving gate 적용 | rollback-excluded API test |
| Previous/active vector path 불일치 | model 결과 혼합으로 품질이 조용히 저하됨 | served vector path를 snapshot에 기록 | snapshot assertion |

---

## 5. Rollout and Rollback

### 5.1 Rollout 계획
- Migration / schema 단계:
  - Search Service는 project/video/chunk DDL을 소유하지 않는다.
  - `SearchResponseSnapshot` table이 존재하고 Search Service가 write 가능함을 확인한다.
  - vector metadata에 `user_id`, `project_id`, `video_id`가 포함되는지 확인한다.
- Config / secret / infra 변경:
  - `JWT_SECRET_KEY`, `DATABASE_URL`, `EMBEDDING_API_URL`, LLM provider settings
  - snapshot TTL setting
- Backward / forward compatibility 고려사항:
  - client는 project-scoped search route를 호출해야 한다.
  - Core API feedback path는 `req_id`로 snapshot lookup을 수행해야 한다.
  - Core API feedback path는 `req_id`와 `rating`만 받고, server-side snapshot context를 읽는다.
- Rollout 중 볼 monitoring signal:
  - `search_not_ready_count`
  - `search_snapshot_write_fail_count`
  - search p95 latency
  - embedding/LLM failure counts
  - empty result rate
- 배포 후 점검:
  - 소유한 ready project search가 200을 반환하고 snapshot row가 존재한다.
  - non-owner project search가 403을 반환한다.
  - non-ready and rollback-excluded projects return 409 before retrieval.
  - release state가 active와 previous를 모두 노출할 때 embedding call에 의도한 `model_version`이 포함되는지 확인한다.

### 5.2 Rollback 계획
- App rollback:
  - route, gate, snapshot write 동작이 실패하면 Search Service deployment를 이전 stable artifact로 되돌린다.
- Data rollback 또는 safe-forward plan:
  - snapshot row는 append-only short-lived record이며 자연 만료될 수 있다.
  - shared table schema rollback은 Core/Search 조율이 필요하다.
- Async / message compatibility fallback:
  - Search Service는 async message를 발행하지 않는다.
- Partial deployment recovery:
  - Search가 Core feedback snapshot validation보다 먼저 배포되면, 명시 승인 없이 feedback UI를 비활성화하거나 compatibility path로만 라우팅한다.

---

## 6. 완료 체크리스트

- [ ] 모든 planned workstream이 Search Service SPEC section 또는 acceptance criteria에 매핑된다.
- [ ] Project-scoped route와 ownership gate가 검증된다.
- [ ] Project all-or-nothing readiness와 rollback exclusion gate가 검증된다.
- [ ] FTS, ANN, SOT gate가 모두 `user_id + project_id`를 강제한다.
- [ ] 성공 응답이 일치하는 `SearchResponseSnapshot` row를 저장한다.
- [ ] Embedding/LLM 실패와 잘못된 LLM output path가 error contract를 따른다.
- [ ] Rollout check가 route, snapshot, vector metadata compatibility를 다룬다.
