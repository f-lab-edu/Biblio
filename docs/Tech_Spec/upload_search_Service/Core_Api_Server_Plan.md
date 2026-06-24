# [Core API Server] PLAN

**메타 정보**
- Component ID: `core-api-server`
- SOT: `docs/system-design.md`
- Target SPEC: `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`
- 관련 문서:
  - `docs/ADR/ADR-007-feedback-ingestion-vector-http-source.md`
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Pipeline_Worker_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Feedback_Ingestion_Pipeline_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Admin_Control_Plane_Spec.md`
- Plan 상태: Draft

---

## 1. 구현 의도

### 1.1 전달 목표
- 이 plan이 끝났을 때 실제로 동작해야 하는 것:
  - 사용자는 본인 소유 프로젝트를 만들고, 프로젝트 하위에 로컬 파일 또는 외부 URL 영상을 추가할 수 있다.
  - Core API는 프로젝트 소유권과 영상 소속을 기준으로 모든 사용자 경로의 테넌시를 강제한다.
  - 영상 처리, 삭제, 재처리 요청은 상태 guard를 통과한 뒤 video-processing message 계약으로 후속 컴포넌트에 전달된다.
  - 검색 피드백은 `SearchResponseSnapshot` 검증을 통과한 경우에만 feedback event로 만들어 FIP internal HTTP endpoint로 전달된다.
- 검증 가능한 형태로 입증되어야 하는 것:
  - API 통합 테스트, repository 테스트, broker/storage/feedback delivery adapter 테스트, migration 검증
  - Search/Pipeline/FIP spec과 공유하는 async transport, state, snapshot field 계약의 diff review

### 1.2 이번 구현의 범위
- 이번 plan에 포함:
  - `Project`/`Video` migration과 `SearchResponseSnapshot` read access
  - project-scoped HTTP route, DTO, service, repository
  - Signed URL 발급, upload completion 검증, playback URL 발급
  - `PREPROCESS_REQUEST`, `DELETE_REQUEST` 발행과 validated feedback event HTTP delivery
  - feedback delivery를 감싸는 `FeedbackEventDeliveryClient` adapter interface와 HTTP 구현체
  - 관측성, 에러 매핑, 주요 통합 테스트
- 명시적 제외 / 후속 phase:
  - Search Service의 검색 로직과 snapshot 생성
  - Pipeline Worker의 미디어 처리와 hard-delete
  - Admin Control Plane의 admin route 구현
  - ML rollback 복구와 project search exclusion 상태 전이

### 1.3 전제조건과 blocker
- 이미 고정된 spec contract:
  - 사용자 테넌시는 `Project.user_id`와 `Video.project_id`로 강제한다.
  - video-processing message는 `docs/system-design.md` 3.13 shared envelope를 사용한다.
  - feedback request body는 `req_id`, `rating`만 받고 검색 문맥은 snapshot에서 복원한다.
- 필요한 upstream work / dependency:
  - JWT 검증 middleware와 requester id 추출
  - PostgreSQL, Object Storage, PGMQ, feedback delivery test double
  - feedback delivery fake/spy/failing test double
  - Search Service가 `SearchResponseSnapshot`을 생성하는 후속 spec/implementation
- 구현을 막는 open question:
  - 없음.

### 1.4 구현 전략
- 전체 접근:
  - project-scoped route, schema, service slice를 먼저 닫고, shared async/message 계약은 system design 기준으로 고정한다.
- 핵심 기술 작업 단위:
  - schema/migration과 tenancy guard를 먼저 닫고, 그 위에 project/video route를 올린다.
  - feedback은 upload/video flow와 분리해 snapshot validation slice로 구현한다.
  - feedback service는 `FeedbackEventDeliveryClient` interface에만 의존하고, HTTP 호출 세부사항은 infra adapter 구현에 둔다.
- 리스크 감소 전략:
  - route/service/repository 테스트에서 ownership, membership, state guard를 먼저 증명한다.
  - broker publish failure, feedback delivery failure, `/complete` idempotency를 통합 테스트로 고정한다.
- 병합 전략:
  - 단일 PR 안에서 workstream별 commit을 분리한다.
- Spec 추적 기준:
  - Core API SPEC §2.1, §2.2, §2.3, §4.1

---

## 2. Workstream과 순서

### 2.1 권장 순서
| 순서 | Workstream | 연결 SPEC | 지금 먼저 하는 이유 | 의존성 |
| --- | --- | --- | --- | --- |
| 1 | Schema and tenancy foundation | §2.2, §2.3 | 모든 사용자 경로의 SOT와 guard를 먼저 고정한다 | DB/test setup |
| 2 | Project-scoped video API | §2.1, §2.3, §4.1 | 핵심 사용자 업로드/관리 경로를 전달한다 | Workstream 1 |
| 3 | Feedback snapshot validation | §2.1, §2.2, §4.1 | Search/FIP 계약과 분리된 피드백 경로를 닫는다 | Workstream 1, Search snapshot contract |
| 4 | Observability and release readiness | §2.5, §3, §4.1 | 운영 확인과 failure path를 병합 전에 고정한다 | Workstream 2, 3 |

### 2.2 Workstream 상세

#### Workstream: Schema and tenancy foundation
- 목표:
  - `Project`를 사용자 소유 작업 단위로 도입하고, `Video`를 프로젝트 하위 엔터티로 고정한다.
- 연결 SPEC:
  - SPEC §2.2 데이터 계약, §2.3 테넌시 규칙
- 주요 변경:
  - Project table/model/repository 추가
  - Video schema에 `project_id` 연결과 project-scoped 조회 인덱스 반영
  - `SearchResponseSnapshot` read model과 repository 조회 함수 추가
  - ownership/membership guard helper 작성
- 영향 가능성이 높은 파일 / 영역:
  - `services/core-api/alembic`
  - `services/core-api/src/models`
  - `services/core-api/src/infra/db`
  - `services/core-api/src/services`
- 의존성 / 연동 지점:
  - Pipeline Worker가 읽는 `Video` 상태 값과 호환되어야 한다.
  - Search Service가 쓸 snapshot table 계약과 충돌하지 않아야 한다.
- 완료 조건:
  - migration이 clean DB에서 적용되고, repository 테스트가 project ownership과 video membership을 검증한다.
- 검증:
  - Alembic upgrade/downgrade smoke
  - repository integration tests

#### Workstream: Project-scoped video API
- 목표:
  - 프로젝트 하위 upload, complete, list/detail, patch, delete, retry, playback-url 경로를 구현한다.
- 연결 SPEC:
  - SPEC §2.1 외부 인터페이스, §2.3 상태 규칙, §4.1 acceptance criteria
- 주요 변경:
  - project router와 video router를 project path 하위로 구성
  - Local file Signed URL 발급과 `/complete` 객체 검증 구현
  - External URL 접수 시 `PREPROCESS_REQUEST` 발행
  - delete/retry state guard와 `DELETE_REQUEST`/`PREPROCESS_REQUEST` 발행
  - standard error response와 idempotent complete 처리
- 영향 가능성이 높은 파일 / 영역:
  - `services/core-api/src/api/v1/routers`
  - `services/core-api/src/schemas`
  - `services/core-api/src/services`
  - `services/core-api/src/infra/storage`
  - `services/core-api/src/infra/broker`
- 의존성 / 연동 지점:
  - Object Storage adapter
  - Broker adapter
  - Pipeline Worker message envelope
- 완료 조건:
  - project-scoped user flows가 API integration tests로 통과한다.
- 검증:
  - success, tenancy violation, invalid state, broker failure에 대한 API test
  - `/complete` duplicate request test

#### Workstream: Feedback snapshot validation
- 목표:
  - feedback request에서 검색 문맥을 클라이언트 입력으로 받지 않고 snapshot에서 복원한다.
- 연결 SPEC:
  - SPEC §2.1 feedback interface, §2.2 참조 데이터, §2.3 피드백 원본성
- 주요 변경:
  - `POST /api/v1/feedbacks` DTO를 `req_id`, `rating` 중심으로 정리
  - snapshot lookup, ownership, expiry 검증 구현
  - `topk_chunk_ids`/`used_chunk_ids`를 feedback event의 `topk_ids`/`used_ids`로 매핑
  - FIP HTTP delivery와 delivery failure metric 추가
  - `FeedbackEventDeliveryClient` interface, HTTP implementation, fake/spy/failing test double 추가
- 영향 가능성이 높은 파일 / 영역:
  - `services/core-api/src/api/v1/routers/feedbacks.py`
  - `services/core-api/src/schemas/feedback_dto.py`
  - `services/core-api/src/services/feedback_service.py`
  - `services/core-api/src/infra/db`
  - `services/core-api/src/infra/feedback_delivery`
- 의존성 / 연동 지점:
  - FIP event schema
  - Search Service snapshot schema
  - Feedback Ingestion Pipeline의 Vector HTTP ingress 계약
- 완료 조건:
  - valid snapshot feedback은 FIP delivery adapter로 전달되고, missing/expired/foreign snapshot은 선언된 에러로 거부된다.
  - feedback service unit test는 실제 HTTP 호출 없이 delivery double로 payload와 failure path를 검증한다.
- 검증:
  - feedback service unit tests
  - API integration tests with snapshot fixtures

#### Workstream: Observability and release readiness
- 목표:
  - Core API의 계약 위반, publish/delivery failure, trace 흐름을 운영자가 확인 가능하게 만든다.
- 연결 SPEC:
  - SPEC §2.5 에러 계약, §3 관측성, §4.1 acceptance criteria
- 주요 변경:
  - request trace propagation
  - structured logs with project/video/request identifiers
  - key metrics for signed URL, broker publish failure, cursor failure, feedback delivery failure
  - final cross-spec contract check
- 영향 가능성이 높은 파일 / 영역:
  - `services/core-api/src/middlewares`
  - `services/core-api/src/common`
  - test support and CI commands
- 의존성 / 연동 지점:
  - existing logging/metrics conventions
  - Pipeline Worker and FIP trace fields
- 완료 조건:
  - failure path는 `trace_id`를 노출하고, metric은 test 또는 smoke check로 확인되며, spec contract check가 기록된다.
- 검증:
  - error handler tests
  - metrics/logging unit tests
  - full service test command

### 2.3 병렬화와 병합 지점
- 안전하게 병렬화 가능한 작업:
  - schema field가 고정된 뒤 DTO/router skeleton과 repository/migration은 병렬 진행할 수 있다.
  - snapshot read model이 준비된 뒤 feedback slice는 병렬 진행할 수 있다.
- 공유 연동 지점 / 충돌 가능 영역:
  - `Video` model/repository
  - broker adapter publish API
  - feedback delivery adapter API
  - auth dependency and tenancy guard helpers
- 최종 통합 checkpoint:
  - 병합 전 Core API test를 실행하고 Search Service, Pipeline Worker, FIP spec과 공유 계약을 수동 확인한다.

---

## 3. 검증 및 테스트 전략

### 3.1 리스크 기반 테스트 초점
| Spec ref | 리스크 / 비즈니스 규칙 | 중요한 이유 | 권장 test level | 계획된 증명 |
| --- | --- | --- | --- | --- |
| SPEC §2.3 | project ownership + video membership guard | 테넌시 누락은 데이터 유출로 이어진다 | Integration | foreign project/video 접근이 403으로 거부됨 |
| SPEC §2.3 | `/complete` idempotency | 중복 완료 신호가 중복 처리 메시지를 만들 수 있다 | Integration | 첫 요청 202, 중복 요청 200, 추가 publish 없음 |
| SPEC §2.3 | snapshot-based feedback | 클라이언트 조작 문맥이 학습 로그로 들어갈 수 있다 | Integration | event field가 request body가 아니라 snapshot에서 복원된다 |

### 3.2 계획된 자동화 테스트
| Spec ref / acceptance criterion | 시나리오 / 규칙 | Test level | 이 level을 쓰는 이유 | 관찰 가능한 증명 |
| --- | --- | --- | --- | --- |
| AC 1 | Project 생성/목록/상세/수정 ownership | API integration | auth + DB filter가 함께 필요 | requester별 결과 분리 |
| AC 2 | Project-scoped video CRUD | API integration | path project와 DB video membership 검증 필요 | project mismatch 403 |
| AC 3 | Local file ingest와 complete | API integration | storage adapter + broker adapter 결합 필요 | signed URL, status, publish 확인 |
| AC 4 | External URL/delete/retry publish | API integration | state transition과 broker side effect 결합 필요 | 예상 message capture |
| AC 5 | Feedback snapshot validation | API integration | snapshot DB read + event delivery 결합 필요 | validated event payload 확인 |
| AC 6 | Error contract와 metrics | Unit + integration | middleware와 service exception mapping 검증 | 표준 error body와 metric increment |

### 3.3 자동화 테스트로 다루지 않는 항목
| Spec ref / rule | 자동화하지 않는 이유 | 수동 / 운영 증명 |
| --- | --- | --- |
| Object Storage provider behavior | provider-specific Signed URL semantics는 adapter mock만으로 완전 증명 불가 | real bucket을 쓰는 staging smoke |
| Feedback delivery during FIP outage | FIP 배포 환경과 internal network에 의존 | staging delivery failure smoke와 Core API failure metric 확인 |
| SearchResponseSnapshot TTL cleanup | Core API 소유 책임이 아님 | Search Service 또는 storage policy 검증 |

### 3.4 테스트 환경과 double
- DB / storage / async transport 설정:
  - PostgreSQL integration DB, Alembic migration, in-memory storage, PGMQ test double, feedback delivery test double
- 외부 의존성 격리 방식:
  - GCS, PGMQ, feedback delivery client는 interface-backed adapter다.
  - feedback service는 `FeedbackEventDeliveryClient` fake/spy/failing double로 테스트하고, HTTP 구현은 adapter contract/smoke에서만 검증한다.
- Time / async / retry 제어 방식:
  - fixed clock or injectable time provider for snapshot expiry and Signed URL TTL tests
  - bounded retry configuration for publish and delivery failure tests
- 필요한 fixture 또는 seed data:
  - users, projects, videos across different owners
  - snapshot rows for valid, expired, foreign, missing cases

### 3.5 검증 명령과 quality gate
- 필수 명령:
  - `cd services/core-api && alembic upgrade head`
  - `cd services/core-api && pytest`
  - `git diff --check -- docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md docs/Tech_Spec/upload_search_Service/Core_Api_Server_Plan.md`
- 병합 전 최소 meaningful check:
  - project-scoped API integration tests pass
  - feedback snapshot tests pass
  - message envelope tests pass
  - no Sonar-sensitive FastAPI/test patterns introduced
- 첨부할 증거:
  - test output, migration output, captured broker and feedback delivery payload examples

---

## 4. 전달 리스크와 안전장치

| 리스크 | 영향 | 완화책 | 검증 |
| --- | --- | --- | --- |
| 기존 video-level route가 client 또는 test에 남음 | user flow가 지원하지 않는 계약을 호출함 | 같은 integration window 안에서 caller를 갱신 | API contract test가 project path만 사용 |
| Project/video tenancy gap | cross-user data exposure | central ownership/membership guard | integration tests with foreign project/video |
| Broker publish와 DB state가 불일치 | pipeline work가 stuck되거나 중복됨 | 명시적 transaction boundary와 publish failure handling | state + publish failure test |
| Feedback event uses client-provided context | poisoned feedback dataset | snapshot-only mapping service | request body cannot override event fields |
| HTTP client details leak into feedback service | infra 변경이 비즈니스 로직 변경으로 번짐 | `FeedbackEventDeliveryClient` adapter boundary | service unit test uses fake/spy/failing delivery client |
| Shared spec drift | Worker/Search/FIP incompatibility | final cross-doc contract grep/review | documented contract check before merge |

---

## 5. Rollout and Rollback

### 5.1 Rollout 계획
- Migration / schema 단계:
  - add `Project`
  - add `Video.project_id` and project-scoped indexes
  - add or align `SearchResponseSnapshot` read model table access
- Config / secret / infra 변경:
  - confirm `DATABASE_URL`, `BROKER_TYPE`, `GCS_VIDEO_BUCKET_NAME`, FIP internal endpoint settings, JWT settings
- Backward / forward compatibility 고려사항:
  - route consumer는 같은 release train 안에서 project-scoped endpoint로 이동해야 한다.
  - video-processing message envelope는 여전히 `video_id`를 담으므로 Pipeline Worker와 호환된다.
  - feedback event는 snapshot field에서 `topk_ids`/`used_ids`를 채워 전달하므로 FIP와 호환된다.
- Rollout 중 볼 monitoring signal:
  - 4xx by route, `mq_publish_fail_count`, `feedback_delivery_fail_count`, queue depth, upload completion failures
- 배포 후 점검:
  - create project, upload local test video, complete upload, confirm `PREPROCESS_REQUEST`
  - create external URL video, confirm accepted response and message
  - submit feedback against valid snapshot, confirm feedback delivery payload

### 5.2 Rollback 계획
- App rollback:
  - route, publish, 또는 feedback delivery 동작이 실패하면 Core API deployment를 이전 stable artifact로 되돌린다.
- Data rollback 또는 safe-forward plan:
  - project/video row가 쓰인 뒤에는 safe-forward migration fix를 우선한다.
  - destructive schema rollback은 명시적 data impact review가 필요하다.
- Async / message compatibility fallback:
  - video-processing messages retain `video_id`, so already published messages remain consumable by Pipeline Worker.
  - 이미 전달되어 저장된 feedback event는 append-only이며 수정하지 않는다.
- Partial deployment recovery:
  - schema가 forward 상태로 남은 채 app만 rollback되면, implementation scope에서 명시 승인된 경우에만 compatibility view 또는 repository fallback을 둔다.

---

## 6. 완료 체크리스트

- [ ] 모든 planned workstream이 Core API SPEC section 또는 acceptance criteria에 매핑된다.
- [ ] Project ownership과 video membership guard가 자동화 테스트로 검증된다.
- [ ] Local/External ingest, complete, delete, retry, playback-url의 핵심 상태 전이가 검증된다.
- [ ] Feedback API가 snapshot 기반 검증과 event mapping을 테스트로 증명한다.
- [ ] Feedback service는 `FeedbackEventDeliveryClient` interface에만 의존하고 실제 HTTP 호출 없이 unit test 가능하다.
- [ ] Message envelope와 feedback event 계약이 관련 spec과 교차 확인된다.
- [ ] 관측성, failure path, rollout/rollback 확인 항목이 merge 전에 기록된다.
- [ ] 남아 있는 open question 또는 deferred item이 명시된다.
