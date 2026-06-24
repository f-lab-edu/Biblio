# [ML Pipeline Execution] SPEC

**메타 정보**
- Component ID: `ml-pipeline-execution`
- SOT: `docs/system-design.md`
- 관련 문서:
  - `docs/PRD.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Feedback_Ingestion_Pipeline_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Managed_Embedding_Endpoint_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Model_Release_and_Reindex_Spec.md`
- Status: Draft

---

## 1. 목적과 범위

### 1.1 한 줄 요약
- ML Pipeline Execution은 피드백 로그를 학습 데이터셋 artifact로 고정하고, training trigger가 최신 eligible dataset을 선택해 후보 임베딩 모델을 학습·평가하도록 제어하는 컴포넌트다.

### 1.2 책임 경계
- 범위에 포함:
  - 피드백 원본 로그를 읽어 학습용 데이터셋 artifact와 manifest를 생성한다.
  - dataset generation scheduler와 admin/manual trigger를 받아들인다.
  - training scheduler와 admin/manual trigger를 받아들인다.
  - Training scheduler는 최신 eligible dataset을 선택하고, 실행 상태가 그 dataset 기준으로 수렴하도록 시작한다.
  - Driver는 정상 경로의 상태 전진을 관리한다.
  - Reconciler는 장시간 진전이 없는 실행을 식별하고, 복구 또는 실패 정리의 시작 책임을 가진다.
  - 동시에 활성 실행인 `MLPipelineRun`을 하나만 유지한다.
  - 실행 중 training trigger가 더 최신 eligible dataset을 발견하면 모든 대기 실행을 쌓지 않고 최신 dataset 기준 다음 실행 하나만 유지한다.
  - 후보 모델을 학습하고 Model Artifact Files에 저장한다.
  - 후보 모델과 기준 모델을 평가용 데이터셋으로 비교 평가한다.
  - `MLPipelineRun`, `ModelEvaluation`, 평가 상세 아티팩트를 기록한다.
  - 평가 `PASS` 시 hand off 준비 상태를 남기고, 같은 Worker 내부의 다음 책임이 이어받을 수 있도록 hand off한다.
- 범위에서 제외:
  - 피드백 이벤트 검증과 원본 로그 적재
  - 후보 인덱스 구축, dual-write, cutover 시각 계산
  - Managed Embedding Endpoint의 모델 로드 방식과 readiness 내부 동작
  - `ModelRelease` 갱신, 서빙 전환, 롤백 실행
  - 운영자용 HTTP API 표면
- 상위 의존성:
  - Feedback Ingestion Pipeline이 적재한 원본 피드백 로그
  - 운영자가 관리하는 변경 불가 평가 데이터셋
  - 현재 활성 모델 버전을 담은 `ModelRelease`
  - dataset generation scheduler와 training scheduler
  - admin/manual trigger 발행자
- 하위 소비자:
  - Model Artifact Files의 후보 모델 산출물
  - Metadata DB의 `MLPipelineRun`, `ModelEvaluation`
  - Object Storage의 학습 데이터셋, 평가 상세 아티팩트
  - 후속 `Model_Release_and_Reindex` 단계

### 간단한 흐름 (Simple Flow)
1. Dataset generation은 최근 raw feedback log를 읽어 학습 데이터셋 artifact와 manifest를 만든다.
2. Manifest에는 source window, generation rule, eligibility, lineage 요약을 기록한다.
3. Training trigger는 latest eligible dataset을 선택한다.
4. 활성 실행이 없으면 즉시 실행을 시작하고, 있으면 최신 dataset 기준 다음 실행 하나만 남긴다.
5. 실행이 시작되면 기준 모델 버전과 데이터셋 버전을 고정하고 후보 모델을 학습한다.
6. 후보 모델을 변경 불가 평가 데이터셋으로 기준 모델과 비교 평가한다.
7. 평가 `PASS`면 재색인 단계로 넘기고, `FAIL` 또는 시스템 오류면 실패로 종료한다.


---

## 2. 계약 (Contracts)

### 2.1 외부 인터페이스

#### 외부 진입 인터페이스
| 인터페이스 | 메서드 / 트리거 | 입력 요약 | 출력 요약 | 인증 / 테넌시 | 비고 |
| --- | --- | --- | --- | --- | --- |
| Dataset generation trigger | 스케줄 또는 admin/manual trigger | 설정된 source window의 raw feedback log | 학습 데이터셋 artifact와 manifest 생성 | 내부 운영 경로 | Dataset generation은 training request를 publish하지 않는다 |
| 학습 실행 consumer | 스케줄 또는 `TRAINING_REQUEST` 수신 | ML 실행 요청용 메시지 | latest eligible dataset 기준 실행 시작 또는 최신 대기 실행 갱신 | 운영자 권한 검증은 upstream 책임 | 자동 트리거와 수동 재트리거 모두 같은 메시지 계약을 사용한다 |

#### 내부 hand off 인터페이스
| 인터페이스 | 트리거 | 입력 요약 | 출력 요약 | 비고 |
| --- | --- | --- | --- | --- |
| 평가 `PASS` 내부 hand off | run이 릴리스 단계로 넘길 준비를 마침 | `run_id`, `trace_id` | 릴리스·재색인 시작 또는 실행 불가 기록 | 같은 Worker 내부 직접 호출만 사용한다. 수신 책임은 `MLPipelineRun`, `ModelEvaluation` 등 공유 SOT를 다시 읽어 문맥을 복원한다 |

#### 내부 실행 책임
- Dataset generation scheduler는 raw feedback log를 읽어 dataset artifact와 manifest를 만드는 시작 책임을 가진다.
- Training scheduler는 latest eligible dataset을 선택하고 training 실행 필요성을 판단하는 시작 책임을 가진다.
- Driver는 `PENDING`, `RUNNING`, `READY_FOR_RELEASE`, `FAILED`, `SUPERSEDED` 상태 전진을 집행한다.
- Reconciler는 장시간 진전이 없는 실행을 식별하고, 복구 가능한 실행은 다시 이어받게 하며, 복구가 불가능한 실행은 운영자가 식별 가능한 실패 상태로 남긴다.

#### 메시지 / 이벤트 계약
- Queue / topic: `TRAINING_REQUEST`
- Producer / consumer 책임:
  - Producer: training scheduler 또는 운영 경로가 실행 요청을 발행한다.
  - Consumer: 실행 시작 시점의 latest eligible dataset과 현재 활성 모델 버전을 조회해 이번 run의 기준으로 고정한다.
- 전달 의미론: at-least-once
- Payload versioning 규칙:
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

비고:
- 이 메시지의 실행 키는 `dataset_version`과 `MLPipelineRun.id`다.

#### 외부 서비스 계약
| 의존성 | 사용 목적 | 필요한 동작 / 가정 |
| --- | --- | --- |
| Feedback Ingestion Pipeline 원본 로그 | 학습 데이터셋 생성 입력 | 원본 이벤트는 append-only이고 검색 시점의 프로젝트, 모델, 인덱스 문맥을 보존해야 한다 |
| Object Storage | 학습 데이터셋/평가 상세 산출물 저장 | 버전형 산출물을 overwrite 없이 보존할 수 있어야 한다 |
| Model Artifact Files | 후보 모델 저장, 최초 기동 부트스트랩 | 모델 파일은 버전별 artifact로 저장되어야 하며, artifact 경로에서 모델 버전을 일관되게 식별할 수 있어야 한다. 최초 기동 시 서비스는 환경변수로 지정한 경로에서 모델을 로드하고 버전을 파싱한다고 가정한다. |
| Metadata DB `ModelRelease` | 실행 중 활성/후보 모델 및 인덱스 조회 | 서비스 실행 중 모델 선택의 SOT는 `ModelRelease`여야 하며, 각 컴포넌트는 여기서 활성/후보 모델 버전과 대응 인덱스를 동일하게 읽는다고 가정한다. 레코드가 없을 때만 환경변수 기반 기본값으로 초기화한다. |
| Evaluation Dataset Artifact | 후보/기준 모델 비교 평가 | 학습셋과 분리된 immutable artifact여야 한다 |

### 2.2 데이터 계약

#### 소유 데이터 (이 컴포넌트가 SOT인 경우)
| 엔터티 / 테이블 | 목적 | 핵심 필드 / 불변조건 | 비고 |
| --- | --- | --- | --- |
| TrainingDataset Artifact | 원본 피드백 로그를 retrieval training group 입력으로 고정한 버전형 산출물 | `dataset_version`, `storage_path`, `created_at`, `generation_rule_version`, `eligible`; 변경 불가; 학습셋과 평가셋은 분리 | Object Storage 저장 |
| `MLPipelineRun` | 실행 제어와 추적의 SOT | `status`, `failed_stage`, `failure_type`, `failure_reason`, `candidate_model_version`, `dataset_version`, `evaluation_id`, `superseded_by_run_id`, `created_at`, `updated_at` | `candidate_index_name`, `cutover_time`는 후속 release/reindex 단계가 채운다 |
| `ModelEvaluation` | 후보 vs 기준 모델의 집계 평가 결과 | `candidate_model_version`, `baseline_model_version`, `evaluation_dataset_ref`, `quality_metrics`, `pass_criteria`, `overall_decision`, `fail_reason` | `overall_decision`은 `PASS | FAIL` |
| ModelEvaluationDetail Artifact | 질의별 상세 비교 결과 | `evaluation_id`, `storage_path`, `format=jsonl`, `created_at`; immutable | Object Storage 저장 |

학습 데이터셋 산출물은 retrieval training group을 기준 구조로 사용한다.
각 group은 한 query에 대한 positive 후보, negative 후보, 판단 근거, 신뢰도, source event 추적 정보를 함께 보존한다.

학습 데이터셋 산출물은 저장 포맷과 무관하게 아래 의미를 보존해야 한다:
- `event_id`
- `user_id`
- `project_id`
- `query_text`
- `rating`
- `topk_ids`
- `used_ids`
- `active_model_version`
- `active_index_name`
- `response_snapshot_ref`
- `created_at`

`topk_ids`와 `used_ids`는 검색 시점의 근거와 추적 정보다.
Dataset generation rule은 이 근거를 positive/negative 후보와 source confidence로 변환한다.

Source별 confidence, random negative 보강량, source window, scheduler cadence, eligibility threshold는 PLAN에서 확정한다.
Dataset selection은 supported generation rule의 eligible manifest만 대상으로 한다.
기준에 못 미친 artifact는 보존하되 `eligible=false`와 `ineligible_reasons`를 manifest에 기록한다.

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
| SOT 소유자 | 엔터티 / 테이블 | 의존 필드 | 읽기 전용 가정 |
| --- | --- | --- | --- |
| Feedback Ingestion Pipeline | 원본 피드백 이벤트 로그 | `event_id`, `user_id`, `project_id`, `query_text`, `rating`, `topk_ids`, `used_ids`, `active_model_version`, `active_index_name`, `response_snapshot_ref`, `created_at` | 이벤트는 append-only이며 후행 수정되지 않는다. `event_id`는 논리적 feedback identity이며, 같은 `user_id`, `req_id`, `rating` 조합의 재전달은 같은 `event_id`를 사용한다 |
| Model Release / Metadata DB | `ModelRelease` | `active_model_version` | 실행 시작 후에는 이번 run의 baseline으로 고정한다 |
| Admin-managed evaluation artifact | 평가 데이터셋 | `evaluation_dataset_ref`, `query_text`, `expected_results` | 변경 불가이며 학습셋과 분리되어 있다 |

### 2.3 상태 및 비즈니스 규칙
- 항상 유지되어야 하는 불변조건:
  - 동시에 활성 실행인 `MLPipelineRun`은 하나만 존재한다.
  - 동시에 다음 실행 대기 레코드도 하나만 존재한다.
  - 다음 실행 대기 레코드는 항상 최신 `dataset_version`을 가리켜야 하며, 이전 대기 실행은 `SUPERSEDED`가 된다.
  - 한 번 시작한 run의 `dataset_version`과 `baseline_model_version`은 중간에 바뀌지 않는다.
  - 평가 데이터셋은 학습 데이터셋과 분리된 변경 불가 산출물이어야 한다.
  - 학습 데이터셋은 feedback event의 `project_id`를 손실 없이 보존해야 한다.
  - Dataset generation 성공은 training run을 직접 만들지 않는다.
  - Training 실행은 latest eligible dataset이 있을 때만 시작할 수 있다.

#### Run 상태 소유권
`MLPipelineRun` 생성과 상태 전이는 실행 상태 관리 경계에서만 수행한다. Scheduler, Driver, Consumer, Reconciler는 `MLPipelineRun` 레코드를 직접 생성하거나 상태를 직접 갱신하지 않고, 이 경계가 제공하는 원자적 전이 작업을 호출한다.

이 경계는 각 상태 전이를 DB 트랜잭션 안에서 처리하며, 2.3의 불변조건과 invalid condition을 같은 쓰기 경계 안에서 검증한다.

- `MLPipelineRun.status`는 SOT의 다섯 상태(`PENDING`, `RUNNING`, `READY_FOR_RELEASE`, `FAILED`, `SUPERSEDED`)만 사용한다.
- `failed_stage`는 최소한 `데이터셋 생성`, `학습`, `평가`를 구분할 수 있어야 한다.
- `failure_type`은 `FAIL | ERROR`를 사용한다.
- 평가 `PASS` hand off는 평가 결과와 상세 아티팩트가 영속 저장된 뒤에만 수행한다.
- 내부 직접 호출에는 최소 식별자만 포함한다. 릴리스·재색인 단계의 실행 문맥은 수신 책임이 공유 SOT에서 다시 읽는다.
- Training scheduler는 실행 필요성을 만들지만, 이미 시스템이 최신 dataset 기준으로 수렴 중이면 같은 목표를 중복으로 확장하지 않는다.
- Driver는 `PENDING` 실행을 `RUNNING`으로 전진시키고, 평가 `PASS`가 확정되면 `READY_FOR_RELEASE`를 기록한 뒤 hand off를 시작한다.
- Reconciler는 장시간 진전이 없는 `RUNNING` 실행을 방치하지 않는다.
- 복구 가능한 실행은 다시 이어받을 수 있어야 하며, 복구가 불가능한 실행은 `FAILED`로 남아야 한다.
- 거부되어야 하는 전이 / invalid condition:
  - 활성 실행이 있는데 두 번째 활성 실행을 시작하는 동작
  - 이미 대기 중인 실행이 있을 때, 더 최신 데이터셋 기준 실행이 생기었을 때 기존 대기 실행이 남아 있는 경우
  - 평가 결과와 상세 아티팩트가 모두 기록되기 전에 재색인 단계로 넘길 준비 완료로 표기하는 동작

- 멱등성 규칙:
  - 동일한 `TRAINING_REQUEST`가 중복 전달되어도 병렬 실행을 추가로 만들지 않는다.
  - 이미 같은 최신 `dataset_version`으로 대기 중인 실행이 있으면 새 대기 실행을 더 만들지 않는다.
  - 같은 원본 피드백 이벤트는 데이터셋 생성 단계에서 `event_id` 기준으로 중복 제거할 수 있어야 한다.
  - 이 `event_id`는 upstream 생성 규칙상 UUIDv5와 canonical string `feedback:{user_id}:{req_id}:{rating}`에 의해 안정적으로 재사용된다고 가정한다.
- 멀티테넌트 / 인가 규칙:
  - 이 컴포넌트는 사용자별 테넌시를 직접 다루지 않는다.
  - `user_id`와 `project_id`는 학습 데이터 lineage와 분석 문맥으로 보존하며, 이 컴포넌트의 권한 판단 기준으로 사용하지 않는다.
  - 정기 배치와 수동 재트리거의 권한 검증은 upstream 운영 경로 책임이다.

| From | To | Trigger | Guard / rule | 필요한 side effect |
| --- | --- | --- | --- | --- |
| 없음 | `RUNNING` | `TRAINING_REQUEST` 수신 | 활성 실행 없음, latest eligible dataset 존재 | `dataset_version`, `baseline_model_version`, `candidate_model_version` 고정 |
| 없음 또는 기존 대기 | `PENDING` | 활성 실행 중 `TRAINING_REQUEST` 수신 | latest eligible dataset 기준 다음 실행만 유지 | 기존 대기 실행은 `SUPERSEDED` 처리 |
| `PENDING` | `RUNNING` | 활성 슬롯 확보 | 가장 최신 대기 실행만 시작 | 실행 기준 버전 고정 |
| `RUNNING` | `READY_FOR_RELEASE` | 학습 완료 + 평가 `PASS` | 후보 모델 저장, 평가 요약/상세 저장 완료 | hand off 준비 상태를 기록하고 `run_id`, `trace_id`로 내부 직접 호출 |
| `RUNNING` | `FAILED` | 평가 `FAIL` 또는 시스템 오류 | `failed_stage`, `failure_type` 기록 | 기존 서빙 유지 |
| `PENDING` | `SUPERSEDED` | 더 최신 데이터셋 준비 | 자신보다 최신 `dataset_version`이 대기 슬롯을 차지 | `superseded_by_run_id` 기록 |

### 2.4 한계와 운영 제약
- 성능 / 지연 목표:
  - 사용자 동기 요청 경로가 아니므로 저지연보다 최신 데이터셋 수렴과 실행 직렬화가 우선이다.
- Throughput / rate / concurrency 한계:
  - 실행 단위 동시성은 활성 실행 1개, 다음 실행 대기 1개로 고정한다.
  - 학습 내부 병렬도와 배치 크기는 PLAN에서 확정한다.
- Payload / 파일 크기 / pagination 한계:
  - `TRAINING_REQUEST` 메시지에는 데이터셋 본문이나 평가 본문을 넣지 않는다.

- Timeout / TTL / retry 제약:
  - 메시지 재전달은 at-least-once를 전제로 한다.
  - 구체 timeout, retry 횟수, backoff 수치는 PLAN에서 확정한다.
  - `FAIL`은 품질 미달, `ERROR`는 시스템 오류로 운영 화면에서 구분 가능해야 한다.
- 보안 / 개인정보 제약:
  - 학습 데이터셋과 원본 피드백 로그는 `user_id`, `project_id`, `query_text`, 모델/인덱스 문맥을 포함할 수 있으므로 내부 운영 경로만 접근 가능해야 한다.
  - 평가 상세 아티팩트는 사용자 외부 노출 경로를 갖지 않는다.

### 2.5 에러 계약
| 표면 | 조건 | 코드 / 상태 | 재시도 가능 | 비고 |
| --- | --- | --- | --- | --- |
| 정기 배치 / 데이터셋 생성 | 원본 피드백 로그 읽기 또는 산출물 저장 실패 | 실행 실패 / 실패 단계=`데이터셋 생성` / `failure_type=ERROR` | Y | 기존 서빙은 유지 |
| 학습 실행 consumer | 지원하지 않는 `payload_version` | invalid message | N | 정상 실행으로 처리하지 않는다 |
| 학습 단계 | 후보 모델 학습 또는 artifact 저장 실패 | 실행 실패 / 실패 단계=`학습` / `failure_type=ERROR` | Y | 기존 서빙은 유지 |
| 평가 단계 | 평가 계산/저장 오류 | 실행 실패 / 실패 단계=`평가` / `failure_type=ERROR` | Y | 기존 서빙은 유지 |
| 평가 단계 | 품질 지표가 `pass_criteria` 미달 | 실행 실패 / 실패 단계=`평가` / `failure_type=FAIL` | N | 후보 모델은 서빙 전환 대상이 아니다 |


---

## 3. 관측성과 운영

- 필수 log field:
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
- 추적할 핵심 metric / alert:
  - 신규 학습 데이터셋 생성 건수
  - 현재 실행 중인 MLPipelineRun 존재 여부
  - 현재 다음 순서로 대기 중인 MLPipelineRun 존재 여부
  - 장시간 진전이 없는 실행 존재 여부
  - `SUPERSEDED` 발생 건수
  - 학습 단계 소요 시간
  - 평가 단계 소요 시간
  - 평가 `PASS` / `FAIL` / `ERROR` 비율
  - `failed_stage`별 실패 건수
  - 최신 성공 run이 반영한 `dataset_version`과 최신 생성 `dataset_version`의 차이
- Trace / correlation 전파 규칙:
  - 자동 트리거와 수동 재트리거 모두 `trace_id`를 `MLPipelineRun`, 로그, 평가 아티팩트 메타데이터에 일관되게 남겨야 한다.
- Reconciliation / cleanup 요구사항:
  - `SUPERSEDED`된 run은 삭제하지 않고 이력으로 남겨야 한다.
  - 장시간 진전이 없는 실행은 운영적으로 식별 가능해야 한다.
  - 재색인 단계로 넘긴 뒤의 후보 인덱스/릴리스 정리는 이 SPEC이 아니라 후속 release/reindex 단계 책임이다.

---

## 4. 인수 기준

### 4.1 반드시 통과해야 하는 시나리오
- [ ] 신규 원본 피드백 로그가 있으면 학습 데이터셋 artifact와 manifest가 생성되고, dataset generation은 training run을 직접 만들지 않는다.
- [ ] Training trigger는 latest eligible dataset을 선택하고, 활성 실행이 없으면 즉시 실행을 시작하며, 있으면 최신 dataset 기준 다음 실행 하나만 유지한다.
- [ ] 학습 데이터셋은 raw feedback event의 `project_id`를 각 retrieval training group의 검색 문맥으로 보존한다.
- [ ] 더 새로운 데이터셋이 준비되면 이전 대기 실행은 `SUPERSEDED`로 남고, 오래된 대기 실행이 실제 시작되지 않는다.
- [ ] 실행이 시작되면 `dataset_version`, `baseline_model_version`, `candidate_model_version`이 이번 run 기준으로 고정되고 후보 모델 artifact가 저장된다.
- [ ] 평가가 끝나면 `ModelEvaluation` 요약과 질의별 상세 artifact가 모두 저장되며, `quality_metrics`와 `pass_criteria`만으로 `PASS` / `FAIL`을 재현할 수 있다.
- [ ] 평가 `PASS` 시 run은 `READY_FOR_RELEASE` 상태로 남고, 내부 직접 호출에는 최소 식별자만 전달되며, 이 단계에서는 아직 `ModelRelease`가 변경되지 않는다.
- [ ] 평가 `FAIL` 또는 시스템 오류 시 run은 실패로 종료되고, `failed_stage`와 `failure_type(FAIL|ERROR)`가 운영 화면에서 구분 가능하게 남는다.
- [ ] 중복 `TRAINING_REQUEST`가 와도 활성 실행이 2개 이상 생기지 않는다.
- [ ] 장시간 진전이 없는 실행은 운영적으로 식별 가능하며, 복구 또는 실패 정리 대상으로 분류된다.

### 4.2 비목표 / 보류 항목
- 후보 인덱스 물리 생성 방식
- 재색인 중 online ingest dual-write 규칙
- 후보 모델 readiness 검증 절차
- `ModelRelease` 갱신과 실제 서빙 전환
- 롤백 실행과 rollback 대상 선택

---
