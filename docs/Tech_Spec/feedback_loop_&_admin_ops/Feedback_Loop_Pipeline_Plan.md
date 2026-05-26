# [Feedback Loop Pipeline] PLAN

> 피드백 기반 학습 루프를 `services/feedback-loop-pipeline`에 구현하기 위한 실행 문서.
> 이미 구현된 foundation schema와 기존 서비스 계약은 다시 만들지 않고, 남은 구현 작업만 추린다.

**메타 정보**
- 컴포넌트 ID: `feedback-loop-pipeline`
- SOT: `docs/system-design.md`
- 대상 SPEC:
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/ML_Pipeline_Execution_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Model_Release_and_Reindex_Spec.md`
- 관련 문서:
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/학습 파이프라인 plan 작업전 결정사항정리.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Admin_Control_Plane_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Feedback_Ingestion_Pipeline_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Pipeline_Worker_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Managed_Embedding_Endpoint_Spec.md`
- Plan 상태: 초안

---

## 1. 구현 의도

### 1.1 전달 목표
- 이 plan이 끝났을 때 실제로 동작해야 하는 것:
  - `feedback-loop-pipeline`이 raw feedback log를 읽어 학습 데이터셋 artifact를 만든다.
  - 최신 eligible 데이터셋 기준으로 `MLPipelineRun`을 생성하고, 활성 실행 1개와 대기 실행 1개 규칙을 지킨다.
  - 로컬 소형 임베딩 모델 학습, 후보 모델 artifact 생성, baseline/candidate 평가를 수행한다.
  - 평가 `PASS` 후보를 candidate release로 열고, candidate 반영 완료 후 cutover한다.
  - rollback 요청은 요청자가 본 active release에만 적용하고, 영향 프로젝트는 검색과 신규 ingest에서 제외한 뒤 복구한다.
- 검증 가능한 형태로 입증되어야 하는 것:
  - 기존 DB schema 위에서 상태 전이가 안전하게 동작한다.
  - dataset/model/evaluation artifact는 버전 경로와 manifest로 재현 가능하다.
  - 중복 trigger, 중복 handoff, stale rollback request, candidate 누락 chunk가 차단된다.

### 1.2 이미 구현되어 이번 plan에서 제외하는 것
- DB / schema:
  - `project.search_serving_state`
  - `search_response_snapshot`
  - `model_evaluation`
  - `ml_pipeline_run`
  - `model_release`
  - `vector_index_entry.index_name`
  - `vector_index_entry.project_id`
  - `vector_index_entry.embedding_model_version`
  - `vector_index_entry.created_at`
- 기존 service projection:
  - Core API의 Admin Ops foundation model / schema skeleton
  - Pipeline Worker의 `ModelEvaluationModel`, `MLPipelineRunModel`, `ModelReleaseModel`, `VectorIndexEntryModel`
  - Search Service의 `ROLLBACK_EXCLUDED` 프로젝트 검색 제외 gate
- 기존 infra pattern:
  - Core API / Pipeline Worker의 GCS 및 in-memory storage client 패턴
  - 기본 `TRAINING_REQUEST`, `ROLLBACK_REQUEST` control message skeleton
- 기존 폴더:
  - `services/feedback-loop-pipeline` 폴더 자체는 이미 존재한다. 현재는 비어 있으므로 서비스 구현 파일은 새로 만든다.

### 1.3 이번 구현의 범위
- 이번 plan에 포함:
  - `services/feedback-loop-pipeline` Poetry 서비스 구성
  - loguru 기반 로깅, pytest 테스트 구조, settings, DB session, storage adapter 구성
  - 기존 Metadata DB schema를 사용하는 repository/port 구현
  - raw feedback log reader, dataset materializer, artifact store
  - run scheduler/consumer/reconciler와 `MLPipelineRun` 상태 전이
  - local training runner, model artifact manifest, evaluator
  - release handoff, candidate index open, candidate completeness check, cutover
  - rollback guard, project exclusion, ingest block, restored-model reembedding orchestration
  - Pipeline Worker dual-write와 rollback upsert gate 보완
  - Core API rollback payload와 ingest admission gate 보완
- 이번 plan에서 제외하고 후속 단계로 미룸:
  - Vertex AI Custom Job 실행기
  - 전체 코퍼스 즉시 재색인
  - 활동도 기반 우선순위 재색인
  - 공개 Admin UI 구현
  - retired/problem index 물리 삭제 자동화
  - production 수준의 모델 품질 최적화

### 1.4 남은 schema / storage 판단
- 새 DB 저장소를 대규모로 만들 필요는 없다.
- `VectorIndexEntry.status`는 추가하지 않는다.
- 단, candidate completeness의 시간 범위 판단은 현재 schema만으로 애매하다. `video.ready_at`이 없고, candidate release 시작 시각도 명시 컬럼으로 없다.
- 따라서 Workstream 1에서 아래 둘 중 하나를 확정해야 한다:
  - 최소 schema delta로 candidate release 시작 시각을 기록한다. 예: `MLPipelineRun.candidate_opened_at` 또는 `ModelRelease.candidate_opened_at`.
  - schema 추가 없이 기존 `updated_at` / `created_at` 조합으로 범위를 정의한다. 이 경우 상태 전이 시각이 다른 갱신으로 오염되지 않는다는 보장이 필요하다.
- 이 결정을 끝내기 전에는 candidate completeness query를 최종 구현하지 않는다.

### 1.5 구현 전략
- DB table 생성은 Core API migration을 SOT로 둔다.
- `feedback-loop-pipeline`은 필요한 table을 직접 소유하지 않고, 기존 schema를 읽고 쓰는 repository/port를 가진다.
- 상태 전이는 두 경계로 나눈다:
  - `control_plane`: `MLPipelineRun` 생성과 상태 전이
  - `serving_transition_manager`: `ModelRelease`와 `Project.search_serving_state` 전이
- 장시간 작업은 checkpoint에서 취소/무효화 여부를 확인하고, 완료 직전 DB SOT를 다시 읽어 stale 결과 반영을 막는다.

---

## 2. 작업 흐름과 순서

### 2.1 권장 순서
| 순서 | 작업 흐름 | 연결되는 SPEC | 지금 먼저 하는 이유 | 의존성 |
| --- | --- | --- | --- | --- |
| 1 | 서비스 기반 구조와 계약 보완 | ML Spec §2.1-2.3, Release Spec §2.2-2.3 | 빈 서비스 폴더를 실행 가능한 컴포넌트로 만들고, 남은 cross-service contract gap을 닫는다 | 기존 Admin Ops Foundation schema |
| 2 | Dataset / Artifact materialization | ML Spec §2.1-2.2 | 학습 실행의 입력 artifact를 먼저 고정해야 run을 만들 수 있다 | Workstream 1, FIP raw log |
| 3 | Run control, local training, evaluation | ML Spec §2.2-2.5 | `READY_FOR_RELEASE`까지의 ML 실행 흐름을 완성한다 | Workstream 2 |
| 4 | Candidate release, dual-write, cutover | Release Spec §2.1-2.5 | 평가 PASS 이후 serving 전환을 구현한다 | Workstream 3, Pipeline Worker, endpoint readiness |
| 5 | Rollback, ingest gate, operability | Release Spec §2.3-2.5, §3 | 운영 실패 경로와 복구 경로를 닫는다 | Workstream 4, Admin rollback request |

### 2.2 Workstream 상세

#### Workstream 1: 서비스 기반 구조와 계약 보완
- 목표:
  - `services/feedback-loop-pipeline`을 실행 가능한 Poetry 서비스로 만들고, 기존 DB schema를 사용하는 접근 경계를 만든다.
  - 이미 구현된 DB table은 다시 만들지 않는다.
- 주요 변경:
  - `pyproject.toml`, `src/feedback_loop_pipeline/`, `tests/` 생성
  - loguru 로깅, pytest 설정, settings, clock, trace id helper 추가
  - DB session factory와 SQLAlchemy model projection 추가 또는 기존 projection 정렬
  - storage adapter port 추가: raw log read, artifact read/write, URI 생성
  - broker/control-message client port 추가
  - `MLPipelineRunStore`, `ModelEvaluationStore`, `ModelReleaseStore`, `ProjectServingStateStore`, `VectorIndexProjectionReader` 구현
  - `RunReleaseUpdatePort` 구현: `record_candidate_index`, `record_cutover_time`, `mark_release_failure`
  - Core API / Pipeline Worker의 `ROLLBACK_REQUEST` schema에 `expected_active_model_version`, `expected_switched_at` 추가
  - candidate completeness 시간 범위 기준 확정 및 필요한 최소 schema delta 반영
- 완료 기준:
  - 서비스가 settings를 로드하고 DB 연결을 만들 수 있다.
  - 현재 `ModelRelease`를 읽을 수 있다.
  - `MLPipelineRun` 상태 변경은 control plane store를 통해서만 수행된다.
  - rollback control message가 expected active release 필드를 포함한다.
  - candidate completeness 시간 범위 기준이 테스트 가능한 계약으로 고정된다.
- 검증:
  - `cd services/feedback-loop-pipeline && poetry run pytest tests/unit/test_settings.py`
  - `cd services/feedback-loop-pipeline && poetry run pytest tests/unit/test_state_stores.py`
  - `cd services/core-api && poetry run pytest tests/unit/test_admin_ops_schemas.py`
  - `cd services/pipeline-worker && poetry run pytest tests/unit/test_message_schemas.py`

#### Workstream 2: Dataset / Artifact materialization
- 목표:
  - Object Storage의 raw feedback log를 retrieval training group 기반 immutable dataset artifact로 변환한다.
- 주요 변경:
  - raw feedback log reader 구현
  - daily dataset generation scheduler는 매일 03:00 KST에 실행
  - 실행 시점 기준 최근 30일 source window 적용
  - `event_id` 기준 dedupe 구현
  - `rating == like`, `used_ids`, `topk_ids - used_ids` 기반 positive/exposed-unused 후보 생성
  - same-project random negative 후보 생성
  - group row에 positive/negative text snapshot, source, confidence, lineage 포함
  - group/positive/negative/source/drop count 중심 dataset manifest 생성
  - `training_group_count >= 10`, `negative_count >= 20` 기준 eligibility 검증
  - artifact path 규칙 구현
  - local/in-memory artifact store double 구현
- 완료 기준:
  - 같은 raw log 입력은 같은 dedupe 결과와 training group count를 만든다.
  - dataset artifact가 retrieval training group row, text snapshot, manifest를 포함한다.
  - latest eligible `retrieval-group-v1` dataset을 선택할 수 있다.
  - dataset generation 성공은 training run을 직접 만들지 않는다.
- 검증:
  - `tests/unit/test_dataset_materializer.py`
  - `tests/unit/test_dataset_manifest.py`
  - artifact golden fixture test

#### Workstream 3: Run control, local training, evaluation
- 목표:
  - dataset을 입력으로 run을 만들고, 로컬 학습과 평가를 거쳐 `READY_FOR_RELEASE` 또는 `FAILED`로 종료한다.
- 주요 변경:
  - `TRAINING_REQUEST` consumer 구현
  - weekly training scheduler는 매주 월요일 04:00 KST에 latest eligible dataset을 선택한다.
  - 활성 실행 1개와 대기 실행 1개 규칙 구현
  - `PENDING`, `RUNNING`, `READY_FOR_RELEASE`, `FAILED`, `SUPERSEDED` 전이 구현
  - `baseline_model_version`, `candidate_model_version`, `dataset_version`, `evaluation_dataset_ref` 고정
  - `TrainingRunner` interface와 `LocalTrainingRunner` 구현
  - `model_manifest.json` 9개 필드 검증
  - evaluation corpus 기반 evaluator 구현
  - `Recall@5`, `MRR@5`, `NDCG@5` 계산 및 PASS/FAIL 판정
  - `ModelEvaluation` row와 evaluation detail artifact 저장
  - 평가 PASS 후 내부 handoff 호출
- 완료 기준:
  - 중복 `TRAINING_REQUEST`가 병렬 run을 만들지 않는다.
  - 학습 성공 시 candidate model artifact와 manifest가 생성된다.
  - 평가 결과는 DB summary와 detail artifact로 재현 가능하다.
  - PASS run은 `READY_FOR_RELEASE`로 남고 handoff에는 `run_id`, `trace_id`만 전달된다.
- 검증:
  - `tests/integration/test_run_slot_rules.py`
  - `tests/unit/test_model_manifest.py`
  - `tests/unit/test_evaluator.py`
  - `tests/integration/test_training_run_flow.py`

#### Workstream 4: Candidate release, dual-write, cutover
- 목표:
  - 평가 PASS 후보를 candidate release로 열고, candidate 반영 완료와 endpoint readiness 확인 후 serving을 전환한다.
- 주요 변경:
  - `serving_transition_manager.open_candidate_release(run_id, trace_id)` 구현
  - `ModelRelease`를 `CANDIDATE_REINDEXING`으로 전이
  - candidate index naming 구현
  - `MLPipelineRun.candidate_index_name`, `MLPipelineRun.cutover_time` 기록
  - Managed Embedding Endpoint candidate readiness adapter 구현
  - Pipeline Worker active/candidate dual-write 구현
  - `CandidateCompletenessChecker` 구현
  - cutover 직전 rollback snapshot capture 구현
  - cutover 성공 시 `active=candidate`, `previous=old active`, candidate fields clear
- 완료 기준:
  - 같은 run의 중복 PASS handoff는 no-op다.
  - `CANDIDATE_REINDEXING` 동안 신규 READY 데이터가 active/candidate index에 모두 기록된다.
  - candidate row가 누락된 chunk가 있으면 cutover가 차단된다.
  - cutover 성공 후 `ModelRelease`가 `STABLE`로 돌아가고 candidate fields가 비워진다.
- 검증:
  - `tests/integration/test_candidate_release_flow.py`
  - Pipeline Worker dual-write integration test
  - candidate missing-row blocked cutover test
  - duplicate handoff no-op test

#### Workstream 5: Rollback, ingest gate, operability
- 목표:
  - stale rollback request와 problem-model 확산을 막고, snapshot 기준 복구와 프로젝트 재편입을 수행한다.
- 주요 변경:
  - `ROLLBACK_REQUEST` consumer 구현
  - `expected_active_model_version`, `expected_switched_at` guard 구현
  - problem-model chunk가 있는 affected project selector 구현
  - affected project를 `ROLLBACK_EXCLUDED`로 전이
  - Core API 신규 video ingest admission gate 구현
  - Pipeline Worker의 `ROLLBACK_PREPARING` 중 problem active model embedding/vector upsert 차단
  - snapshot active index restore orchestration 구현
  - rollback target readiness adapter 구현
  - restored-model reembedding과 vector reflection 구현
  - project reentry checker 구현
  - stuck run / stuck rollback reconciliation 구현
  - stage별 timeout, retry budget, batch size, backoff 설정 키와 기본값 고정
  - loguru structured log와 주요 metric 추가
- 완료 기준:
  - stale rollback request는 상태 변경 없이 무시된다.
  - 영향 프로젝트는 검색에서 제외되고 신규 ingest도 차단된다.
  - rollback restore 후 candidate fields가 비워지고 snapshot active model/index가 복원된다.
  - 복구된 프로젝트는 restored-model vector reflection 완료 후에만 `SERVABLE`로 돌아온다.
- 검증:
  - `tests/integration/test_rollback_flow.py`
  - Core API ingest block test
  - Pipeline Worker rollback upsert gate test
  - stale rollback request test
  - settings validation test

### 2.3 병렬화와 병합 지점
- 병렬로 진행해도 안전한 작업:
  - ArtifactStore와 evaluator는 artifact 구조가 고정된 뒤 병렬 구현 가능
  - Endpoint readiness adapter와 candidate completeness checker는 병렬 구현 가능
  - Core API ingest gate와 Pipeline Worker dual-write는 payload/schema가 고정된 뒤 병렬 구현 가능
- 충돌 가능성이 높은 영역:
  - `ModelRelease` transaction boundary
  - `MLPipelineRun.candidate_index_name`, `cutover_time`, candidate 시작 시각 기록
  - `Project.search_serving_state` 전이
  - Pipeline Worker vector write path
  - Core API video ingest admission path
- 최종 통합 checkpoint:
  - raw feedback log seed
  - dataset materialization
  - local train/evaluate
  - candidate release open
  - candidate missing row cutover block 확인
  - candidate entry 완성 후 cutover
  - rollback request 발행
  - snapshot restore와 project reentry 확인

---

## 3. 검증 및 테스트 전략

### 3.1 리스크 기반 테스트 초점
| Spec ref | 리스크 또는 business rule | 중요한 이유 | 적합한 테스트 수준 | 계획된 증거 |
| --- | --- | --- | --- | --- |
| ML §2.3 | active/pending run 중복 생성 | baseline/candidate 기준이 깨진다 | Integration | concurrent trigger 후 `RUNNING` 1개와 최신 `PENDING` 1개만 존재 |
| ADR-011 | dataset group에 text snapshot 누락 | 학습 재현성이 DB 상태에 의존한다 | Unit + fixture | group row에 positive/negative text, source, confidence 포함 |
| ML §2.5 | 평가 PASS 재현 불가 | 잘못된 후보가 release로 넘어간다 | Unit + integration | 저장된 metrics/pass criteria로 PASS/FAIL 재현 |
| Release §2.3 | candidate row 누락 상태 cutover | 전환 후 검색 누락 발생 | Integration | missing candidate `VectorIndexEntry`가 cutover 차단 |
| Release §2.3 | stale rollback request 처리 | 잘못된 active release 복원 | Integration | expected active mismatch 시 상태 변경 없음 |
| Release §2.3 | rollback 중 problem-model ingest/upsert 지속 | 오염 데이터와 복구 backlog 증가 | Integration / contract | ingest와 problem-model upsert 차단 |

### 3.2 계획된 자동화 테스트
| Spec ref / acceptance criterion | 시나리오 또는 규칙 | 테스트 수준 | 이 수준이 필요한 이유 | 관찰 가능한 증거 |
| --- | --- | --- | --- | --- |
| ML AC 1 | raw log가 eligible dataset artifact를 만들고 training trigger가 run 생성 | Integration | storage와 DB 상태가 함께 동작 | dataset artifact 존재, training trigger 후 run slot 갱신 |
| ML AC 3 | 최신 pending이 오래된 pending supersede | Integration | transaction과 unique index가 중요 | 오래된 pending이 `SUPERSEDED` |
| ML AC 4 | run start 시 version 고정 | Integration | baseline은 시작 시점 current release 기준 | run row에 기준 version/ref 고정 |
| ML AC 5 | evaluation summary/detail 저장 | Integration | artifact write와 DB write 정렬 필요 | `ModelEvaluation` row와 detail artifact ref 존재 |
| Release AC 1 | PASS handoff가 candidate state open | Integration | release state와 run tracking 결합 | `ModelRelease=CANDIDATE_REINDEXING`, candidate fields set |
| Release AC 2 | candidate completeness가 cutover 차단 | Integration | DB projection 기준 completeness | missing candidate row가 release 유지 |
| Release AC 6 | rollback generation guard | Integration | at-least-once message stale 가능 | expected active mismatch reject |
| Release AC 8 | rollback excluded project ingest 차단 | API/Integration | Core API admission gate 필요 | excluded 중 create/ingest 차단 |

### 3.3 자동화 테스트로 다루지 않는 항목
| Spec ref / rule | 자동화하지 않는 이유 | 수동 또는 운영 증거 |
| --- | --- | --- |
| 실제 임베딩 모델 품질 최적화 | v1 목표는 end-to-end lifecycle 검증 | curated evaluation report와 metric artifact |
| Vertex runner compatibility | v1 scope는 local runner only | interface review와 향후 `VertexTrainingRunner` plan |
| production object storage retention policy | storage lifecycle은 ops 정책 | deployment config review |
| retired/problem index physical deletion | target specs에서 deferred | operator cleanup ticket |

### 3.4 테스트 환경과 double
- DB:
  - 기존 PostgreSQL/SQLAlchemy fixture를 우선 사용한다.
  - Admin Ops Foundation schema test는 재사용하고, 신규 schema delta가 생긴 경우에만 추가한다.
- Storage:
  - feedback-loop-pipeline 내부에는 local filesystem 또는 in-memory artifact store double을 둔다.
  - 기존 Core API / Pipeline Worker storage client는 패턴 참조만 하고 큰 코드 복사는 피한다.
- Broker:
  - unit/integration은 in-memory control-message client를 사용한다.
  - PGMQ contract test는 broker adapter가 생긴 뒤 추가한다.
- 외부 의존성:
  - Training runner는 fake runner와 deterministic small model을 분리한다.
  - Managed Embedding Endpoint는 readiness client double을 사용한다.
  - Vector completeness는 live ANN query가 아니라 DB `VectorIndexEntry` row로 검증한다.

### 3.5 검증 명령과 quality gate
- 필수 명령:
  - `cd services/feedback-loop-pipeline && poetry run pytest`
  - `cd services/core-api && poetry run pytest tests/unit/test_admin_ops_schemas.py tests/api/v1/test_admin_ops_router_wiring.py tests/integration/test_admin_ops_foundation_schema.py`
  - `cd services/pipeline-worker && poetry run pytest tests/unit/test_message_schemas.py tests/integration/test_repositories.py`
  - `cd services/search-service && poetry run pytest tests/integration/test_search_repository.py`
  - `cd services/managed-embedding-endpoint && poetry run pytest tests/api/test_health.py tests/unit/test_model_state.py`
- merge 전 최소 meaningful check:
  - dataset artifact fixture test 통과
  - local training/evaluation integration test 통과
  - run slot concurrency test 통과
  - candidate completeness missing-row test 통과
  - rollback stale request와 ingest block test 통과
- 첨부할 증거:
  - pytest output
  - sample dataset manifest
  - sample `model_manifest.json`
  - sample evaluation detail artifact
  - run, evaluation, handoff, cutover, rollback trace log

---

## 4. 전달 리스크와 안전장치

| 리스크 | 영향 | 완화책 | 검증 |
| --- | --- | --- | --- |
| 기존 DB contract 오해 | 이미 있는 schema를 중복 생성하거나 다른 의미로 사용 | Core API migration을 schema SOT로 고정 | schema/model contract test |
| candidate completeness 범위 불명확 | cutover 차단이 너무 약하거나 너무 강함 | candidate 시작 시각 기준을 Workstream 1에서 확정 | missing-row cutover test |
| duplicate run creation | 여러 후보가 서로 다른 baseline으로 학습 | transactional run slot operation | concurrent trigger test |
| model artifact load 실패 | candidate readiness가 늦게 실패 | manifest 9개 필드와 artifact format 검증 | manifest validation test |
| Pipeline Worker dual-write 누락 | candidate index가 비어 cutover 불가 | release state 기반 dual-write gate 구현 | dual-write integration test |
| stale rollback request | 잘못된 active release 복원 | expected active model/switched time 비교 | stale request test |
| rollback 중 신규 problem-model data | 복구 backlog 증가 | ingest block과 upsert gate 구현 | ingest/upsert block test |

---

## 5. 배포와 롤백

### 5.1 배포 계획
- schema:
  - 기존 Admin Ops Foundation table은 재사용한다.
  - 신규 schema는 candidate completeness 시간 범위 판단에 필요한 최소 delta가 확정된 경우에만 추가한다.
  - `VectorIndexEntry.status`는 추가하지 않는다.
- 설정:
  - `feedback-loop-pipeline` DB URL
  - raw feedback log prefix
  - dataset artifact prefix
  - model artifact prefix
  - evaluation artifact prefix
  - local training model name
  - embedding dimension
  - training config path/hash
  - evaluation dataset ref
  - Managed Embedding Endpoint base URL
  - stage timeout, retry budget, batch size, backoff
- compatibility:
  - Search Service의 `ROLLBACK_EXCLUDED` 검색 제외는 이미 있으므로 재구현하지 않는다.
  - Pipeline Worker dual-write 배포 전에는 candidate cutover를 enable하지 않는다.
  - Core API ingest block 배포 전에는 rollback execution을 enable하지 않는다.
  - rollback producer/consumer 모두 expected active release 필드를 지원한 뒤 rollback을 enable한다.
- 배포 후 점검:
  - worker boot 후 current `ModelRelease` 읽기 성공
  - fixture raw log로 dataset artifact 생성
  - local training/evaluation dry run
  - seeded data에서 candidate completeness query 결과 확인

### 5.2 롤백 계획
- 애플리케이션 rollback:
  - `feedback-loop-pipeline` consumer를 먼저 중지한다.
  - Search Service와 Pipeline Worker는 current active release serving을 유지한다.
- 데이터 rollback 또는 safe-forward:
  - dataset/model/evaluation artifact는 기본적으로 hard delete하지 않는다.
  - candidate release가 열렸지만 cutover 전이면 safe-forward cleanup으로 candidate fields를 비운다.
  - cutover 완료 후에는 expected active release field가 있는 rollback request를 사용한다.
- 부분 배포 복구:
  - Pipeline Worker dual-write가 없으면 candidate cutover를 비활성화한다.
  - Core API ingest block이 없으면 rollback execution을 비활성화한다.
  - endpoint readiness 확인이 안 되면 `CANDIDATE_REINDEXING` 또는 `ROLLBACK_PREPARING`을 유지하고 alert한다.

---

## 6. 완료 체크리스트

- [ ] 이미 구현된 DB foundation schema를 중복 생성하지 않는다.
- [ ] 남은 workstream이 target SPEC에 매핑된다.
- [ ] candidate completeness 시간 범위 기준이 확정되어 있다.
- [ ] rollback request payload가 stale guard 필드를 포함한다.
- [ ] Pipeline Worker dual-write와 rollback upsert gate가 구현되어 있다.
- [ ] Core API ingest block이 구현되어 있다.
- [ ] 핵심 failure path 테스트가 있다.
- [ ] rollout / rollback compatibility gate가 문서화되어 있다.
