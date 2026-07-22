# [Model Release and Reindex] SPEC

**메타 정보**
- Component ID: `model-release-and-reindex`
- SOT: `docs/system-design.md` (이 SPEC은 system design SOT와 일관되어야 한다)
- 관련 문서:
  - `docs/PRD.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/ML_Pipeline_Execution_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Managed_Embedding_Endpoint_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`
- Status: Draft

---

## 1. 목적과 범위

### 1.1 한 줄 요약
- Model Release and Reindex는 후보 임베딩 모델의 candidate 재색인, 서빙 전환, 마지막으로 정상 서빙되던 active 조합만 담는 rollback snapshot 복구, rollback 중 검색 범위 제외, 복구 후 재편입을 관리하는 릴리스 제어 컴포넌트다.

### 1.2 책임 경계
- 범위에 포함:
  - 현재 서빙 조합과 전환 상태는 릴리스 레코드(`ModelRelease`)를 SOT로 관리한다.
  - 평가 PASS 후보에 대해서는 candidate index를 만들고, 서빙 전환 전까지 새로 READY가 되는 데이터만 우선 반영한다.
  - 서빙 전환 직전의 마지막 정상 active 조합만 rollback snapshot으로 저장하고, 전환 후에는 active/previous 2세대만 서빙 대상으로 유지한다.
  - rollback 요청(`ROLLBACK_REQUEST`)을 받으면 rollback 준비 상태를 열고 snapshot 기반 복구를 수행한다.
  - rollback 중 영향을 받는 프로젝트는 검색에서 제외하고, 복구 완료분부터 다시 검색 범위에 편입한다. 이 상태 표시는 `Project.search_serving_state`로 관리한다.
  - 배포용 재색인(`CANDIDATE_REINDEXING`) 상태에서는 새로 READY가 되는 데이터를 active index와 candidate index에 함께 기록한다.
  - rollback restore의 복원 대상은 snapshot이 가리키는 active 조합으로 한정한다.
- 범위에서 제외:
  - 모델 학습, 평가, `MLPipelineRun` 활성 실행 제어
  - 검색 랭킹, RRF, LLM 응답 생성
  - 공개 Admin HTTP API
  - full immediate reindex
  - retired/problem index의 최종 물리 삭제 시점
  - Search Service의 사용자 고지 문구 포맷
- 상위 의존성:
  - 같은 Worker 내부 ML Pipeline Execution이 남긴 `READY_FOR_RELEASE` run 상태
  - Admin control path가 발행한 `ROLLBACK_REQUEST` control message
  - Managed Embedding Endpoint readiness
  - Metadata DB의 `ModelRelease`, `Project`, `Video`, `Chunk`
- 하위 소비자:
  - Search Service
  - Media & AI Pipeline Worker online ingest 경로
  - Admin Dashboard의 상태 조회 경로

### 간단한 흐름 (Simple Flow)
1. 평가 `PASS` 후보가 전달되면 `ModelRelease`를 `CANDIDATE_REINDEXING`으로 전환하고 candidate index를 생성한다.
2. CANDIDATE_REINDEXING 상태에 들어간 뒤 처리 완료(READY)된 새 데이터는 현재 서빙용 active index와 후보용 candidate index에 함께 기록한다.
3. 새 모델이 실제 서빙 가능한 상태이고, 전환 기준 시점까지의 데이터가 새 인덱스에 모두 반영된 것이 확인되면, 현재 서빙 상태를 snapshot으로 저장한 뒤 candidate를 active로 전환한다.
4. 서빙 전환 이후 원래 active 였던 모델은 previous로 남기고, previous보다 오래된 모델로 색인한 데이터는 최신 active 기준으로 점진 재임베딩한다.
5. 롤백 요청이 들어오면, 영향받는 프로젝트는 우선 검색 대상에서 제외한다. 그 후 미리 저장해 둔 정상 서빙 상태가 다시 준비되면, 시스템을 그 상태로 복구한다.
6. 복구 중 제외된 프로젝트는 Search Service의 검색 범위에서 빠지며, 복구 완료 후 다시 검색 범위에 합류한다.


---

## 2. 계약 (Contracts)

### 2.1 외부 인터페이스

#### 외부 진입 인터페이스
| 인터페이스 | 메서드 / 트리거 | 입력 요약 | 출력 요약 | 인증 / 테넌시 | 비고 |
| --- | --- | --- | --- | --- | --- |
| 평가 `PASS` 수신 | 같은 Worker 내부 직접 호출 | `run_id`, `trace_id` | 후보 재색인 시작 또는 run 실패 기록 | internal only | 이 컴포넌트는 `READY_FOR_RELEASE` 상태의 run을 다음 단계 시작 조건으로 사용한다. 실행 문맥은 `MLPipelineRun`, `ModelEvaluation`, `ModelRelease`를 다시 읽어 복원한다 |
| 롤백 요청 수신 | `ROLLBACK_REQUEST` control message 수신 | `message_type`, `payload_version`, `trace_id`, `attempt`, `issued_at`, `expected_active_model_version`, `expected_switched_at` | rollback 준비 시작 또는 invalid state 기록 | admin 권한 검증은 upstream 책임 | rollback 대상 선택은 요청자가 본 active release와 `ModelRelease` snapshot 기준이다 |

#### 메시지 / 이벤트 계약 (해당 시)
- Queue / topic: rollback control channel (`ROLLBACK_REQUEST`)
- 평가 `PASS` hand off는 broker나 polling을 쓰지 않고 같은 Worker 내부 직접 호출로 처리한다.
- Producer / consumer 책임:
  - Producer: admin control path가 rollback control message를 발행한다.
  - Consumer: Consumer는 되돌릴 정상 모델과 인덱스가 실제로 다시 준비됐는지 확인한 뒤, 롤백 상태 전환을 수행한다.
- 전달 의미론: at-least-once
- Payload versioning 규칙:
  - ROLLBACK_REQUEST는 video-processing shared envelope가 아니라 별도의 control-message schema를 사용한다.
  - control-message schema에는 `message_type`, `payload_version`, `trace_id`, `attempt`, `issued_at`, `expected_active_model_version`, `expected_switched_at`을 포함한다.
  - 지원하지 않는 `payload_version`은 정상 rollback 실행으로 처리하지 않는다.
- 필수 control-message field:
  - `message_type`
  - `payload_version`
  - `trace_id`
  - `attempt`
  - `issued_at`
  - `expected_active_model_version`
  - `expected_switched_at`

```json
{
  "message_type": "ROLLBACK_REQUEST",
  "payload_version": "v1",
  "trace_id": "UUID4",
  "attempt": 1,
  "issued_at": "ISO8601_TIMESTAMP",
  "expected_active_model_version": "model-v2",
  "expected_switched_at": "ISO8601_TIMESTAMP"
}
```

#### 외부 연동 컴포넌트 계약 (해당 시)
| 의존성 | 사용 목적 | 필요한 동작 / 가정 | 실패 영향 |
| --- | --- | --- | --- |
| Metadata DB `ModelRelease` | current serving SOT | active/previous/candidate 및 rollback snapshot active field를 원자적으로 갱신할 수 있어야 한다 | 서빙 전환 또는 rollback restore가 불가능하다 |
| Metadata DB `Project` | search exclusion gate | `search_serving_state`는 `SERVABLE | ROLLBACK_EXCLUDED`만 허용한다 | rollback 중 프로젝트 검색 제외/복귀 계약이 깨진다 |
| Metadata DB `Video` / `Chunk` | rollback 영향 범위 식별 | `Video.project_id`와 `Chunk.embedding_model_version`으로 문제 모델 데이터가 포함된 프로젝트를 식별할 수 있어야 한다 | affected project 선별이 불가능하다 |
| Managed Embedding Endpoint | candidate / rollback target readiness | target model이 실제로 로드되고 readiness를 통과해야만 release record를 전환할 수 있다 | 상태는 유지되고 전환은 차단된다 |
| Vector Store | candidate / active / previous index 유지 | candidate index는 staging 전용이며 end-user search 대상이 아니다 | 검색 또는 reindex 정합성이 깨진다 |
| Index Snapshot Files | rollback snapshot restore | rollback snapshot active index 식별자에 대응하는 인덱스 스냅샷을 복원할 수 있어야 한다 | rollback restore가 불가능하다 |
| Media & AI Pipeline Worker | online ingest 반영 | release 상태와 관계없이 현재 active model/index 한 곳에 기록하며, rollback 후 영향 프로젝트의 복구 대상 영상을 재임베딩할 수 있어야 한다 | active 반영과 rollback 복구 계약이 깨진다 |

### 2.2 데이터 계약

#### 소유 데이터 (이 컴포넌트가 SOT인 경우)
| 엔터티 / 테이블 | 목적 | 핵심 필드 / 불변조건 | 비고 |
| --- | --- | --- | --- |
| `ModelRelease` | 현재 서빙 조합과 전환 상태의 SOT | `release_status`, `active_model_version`, `active_index_name`, `previous_model_version`, `previous_index_name`, `candidate_model_version`, `candidate_index_name`, `rollback_snapshot_active_model_version`, `rollback_snapshot_active_index_name`, `rollback_snapshot_captured_at`, `candidate_ready_at`, `switched_at` | `release_status` 허용값은 정확히 `STABLE`, `CANDIDATE_REINDEXING`, `ROLLBACK_PREPARING`이다 |
| `Project.search_serving_state` (shared field) | rollback 중 일시 검색 제외와 신규 ingest 제한 | 허용값은 `SERVABLE`, `ROLLBACK_EXCLUDED`다 | Search Service는 `SERVABLE` 프로젝트만 검색에 포함하고, ingest 경로는 `ROLLBACK_EXCLUDED` 프로젝트의 신규 video ingest를 일시 차단한다 |
| `MLPipelineRun.candidate_index_name`, `MLPipelineRun.cutover_time` (shared fields) | 평가 PASS hand off 추적 | `candidate_index_name`은 candidate index 식별자다. `cutover_time`은 재시도에서도 같은 전환 시각을 사용하도록 release/reindex 단계가 서빙 전환 직전에 고정한다. | 나머지 run 제어는 `ML_Pipeline_Execution_Spec.md`가 소유한다 |

#### 참조 데이터 (다른 SOT를 읽는 경우)
| SOT 소유자 | 엔터티 / 테이블 | 의존 필드 | 읽기 전용 가정 |
| --- | --- | --- | --- |
| ML Pipeline Execution | `MLPipelineRun` | `id`, `status`, `candidate_model_version`, `dataset_version`, `evaluation_id`, `cutover_time` | `READY_FOR_RELEASE` 상태의 run만 이 컴포넌트로 들어오며, 수신 시 공유 SOT를 다시 읽어 기준 상태를 복원한다 |
| Search Service | serving path | active/previous index 조합, project-level serving gate, project-internal readiness gate | candidate index는 검색 대상이 아니며, `ROLLBACK_EXCLUDED` 프로젝트가 있어도 다른 검색 가능 프로젝트의 검색은 계속 제공할 수 있다 |
| Managed Embedding Endpoint | runtime state | active/previous/candidate readiness | readiness가 통과한 모델만 release record에 반영한다 |

### 2.3 상태 및 비즈니스 규칙
- 항상 유지되어야 하는 불변조건:
  - `release_status`는 `STABLE | CANDIDATE_REINDEXING | ROLLBACK_PREPARING`만 허용한다.
  - end-user search는 active/previous 2세대까지만 사용한다.
  - candidate index는 검증/재색인 전용이며 end-user search 표면에 노출되지 않는다.
  - online ingest는 `CANDIDATE_REINDEXING` 중에도 현재 active model/index 한 곳에만 기록한다.
  - candidate model/index는 cutover로 active가 된 뒤부터 신규 online ingest 데이터를 받는다.
  - previous보다 더 오래된 데이터는 서빙 전환 이후 최신 active 모델로 점진 재임베딩한다.
  - rollback 중 영향 프로젝트는 현재 문제 active 모델 버전과 같은 `Chunk.embedding_model_version`을 가진 청크가 1개 이상 있는 프로젝트다.
  - `ROLLBACK_EXCLUDED` 프로젝트는 restored active 모델 버전 기준으로 필요한 재임베딩과 vector 반영이 끝난 뒤에만 다시 `SERVABLE`이 된다.
  - `ROLLBACK_EXCLUDED` 프로젝트는 rollback 복구가 끝나기 전까지 신규 video ingest도 일시 차단한다.
  - `ROLLBACK_PREPARING` 동안 신규 데이터는 problem active model 기준 embedding/vector upsert를 수행하지 않는다.
  - rollback은 문제 모델 확산 방지와 snapshot restore를 우선 수행한다.
  - rollback snapshot은 cutover 직전의 last known-good active model/index만 저장한다. `previous_model_version` / `previous_index_name`은 snapshot에 포함하지 않으며 restore에도 사용하지 않는다.
  - rollback restore는 서빙 상태를 마지막 정상 상태로 되돌리는 책임이다. 영향 프로젝트의 재임베딩과 검색 재편입은 그 뒤에 이어지는 후속 복구 책임이다.
- 서빙 전환 기준:
  - candidate readiness가 확인되면, 서빙 전환 직전에 이 컴포넌트가 `MLPipelineRun.cutover_time`을 고정한다.
  - previous보다 오래된 데이터의 legacy 재색인이 완료된 뒤에만 서빙을 전환한다.
  - cutover 직전까지 기존 active에 기록된 데이터는 cutover 후 previous 검색으로 계속 제공하고, 이후 legacy 재색인으로 새 active에 옮긴다.
- 릴리스·재색인 판단은 호출자가 들고 온 메모리 문맥이 아니라 공유 SOT에 기록된 상태를 기준으로 수행한다.
- snapshot capture / restore 규칙:
  - snapshot capture는 서빙 전환 직전에 수행한다.
  - capture 시 `active_model_version`, `active_index_name`만 각각 `rollback_snapshot_active_model_version`, `rollback_snapshot_active_index_name`으로 복사하고 `rollback_snapshot_captured_at`를 기록한다.
  - rollback snapshot 필드는 실제 모델/인덱스 본체가 아니라 복원 대상 active model/index를 가리키는 포인터 메타데이터다.
  - 서빙 전환 성공 시 `candidate_model_version`, `candidate_index_name`, `candidate_ready_at`는 null로 초기화한다.
  - rollback restore 시 rollback snapshot이 가리키는 active model/index가 준비된 뒤 현재 serving 필드의 active model/index만 복원하고, `previous_model_version` / `previous_index_name`은 snapshot에서 복원하지 않는다. `candidate_model_version`, `candidate_index_name`, `candidate_ready_at`는 null로 초기화한다.
- online ingest / visibility 규칙:
  - online ingest는 release 상태와 관계없이 현재 active model/index 한 곳에만 기록한다.
  - candidate model/index는 cutover 전 online ingest 대상이 아니다.
  - candidate index는 서빙 전환 전까지 staging 전용이며 Search Service의 user-facing query surface에 포함되면 안 된다.
- 거부되어야 하는 전이 / invalid condition:
  - candidate readiness 또는 legacy 재색인 완료 전에 서빙 전환을 완료하는 동작
  - rollback snapshot 없이 rollback restore를 시도하는 동작
  - rollback snapshot active model/index 없이 restore를 완료하는 동작
  - rollback restore에서 `previous_model_version` / `previous_index_name`를 snapshot 값으로 복원하는 동작
  - rollback request의 `expected_active_model_version` 또는 `expected_switched_at`이 현재 `ModelRelease`와 다른데 rollback을 시작하는 동작
  - `release_status!=STABLE`인 상태에서 새 `ROLLBACK_REQUEST`를 정상 수락하는 동작
  - `ROLLBACK_EXCLUDED` 프로젝트를 search-visible로 취급하는 동작
  - `ROLLBACK_EXCLUDED` 프로젝트의 신규 video ingest를 정상 수락하는 동작
  - `ROLLBACK_PREPARING` 동안 신규 데이터를 problem active model 기준으로 embedding/vector upsert 하는 동작
  - cutover 전에 online ingest를 candidate index에 기록하는 동작
- 멱등성 규칙:
  - 동일한 평가 `PASS` hand off가 재전달되어도 같은 `MLPipelineRun.id` 기준으로 candidate 상태를 중복으로 열지 않는다.
  - 동일한 `ROLLBACK_REQUEST`가 재전달되어도 이미 `ROLLBACK_PREPARING`이면 추가 side effect 없이 현재 상태를 유지한다.
  - rollback restore가 완료되어 현재 active 조합이 rollback snapshot과 이미 같으면, 이후 동일 rollback 요청 재전달은 no-op로 처리한다.
  - 이 작업들은 재시도될 수 있지만, 현재 상태와 필드 값을 보고 아직 필요한 경우에만 실행한다.
- 멀티테넌트 / 인가 규칙:
  - 이 컴포넌트는 사용자별 권한 판정을 직접 하지 않는다.
  - rollback 요청 권한 검증은 upstream admin control path 책임이다.

| From | To | Trigger | Guard / rule | 필요한 side effect |
| --- | --- | --- | --- | --- |
| `STABLE` | `CANDIDATE_REINDEXING` | `READY_FOR_RELEASE` run 수신 | candidate 전환 중인 다른 release가 없어야 함 | `candidate_model_version/candidate_index_name` 설정, candidate model/index 준비 시작, online ingest는 기존 active만 유지 |
| `CANDIDATE_REINDEXING` | `STABLE` | 서빙 전환 실행 | candidate readiness 통과 + legacy 재색인 완료 | rollback snapshot 저장, `rollback_snapshot_active_model_version/rollback_snapshot_active_index_name/rollback_snapshot_captured_at` 갱신, `active=candidate`, `previous=직전 active`, `candidate_model_version/candidate_index_name/candidate_ready_at=null`, `switched_at` 갱신 |
| `STABLE` | `ROLLBACK_PREPARING` | `ROLLBACK_REQUEST` control message 수신 | rollback snapshot이 존재하고, 현재 active release가 request의 기대값과 일치해야 함 | 현재 active 모델 버전과 같은 `Chunk.embedding_model_version`을 가진 프로젝트 전체를 `ROLLBACK_EXCLUDED`, 신규 problem-model embedding 차단, rollback target model load 시작, snapshot active index restore 시작 |
| `ROLLBACK_PREPARING` | `STABLE` | rollback restore 실행 | rollback target readiness 통과 + snapshot active index restore 완료 | snapshot active model/index 복원, `previous_model_version` / `previous_index_name`은 snapshot에서 복원하지 않음, `candidate_model_version/candidate_index_name/candidate_ready_at=null`, `switched_at` 갱신, 복구 완료 프로젝트부터 `SERVABLE` 재편입 |
| `CANDIDATE_REINDEXING` | `STABLE` | candidate 배포 실패 | current serving은 유지 | candidate fields clear, 관련 run 실패 기록 |

### 2.4 한계와 운영 제약
- 성능 / 지연 목표:
  - 사용자 동기 요청 경로가 아니므로 즉시성보다 serving 정합성과 복구 가능성을 우선한다.
- Throughput / rate / concurrency 한계:
  - 동시에 열린 release state는 한 serving 조합당 하나만 허용한다.
  - exact batch size, worker parallelism, retry 횟수는 PLAN에서 확정한다.
- Payload / 파일 크기 / pagination 한계:
  - `ROLLBACK_REQUEST`는 video_id나 추가 payload 없이 control-message schema만 사용한다.
- Timeout / TTL / retry 제약:
  - 서빙 전환과 rollback restore는 readiness gate가 만족될 때까지 전이를 완료하지 않는다.
  - readiness load 실패와 rollback restore 실패는 retryable로 취급하되, 구체 backoff 수치는 여기서 고정하지 않는다.
- 보안 / 개인정보 제약:
  - `ROLLBACK_EXCLUDED`는 hard delete가 아니라 프로젝트 단위의 일시적 서빙 제외와 신규 ingest 제한이다.
  - release record와 rollback snapshot은 내부 운영 경로만 접근 가능해야 한다.

### 2.5 에러 계약
| 표면 | 조건 | 코드 / 상태 | 재시도 가능 | 비고 |
| --- | --- | --- | --- | --- |
| 평가 `PASS` hand off | `MLPipelineRun`이 릴리스·재색인 진입 가능 상태가 아님 | invalid handoff | N | current serving과 `ModelRelease`는 유지한다 |
| Cutover | candidate readiness 미충족 또는 legacy 재색인 미완료 | transition blocked | Y | `CANDIDATE_REINDEXING` 유지 |
| Rollback consumer | `release_status!=STABLE`에서 `ROLLBACK_REQUEST` 수신 | invalid rollback request | N | 현재 전환 상태를 유지하고 새 rollback은 시작하지 않는다 |
| Rollback consumer | rollback request의 기대 active release가 현재 `ModelRelease`와 불일치 | stale rollback request | N | 현재 전환 상태를 유지하고 새 rollback은 시작하지 않는다 |
| Rollback consumer | rollback snapshot active model/index 부재 | invalid rollback request | N | `release_status`는 유지한다 |
| Rollback restore | rollback target readiness 미충족 또는 snapshot active index restore 미완료 | transition blocked | Y | `ROLLBACK_PREPARING`과 project-level `ROLLBACK_EXCLUDED`를 유지한다 |
| Search serving gate | project `search_serving_state!=SERVABLE` | excluded from search | Y | restored-model 재임베딩 완료 후 재편입 대상이다 |

- 이 컴포넌트는 public HTTP API를 소유하지 않는다. operator-visible failure는 `MLPipelineRun`, `ModelRelease`, 구조화 로그에 기록한다.

---

## 3. 관측성과 운영

- 필수 log field:
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
  - `affected_project_count`
  - `excluded_project_count`
- 추적할 핵심 metric / alert:
  - current `release_status`
  - candidate cutover backlog
  - candidate readiness 지연
  - `ROLLBACK_EXCLUDED` project count
  - rollback snapshot index restore status
  - cutover success/failure count
  - rollback request / restore success/failure count
- Trace / correlation 전파 규칙:
  - 평가 `PASS` hand off, `ROLLBACK_REQUEST` control message, `ModelRelease` update, candidate/rollback model load, 관련 reindex 작업은 동일 `trace_id`로 연결해야 한다.
- Reconciliation / cleanup 요구사항:
  - rollback restore 후 `candidate_model_version`, `candidate_index_name`, `candidate_ready_at`는 즉시 null이어야 한다.
  - problem-model index의 최종 물리 삭제는 current active/previous가 아닌 것이 확인된 뒤 비동기로 수행할 수 있다.
  - `previous_model_version` / `previous_index_name` 처리와 project-level `ROLLBACK_EXCLUDED` 해제 조건은 2.3의 snapshot capture / restore 규칙을 따른다.

---

## 4. 인수 기준

### 4.1 반드시 통과해야 하는 시나리오
- [ ] 평가 `PASS` hand off가 들어오면 `ModelRelease`가 `CANDIDATE_REINDEXING`으로 전환되고 `candidate_model_version/candidate_index_name`이 채워지며, end-user serving 조합은 아직 바뀌지 않는다.
- [ ] `CANDIDATE_REINDEXING` 동안 신규 online ingest는 기존 active에만 반영되고, candidate는 cutover 후 active가 된 뒤부터 신규 데이터를 받는다.
- [ ] 서빙 전환은 candidate readiness와 legacy 재색인 완료가 모두 확인될 때만 실행되고, 실행 직전 rollback snapshot이 저장된다.
- [ ] 서빙 전환 후 `active=candidate`, `previous=직전 active`, `candidate_model_version/candidate_index_name/candidate_ready_at`는 null로 정리되고 `release_status=STABLE`이 된다.
- [ ] rollback control message가 수신되면 problem-model 데이터가 포함된 프로젝트는 `ROLLBACK_EXCLUDED`가 되고, snapshot active index restore가 시작된다.
- [ ] rollback control message는 현재 `ModelRelease.active_model_version`과 `switched_at`이 요청의 기대값과 일치할 때만 처리된다.
- [ ] rollback restore는 snapshot active model/index 존재, rollback target readiness, snapshot active index restore 완료가 모두 확인될 때만 실행되며, 복원 결과는 2.3의 snapshot capture / restore 규칙과 일치한다.
- [ ] restored-model 기준 재임베딩이 완료되기 전까지 `ROLLBACK_EXCLUDED` 프로젝트는 검색 결과에 포함되지 않는다.
- [ ] restored-model 기준 재임베딩이 완료되기 전까지 `ROLLBACK_EXCLUDED` 프로젝트의 신규 video ingest는 차단된다.
- [ ] `ROLLBACK_EXCLUDED` 프로젝트가 있어도 Search Service는 남아 있는 `SERVABLE` 프로젝트로 검색을 계속 제공할 수 있다.
- [ ] 동일한 평가 `PASS` hand off 또는 rollback 요청이 중복 수신되어도 duplicate cutover 또는 duplicate restore가 발생하지 않는다.

### 4.2 비목표 / 보류 항목
- activity-based priority reindexing
- full immediate reindex
- 모델 학습/평가와 pass/fail 판정
- 공개 Admin API와 UI 설계
- retired/problem index의 최종 cleanup 스케줄

---
