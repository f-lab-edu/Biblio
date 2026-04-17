# [Model Release and Reindex] SPEC

**메타 정보 (Meta)**
- Component ID: `model-release-and-reindex`
- SOT: `docs/system-design.md` (이 SPEC은 system design SOT와 일관되어야 한다)
- Related docs:
  - `docs/PRD.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/ML_Pipeline_Execution_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Managed_Embedding_Endpoint_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`
- Status: Draft

---

## 1. 목적과 범위 (Purpose and Scope)

### 1.1 한 줄 요약
- Model Release and Reindex는 후보 임베딩 모델의 candidate 재색인, 서빙 전환, 마지막으로 정상 서빙되던 active 조합만 담는 rollback snapshot 복구, rollback 중 검색 범위 제외, 복구 후 재편입을 관리하는 릴리스 제어 컴포넌트다.

### 1.2 책임 경계
- In scope:
  - 현재 서빙 조합과 전환 상태는 릴리스 레코드(`ModelRelease`)를 SOT로 관리한다.
  - 평가 PASS 후보에 대해서는 candidate index를 만들고, 서빙 전환 전까지 새로 READY가 되는 데이터만 우선 반영한다.
  - 서빙 전환 직전의 마지막 정상 active 조합만 rollback snapshot으로 저장하고, 전환 후에는 active/previous 2세대만 서빙 대상으로 유지한다.
  - rollback 요청(`ROLLBACK_REQUEST`)을 받으면 rollback 준비 상태를 열고 snapshot 기반 복구를 수행한다.
  - rollback 중 영향을 받는 영상은 검색에서 제외하고, 복구 완료분부터 다시 검색 범위에 편입한다. 이 상태 표시는 `Video.search_serving_state`로 관리한다.
  - 배포용 재색인(`CANDIDATE_REINDEXING`) 상태에서는 새로 READY가 되는 데이터를 active index와 candidate index에 함께 기록한다.
  - rollback restore의 복원 대상은 snapshot이 가리키는 active 조합으로 한정한다.
- Out of scope:
  - 모델 학습, 평가, `MLPipelineRun` 활성 실행 제어
  - 검색 랭킹, RRF, LLM 응답 생성
  - 공개 Admin HTTP API
  - full immediate reindex
  - retired/problem index의 최종 물리 삭제 시점
  - Search Service의 사용자 고지 문구 포맷
- Upstream dependencies:
  - 같은 Worker 내부 ML Pipeline Execution이 남긴 `READY_FOR_RELEASE` run 상태
  - Admin control path가 발행한 `ROLLBACK_REQUEST` control message
  - Managed Embedding Endpoint readiness
  - Metadata DB의 `ModelRelease`, `Video`, `Chunk`
- Downstream consumers:
  - Search Service
  - Media & AI Pipeline Worker online ingest 경로
  - Admin Dashboard의 상태 조회 경로

### 간단한 흐름 (Simple Flow)
1. 평가 `PASS` 후보가 전달되면 `ModelRelease`를 `CANDIDATE_REINDEXING`으로 전환하고 candidate index를 생성한다.
2. CANDIDATE_REINDEXING 상태에 들어간 뒤 처리 완료(READY)된 새 데이터는 현재 서빙용 active index와 후보용 candidate index에 함께 기록한다.
3. 새 모델이 실제 서빙 가능한 상태이고, 전환 기준 시점까지의 데이터가 새 인덱스에 모두 반영된 것이 확인되면, 현재 서빙 상태를 snapshot으로 저장한 뒤 candidate를 active로 전환한다.
4. 서빙 전환 이후 원래 active 였던 모델은 previous로 남기고, previous보다 오래된 모델로 색인한 데이터는 최신 active 기준으로 점진 재임베딩한다.
5. 롤백 요청이 들어오면, 영향받는 영상은 우선 검색 대상에서 제외한다. 그 후 미리 저장해 둔 정상 서빙 상태가 다시 준비되면, 시스템을 그 상태로 복구한다.
6. 복구 중 제외된 영상은 Search Service의 검색 범위에서 빠지며, 복구 완료 후 다시 검색 범위에 합류한다.


---

## 2. 계약 (Contracts)

### 2.1 외부 인터페이스

#### 외부 진입 인터페이스
| Interface | Method / Trigger | Input summary | Output summary | Auth / tenancy | Notes |
| --- | --- | --- | --- | --- | --- |
| 평가 `PASS` 수신 | 같은 Worker 내부 직접 호출 | `run_id`, `trace_id` | 후보 재색인 시작 또는 run 실패 기록 | internal only | 이 컴포넌트는 `READY_FOR_RELEASE` 상태의 run을 다음 단계 시작 조건으로 사용한다. 실행 문맥은 `MLPipelineRun`, `ModelEvaluation`, `ModelRelease`를 다시 읽어 복원한다 |
| 롤백 요청 수신 | `ROLLBACK_REQUEST` control message 수신 | `message_type`, `payload_version`, `trace_id`, `attempt`, `issued_at` | rollback 준비 시작 또는 invalid state 기록 | admin 권한 검증은 upstream 책임 | rollback 대상 선택은 `ModelRelease` snapshot 기준이다 |

#### 메시지 / 이벤트 계약 (해당 시)
- Queue / topic: rollback control channel (`ROLLBACK_REQUEST`)
- 평가 `PASS` hand off는 broker나 polling을 쓰지 않고 같은 Worker 내부 직접 호출로 처리한다.
- Producer / consumer responsibility:
  - Producer: admin control path가 rollback control message를 발행한다.
  - Consumer: Consumer는 되돌릴 정상 모델과 인덱스가 실제로 다시 준비됐는지 확인한 뒤, 롤백 상태 전환을 수행한다.
- Delivery semantics: at-least-once
- Payload versioning rules:
  - ROLLBACK_REQUEST는 video-processing shared envelope가 아니라 별도의 control-message schema를 사용한다.
  - control-message schema에는 `message_type`, `payload_version`, `trace_id`, `attempt`, `issued_at`만 포함한다.
  - 지원하지 않는 `payload_version`은 정상 rollback 실행으로 처리하지 않는다.
- Required control-message fields:
  - `message_type`
  - `payload_version`
  - `trace_id`
  - `attempt`
  - `issued_at`

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
| Metadata DB `ModelRelease` | current serving SOT | active/previous/candidate 및 rollback snapshot active field를 원자적으로 갱신할 수 있어야 한다 | 서빙 전환 또는 rollback restore가 불가능하다 |
| Metadata DB `Video` | search exclusion gate | `search_serving_state`는 `SERVABLE | ROLLBACK_EXCLUDED`만 허용한다 | rollback 중 검색 제외/복귀 계약이 깨진다 |
| Metadata DB `Chunk` | rollback 영향 범위 식별 | `embedding_model_version`으로 문제 모델 데이터를 식별할 수 있어야 한다 | affected video 선별이 불가능하다 |
| Managed Embedding Endpoint | candidate / rollback target readiness | target model이 실제로 로드되고 readiness를 통과해야만 release record를 전환할 수 있다 | 상태는 유지되고 전환은 차단된다 |
| Vector Store | candidate / active / previous index 유지 | candidate index는 staging 전용이며 end-user search 대상이 아니다 | 검색 또는 reindex 정합성이 깨진다 |
| Index Snapshot Files | rollback snapshot restore | rollback snapshot active index 식별자에 대응하는 인덱스 스냅샷을 복원할 수 있어야 한다 | rollback restore가 불가능하다 |
| Media & AI Pipeline Worker | online ingest 반영 | `CANDIDATE_REINDEXING`이면 active/candidate dual-write를 따라야 하며, rollback 후 영향 영상 재임베딩을 수행할 수 있어야 한다 | latest-only 반영과 rollback 복구 계약이 깨진다 |

### 2.2 데이터 계약

#### 소유 데이터 (이 컴포넌트가 SOT인 경우)
| Entity / table | Purpose | Key fields / invariants | Notes |
| --- | --- | --- | --- |
| `ModelRelease` | 현재 서빙 조합과 전환 상태의 SOT | `release_status`, `active_model_version`, `active_index_name`, `previous_model_version`, `previous_index_name`, `candidate_model_version`, `candidate_index_name`, `rollback_snapshot_active_model_version`, `rollback_snapshot_active_index_name`, `rollback_snapshot_captured_at`, `candidate_ready_at`, `switched_at` | `release_status` 허용값은 정확히 `STABLE`, `CANDIDATE_REINDEXING`, `ROLLBACK_PREPARING`이다 |
| `Video.search_serving_state` (shared field) | rollback 중 일시 검색 제외 | 허용값은 `SERVABLE`, `ROLLBACK_EXCLUDED`다 | Search Service는 `READY + SERVABLE`인 영상만 검색에 포함한다 |
| `MLPipelineRun.candidate_index_name`, `MLPipelineRun.cutover_time` (shared fields) | 평가 PASS hand off 추적 | `candidate_index_name`은 candidate index 식별자다. `cutover_time`은 서빙 전환 전에 candidate 반영이 끝나 있어야 하는 데이터 범위를 가르는 기준 시각이다. 이 값은 release/reindex 단계가 서빙 전환 직전에 고정한다. | 나머지 run 제어는 `ML_Pipeline_Execution_Spec.md`가 소유한다 |

#### 참조 데이터 (다른 SOT를 읽는 경우)
| Source owner | Entity / table | Fields relied on | Read-only assumptions |
| --- | --- | --- | --- |
| ML Pipeline Execution | `MLPipelineRun` | `id`, `status`, `candidate_model_version`, `dataset_version`, `evaluation_id`, `cutover_time` | `READY_FOR_RELEASE` 상태의 run만 이 컴포넌트로 들어오며, 수신 시 공유 SOT를 다시 읽어 기준 상태를 복원한다 |
| Search Service | serving path | active/previous index 조합, `READY + SERVABLE` gate | candidate index는 검색 대상이 아니며, `ROLLBACK_EXCLUDED` 영상이 있어도 다른 검색 가능 영상으로 검색을 계속 제공할 수 있다 |
| Managed Embedding Endpoint | runtime state | active/previous/candidate readiness | readiness가 통과한 모델만 release record에 반영한다 |

### 2.3 상태 및 비즈니스 규칙
- 항상 유지되어야 하는 불변조건:
  - `release_status`는 `STABLE | CANDIDATE_REINDEXING | ROLLBACK_PREPARING`만 허용한다.
  - end-user search는 active/previous 2세대까지만 사용한다.
  - candidate index는 검증/재색인 전용이며 end-user search 표면에 노출되지 않는다.
  - 서빙 전환 전 반영 대상은 `CANDIDATE_REINDEXING`이 열린 뒤부터 `cutover_time`까지 dual-write gate에 진입한 online ingest 작업 집합이다.
  - previous보다 더 오래된 데이터는 서빙 전환 이후 최신 active 모델로 점진 재임베딩한다.
  - rollback 중 영향 영상은 현재 문제 active 모델 버전과 같은 `Chunk.embedding_model_version`을 가진 청크가 1개 이상 있는 `Video`다.
  - `ROLLBACK_EXCLUDED` 영상은 restored active 모델 버전 기준으로 재임베딩과 vector 반영이 끝난 뒤에만 다시 `SERVABLE`이 된다.
  - rollback 중 신규 업로드 요청과 일반 ingest는 계속 처리될 수 있다. rollback은 영향 영상 검색 제외와 snapshot restore를 우선 수행한다.
  - rollback snapshot은 cutover 직전의 last known-good active model/index만 저장한다. `previous_model_version` / `previous_index_name`은 snapshot에 포함하지 않으며 restore에도 사용하지 않는다.
  - rollback restore는 서빙 상태를 마지막 정상 상태로 되돌리는 책임이다. 영향 영상의 재임베딩과 검색 재편입은 그 뒤에 이어지는 후속 복구 책임이다.
- 서빙 전환 전 반영 기준:
  - `CANDIDATE_REINDEXING`이 시작되면 online ingest는 active index와 candidate index에 함께 기록된다.
  - candidate readiness가 확인되면, 서빙 전환 직전에 이 컴포넌트가 기준 시각 `MLPipelineRun.cutover_time`을 고정한다.
  - `cutover_time` 이전에 들어온 데이터가 candidate index에 모두 반영된 것이 확인된 뒤에만 서빙을 전환한다.
- 릴리스·재색인 판단은 호출자가 들고 온 메모리 문맥이 아니라 공유 SOT에 기록된 상태를 기준으로 수행한다.
- snapshot capture / restore 규칙:
  - snapshot capture는 서빙 전환 직전에 수행한다.
  - capture 시 `active_model_version`, `active_index_name`만 각각 `rollback_snapshot_active_model_version`, `rollback_snapshot_active_index_name`으로 복사하고 `rollback_snapshot_captured_at`를 기록한다.
  - rollback snapshot 필드는 실제 모델/인덱스 본체가 아니라 복원 대상 active model/index를 가리키는 포인터 메타데이터다.
  - 서빙 전환 성공 시 `candidate_model_version`, `candidate_index_name`, `candidate_ready_at`는 null로 초기화한다.
  - rollback restore 시 rollback snapshot이 가리키는 active model/index가 준비된 뒤 현재 serving 필드의 active model/index만 복원하고, `previous_model_version` / `previous_index_name`은 snapshot에서 복원하지 않는다. `candidate_model_version`, `candidate_index_name`, `candidate_ready_at`는 null로 초기화한다.
- dual-write / visibility 규칙:
  - dual-write는 `release_status=CANDIDATE_REINDEXING`이고 candidate fields가 non-null일 때만 시작된다.
  - dual-write는 `release_status`가 `CANDIDATE_REINDEXING`을 벗어나거나 candidate fields가 cleared 되면 즉시 중단된다.
  - candidate index는 서빙 전환 전까지 staging 전용이며 Search Service의 user-facing query surface에 포함되면 안 된다.
- 거부되어야 하는 전이 / invalid condition:
  - candidate readiness 또는 서빙 전환 전 반영 기준 충족 전에 서빙 전환을 완료하는 동작
  - rollback snapshot 없이 rollback restore를 시도하는 동작
  - rollback snapshot active model/index 없이 restore를 완료하는 동작
  - rollback restore에서 `previous_model_version` / `previous_index_name`를 snapshot 값으로 복원하는 동작
  - `release_status!=STABLE`인 상태에서 새 `ROLLBACK_REQUEST`를 정상 수락하는 동작
  - `ROLLBACK_EXCLUDED` 영상을 search-visible로 취급하는 동작
  - `CANDIDATE_REINDEXING`이 아닌데 candidate index로 dual-write 하는 동작
- Idempotency rule:
  - 동일한 평가 `PASS` hand off가 재전달되어도 같은 `MLPipelineRun.id` 기준으로 candidate 상태를 중복으로 열지 않는다.
  - 동일한 `ROLLBACK_REQUEST`가 재전달되어도 이미 `ROLLBACK_PREPARING`이면 추가 side effect 없이 현재 상태를 유지한다.
  - rollback restore가 완료되어 현재 active 조합이 rollback snapshot과 이미 같으면, 이후 동일 rollback 요청 재전달은 no-op로 처리한다.
  - 이 작업들은 재시도될 수 있지만, 현재 상태와 필드 값을 보고 아직 필요한 경우에만 실행한다.
- Multi-tenant / authorization rule:
  - 이 컴포넌트는 사용자별 권한 판정을 직접 하지 않는다.
  - rollback 요청 권한 검증은 upstream admin control path 책임이다.

| From | To | Trigger | Guard / rule | Required side effects |
| --- | --- | --- | --- | --- |
| `STABLE` | `CANDIDATE_REINDEXING` | `READY_FOR_RELEASE` run 수신 | candidate 전환 중인 다른 release가 없어야 함 | `candidate_model_version/candidate_index_name` 설정, candidate index 생성 시작, online ingest dual-write 개시 |
| `CANDIDATE_REINDEXING` | `STABLE` | 서빙 전환 실행 | candidate readiness 통과 + `cutover_time`까지의 서빙 전환 전 반영 대상 데이터가 candidate에 반영 완료 | rollback snapshot 저장, `rollback_snapshot_active_model_version/rollback_snapshot_active_index_name/rollback_snapshot_captured_at` 갱신, `active=candidate`, `previous=직전 active`, `candidate_model_version/candidate_index_name/candidate_ready_at=null`, `switched_at` 갱신 |
| `STABLE` | `ROLLBACK_PREPARING` | `ROLLBACK_REQUEST` control message 수신 | rollback snapshot이 존재해야 함 | 현재 active 모델 버전과 같은 `Chunk.embedding_model_version`을 가진 영상 전체를 `ROLLBACK_EXCLUDED`, rollback target model load 시작, snapshot active index restore 시작 |
| `ROLLBACK_PREPARING` | `STABLE` | rollback restore 실행 | rollback target readiness 통과 + snapshot active index restore 완료 | snapshot active model/index 복원, `previous_model_version` / `previous_index_name`은 snapshot에서 복원하지 않음, `candidate_model_version/candidate_index_name/candidate_ready_at=null`, `switched_at` 갱신, 복구 완료 영상부터 `SERVABLE` 재편입 |
| `CANDIDATE_REINDEXING` | `STABLE` | candidate reindex 실패 | current serving은 유지 | candidate fields clear, 관련 run 실패 기록, dual-write 중단 |

### 2.4 한계와 운영 제약
- Performance / latency target:
  - 사용자 동기 요청 경로가 아니므로 즉시성보다 serving 정합성과 복구 가능성을 우선한다.
- Throughput / rate / concurrency limits:
  - 동시에 열린 release state는 한 serving 조합당 하나만 허용한다.
  - exact batch size, worker parallelism, retry 횟수는 PLAN에서 확정한다.
- Payload / file size / pagination limits:
  - `ROLLBACK_REQUEST`는 video_id나 추가 payload 없이 control-message schema만 사용한다.
- Timeout / TTL / retry constraints:
  - 서빙 전환과 rollback restore는 readiness gate가 만족될 때까지 전이를 완료하지 않는다.
  - readiness load 실패와 rollback restore 실패는 retryable로 취급하되, 구체 backoff 수치는 여기서 고정하지 않는다.
- Security / privacy constraints:
  - `ROLLBACK_EXCLUDED`는 hard delete가 아니라 일시적 서빙 제외다.
  - release record와 rollback snapshot은 내부 운영 경로만 접근 가능해야 한다.

### 2.5 에러 계약
| Surface | Condition | Code / status | Retryable | Notes |
| --- | --- | --- | --- | --- |
| 평가 `PASS` hand off | `MLPipelineRun`이 릴리스·재색인 진입 가능 상태가 아님 | invalid handoff | N | current serving과 `ModelRelease`는 유지한다 |
| Cutover | candidate readiness 미충족 또는 `cutover_time`까지 서빙 전환 전 반영 대상 데이터의 candidate 반영 미완료 | transition blocked | Y | `CANDIDATE_REINDEXING` 유지 |
| Rollback consumer | `release_status!=STABLE`에서 `ROLLBACK_REQUEST` 수신 | invalid rollback request | N | 현재 전환 상태를 유지하고 새 rollback은 시작하지 않는다 |
| Rollback consumer | rollback snapshot active model/index 부재 | invalid rollback request | N | `release_status`는 유지한다 |
| Rollback restore | rollback target readiness 미충족 또는 snapshot active index restore 미완료 | transition blocked | Y | `ROLLBACK_PREPARING`과 `ROLLBACK_EXCLUDED`를 유지한다 |
| Search serving gate | `READY`지만 `search_serving_state!=SERVABLE` | excluded from search | Y | restored-model 재임베딩 완료 후 재편입 대상이다 |

- 이 컴포넌트는 public HTTP API를 소유하지 않는다. operator-visible failure는 `MLPipelineRun`, `ModelRelease`, 구조화 로그에 기록한다.

---

## 3. 관측성과 운영 (Observability and Operations)

- Required log fields:
  - `trace_id`
  - `ml_pipeline_run_id`
  - `release_status`
  - `active_model_version`
  - `active_index_name`
  - `previous_model_version`
  - `previous_index_name`
  - `candidate_model_version`
  - `candidate_index_name`
  - `cutover_time`
  - `rollback_snapshot_active_model_version`
  - `rollback_snapshot_active_index_name`
  - `rollback_snapshot_captured_at`
  - `candidate_ready_at`
  - `switched_at`
  - `affected_video_count`
  - `excluded_video_count`
- Key metrics / alerts worth tracking:
  - current `release_status`
  - candidate cutover backlog
  - candidate dual-write lag
  - `ROLLBACK_EXCLUDED` video count
  - rollback snapshot index restore status
  - cutover success/failure count
  - rollback request / restore success/failure count
- Trace / correlation propagation rule:
  - 평가 `PASS` hand off, `ROLLBACK_REQUEST` control message, `ModelRelease` update, candidate/rollback model load, 관련 reindex 작업은 동일 `trace_id`로 연결해야 한다.
- Reconciliation / cleanup requirement (if any):
  - rollback restore 후 `candidate_model_version`, `candidate_index_name`, `candidate_ready_at`는 즉시 null이어야 한다.
  - problem-model index의 최종 물리 삭제는 current active/previous가 아닌 것이 확인된 뒤 비동기로 수행할 수 있다.
  - `previous_model_version` / `previous_index_name` 처리와 `ROLLBACK_EXCLUDED` 해제 조건은 2.3의 snapshot capture / restore 규칙을 따른다.

---

## 4. 인수 기준 (Acceptance Criteria)

### 4.1 반드시 통과해야 하는 시나리오
- [ ] 평가 `PASS` hand off가 들어오면 `ModelRelease`가 `CANDIDATE_REINDEXING`으로 전환되고 `candidate_model_version/candidate_index_name`이 채워지며, end-user serving 조합은 아직 바뀌지 않는다.
- [ ] `CANDIDATE_REINDEXING` 동안 `cutover_time`까지 새로 `READY`가 된 데이터는 active/candidate에 모두 반영되고, Search Service는 여전히 active/previous만 사용한다.
- [ ] 서빙 전환은 candidate readiness와 서빙 전환 전 반영 기준 충족이 모두 확인될 때만 실행되고, 실행 직전 rollback snapshot이 저장된다.
- [ ] 서빙 전환 후 `active=candidate`, `previous=직전 active`, `candidate_model_version/candidate_index_name/candidate_ready_at`는 null로 정리되고 `release_status=STABLE`이 된다.
- [ ] rollback control message가 수신되면 problem-model 데이터가 포함된 영상은 `ROLLBACK_EXCLUDED`가 되고, snapshot active index restore가 시작된다.
- [ ] rollback restore는 snapshot active model/index 존재, rollback target readiness, snapshot active index restore 완료가 모두 확인될 때만 실행되며, 복원 결과는 2.3의 snapshot capture / restore 규칙과 일치한다.
- [ ] restored-model 기준 재임베딩이 완료되기 전까지 `ROLLBACK_EXCLUDED` 영상은 검색 결과에 포함되지 않는다.
- [ ] `ROLLBACK_EXCLUDED` 영상이 있어도 Search Service는 남아 있는 `READY + SERVABLE` 영상으로 검색을 계속 제공할 수 있다.
- [ ] 동일한 평가 `PASS` hand off 또는 rollback 요청이 중복 수신되어도 duplicate cutover 또는 duplicate restore가 발생하지 않는다.

### 4.2 비목표 / 보류 항목
- activity-based priority reindexing
- full immediate reindex
- 모델 학습/평가와 pass/fail 판정
- 공개 Admin API와 UI 설계
- retired/problem index의 최종 cleanup 스케줄

---
