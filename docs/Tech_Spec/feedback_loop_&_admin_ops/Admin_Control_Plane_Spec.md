# [Admin Control Plane] SPEC

**메타 정보 (Meta)**
- Component ID: `admin-control-plane`
- SOT: `docs/system-design.md`
- Related docs:
  - `docs/PRD.md`
  - `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/ML_Pipeline_Execution_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Model_Release_and_Reindex_Spec.md`
- Status: Draft

---

## 1. 목적과 범위 (Purpose and Scope)

### 1.1 한 줄 요약
- Admin Control Plane은 운영자가 video 처리 상태와 ML 운영 상태를 조회하고, 허용된 운영 액션만 기존 async 계약에 맞춰 안전하게 요청할 수 있게 하는 admin 전용 HTTP 표면이다.

### 1.2 책임 경계
- In scope:
  - 운영자 권한 검증을 거친 admin 전용 조회/액션 HTTP 계약
  - 소유권 제한 없이 `Video`, `MLPipelineRun`, `ModelRelease` 상태 조회
  - 실패 video 강제 재처리 요청
  - 임의 video 삭제 절차 시작 요청
  - ML 파이프라인 수동 재트리거 요청
  - rollback 요청의 동기 precondition 확인과 control message 발행
- Out of scope:
  - dashboard 화면 구성, polling 주기, 시각화 방식
  - raw log / metrics 저장소의 검색 API 또는 프록시
  - `MLPipelineRun` 내부 상태 전이 의미
  - `ModelRelease` cutover / rollback restore 내부 절차
  - 평가 데이터셋 관리, 모델 설정 관리, 모델 파일 배포 절차
- Upstream dependencies:
  - JWT claim의 admin role
  - 외부 observability 인프라의 로그/메트릭 수집 체계
- Downstream consumers:
  - Admin Dashboard
  - Core API Server 내부 user/admin 경로 구현
  - Message Broker의 `PREPROCESS_REQUEST`, `DELETE_REQUEST`, `TRAINING_REQUEST`, `ROLLBACK_REQUEST` 소비자

### 간단한 흐름 (Simple Flow)
1. 운영자가 admin 경로로 조회 또는 액션 요청을 보낸다.
2. Admin Control Plane은 JWT claim의 role을 기준으로 운영자 권한을 검증한다.
3. 조회 요청은 shared SOT인 `Video`, `MLPipelineRun`, `ModelRelease`를 읽어 응답한다.
4. 액션 요청은 현재 상태를 확인한 뒤 허용된 경우에만 shared SOT를 갱신하거나 기존 control message를 발행한다.
5. 실제 재처리, 삭제 연쇄, ML 실행 요청, rollback restore는 후속 컴포넌트가 수행한다.

### 1.3 기술 스택 선택
| 영역 (Area) | 선택안 (Choice) | 왜 이 선택인가 |
| --- | --- | --- |
| Runtime / framework | Core API Server의 admin 전용 FastAPI surface | system design이 admin 기능을 Core API Server 경로로 둔다 |
| Storage / DB | Metadata DB (`Video`, `MLPipelineRun`, `ModelRelease`) | 운영 조회와 동기 precondition 판단의 SOT가 이미 여기에 있다 |
| Messaging / async | 기존 broker control message 발행 | 재처리/삭제/학습/rollback은 기존 async 계약을 그대로 사용한다 |
| Key libraries | Core API와 동일한 JWT 검증, DB session, broker adapter 계층 | user path와 다른 구현 체계를 새로 만들지 않고 auth/error/trace semantics를 재사용한다 |

---

## 2. 계약 (Contracts)

### 2.1 외부 인터페이스

#### 외부 진입 인터페이스
| Interface | Method / Trigger | Input summary | Output summary | Auth / tenancy | Notes |
| --- | --- | --- | --- | --- | --- |
| `/api/v1/admin/videos` | `GET` | `status?`, `failed_stage?`, `user_id?`, `cursor?`, `limit?` | `200 OK`, `items[]`, `next_cursor` | admin role 필수, user ownership filter 없음 | 정렬은 `(updated_at DESC, id DESC)` |
| `/api/v1/admin/videos/{video_id}` | `GET` | path `video_id` | `200 OK`, 단일 video 상태와 실패 요약 | admin role 필수 | raw log 본문은 포함하지 않는다 |
| `/api/v1/admin/videos/{video_id}/retry` | `POST` | path `video_id` | `202 Accepted`, `{"video_id","status":"PENDING","retry_requested":true}` | admin role 필수 | `FAILED`일 때만 허용 |
| `/api/v1/admin/videos/{video_id}` | `DELETE` | path `video_id` | `202 Accepted`, `{"video_id","delete_requested":true}` | admin role 필수 | 삭제 절차 시작 요청만 수행한다 |
| `/api/v1/admin/ml-pipeline-runs` | `GET` | `status?`, `failure_type?`, `cursor?`, `limit?` | `200 OK`, `items[]`, `next_cursor` | admin role 필수 | 정렬은 `(created_at DESC, id DESC)` |
| `/api/v1/admin/ml-pipeline-runs/{run_id}` | `GET` | path `run_id` | `200 OK`, 단일 run 상태와 실패 요약 | admin role 필수 | 평가 상세 artifact 본문은 포함하지 않는다 |
| `/api/v1/admin/ml-pipeline-runs/retrigger` | `POST` | empty body | `202 Accepted`, `{"retrigger_requested":true}` | admin role 필수 | `TRAINING_REQUEST` 발행 요청 |
| `/api/v1/admin/model-release` | `GET` | empty | `200 OK`, 현재 serving 조합과 rollback 가능 여부 | admin role 필수 | `ModelRelease` 단일 레코드 조회 |
| `/api/v1/admin/model-release/rollback` | `POST` | empty body | `202 Accepted`, `{"rollback_requested":true}` | admin role 필수 | precondition을 통과한 경우만 `ROLLBACK_REQUEST` 발행 |

Response shape rules:
- `GET /api/v1/admin/videos`의 각 item은 최소 `video_id`, `user_id`, `status`, `failed_stage`, `search_serving_state`, `updated_at`를 포함한다.
- `GET /api/v1/admin/videos/{video_id}`는 목록 item 필드에 더해 최소 `title`, `category`, `input_type`, `source_url`, `created_at`를 포함한다.
- `GET /api/v1/admin/ml-pipeline-runs`의 각 item은 최소 `run_id`, `status`, `failed_stage`, `failure_type`, `dataset_version`, `candidate_model_version`, `created_at`, `updated_at`를 포함한다.
- `GET /api/v1/admin/ml-pipeline-runs/{run_id}`는 목록 item 필드에 더해 최소 `failure_reason`, `evaluation_id`, `candidate_index_name`, `cutover_time`를 포함한다.
- `GET /api/v1/admin/model-release`는 최소 `release_status`, `active_model_version`, `active_index_name`, `previous_model_version`, `previous_index_name`, `candidate_model_version`, `candidate_index_name`, `rollback_snapshot_captured_at`, `rollback_available`를 포함한다.
- 위에 열거한 최소 필드는 응답에서 항상 같은 key로 제공한다. 값이 현재 상태에 없으면 key를 생략하지 않고 `null`로 표현 한다.
- `source_url`은 `input_type=LOCAL_FILE`이면 `null`이다.
- `failed_stage`, `failure_type`, `failure_reason`, `evaluation_id`, `candidate_model_version`, `candidate_index_name`, `cutover_time`는 해당 상태에서 값이 없으면 `null`이다.
- `previous_model_version`, `previous_index_name`, `candidate_model_version`, `candidate_index_name`, `rollback_snapshot_captured_at`는 현재 release 상태에서 값이 없으면 `null`이다.
- `rollback_available`은 `release_status=STABLE`이고 rollback snapshot 포인터가 모두 존재할 때만 `true`다.
- list filter는 exact-match만 지원하며, 여러 filter가 함께 주어지면 AND로 적용한다.
- `cursor`는 정렬 키를 담는 opaque token이며, video list는 `(updated_at,id)`, run list는 `(created_at,id)`를 기준으로 한다.
- `cursor`는 생성 당시의 filter set에 바인딩된다. filter를 바꾼 뒤 이전 `cursor`를 재사용하면 `400 INVALID_ARGUMENT`로 거부한다.

#### 메시지 / 이벤트 계약 (해당 시)
- Queue / topic:
  - `PREPROCESS_REQUEST`
  - `DELETE_REQUEST`
  - `TRAINING_REQUEST`
  - `ROLLBACK_REQUEST`
- Producer / consumer responsibility:
  - Producer(Admin Control Plane):
    - video retry 승인 시 `PREPROCESS_REQUEST`를 발행한다.
    - video delete 승인 시 `DELETE_REQUEST`를 발행한다.
    - ML retrigger 요청 시 `TRAINING_REQUEST`를 발행한다.
    - rollback precondition 통과 시 `ROLLBACK_REQUEST`를 발행한다.
  - Consumer:
    - video worker는 `PREPROCESS_REQUEST`, `DELETE_REQUEST`를 처리한다.
    - ML Pipeline Execution은 `TRAINING_REQUEST`를 받아 활성 실행 1개와 최신 대기 실행 1개만 유지하도록 처리한다.
    - Model Release and Reindex는 `ROLLBACK_REQUEST`를 rollback 규칙에 따라 처리한다.
- Delivery semantics: at-least-once
- Payload versioning rules:
  - `PREPROCESS_REQUEST`, `DELETE_REQUEST`는 `docs/system-design.md` 3.12의 video-processing shared envelope를 따른다.
  - `TRAINING_REQUEST`, `ROLLBACK_REQUEST`는 같은 section의 control-message schema를 따른다.
  - Admin Control Plane은 새 payload shape를 정의하지 않는다.

```json
{
  "message_type": "ROLLBACK_REQUEST",
  "payload_version": "v1",
  "trace_id": "UUID4",
  "attempt": 1,
  "issued_at": "ISO8601_TIMESTAMP"
}
```

#### 외부 서비스 계약 (해당 시)
| Dependency | Used for | Required behavior / assumption | Failure impact |
| --- | --- | --- | --- |
| Metadata DB `Video` | video 상태 조회, retry/delete precondition 확인 | `status`, `failed_stage`, `search_serving_state`, `updated_at`를 최신 기준으로 읽고 갱신할 수 있어야 한다 | 잘못된 운영 판단 또는 잘못된 액션 허용으로 이어진다 |
| Metadata DB `MLPipelineRun` | run 목록/상세 조회 | `status`, `failed_stage`, `failure_type`, `failure_reason`, `dataset_version`, `candidate_model_version`를 읽을 수 있어야 한다 | 운영자가 실패 상태를 정확히 파악할 수 없다 |
| Metadata DB `ModelRelease` | serving 상태 조회, rollback precondition 확인 | `release_status`와 rollback snapshot 포인터를 동기 조회할 수 있어야 한다 | invalid rollback 요청을 동기 차단할 수 없다 |
| Message Broker | async control message 발행 | 동일 `trace_id`를 포함해 메시지를 발행할 수 있어야 한다 | 액션은 접수되었지만 후속 실행이 시작되지 않는다 |
| 외부 observability 인프라 | trace 기반 장애 분석 | backend 로그/메트릭이 `trace_id`와 자원 식별자를 기준으로 조회 가능해야 한다 | Admin Dashboard의 심화 장애 분석은 admin API만으로 완결되지 않는다 |

### 2.2 데이터 계약

#### 소유 데이터 (이 컴포넌트가 SOT인 경우)
- 이 컴포넌트는 신규 SOT 엔터티를 만들지 않는다.
- 이 컴포넌트가 소유하는 것은 admin-only HTTP contract와 그에 따른 허용 액션 규칙이다.

#### 참조 데이터 (다른 SOT를 읽는 경우)
| Source owner | Entity / table | Fields relied on | Read-only assumptions |
| --- | --- | --- | --- |
| Core video lifecycle | `Video` | `id`, `user_id`, `status`, `failed_stage`, `search_serving_state`, `created_at`, `updated_at` | retry 가능 여부는 `status=FAILED`로만 판단한다 |
| ML Pipeline Execution | `MLPipelineRun` | `id`, `status`, `failed_stage`, `failure_type`, `failure_reason`, `dataset_version`, `candidate_model_version`, `evaluation_id`, `created_at`, `updated_at` | 활성 실행 1개와 최신 대기 실행 1개 규칙은 ML Pipeline Execution의 SOT와 이를 집행하는 ML Lifecycle Worker가 강제한다. |
| Model Release and Reindex | `ModelRelease` | `release_status`, `active_model_version`, `active_index_name`, `previous_model_version`, `previous_index_name`, `rollback_snapshot_active_model_version`, `rollback_snapshot_active_index_name`, `rollback_snapshot_captured_at`, `switched_at` | rollback 허용 여부는 `release_status`와 snapshot 포인터 존재로 판단한다 |

### 2.3 상태 및 비즈니스 규칙
- 항상 유지되어야 하는 불변조건:
  - admin 경로는 JWT claim의 role이 운영자 권한일 때만 접근할 수 있다.
  - admin 경로는 user ownership 제한을 우회할 수 있지만, user 경로의 tenancy 규칙을 바꾸지 않는다.
  - video retry는 `Video.status=FAILED`일 때만 허용된다.
  - rollback 요청은 `ModelRelease.release_status=STABLE`이고 rollback snapshot 포인터가 존재할 때만 허용된다.
  - admin API는 raw log/metrics 저장소를 직접 프록시하지 않고, domain 상태와 control action만 제공한다.
- 거부되어야 하는 전이 / invalid condition:
  - `FAILED`가 아닌 video에 retry를 요청하는 동작
  - 존재하지 않는 `video_id` 또는 `run_id`에 대한 조회/액션
  - rollback snapshot이 없거나 `release_status!=STABLE`인 상태에서 rollback을 요청하는 동작
  - non-admin 사용자가 admin 경로에 접근하는 동작
- Idempotency rule:
  - 이미 `DELETING`인 video에 대한 admin delete는 `202 Accepted`로 처리하되 새 `DELETE_REQUEST`를 추가 발행하지 않는다.
  - 중복 `TRAINING_REQUEST`가 들어오면 downstream은 활성 실행 1개와 최신 대기 실행 1개만 남도록 정리한다.
  - 중복 `ROLLBACK_REQUEST`는 admin path가 precondition을 먼저 검사하고, downstream은 이미 같은 rollback 요청이 처리 중이거나 완료된 경우 추가 변경 없이 그대로 유지할 수 있어야 한다.
- Multi-tenant / authorization rule:
  - admin path는 소유권 확인 대신 운영자 role만 본다.
  - 응답에는 운영 판단에 필요한 shared SOT 필드만 포함하며, 민감 원문 로그나 평가 artifact 본문은 포함하지 않는다.

| From | To | Trigger | Guard / rule | Required side effects |
| --- | --- | --- | --- | --- |
| `Video.status=FAILED` | `PENDING` | admin retry 요청 | target video 존재, admin role 확인 | `PREPROCESS_REQUEST` 발행 |
| `Video.status!=DELETING` | `DELETING` | admin delete 요청 | target video 존재, admin role 확인 | `DELETE_REQUEST` 발행 |
| `Video.status=DELETING` | `DELETING` | admin delete 재요청 | 중복 삭제 요청은 허용하되 추가 publish 없음 | 기존 delete 절차 유지 |
| `ModelRelease.release_status=STABLE` | direct state change 없음 | admin rollback 요청 | rollback snapshot 포인터 존재, admin role 확인 | `ROLLBACK_REQUEST` 발행 |

### 2.4 한계와 운영 제약
- Performance / latency target:
  - admin 조회는 운영 판단용 동기 경로이며, dashboard polling에 사용할 수 있을 정도로 안정적으로 응답해야 한다.
- Throughput / rate / concurrency limits:
  - 목록 조회 pagination은 기본 20, 최대 50으로 제한한다.
  - 동시에 들어온 운영 액션의 최종 적용 순서와 충돌 해결은 shared SOT(Video, MLPipelineRun, ModelRelease)와 후속 처리 컴포넌트가 담당한다.
- Payload / file size / pagination limits:
  - admin 조회 응답은 상태 요약과 식별자만 포함하며, 대용량 raw log/metrics payload는 포함하지 않는다.
- Timeout / TTL / retry constraints:
  - broker publish 재시도 횟수와 backoff 값은 Core API 운영 정책을 따른다.
  - `TRAINING_REQUEST`와 `ROLLBACK_REQUEST`의 최종 실행 여부는 동기 응답이 아니라 downstream 처리 결과로 확정된다.
- Security / privacy constraints:
  - admin 경로는 모든 사용자 자원 메타데이터를 읽을 수 있으므로 내부 운영 경로로만 노출되어야 한다.
  - `query_text`, raw transcript, raw logs, 평가 상세 artifact 원문은 이 API의 기본 응답에 포함하지 않는다.

### 2.5 에러 계약
| Surface | Condition | Code / status | Retryable | Notes |
| --- | --- | --- | --- | --- |
| 모든 admin 경로 | JWT 누락 또는 검증 실패 | `401 UNAUTHENTICATED` | N | user path와 같은 인증 실패 의미 |
| 모든 admin 경로 | admin role 부재 | `403 FORBIDDEN` | N | ownership이 아니라 role 기준 거부 |
| list/query | 잘못된 cursor 또는 filter 값 | `400 INVALID_ARGUMENT` | N | pagination/filter contract 위반 |
| video 조회/액션 | `video_id` 미존재 | `404 NOT_FOUND` | N |  |
| run 조회 | `run_id` 미존재 | `404 NOT_FOUND` | N |  |
| retry | `Video.status!=FAILED` | `409 CONFLICT` | N | 강제 재처리는 실패 건만 허용 |
| rollback | `release_status!=STABLE` 또는 snapshot 포인터 부재 | `409 CONFLICT` | N | 동기 precondition 차단 |
| action path | DB 갱신 또는 broker publish 최종 실패 | `500 INTERNAL_ERROR` | Y | operator는 같은 요청을 재시도할 수 있다 |

- 표준 에러 응답 형태:
```json
{"code":"ERROR_CODE","message":"human-readable summary","trace_id":"UUID4"}
```

---

## 3. 관측성과 운영 (Observability and Operations)

- Required log fields:
  - `trace_id`
  - `operator_user_id`
  - `admin_action`
  - `target_type`
  - `target_id`
  - `result`
  - `http_status`
- Key metrics / alerts worth tracking:
  - `admin_request_count{action,result}`
  - `admin_retry_request_count`
  - `admin_delete_request_count`
  - `admin_ml_retrigger_request_count`
  - `admin_rollback_request_count`
  - `admin_precondition_conflict_count{action}`
  - `admin_broker_publish_failure_count{message_type}`
- Trace / correlation propagation rule:
  - admin 요청에서 생성하거나 전달받은 `trace_id`는 응답, 구조화 로그, 후속 broker message에 동일하게 유지한다.
- Reconciliation / cleanup requirement (if any):
  - 별도 admin 전용 정리 배치는 두지 않는다.
  - delete/retry/retrigger/rollback의 최종 상태 정합성은 shared SOT와 downstream consumer 이력으로 재구성 가능해야 한다.

---

## 4. 인수 기준 (Acceptance Criteria)

### 4.1 반드시 통과해야 하는 시나리오
- [ ] admin role을 가진 운영자는 소유권 제한 없이 `Video`, `MLPipelineRun`, `ModelRelease` 상태를 조회할 수 있다.
- [ ] admin retry는 `Video.status=FAILED`인 대상에만 허용되고, 승인 시 `PENDING` 전환과 `PREPROCESS_REQUEST` 발행이 함께 일어난다.
- [ ] admin delete는 대상 video를 `DELETING`으로 전환하고 `DELETE_REQUEST`를 발행하며, 이미 `DELETING`이면 중복 publish 없이 수용된다.
- [ ] admin ML retrigger 요청은 `TRAINING_REQUEST`를 발행하고, 중복 요청이 와도 downstream의 활성 실행/최신 대기 실행 규칙을 깨지 않는다.
- [ ] admin rollback 요청은 `release_status=STABLE`과 rollback snapshot 존재를 동기 검증한 뒤에만 `ROLLBACK_REQUEST`를 발행한다.
- [ ] 모든 admin 액션은 `trace_id`, `operator_user_id`, action 결과를 구조화 로그에 남기며, 후속 async message와 상관관계를 유지한다.

### 4.2 비목표 / 보류 항목
- Dashboard UI layout, widget 구성, polling 주기
- raw log / metrics / Grafana query proxy
- 평가 데이터셋 CRUD와 모델 설정 관리
- rollback restore 이후의 재임베딩 진행도 표현 방식

---
