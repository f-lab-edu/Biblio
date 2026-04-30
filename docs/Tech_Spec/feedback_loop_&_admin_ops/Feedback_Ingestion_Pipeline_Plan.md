# [Feedback Ingestion Pipeline] PLAN

**메타 정보**
- Component ID: `feedback-ingestion-pipeline`
- SOT: `docs/system-design.md`
- Target SPEC: `docs/Tech_Spec/feedback_loop_&_admin_ops/Feedback_Ingestion_Pipeline_Spec.md`
- 관련 문서:
  - `docs/ADR/ADR-007-feedback-ingestion-vector-http-source.md`
  - `docs/ADR/ADR-006-feedback-ingestion-broker-for-vector.md`
  - `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/ML_Pipeline_Execution_Spec.md`
  - `docs/Tech_Spec/Observability_and_Ops_Standards.md`
- Plan 상태: Draft

---

## 1. 구현 의도

### 1.1 전달 목표
- 이 plan이 끝났을 때 실제로 동작해야 하는 것:
  - FIP는 Vector `http_server` source로 internal feedback ingestion endpoint를 연다.
  - Core API는 검증 완료한 feedback event를 FIP의 Vector HTTP ingress로 전달한다.
  - Core API의 business/service logic은 feedback delivery를 adapter 계약으로만 호출하고, Vector HTTP 호출 세부사항에 직접 의존하지 않는다.
  - Vector topology는 공통 transform/routing 계약과 환경별 source/sink 설정을 분리해, 로컬/CI에서 실제 GCS 없이 검증할 수 있다.
  - Vector는 `schema_version`과 필수 필드 기준으로 정상 event와 error event를 분기한다.
  - 정상 event는 별도 feedback log bucket의 append-only `raw_logs/` 경로에 저장된다.
  - 구조상 처리할 수 없는 event는 `error_logs/` 경로에 원본 payload와 실패 원인을 보존한다.
  - Object Storage 일시 장애 시 event를 즉시 폐기하지 않고 Vector buffer/retry와 Core API delivery failure handling 경로를 따른다.
  - 운영자는 Vector HTTP ingress 지연/거절, ingestion lag, raw/error 적재 건수, retry/buffer 상태를 확인할 수 있다.
- 검증 가능한 형태로 입증되어야 하는 것:
  - Vector config validation
  - HTTP seed event에서 GCS raw/error object write까지의 component smoke
  - duplicate delivery가 overwrite 없이 raw log에 보존된다는 fixture 검증
  - GCS sink 실패 시 HTTP delivery가 성공으로 오판되지 않는 failure-path 검증

### 1.2 이번 구현의 범위
- 이번 plan에 포함:
  - `services/feedback-ingestion-pipeline/` 컴포넌트 추가
  - Vector runtime 설정, Dockerfile, env sample, README 또는 runbook
  - Vector `http_server` source와 GCS raw/error sink 구성
  - internal HTTP ingress config wiring
  - 별도 feedback log bucket 설정값 `GCS_FEEDBACK_LOG_BUCKET_NAME`
  - raw/error Object Storage sink와 append-only object key layout
  - retry, buffer, timeout, batch의 초기 운영 기본값
  - component smoke test와 routing contract test
  - local/CI test profile에서 사용할 Vector fixture source와 local file sink 설정
  - `docker-compose.yml` local wiring 또는 local 실행 문서
- 명시적 제외 / 후속 phase:
  - Core API의 `SearchResponseSnapshot` 검증 로직 구현
  - Core API의 HTTP delivery adapter 구현 상세
  - Search Service의 snapshot 생성 또는 TTL cleanup
  - ML Lifecycle Worker의 dataset generation, dedupe, curation
  - 운영 dashboard 화면 구성
  - 장기 retention, legal hold, 개인정보 삭제 정책의 최종 운영값

### 1.3 전제조건과 blocker
- 이미 고정된 spec contract:
  - Core API만 feedback request 의미 검증을 수행한다.
  - FIP는 검증된 feedback event를 원본 로그로 보존한다.
  - feedback event는 video-processing shared envelope와 분리된 schema를 사용한다.
  - 지원 schema version은 `schema_version=1`이다.
  - 전달 의미론은 at-least-once이며 duplicate delivery를 허용한다.
  - non-retryable event는 Object Storage `error_logs/` 계열에 보존한다.
  - ADR-007에 따라 FIP input boundary는 Vector internal HTTP ingress다.
- 필요한 upstream work / dependency:
  - Core API feedback delivery adapter가 FIP internal HTTP endpoint로 spec-compatible payload를 전달해야 한다.
  - Core API feedback delivery adapter는 service interface 뒤에 두고, 실제 구현은 HTTP client adapter로 둔다.
  - FIP 배포 환경은 Core API에서 접근 가능한 internal network endpoint와 feedback log bucket write 권한을 가져야 한다.
  - Object Storage bucket은 사용자 업로드 bucket과 분리된 feedback log bucket으로 준비한다.
  - Target SPEC과 Core API 관련 문서는 ADR-007에 맞춰 direct HTTP delivery 계약으로 후속 개정되어야 한다.
- 구현 전 확정할 config:
  - internal HTTP address, path, service-to-service 보호 방식, Core API timeout/retry 수치는 Core API/FIP implementation slice에서 같은 config contract로 확정한다.

### 1.4 구현 전략
- 전체 접근:
  - Vector 공식 source/sink 조합으로 source, routing, sink를 환경별 topology에 둔다.
  - source/sink는 환경별로 교체 가능하게 두고, schema validation과 raw/error routing transform은 공통 설정으로 공유한다.
- 핵심 기술 작업 단위:
  - `services/feedback-ingestion-pipeline/` 아래에 Vector 설정과 실행 자산을 둔다.
  - production/staging 설정은 HTTP source와 GCS sink를 사용하고, test 설정은 fixture 또는 HTTP source와 local file sink를 사용한다.
  - HTTP source에서 받은 event를 `schema_version`과 필수 필드 기준으로 raw/error 경로에 분기한다.
  - GCS object path는 시간 partition과 unique object suffix를 사용해 batch object가 기존 로그를 덮어쓰지 못하게 한다.
  - duplicate 여부는 object path가 아니라 event body의 `event_id`와 HTTP delivery metadata로 추적한다.
  - Vector acknowledgement는 Object Storage persistence 결과가 Core API delivery 결과와 어긋나지 않도록 smoke로 검증한다.
- 리스크 감소 전략:
  - HTTP source, transform, local sink를 가장 작은 seed event smoke로 먼저 닫는다.
  - 공통 transform 설정은 fixture route test로 먼저 고정하고, 실제 GCS 연결은 staging smoke에서만 증명한다.
  - Core API delivery path가 완성되기 전에는 HTTP seed fixture로 FIP behavior를 검증한다.
  - malformed event도 error sink에 보존해 운영자가 수동 재처리 입력으로 사용할 수 있게 한다.
- 병합 전략:
  - 단일 PR 안에서 component scaffold, Vector topology, observability/test를 commit 단위로 분리한다.
- Spec 추적 기준:
  - Feedback Ingestion Pipeline SPEC §2.1, §2.2, §2.3, §2.4, §2.5, §3, §4.1
  - ADR-007 §3, §4, §7

---

## 2. Workstream과 순서

### 2.1 권장 순서

| 순서 | Workstream | 연결 SPEC / ADR | 지금 먼저 하는 이유 | 의존성 |
| --- | --- | --- | --- | --- |
| 1 | Runtime packaging and HTTP source | ADR-007 §3-§4, SPEC §2.1 | Vector 공식 source로 입력 경계를 먼저 고정한다 | internal network endpoint config |
| 2 | Schema routing and error classification | SPEC §2.1, §2.3, §2.5 | raw/error sink를 나누는 계약을 먼저 고정한다 | Workstream 1 |
| 3 | Append-only GCS persistence | SPEC §2.2, §2.3, §2.4 | 손실 최소화와 duplicate 보존의 핵심 경계다 | Workstream 2, feedback bucket |
| 4 | Observability and component smoke | SPEC §3, §4.1 | 운영자가 ingress 지연과 실패를 확인할 수 있어야 rollout 가능하다 | Workstream 3 |

### 2.2 Workstream 상세

#### Workstream: Runtime packaging and HTTP source
- 목표:
  - FIP를 독립 서비스 컴포넌트로 배치하고, Vector가 internal HTTP ingress로 feedback event를 수신하게 한다.
- 연결 SPEC / ADR:
  - ADR-007 §3 결정 사항, §4 결정된 설계 원칙
  - SPEC §1.3 기술 스택, §2.1 외부 인터페이스, §2.4 운영 제약
- 주요 변경:
  - `services/feedback-ingestion-pipeline/` 디렉터리 추가
  - Vector config, Dockerfile, `.env.example`, README 또는 runbook 추가
  - Vector 설정 분리:
    - 공통 transform/routing 설정
    - production/staging HTTP source + GCS sink 설정
    - local/CI fixture source + local file sink 설정
  - `docker-compose.yml`에 FIP service 또는 local execution guide 추가
  - env config 추가:
    - `FIP_HTTP_ADDRESS`
    - `FIP_HTTP_PATH`
    - `GCS_FEEDBACK_LOG_BUCKET_NAME`
    - service-to-service 보호에 필요한 secret 또는 network policy 설정값
  - Vector `http_server` source와 required ingress wiring
- 영향 가능성이 높은 파일 / 영역:
  - `services/feedback-ingestion-pipeline/`
  - `docker-compose.yml`
  - local env documentation
- 의존성 / 연동 지점:
  - Core API에서 접근 가능한 internal service DNS/route
  - feedback log bucket credentials
  - Core API feedback delivery adapter
- 완료 조건:
  - FIP container가 required env와 credentials로 기동된다.
  - HTTP seed event가 Vector transform으로 전달된다.
  - local/CI profile은 GCP credential 없이 fixture event를 transform으로 전달한다.
- 검증:
  - `vector validate`
  - container startup smoke
  - HTTP source to local file sink smoke
  - fixture source to local file sink smoke

#### Workstream: Schema routing and error classification
- 목표:
  - 수신 event를 `schema_version=1` 정상 event와 non-retryable malformed event로 분기한다.
- 연결 SPEC / ADR:
  - SPEC §2.1 메시지 계약, §2.3 상태 및 비즈니스 규칙, §2.5 에러 계약
  - ADR-007 §4 결정된 설계 원칙
- 주요 변경:
  - required field presence check
  - `schema_version=1` routing
  - source/sink implementation에 의존하지 않는 공통 transform fixture
  - unsupported version classification: `unsupported_schema_version`
  - malformed payload classification: `malformed_feedback_event`
  - error sink payload에 원본 payload, `event_id` 가능 값, `trace_id` 가능 값, HTTP delivery metadata 가능 값, 실패 원인, `ingested_at` 포함
- 영향 가능성이 높은 파일 / 영역:
  - `services/feedback-ingestion-pipeline/` Vector config files
  - `services/feedback-ingestion-pipeline/tests`
- 의존성 / 연동 지점:
  - Core API `FeedbackEvent` schema
  - ML Lifecycle Worker가 읽을 raw event fields
- 완료 조건:
  - 정상 event는 raw route로만 간다.
  - 미지원 version과 필수 필드 누락 event는 error route로만 간다.
  - 구조상 처리할 수 없는 event도 원본 payload를 잃지 않는다.
- 검증:
  - valid event fixture route test
  - unsupported `schema_version` fixture route test
  - missing required field fixture route test
  - production/test topology가 같은 transform contract를 참조하는지 확인하는 config check

#### Workstream: Append-only GCS persistence
- 목표:
  - 정상 event와 error event를 별도 feedback log bucket에 overwrite 없이 저장한다.
- 연결 SPEC / ADR:
  - SPEC §2.2 데이터 계약, §2.3 idempotency/dedupe, §2.4 retry 제약, §4.1 acceptance criteria
  - ADR-007 §4 결정된 설계 원칙
- 주요 변경:
  - 별도 bucket 설정 `GCS_FEEDBACK_LOG_BUCKET_NAME` 사용
  - raw object key layout:
    - `feedback/raw_logs/schema_version=1/ingest_date=YYYY-MM-DD/hour=HH/{batch_timestamp}-{object_uuid}.jsonl`
  - error object key layout:
    - `feedback/error_logs/schema_version=unknown/ingest_date=YYYY-MM-DD/hour=HH/{batch_timestamp}-{object_uuid}.jsonl`
    - `feedback/error_logs/schema_version=1/ingest_date=YYYY-MM-DD/hour=HH/{batch_timestamp}-{object_uuid}.jsonl`
  - partition 기준은 `created_at`이 아니라 FIP가 수신한 `ingested_at`이다.
  - object path에는 `user_id`, `project_id`, `query_text`를 넣지 않는다.
  - duplicate 추적에 필요한 `event_id`와 HTTP delivery metadata는 object body에 남긴다.
  - local/CI test profile에서는 같은 sink contract를 local file sink로 검증한다.
  - 초기 sink 설정값:
    - `FIP_SINK_BATCH_MAX_EVENTS=100`
    - `FIP_SINK_FLUSH_TIMEOUT_SEC=10`
    - `FIP_SINK_TIMEOUT_SEC=30`
    - `FIP_RETRY_MAX_ATTEMPTS=5`
    - `FIP_RETRY_INITIAL_BACKOFF_SEC=1`
    - `FIP_RETRY_MAX_BACKOFF_SEC=60`
    - `FIP_DISK_BUFFER_MAX_SIZE_MB=512`
- 영향 가능성이 높은 파일 / 영역:
  - `services/feedback-ingestion-pipeline/` Vector config files
  - `services/feedback-ingestion-pipeline/.env.example`
  - `services/feedback-ingestion-pipeline/README.md`
- 의존성 / 연동 지점:
  - GCS bucket IAM
  - Vector GCS sink
  - ML Lifecycle Worker batch reader convention
- 완료 조건:
  - 같은 `event_id`가 두 번 전달되어도 raw log에서 두 delivery를 추적할 수 있다.
  - Object Storage write가 성공하기 전에는 Core API delivery가 성공으로 오판되지 않는다.
  - retry exhausted 또는 구조상 처리 불가 event는 error 경로에 보존된다.
- 검증:
  - object key generation test 또는 config fixture assertion
  - duplicate delivery smoke
  - storage transient failure smoke
  - local file sink append-only fixture assertion

#### Workstream: Observability and component smoke
- 목표:
  - FIP의 정상 처리, 지연, 오류, error sink 유입을 운영자가 확인할 수 있게 한다.
- 연결 SPEC / ADR:
  - SPEC §3 관측성과 운영, §4.1 인수 기준
  - ADR-007 §6 위험 대응
  - Observability and Ops Standards §4-§7
- 주요 변경:
  - structured logs with `component=feedback-ingestion-pipeline`
  - log fields:
    - `trace_id`, `event_id`, `req_id`, `schema_version`, `result`, `error_code`
  - HTTP ingress operational signals:
    - request count, rejected request count
    - delivery latency and timeout count
    - source-side error count
  - Vector/GCS operational signals:
    - raw log write success/failure count
    - error sink write count
    - retry count
    - buffer utilization
  - raw event 본문과 `query_text`는 기본 운영 로그에 남기지 않는다.
  - component smoke script 또는 documented command 추가
- 영향 가능성이 높은 파일 / 영역:
  - `services/feedback-ingestion-pipeline/`
  - `docker-compose.yml`
  - `docs` 또는 service README
- 의존성 / 연동 지점:
  - Vector internal metrics
  - Core API delivery metrics
  - Admin Dashboard integration은 후속 범위다.
- 완료 조건:
  - valid event seed가 raw object로 저장되고 관련 성공 로그가 남는다.
  - malformed event seed가 error object로 저장되고 관련 error metric-ready signal이 남는다.
  - ingress 지연, rejected request, buffer utilization을 확인하는 운영 절차가 문서화된다.
- 검증:
  - local or staging smoke with HTTP seeded message
  - error-route smoke
  - log field assertion where practical

### 2.3 병렬화와 병합 지점
- 안전하게 병렬화 가능한 작업:
  - service scaffold와 README 작성은 HTTP source smoke와 병렬 진행할 수 있다.
  - schema route fixture와 object key fixture는 실제 bucket 없이 병렬 작성할 수 있다.
- 공유 연동 지점 / 충돌 가능 영역:
  - `docker-compose.yml`
  - feedback event schema fixture
  - internal HTTP endpoint config
  - GCS credential wiring
- 최종 통합 checkpoint:
  - HTTP source에 valid, duplicate, malformed event를 seed한다.
  - FIP를 실행해 raw/error object 경로와 로그를 확인한다.
  - Core API feedback delivery adapter가 완성된 뒤에는 direct seed smoke를 producer-integrated smoke로 대체한다.
  - producer-integrated smoke 전에는 Core API delivery adapter의 fake/spy/failing test로 event payload 계약을 고정한다.

---

## 3. 검증 및 테스트 전략

### 3.1 리스크 기반 테스트 초점

| Spec ref | 리스크 / 비즈니스 규칙 | 중요한 이유 | 권장 test level | 계획된 증명 |
| --- | --- | --- | --- | --- |
| ADR-007 §3 | FIP input boundary가 HTTP source로 열리지 않음 | Core API가 event를 전달할 수 없다 | Contract | FIP config가 HTTP source를 사용함 |
| ADR-007 §4 | Core API service logic이 HTTP client 세부사항에 직접 결합됨 | transport 변경 시 비즈니스 로직 변경이 필요해진다 | Unit | service는 feedback delivery adapter fake/spy로 검증됨 |
| SPEC §2.1 | feedback event가 video-processing envelope에 섞임 | producer/consumer 계약이 깨지고 dataset 입력이 오염된다 | Contract | `schema_version=1` payload fixture에 `video_id` envelope requirement가 없음 |
| User decision | Vector test topology가 production transform과 분리됨 | local test는 통과하지만 production route가 깨질 수 있다 | Contract | production/test config가 같은 transform/routing contract를 참조함 |
| SPEC §2.3 | raw log overwrite | duplicate delivery 증거가 사라지고 dataset dedupe 근거가 깨진다 | Component | duplicate event deliveries are preserved in raw log body |
| SPEC §2.3 | sink 성공 전 delivery success | Object Storage 일시 장애가 영구 손실로 이어진다 | Component | failed write is observed as failed delivery or retryable state |
| SPEC §2.5 | malformed event가 raw log로 저장 | downstream dataset 품질과 schema 해석이 깨진다 | Contract | unsupported/malformed fixture가 error 경로로만 저장됨 |
| SPEC §3 | observability blind spot | 운영자가 ingress 지연, 거절, error sink 증가를 감지하지 못한다 | Smoke | ingress/raw/error/buffer 신호 확인 절차가 통과함 |

### 3.2 계획된 자동화 테스트

| Spec ref / acceptance criterion | 시나리오 / 규칙 | Test level | 이 level을 쓰는 이유 | 관찰 가능한 증명 |
| --- | --- | --- | --- | --- |
| AC 1 | valid feedback event is persisted to raw log | Component smoke | source, transform, sink boundary가 함께 중요하다 | expected raw object exists |
| AC 1 | valid feedback event routes without real GCP infra | Contract | transform/routing은 외부 인프라 없이 빠르게 고정해야 한다 | fixture source output reaches local file raw sink |
| AC 2 | transport uses feedback-specific schema | Contract | pure schema/transport 계약이다 | HTTP source and no video envelope requirement |
| AC 3 | duplicate delivery is append-only | Component | duplicate 보존은 sink 결과로 증명해야 한다 | raw log contains both deliveries or metadata |
| AC 4 | transient Object Storage failure is retryable | Component | delivery boundary와 retry 설정이 결합된다 | failed write is not reported as completed processing |
| AC 5 | raw log preserves Feedback Event fields | Contract | downstream ML input contract다 | raw object JSONL contains required logical fields |
| AC 6 | unsupported or malformed event goes to error sink | Contract + smoke | routing and operator visibility가 함께 필요하다 | expected error object with error code |

### 3.3 자동화 테스트로 다루지 않는 항목

| Spec ref / rule | 자동화하지 않는 이유 | 수동 / 운영 증명 |
| --- | --- | --- |
| Production internal ingress networking | 배포 환경의 service DNS, ingress policy, network policy에 의존한다 | staging deploy smoke와 network policy review |
| Production GCS IAM and retention | 환경 권한과 조직 정책에 의존한다 | staging deploy smoke와 bucket policy review |
| Long-running buffer recovery at production scale | 실제 트래픽, bucket latency, Vector runtime에 의존한다 | staging soak test와 ingress/buffer dashboard 확인 |
| ML dataset dedupe by `event_id` | ML Lifecycle Worker 소유 범위다 | ML Pipeline Execution implementation에서 dataset test |
| Dashboard alert threshold | dashboard/alerting 구현은 후속 운영 범위다 | 운영 runbook 또는 dashboard spec에서 확인 |

### 3.4 테스트 환경과 double
- DB / storage / delivery 설정:
  - Core API producer unit test는 feedback delivery adapter fake, spy, failing double을 사용한다.
  - HTTP source는 local profile에서 loopback 또는 docker compose network로 검증하고, production/staging smoke에서 실제 internal service route를 사용한다.
  - Object Storage는 config contract test에서는 local file sink 또는 fake sink를 사용하고, staging smoke에서 실제 GCS bucket을 사용한다.
  - HTTP seed helper를 둔다.
- 외부 의존성 격리 방식:
  - Core API는 component smoke에서 직접 호출하지 않고 spec-compatible event fixture로 대체한다.
  - producer-integrated smoke는 Core API feedback delivery path 완료 후 추가한다.
  - Vector는 공통 transform/routing 설정을 production/test topology에서 공유한다.
  - local/CI 검증은 GCP credential, 실제 GCS 없이 통과해야 한다.
- Time / async / retry 제어 방식:
  - object partition은 fixed `ingested_at` fixture로 검증한다.
  - retry/backoff는 test profile에서 낮은 값으로 override한다.
  - duplicate delivery는 같은 `event_id`와 다른 delivery metadata fixture로 만든다.
- 필요한 fixture 또는 seed data:
  - valid `schema_version=1` feedback event
  - unsupported `schema_version=2` event
  - missing required field event
  - duplicate `event_id` event pair
  - transient sink failure fixture

### 3.5 검증 명령과 quality gate
- 필수 명령:
  - `cd services/feedback-ingestion-pipeline && vector validate <production config>`
  - `cd services/feedback-ingestion-pipeline && vector validate <test config>`
  - `cd services/feedback-ingestion-pipeline && ./scripts/smoke_valid_event.sh`
  - `cd services/feedback-ingestion-pipeline && ./scripts/smoke_error_event.sh`
  - `docker compose config`
- 병합 전 최소 meaningful check:
  - FIP container starts with required env.
  - valid HTTP event reaches raw object path.
  - unsupported/malformed event reaches error object path.
  - duplicate delivery does not overwrite previous raw log evidence.
  - Object Storage write failure is not acknowledged as completed processing.
  - no raw `query_text` appears in default structured logs.
- 첨부할 증거:
  - Vector config validation output
  - smoke command output
  - raw/error object path examples
  - retry/failure log excerpt without sensitive payload

---

## 4. 전달 리스크와 안전장치

| 리스크 | 영향 | 완화책 | 검증 |
| --- | --- | --- | --- |
| FIP HTTP source가 열리지 않음 | Core API가 event를 전달하지 못한다 | startup smoke와 health/runbook에 HTTP source 확인을 포함한다 | HTTP source smoke |
| Core API service logic이 HTTP client 세부사항에 결합됨 | infra 변경이 비즈니스 로직 변경으로 번진다 | feedback delivery adapter interface 뒤에 HTTP 구현을 둔다 | service unit test with fake/spy/failing adapter |
| internal endpoint auth 또는 network policy 누락 | 내부 ingestion endpoint 오용 가능성이 생긴다 | 내부 네트워크 제한과 service-to-service 보호를 배포 checklist에 둔다 | staging network/security review |
| Schema 또는 transport drift | Core API가 전달한 event를 FIP가 처리하지 못한다 | event schema fixture와 Vector route test로 고정 | contract test |
| Test topology와 production topology drift | local test가 실제 배포 경로를 대변하지 못한다 | transform/routing 설정을 공유하고 source/sink만 환경별로 교체한다 | config contract check |
| Raw log overwrite | duplicate delivery 이력이 사라진다 | batch object key에 unique suffix를 포함하고 event body에 `event_id`를 보존한다 | duplicate smoke |
| Sink 성공 전 delivery success | 일시 장애가 event loss로 이어진다 | Vector source/sink acknowledgement path를 smoke로 검증 | transient failure smoke |
| Error sink 누락 | malformed event가 조용히 유실되거나 raw로 섞인다 | unsupported/malformed route를 error sink로 고정 | error route smoke |
| 민감 정보가 path 또는 기본 로그에 노출 | bucket listing 또는 log 접근으로 질의 내용이 노출된다 | object path와 default logs에서 `query_text`, `user_id`, `project_id` 노출을 제한한다 | log/path review |
| Vector/GCS 지연이 Core API feedback 요청에 전파됨 | 사용자 요청 지연 또는 실패가 증가한다 | Core API timeout, 제한된 retry와 FIP latency metric을 함께 둔다 | producer-integrated failure test |
| Vector/GCS 비용 증가 | 낮은 이벤트량에서도 runtime/storage 비용이 누적된다 | resource request와 lifecycle policy를 MVP 규모로 시작한다 | cost review |

---

## 5. Rollout and Rollback

### 5.1 Rollout 계획
- Migration / schema 단계:
  - FIP 자체 DB migration은 없다.
  - internal HTTP service route가 Core API에서 접근 가능해야 한다.
- Config / secret / infra 변경:
  - `FIP_HTTP_ADDRESS`를 추가한다.
  - `FIP_HTTP_PATH`를 추가한다.
  - `GCS_FEEDBACK_LOG_BUCKET_NAME`을 추가한다.
  - Core API에는 FIP internal endpoint URL과 delivery timeout/retry 설정을 추가한다.
  - FIP service account에는 feedback log bucket write 권한을 부여한다.
  - internal endpoint는 public ingress에 노출하지 않는다.
  - 초기 운영값은 아래 config로 시작한다.
    - `FIP_SINK_BATCH_MAX_EVENTS=100`
    - `FIP_SINK_FLUSH_TIMEOUT_SEC=10`
    - `FIP_SINK_TIMEOUT_SEC=30`
    - `FIP_RETRY_MAX_ATTEMPTS=5`
    - `FIP_RETRY_INITIAL_BACKOFF_SEC=1`
    - `FIP_RETRY_MAX_BACKOFF_SEC=60`
    - `FIP_DISK_BUFFER_MAX_SIZE_MB=512`
- Backward / forward compatibility 고려사항:
  - Core API가 아직 feedback delivery를 활성화하지 않아도 FIP는 idle 상태로 배포 가능해야 한다.
  - `schema_version=1`만 raw로 처리하고, 새 major version은 error sink로 보낸다.
  - object layout은 ML Lifecycle Worker가 batch prefix scan에 사용할 계약이므로 배포 후 임의 변경하지 않는다.
  - video-processing과 control-message PGMQ flow는 이 rollout에서 변경하지 않는다.
- Rollout 중 볼 monitoring signal:
  - HTTP ingress request count, rejected request count, delivery latency
  - raw log write success/failure count
  - error sink count
  - retry count and buffer utilization
- 배포 후 점검:
  - valid seed event가 raw 경로에 저장된다.
  - malformed seed event가 error 경로에 저장된다.
  - FIP idle 상태에서 container가 crash loop 없이 유지된다.
  - 기본 로그에 raw `query_text`가 출력되지 않는다.

### 5.2 Rollback 계획
- App rollback:
  - FIP service를 중지하거나 이전 image로 되돌린다.
  - Core API feedback delivery가 활성화되어 있다면 delivery pause 또는 feature flag 비활성화를 함께 수행한다.
- Data rollback 또는 safe-forward plan:
  - 이미 저장된 raw/error logs는 append-only 운영 기록으로 삭제하지 않는다.
  - 잘못된 object layout으로 저장된 경우 새 prefix로 safe-forward하고, 잘못된 prefix는 quarantine marker 또는 운영 메모로 격리한다.
- Delivery compatibility fallback:
  - schema mismatch가 발생하면 Core API delivery를 중단하고 error sink sample로 payload 차이를 확인한다.
  - PGMQ video-processing flow는 fallback 대상이 아니며 그대로 유지한다.
- Partial deployment recovery:
  - FIP만 먼저 배포된 경우에는 HTTP ingress idle 상태로 대기한다.
  - Core API가 먼저 delivery를 시작했지만 FIP가 실패하면 Core API delivery failure metric과 FIP ingress 상태를 기준으로 복구 우선순위를 판단한다.
  - Object Storage 장애 중에는 retry/buffer가 허용하는 범위에서 대기하고, 장기 장애면 Core API delivery pause를 우선 검토한다.

---

## 6. 완료 체크리스트

- [ ] 모든 계획된 workstream이 target SPEC 또는 ADR-007에 매핑된다.
- [ ] `services/feedback-ingestion-pipeline/`에 Vector runtime 설정과 실행 문서가 있다.
- [ ] FIP는 Vector `http_server` source를 사용한다.
- [ ] Core API service logic은 feedback delivery adapter interface에만 의존하고 HTTP client 세부사항을 직접 호출하지 않는다.
- [ ] HTTP ingress address/path config가 env로 주입된다.
- [ ] 별도 feedback log bucket 설정 `GCS_FEEDBACK_LOG_BUCKET_NAME`을 사용한다.
- [ ] Vector production/test topology는 같은 transform/routing contract를 공유한다.
- [ ] local/CI test는 실제 GCS, GCP credential 없이 통과한다.
- [ ] raw object key layout이 append-only duplicate 보존을 지원한다.
- [ ] error object key layout이 unsupported/malformed event 원본을 보존한다.
- [ ] 정상 event와 error event routing이 fixture로 검증된다.
- [ ] Object Storage transient failure가 event loss로 이어지지 않는 증거가 있다.
- [ ] HTTP ingress latency, rejected request, raw/error write count, retry, buffer signal을 확인할 수 있다.
- [ ] 기본 로그와 object path에 raw `query_text`가 노출되지 않는다.
- [ ] rollout / rollback 단계에 compatibility 가정과 monitoring signal이 포함되어 있다.
- [ ] Target SPEC과 Core API 관련 문서의 ADR-007 반영 필요성이 기록되어 있다.
