# [ML Pipeline Execution] SPEC

**메타 정보 (Meta)**
- Component ID: `ml-pipeline-execution`
- SOT: `docs/system-design.md`
- Related docs:
  - `docs/PRD.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Feedback_Ingestion_Pipeline_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Managed_Embedding_Endpoint_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Model_Release_and_Reindex_Spec.md` (후속 작성 )
- Status: Draft

---

## 1. 목적과 범위 (Purpose and Scope)

### 1.1 한 줄 요약
- ML Pipeline Execution은 신규 피드백 로그를 학습 데이터셋 버전으로 만들고, 그 최신 버전으로 후보 임베딩 모델을 학습·평가하며, 동시에 1개의 활성 실행만 유지하도록 파이프라인을 제어하는 컴포넌트다.

### 1.2 책임 경계
- In scope:
  - 피드백 원본 로그를 읽어 학습용 데이터셋 버전을 생성한다.
  - 정기 배치와 수동 재트리거를 동일한 실행 계약으로 받아들인다.
  - 동시에 활성 실행인 `MLPipelineRun`을 하나만 유지한다.
  - 실행 중 새 데이터셋이 준비되면 모든 대기 실행을 쌓지 않고 최신 데이터셋 기준 다음 실행 하나만 유지한다.
  - 후보 모델을 학습하고 Model Artifact Files에 저장한다.
  - 후보 모델과 기준 모델을 평가용 데이터셋으로 비교 평가한다.
  - `MLPipelineRun`, `ModelEvaluation`, 평가 상세 아티팩트를 기록한다.
  - 평가 `PASS` 시 재색인 단계가 바로 이어서 사용할 수 있는 실행 결과를 남긴다.
- Out of scope:
  - 피드백 이벤트 검증과 원본 로그 적재
  - 후보 인덱스 구축, dual-write, cutover 시각 계산
  - Managed Embedding Endpoint의 모델 로드 방식과 readiness 내부 동작
  - `ModelRelease` 갱신, 서빙 전환, 롤백 실행
  - 운영자용 HTTP API 표면
- Upstream dependencies:
  - Feedback Ingestion Pipeline이 적재한 원본 피드백 로그
  - 운영자가 관리하는 변경 불가 평가 데이터셋
  - 현재 활성 모델 버전을 담은 `ModelRelease`
  - 정기 스케줄러 또는 수동 재트리거 발행자
- Downstream consumers:
  - Model Artifact Files의 후보 모델 산출물
  - Metadata DB의 `MLPipelineRun`, `ModelEvaluation`
  - Object Storage의 학습 데이터셋, 평가 상세 아티팩트
  - 후속 `Model_Release_and_Reindex` 단계

### 간단한 흐름 (Simple Flow)
1. 원본 피드백 로그에서 신규 이벤트를 읽어 학습 데이터셋 버전을 만든다.
2. 활성 실행이 없으면 즉시 실행을 시작하고, 있으면 최신 데이터셋 기준 다음 실행 하나만 남긴다.
3. 실행이 시작되면 기준 모델 버전과 데이터셋 버전을 고정하고 후보 모델을 학습한다.
4. 후보 모델을 변경 불가 평가 데이터셋으로 기준 모델과 비교 평가한다.
5. 평가 `PASS`면 재색인 단계로 넘기고, `FAIL` 또는 시스템 오류면 실패로 종료한다.


---

## 2. 계약 (Contracts)

### 2.1 외부 인터페이스

#### HTTP / RPC / Consumer 인터페이스
| Interface | Method / Trigger | Input summary | Output summary | Auth / tenancy | Notes |
| --- | --- | --- | --- | --- | --- |
| 정기 배치 트리거 | 스케줄 도래 | raw feedback log의 신규 구간 | 새 학습 데이터셋 생성, 필요 시 실행 시작 또는 대기 실행 갱신 | 내부 운영 경로 | 데이터셋 생성 책임은 ML Lifecycle Worker가 직접 가진다 |
| 학습 실행 consumer | `TRAINING_REQUEST` 수신 | ML 실행 요청용 메시지 | 실행 시작 또는 최신 대기 실행 갱신 | 운영자 권한 검증은 upstream 책임 | 자동 트리거와 수동 재트리거 모두 같은 메시지 계약을 사용한다 |

#### 메시지 / 이벤트 계약
- Queue / topic: `TRAINING_REQUEST`
- Producer / consumer responsibility:
  - Producer: 스케줄러 또는 운영 경로가 실행 요청을 발행한다.
  - Consumer: 실행 시작 시점의 최신 학습 데이터셋 버전과 현재 활성 모델 버전을 조회해 이번 run의 기준으로 고정한다.
- Delivery semantics: at-least-once
- Payload versioning rules:
  - `TRAINING_REQUEST`는 video 처리 메시지와 분리된 ML 전용 메시지 규격을 사용한다.
  - 공통 메타데이터는 `message_type`, `payload_version`, `trace_id`, `attempt`, `issued_at`만 사용한다.
  - 학습 대상 `dataset_version`은 payload에 넣지 않는다. Consumer가 시작 시점에 최신 버전을 조회한다.
  - 지원하지 않는 `payload_version`은 정상 실행으로 처리하지 않는다.

```json
{
  "message_type": "TRAINING_REQUEST",
  "payload_version": "v1",
  "trace_id": "UUID4",
  "attempt": 1,
  "issued_at": "ISO8601_TIMESTAMP"
}
```

Notes:
- 이 메시지의 실행 키는 `dataset_version`과 `MLPipelineRun.id`다.

#### 외부 서비스 계약
| Dependency | Used for | Required behavior / assumption | 
| --- | --- | --- |
| Feedback Ingestion Pipeline 원본 로그 | 학습 데이터셋 생성 입력 | 원본 이벤트는 append-only이고 검색 시점의 모델/인덱스 문맥을 보존해야 한다 | 
| Object Storage | 학습 데이터셋/평가 상세 산출물 저장 | 버전형 산출물을 overwrite 없이 보존할 수 있어야 한다 | 
| Model Artifact Files | 후보 모델 저장, 최초 기동 부트스트랩 | 모델 파일은 버전별 artifact로 저장되어야 하며, artifact 경로에서 모델 버전을 일관되게 식별할 수 있어야 한다. 최초 기동 시 서비스는 환경변수로 지정한 경로에서 모델을 로드하고 버전을 파싱한다고 가정한다. |
| Metadata DB `ModelRelease` | 실행 중 활성/후보 모델 및 인덱스 조회 | 서비스 실행 중 모델 선택의 SOT는 `ModelRelease`여야 하며, 각 컴포넌트는 여기서 활성/후보 모델 버전과 대응 인덱스를 동일하게 읽는다고 가정한다. 레코드가 없을 때만 환경변수 기반 기본값으로 초기화한다. |
| Evaluation Dataset Artifact | 후보/기준 모델 비교 평가 | 학습셋과 분리된 immutable artifact여야 한다 | 

### 2.2 데이터 계약

#### 소유 데이터 (이 컴포넌트가 SOT인 경우)
| Entity / table | Purpose | Key fields / invariants | Notes |
| --- | --- | --- | --- |
| TrainingDataset Artifact | 원본 피드백 로그를 학습 입력으로 고정한 버전형 산출물 | `dataset_version`, `storage_path`, `created_at`; 변경 불가; 학습셋과 평가셋은 분리 | Object Storage 저장 |
| `MLPipelineRun` | 실행 제어와 추적의 SOT | `status`, `failed_stage`, `failure_type`, `failure_reason`, `candidate_model_version`, `dataset_version`, `evaluation_id`, `superseded_by_run_id`, `created_at`, `updated_at` | `candidate_index_name`, `cutover_time`는 후속 release/reindex 단계가 채운다 |
| `ModelEvaluation` | 후보 vs 기준 모델의 집계 평가 결과 | `candidate_model_version`, `baseline_model_version`, `evaluation_dataset_ref`, `quality_metrics`, `pass_criteria`, `overall_decision`, `fail_reason` | `overall_decision`은 `PASS | FAIL` |
| ModelEvaluationDetail Artifact | 질의별 상세 비교 결과 | `evaluation_id`, `storage_path`, `format=jsonl`, `created_at`; immutable | Object Storage 저장 |

학습 데이터셋 산출물은 저장 포맷과 무관하게 아래 의미를 보존해야 한다:
- `event_id`
- `user_id`
- `query_text`
- `rating`
- `topk_ids`
- `used_ids`
- `active_model_version`
- `active_index_name`
- `response_snapshot_ref`
- `created_at`

평가 데이터셋은 저장 포맷과 무관하게 아래 의미를 제공해야 한다:
- `query_text`
- `expected_results`
- `evaluation_dataset_ref`

`ModelEvaluation.quality_metrics`는 최소 아래 집계 지표를 포함해야 한다:
- `recall_at_5`
- `mrr_at_5`
- `ndcg_at_5`

`ModelEvaluation.pass_criteria`는 최소 아래 판정 규칙을 기록해야 한다:
- 후보 모델의 `recall_at_5`, `mrr_at_5`, `ndcg_at_5`가 모두 기준 모델 이상일 때만 `PASS`

#### 참조 데이터 (다른 SOT를 읽는 경우)
| Source owner | Entity / table | Fields relied on | Read-only assumptions |
| --- | --- | --- | --- |
| Feedback Ingestion Pipeline | 원본 피드백 이벤트 로그 | `event_id`, `user_id`, `query_text`, `rating`, `topk_ids`, `used_ids`, `active_model_version`, `active_index_name`, `response_snapshot_ref`, `created_at` | 이벤트는 append-only이며 후행 수정되지 않는다 |
| Model Release / Metadata DB | `ModelRelease` | `active_model_version` | 실행 시작 후에는 이번 run의 baseline으로 고정한다 |
| Admin-managed evaluation artifact | 평가 데이터셋 | `evaluation_dataset_ref`, `query_text`, `expected_results` | 변경 불가이며 학습셋과 분리되어 있다 |

### 2.3 상태 및 비즈니스 규칙
- 항상 유지되어야 하는 불변조건:
  - 동시에 활성 실행인 `MLPipelineRun`은 하나만 존재한다.
  - 동시에 다음 실행 대기 레코드도 하나만 존재한다.
  - 다음 실행 대기 레코드는 항상 최신 `dataset_version`을 가리켜야 하며, 이전 대기 실행은 `SUPERSEDED`가 된다.
  - 한 번 시작한 run의 `dataset_version`과 `baseline_model_version`은 중간에 바뀌지 않는다.
  - 평가 데이터셋은 학습 데이터셋과 분리된 변경 불가 산출물이어야 한다.
- MLPipelineRun 전체 상태 중, 이 문서가 다루는 상태 의미:
  - 실행 중
  - 다음 실행 대기
  - 재색인 단계로 넘길 준비 완료
  - 실패
  - SUPERSEDED
- `failed_stage`는 최소한 `데이터셋 생성`, `학습`, `평가`를 구분할 수 있어야 한다.
- `failure_type`은 `FAIL | ERROR`를 사용한다.
- 거부되어야 하는 전이 / invalid condition:
  - 활성 실행이 있는데 두 번째 활성 실행을 시작하는 동작
  - 이미 대기 중인 실행이 있을 때, 더 최신 데이터셋 기준 실행이 생기었을 때 기존 대기 실행이 남아 있는 경우
  - 평가 결과와 상세 아티팩트가 모두 기록되기 전에 재색인 단계로 넘길 준비 완료로 표기하는 동작

- Idempotency rule:
  - 동일한 `TRAINING_REQUEST`가 중복 전달되어도 병렬 실행을 추가로 만들지 않는다.
  - 이미 같은 최신 `dataset_version`으로 대기 중인 실행이 있으면 새 대기 실행을 더 만들지 않는다.
  - 같은 원본 피드백 이벤트는 데이터셋 생성 단계에서 `event_id` 기준으로 중복 제거할 수 있어야 한다.
- Multi-tenant / authorization rule:
  - 이 컴포넌트는 사용자별 테넌시를 직접 다루지 않는다.
  - 정기 배치와 수동 재트리거의 권한 검증은 upstream 운영 경로 책임이다.

| From | To | Trigger | Guard / rule | Required side effects |
| --- | --- | --- | --- | --- |
| 없음 | 실행 중 | 새 데이터셋 생성 또는 `TRAINING_REQUEST` 수신 | 활성 실행 없음, 시작 가능한 최신 데이터셋 존재 | `dataset_version`, `baseline_model_version`, `candidate_model_version` 고정 |
| 없음 또는 기존 대기 | 다음 실행 대기 | 활성 실행 중 새 데이터셋 준비 | 최신 데이터셋 기준 다음 실행만 유지 | 기존 대기 실행은 `SUPERSEDED` 처리 |
| 다음 실행 대기 | 실행 중 | 활성 슬롯 확보 | 가장 최신 대기 실행만 시작 | 실행 기준 버전 고정 |
| 실행 중 | 재색인 단계로 넘길 준비 완료 | 학습 완료 + 평가 `PASS` | 후보 모델 저장, 평가 요약/상세 저장 완료 | 후속 release/reindex 단계가 읽을 handoff 정보 보존 |
| 실행 중 | 실패 | 평가 `FAIL` 또는 시스템 오류 | `failed_stage`, `failure_type` 기록 | 기존 서빙 유지 |
| 다음 실행 대기 | `SUPERSEDED` | 더 최신 데이터셋 준비 | 자신보다 최신 `dataset_version`이 대기 슬롯을 차지 | `superseded_by_run_id` 기록 |

### 2.4 한계와 운영 제약
- Performance / latency target:
  - 사용자 동기 요청 경로가 아니므로 저지연보다 최신 데이터셋 수렴과 실행 직렬화가 우선이다.
- Throughput / rate / concurrency limits:
  - 실행 단위 동시성은 활성 실행 1개, 다음 실행 대기 1개로 고정한다.
  - 학습 내부 병렬도와 배치 크기는 PLAN에서 확정한다.
- Payload / file size / pagination limits:
  - `TRAINING_REQUEST` 메시지에는 데이터셋 본문이나 평가 본문을 넣지 않는다.

- Timeout / TTL / retry constraints:
  - 메시지 재전달은 at-least-once를 전제로 한다.
  - 구체 timeout, retry 횟수, backoff 수치는 PLAN에서 확정한다.
  - `FAIL`은 품질 미달, `ERROR`는 시스템 오류로 운영 화면에서 구분 가능해야 한다.
- Security / privacy constraints:
  - 학습 데이터셋과 원본 피드백 로그는 `user_id`, `query_text`, 모델/인덱스 문맥을 포함할 수 있으므로 내부 운영 경로만 접근 가능해야 한다.
  - 평가 상세 아티팩트는 사용자 외부 노출 경로를 갖지 않는다.

### 2.5 에러 계약
| Surface | Condition | Code / status | Retryable | Notes |
| --- | --- | --- | --- | --- |
| 정기 배치 / 데이터셋 생성 | 원본 피드백 로그 읽기 또는 산출물 저장 실패 | 실행 실패 / 실패 단계=`데이터셋 생성` / `failure_type=ERROR` | Y | 기존 서빙은 유지 |
| 학습 실행 consumer | 지원하지 않는 `payload_version` | invalid message | N | 정상 실행으로 처리하지 않는다 |
| 학습 단계 | 후보 모델 학습 또는 artifact 저장 실패 | 실행 실패 / 실패 단계=`학습` / `failure_type=ERROR` | Y | 기존 서빙은 유지 |
| 평가 단계 | 평가 계산/저장 오류 | 실행 실패 / 실패 단계=`평가` / `failure_type=ERROR` | Y | 기존 서빙은 유지 |
| 평가 단계 | 품질 지표가 `pass_criteria` 미달 | 실행 실패 / 실패 단계=`평가` / `failure_type=FAIL` | N | 후보 모델은 서빙 전환 대상이 아니다 |


---

## 3. 관측성과 운영 (Observability and Operations)

- Required log fields:
  - `trace_id`
  - `ml_pipeline_run_id`
  - `dataset_version`
  - `baseline_model_version`
  - `candidate_model_version`
  - `evaluation_id`
  - `status`
  - `failed_stage`
  - `failure_type`
  - `trigger_source` (`schedule` or `manual`)
- Key metrics / alerts worth tracking:
  - 신규 학습 데이터셋 생성 건수
  - 현재 실행 중인 MLPipelineRun 존재 여부
  - 현재 다음 순서로 대기 중인 MLPipelineRun 존재 여부
  - `SUPERSEDED` 발생 건수
  - 학습 단계 소요 시간
  - 평가 단계 소요 시간
  - 평가 `PASS` / `FAIL` / `ERROR` 비율
  - `failed_stage`별 실패 건수
  - 최신 성공 run이 반영한 `dataset_version`과 최신 생성 `dataset_version`의 차이
- Trace / correlation propagation rule:
  - 자동 트리거와 수동 재트리거 모두 `trace_id`를 `MLPipelineRun`, 로그, 평가 아티팩트 메타데이터에 일관되게 남겨야 한다.
- Reconciliation / cleanup requirement:
  - `SUPERSEDED`된 run은 삭제하지 않고 이력으로 남겨야 한다.
  - 재색인 단계로 넘긴 뒤의 후보 인덱스/릴리스 정리는 이 SPEC이 아니라 후속 release/reindex 단계 책임이다.

---

## 4. 인수 기준 (Acceptance Criteria)

### 4.1 반드시 통과해야 하는 시나리오
- [ ] 신규 원본 피드백 로그가 있으면 학습 데이터셋 버전이 생성되고, 활성 실행이 없으면 즉시 실행을 시작하며, 있으면 최신 데이터셋 기준 다음 실행 하나만 유지된다.
- [ ] 더 새로운 데이터셋이 준비되면 이전 대기 실행은 `SUPERSEDED`로 남고, 오래된 대기 실행이 실제 시작되지 않는다.
- [ ] 실행이 시작되면 `dataset_version`, `baseline_model_version`, `candidate_model_version`이 이번 run 기준으로 고정되고 후보 모델 artifact가 저장된다.
- [ ] 평가가 끝나면 `ModelEvaluation` 요약과 질의별 상세 artifact가 모두 저장되며, `quality_metrics`와 `pass_criteria`만으로 `PASS` / `FAIL`을 재현할 수 있다.
- [ ] 평가 `PASS` 시 run은 재색인 단계가 이어서 사용할 수 있는 완료 상태로 남고, 이 단계에서는 아직 `ModelRelease`가 변경되지 않는다.
- [ ] 평가 `FAIL` 또는 시스템 오류 시 run은 실패로 종료되고, `failed_stage`와 `failure_type(FAIL|ERROR)`가 운영 화면에서 구분 가능하게 남는다.
- [ ] 중복 `TRAINING_REQUEST` 또는 중복 배치 트리거가 와도 활성 실행이 2개 이상 생기지 않는다.

### 4.2 비목표 / 보류 항목
- 후보 인덱스 물리 생성 방식
- 재색인 중 online ingest dual-write 규칙
- 후보 모델 readiness 검증 절차
- `ModelRelease` 갱신과 실제 서빙 전환
- 롤백 실행과 rollback 대상 선택

---
