ml-lifecycle-worker 단일 컴포넌트

• # 모듈 초안

  - control_plane
    역할: 스케줄링, 실행 시작 판단, 상태 전이 오케스트레이션, 재시도/복구 트리거를 담당한다. MLPipelineRun
    관련 흐름의 상위 제어면이다.
  - dataset_materializer
    역할: Object Storage의 raw feedback log를 읽고, dedupe와 lineage 보존 규칙을 적용해 immutable training
    dataset artifact를 생성한다.
  - model_build_pipeline
    역할: 생성된 dataset으로 후보 모델 학습을 수행하고, baseline 대비 평가를 실행해 PASS 또는 FAIL 판단에 필
    요한 결과를 만든다.
  - serving_transition_manager
    역할: 평가 PASS 이후 candidate reindex, cutover 준비, snapshot capture, serving 전환, rollback 오케스트
    레이션을 담당한다. ModelRelease와 Project.search_serving_state의 상태 전이 소유 경계다.
  - reindex_job_runner
    역할: candidate index 생성, candidate 반영용 대용량 재색인, cutover 전 반영 상태 충족에 필요한 실제 데이
    터 작업을 수행한다.
  - rollback_recovery_runner
    역할: snapshot index restore, rollback 이후 restored-model 기준 재임베딩, 프로젝트별 검색 재편입에 필요
    한 실제 복구 작업을 수행한다.

control_plane이 독점해야 하는 것:

  - MLPipelineRun 생성
  - MLPipelineRun.status 전이
  - failed_stage, failure_type, failure_reason
  - superseded_by_run_id
  - 실행 시작/실패/대기/승격 관련 상태 기록

serving_transition_manager가 독점해야 하는 것:

  - ModelRelease.release_status 전이
  - ModelRelease.active_*, previous_*, candidate_* 필드 갱신
  - rollback_snapshot_* , candidate_ready_at, switched_at 갱신
  - Project.search_serving_state 전이

release/reindex/rollback 관련 상태 전이 원칙:

  - serving_transition_manager는 ModelRelease와 Project.search_serving_state의 단일 상태 소유 경계다.
  - reindex_job_runner와 rollback_recovery_runner는 장시간 실행 작업만 수행하고, ModelRelease나 Project.search_serving_state를 직접 갱신하지 않는다.
  - control_plane은 release/rollback 시작을 요청할 수는 있지만, ModelRelease 상태를 직접 전이하지 않는다.
  - MLPipelineRun은 control_plane의 단일 writer 모델을 유지하며, release 단계에서 산출된 candidate_index_name과 cutover_time도 최종 DB 반영은 control_plane을 통해 수행한다.

## 4. 동시성 제어 방식

- `MLPipelineRun`의 `RUNNING`은 동시에 1개만 허용한다.
- `MLPipelineRun`의 `PENDING`도 동시에 1개만 허용한다.
- 정기 스케줄 실행과 admin retrigger는 모두 같은 실행 슬롯 규칙을 따른다.
- admin retrigger는 별도 병렬 run을 추가로 만드는 방식이 아니라, 현재 실행 흐름의 재시도 또는 재개 요청으로 다룬다.
- `control_plane`은 `MLPipelineRun` 생성 및 상태 전이를 DB 트랜잭션 안에서 처리한다.

- `ModelRelease.release_status != STABLE`이면 새 cutover와 새 rollback을 바로 시작하지 않는다.
- rollback 우선순위는 cutover보다 높다.
- candidate 재색인 중 rollback이 필요해지면 cutover를 계속 밀지 않고, candidate 배포 흐름을 무효화한 뒤 snapshot 기준 복구를 우선한다.
- rollback 진행 중 새 배포 후보가 생겨도 즉시 cutover하지 않고 대기 상태로 남긴다.

- rollback으로 검색에서 제외된 프로젝트는 복귀 기준이 충족되기 전까지 `SERVABLE`로 되돌리지 않는다.
- 프로젝트 복귀 판단은 `serving_transition_manager`만 수행한다.
- 복귀 기준은 restored model 기준 복구 작업과 vector 반영이 완료된 뒤에만 충족된 것으로 본다.

- `reindex_job_runner`와 `rollback_recovery_runner`는 장시간 실행 작업에 대해 `cancel_requested` 신호를 확인할 수 있어야 한다.
- `cancel_requested`는 작업이 더 이상 유효하지 않음을 나타내는 중단 요청 신호로 사용한다.
- worker는 row 단위 polling이 아니라 배치 또는 체크포인트 경계에서 `cancel_requested`를 확인한다.
- 장시간 실행 작업은 완료 시점에 자신이 시작할 때의 기대 상태와 버전이 아직 유효한지 다시 검증한 뒤에만 최종 결과를 반영한다.
- `cancel_requested` 확인은 중간 중단을 위한 장치이고, 완료 시점 기대 상태 재검증은 stale 결과 반영 방지를 위한 마지막 안전장치로 사용한다.

## 5. dataset generation 경계

- `dataset_materializer`가 Object Storage의 raw feedback log를 읽고 immutable dataset artifact를 생성한다.
- dataset 생성은 `MLPipelineRun` 내부 step이 아니라 별도 주기 배치로 실행한다.
- 기본 생성 주기는 하루 1회로 두고, v1 기준 실행 시각은 매일 03:00 KST다.
- dataset 생성 성공은 training run을 직접 만들지 않는다.
- 학습 run은 별도 training scheduler 또는 admin/manual trigger가 latest eligible dataset을 선택할 때 시작한다.
- v1 기준 training scheduler 실행 시각은 매주 월요일 04:00 KST다.

- training dataset artifact의 기준 구조는 retrieval training group이다.
- group 1개는 한 query와 그 query에 대한 positive 후보, negative 후보, 판단 근거, 신뢰도, source event 추적 정보를 함께 가진다.
- training dataset artifact는 학습에 필요한 chunk text snapshot을 함께 포함한다.
- group row는 최소 아래 의미를 포함한다.
  - `source_event_ids`
  - `query_text`
  - `project_id`
  - `positives[]`
  - `negatives[]`
  - `source_active_model_version`
  - `source_active_index_name`
  - `generation_rule_version`
- 학습 시점에는 Chunk DB를 다시 조회하지 않고 dataset artifact의 text snapshot을 기준으로 학습한다.

- 학습 입력에는 `rating == like`인 feedback event만 사용한다.
- `used_ids`는 positive 후보 source로 본다.
- `topk_ids - used_ids`는 확정 negative가 아니라 weak negative 후보 `exposed_unused`로 본다.
- 같은 project의 chunk pool에서 `random_same_project` weak negative 후보를 추가한다.
- positive 후보와 `exposed_unused` 후보는 `random_same_project` 후보에서 중복 제외한다.
- `used_ids`가 비어 있거나 positive/negative 후보를 만들 수 없는 event는 trainable event에서 제외한다.

- v1 source별 confidence는 아래 값으로 둔다.
  - `liked_response_used_chunk`: `0.8`
  - `exposed_unused`: `0.4`
  - `random_same_project`: `0.2`

- `random_same_project` 수는 `exposed_unused` 수 기준 target ratio `0.5`로 계산한다.
- group별 minimum은 `1`, maximum은 `3`으로 둔다.
- same-project 후보 pool이 없으면 `0`개를 사용한다.

- dedupe는 raw log 적재 시점이 아니라 dataset 생성 시점에 수행한다.
- dedupe key는 `event_id`다.
- 같은 `event_id`가 여러 번 존재하면 가장 최신 `created_at` record를 대표본으로 사용한다.
- dedupe에서 제외된 raw event는 lineage 집계에는 포함될 수 있지만 training group 생성에는 사용하지 않는다.

- dataset version은 timestamp 기반으로 부여한다.
- dataset artifact manifest는 dataset selection과 재현에 필요한 필드를 유지한다.
- manifest 필수 필드는 아래와 같다.
  - `dataset_version`
  - `created_at`
  - `generation_rule_version`
  - `source_window_start`
  - `source_window_end`
  - `input_event_count`
  - `deduped_event_count`
  - `trainable_event_count`
  - `training_group_count`
  - `positive_count`
  - `negative_count`
  - `negative_source_counts`
  - `missing_text_drop_count`
  - `eligible`
  - `ineligible_reasons`
  - `status`

- `eligible` 판정은 latest eligible dataset 선택 전에 적용한다.
- v1 eligibility는 최소한 아래 조건을 통과해야 한다.
  - manifest validation success
  - `generation_rule_version == retrieval-group-v1`
  - `training_group_count >= 10`
  - `negative_count >= 20`

## 6. training run 기준 고정 방식

- `baseline_model_version`은 `MLPipelineRun`이 `RUNNING`으로 전이되는 시점에 읽어 고정한다.
- `PENDING` 상태에서는 `baseline_model_version`을 고정하지 않는다.
- 대기 중 active model이 바뀔 수 있으므로, 실제 실행 시작 시점의 baseline을 run 기준으로 사용한다.

- `candidate_model_version`은 run 단위로 한 번만 생성한다.
- 생성 시점은 `MLPipelineRun`의 `RUNNING` 진입 시점이다.
- v1 naming 규칙은 `run_id` 기반으로 둔다.

- run 시작 시 아래 필드를 함께 고정한다.
  - `dataset_version`
  - `baseline_model_version`
  - `candidate_model_version`
  - `evaluation_dataset_ref`

- 동일 run의 재시도는 같은 `candidate_model_version`을 재사용한다.
- 새로운 run이 생성될 때만 새로운 `candidate_model_version`을 발급한다.

## 7. 모델 학습 실행 방식

- v1에서는 실제 학습 코드를 현재 repo 내부에서 수행한다.
- 현재 기본 실행 주체는 로컬 인프라다.
- 다만 학습 인프라 제공 주체가 바뀌어도 main logic이 흔들리지 않도록, `model_build_pipeline`은 concrete infra가 아니라 추상 실행 경계에만 의존하게 설계한다.
- 즉 control plane과 상태 전이 로직은 `local subprocess`나 `Vertex Custom Job` 같은 구체 실행 방식을 직접 알지 않도록 유지한다.

- 학습 실행 경계는 최소 두 개의 역할로 나눈다.
  - `TrainingRunner`
    - 역할: 학습 시작, 상태 조회, 취소, 최종 결과 반환
  - `ArtifactStore`
    - 역할: dataset artifact 읽기, candidate model artifact 쓰기, 학습 메타데이터 쓰기
- v1 구현체는 `LocalTrainingRunner`와 현재 저장소/오브젝트 스토리지 계약을 따르는 `ArtifactStore`를 사용한다.
- 이후 원격 인프라로 확장할 때는 `VertexTrainingRunner` 같은 대체 구현체만 추가하고, `model_build_pipeline`의 main logic과 상태 전이 코드는 그대로 재사용하는 것을 목표로 한다.

- 학습 step의 입력 contract는 최소 아래 필드를 포함한다.
  - `ml_pipeline_run_id`
  - `trace_id`
  - `dataset_version`
  - `dataset_artifact_ref`
  - `baseline_model_version`
  - `candidate_model_version`
  - `training_config_ref`
- 학습 step의 출력 contract는 최소 아래 필드를 포함한다.
  - `candidate_model_version`
  - `model_artifact_ref`
  - `training_metadata_ref`
  - `completed_at`
  - 실패 시 `failure_reason`

- artifact 저장 경계는 로컬 임시 경로가 아니라 versioned artifact 기준으로 다룬다.
- v1 path 규칙은 candidate version 중심으로 고정한다.
- candidate model artifact 기본 경로 규칙은 아래와 같다.
  - `model_artifacts/candidates/{candidate_model_version}/`
- candidate model artifact root에는 `model_manifest.json`을 둔다.
- `model_manifest.json`의 필수 필드는 아래와 같다.
  - `candidate_model_version`
  - `baseline_model_version`
  - `dataset_version`
  - `evaluation_dataset_ref`
  - `training_config_hash`
  - `base_model_name`
  - `embedding_dimension`
  - `artifact_format`
  - `created_at`
- 학습 메타데이터 기본 경로 규칙은 아래와 같다.
  - `model_artifacts/candidates/{candidate_model_version}/training_metadata.json`
- 학습 중간 산출물과 체크포인트는 아래 임시 경로 아래에 둘 수 있다.
  - `model_artifacts/candidates/{candidate_model_version}/checkpoints/`
- exact bucket name 또는 storage root는 환경 설정으로 주입하고, main logic은 경로 prefix 규칙만 의존하도록 한다.

- 실패 시 partial artifact는 무조건 즉시 hard delete하지 않는다.
- 실패한 run의 partial artifact는 debugging과 수동 정리에 활용할 수 있도록 남길 수 있다.
- 다만 `READY_FOR_RELEASE` 이후 단계는 partial artifact를 정상 candidate artifact로 취급하면 안 된다.
- partial artifact가 남아 있더라도 성공 판정 기준은 `TrainingRunner`가 반환한 최종 `model_artifact_ref`와 학습 완료 상태를 기준으로 한다.
- 동일 run 재시도에서 기존 partial checkpoint를 재사용할지 여부는 v1 필수 계약으로 고정하지 않고 구현체 선택 사항으로 둔다.
- v1은 소형 임베딩 모델을 사용한 로컬 학습 결과도 평가 `PASS`를 통과하면 정상 candidate artifact로 취급한다.
- v1에서는 `training_mode`, `release_eligible`, `promotion_scope` 같은 별도 release guard를 두지 않는다.
- 이 결정은 v1 목표를 모델 품질 최적화보다 end-to-end 학습, 평가, 재색인, cutover, rollback 흐름 검증에 두기 때문이다.

## 8. 평가 방식

- baseline/candidate 평가는 `model_build_pipeline`이 수행한다.
- evaluation dataset artifact는 학습 dataset과 분리된 immutable artifact로 두고, GCS에 저장한다.
- evaluation dataset은 운영자가 수동으로 curated 하고 직접 업로드한다.
- evaluation dataset row shape는 `query_text + relevant_chunk_ids`로 둔다.
- evaluation dataset artifact는 평가 검색 대상 corpus를 함께 포함한다.
- evaluation corpus row는 최소 `chunk_id`와 `chunk_text`를 포함한다.
- baseline과 candidate 평가는 동일한 evaluation corpus를 각각 임베딩해 Top-K를 만든 뒤 `relevant_chunk_ids`와 비교한다.
- v1 평가는 작은 고정 evaluation corpus를 기준으로 in-memory 또는 local vector index에서 수행할 수 있다.
- 정답 라벨 의미는 binary relevant로 고정한다.
- v1 평가 metric은 `Recall@5`, `MRR@5`, `NDCG@5`를 사용한다.
- metric 계산은 `model_build_pipeline` 내부 evaluator helper에서 수행한다.
- PASS 판정은 metric 계산 직후 같은 evaluation 흐름 안에서 수행한다.
- query별 상세 평가 결과 artifact를 먼저 GCS에 저장하고, 그 artifact ref를 포함해 `ModelEvaluation` row를 생성한다.

## 9. handoff 방식

- 평가 `PASS` handoff는 broker나 polling을 쓰지 않고 같은 worker 내부 직접 호출로 처리한다.
- handoff 입력은 최소 식별자만 유지한다.
  - `run_id`
  - `trace_id`
- handoff는 아래 조건이 모두 충족된 뒤에만 시작한다.
  - `MLPipelineRun.status = READY_FOR_RELEASE`
  - `ModelEvaluation` row 저장 완료
  - query별 상세 평가 결과 artifact 저장 완료
- handoff 수신 측은 호출자가 들고 온 메모리 문맥을 신뢰하지 않고 shared SOT를 다시 읽어 실행 문맥을 복원한다.
  - `MLPipelineRun`
  - `ModelEvaluation`
  - `ModelRelease`
- release/reindex 단계는 `MLPipelineRun`의 단일 writer 원칙을 깨지 않는다.
- release/reindex 단계에서 `MLPipelineRun`에 값을 남겨야 하면 control plane 경계를 통해 반영한다.
- 동일한 `run_id`의 평가 `PASS` handoff가 중복 전달되어도 candidate 전환 상태를 중복으로 열지 않는다.

## 10. candidate 배포 준비 설계

- `READY_FOR_RELEASE` run을 수신했을 때 현재 `ModelRelease.release_status = STABLE`인 경우에만 candidate 배포 흐름을 연다.
- candidate 배포 흐름이 열리면 `ModelRelease.release_status`를 `CANDIDATE_REINDEXING`으로 전이한다.
- 이 시점에 아래 값을 candidate 문맥으로 기록한다.
  - `candidate_model_version`
  - `candidate_index_name`
- `candidate_index_name`은 release/reindex 단계가 소유하는 candidate index 식별자다.
- 같은 값은 handoff 추적을 위해 `MLPipelineRun.candidate_index_name`에도 남긴다.
- candidate index는 staging 전용이다.
- candidate index는 end-user search surface에 포함하지 않는다.
- `CANDIDATE_REINDEXING` 중에도 online ingest는 현재 active index 한 곳에만 기록한다.
- candidate model/index는 cutover로 active가 된 뒤부터 신규 online ingest 데이터를 받는다.
- cutover 직전까지 기존 active에 기록된 데이터는 cutover 후 previous 검색으로 계속 제공한다.
- previous-only 데이터는 cutover 후 legacy 재색인으로 새 active에 점진 반영한다.
- candidate 배포 실패 시 current serving은 유지한다.
- 실패 시 candidate fields를 정리하고 관련 run failure 기록은 control plane 경계를 통해 남긴다.

## 11. cutover 방식

- cutover는 candidate readiness와 legacy 재색인 완료가 확인된 뒤에만 수행한다.
- candidate readiness는 Managed Embedding Endpoint 쪽에서 candidate 모델이 실제로 로드되고 readiness를 통과한 상태를 의미한다.
- cutover 직전에 release/reindex 단계가 `MLPipelineRun.cutover_time`을 고정한다.
- `cutover_time`은 재시도에서도 같은 전환 시각을 사용하기 위한 기준이다.
- candidate vector row의 존재 여부는 cutover 조건으로 사용하지 않는다.
- cutover 직전에는 마지막 정상 서빙 상태를 rollback snapshot으로 캡처한다.
- snapshot에는 아래 active 조합만 저장한다.
  - `rollback_snapshot_active_model_version`
  - `rollback_snapshot_active_index_name`
  - `rollback_snapshot_captured_at`
- snapshot은 previous 조합을 보존하지 않는다.
- cutover가 성공하면 serving 조합은 아래 원칙으로 갱신한다.
  - `active = candidate`
  - `previous = 직전 active`
  - `candidate_model_version`, `candidate_index_name`, `candidate_ready_at`는 null로 정리
- cutover 이후 end-user search는 active/previous 두 세대만 사용한다.
- previous보다 더 오래된 데이터는 cutover 이후 최신 active 기준으로 점진 재임베딩한다.

## 12. rollback 방식

- rollback 요청은 현재 `ModelRelease.release_status = STABLE`이고 rollback snapshot 포인터가 있을 때만 시작한다.
- rollback 요청은 요청자가 본 active release에 고정한다.
- rollback request에는 최소 아래 기대값을 포함한다.
  - `expected_active_model_version`
  - `expected_switched_at`
- consumer는 현재 `ModelRelease.active_model_version`과 `ModelRelease.switched_at`이 rollback request의 기대값과 일치할 때만 rollback을 시작한다.
- 기대값이 일치하지 않으면 stale rollback request로 보고 새 rollback을 시작하지 않는다.
- rollback 시작 시 `ModelRelease.release_status`를 `ROLLBACK_PREPARING`으로 전이한다.
- rollback 복구 대상은 snapshot이 가리키는 마지막 정상 active model/index 조합이다.
- rollback 시작 시점에 problem-model 영향을 받은 프로젝트는 먼저 `ROLLBACK_EXCLUDED`로 바꿔 검색에서 제외한다.
- 영향 프로젝트 기준은 현재 문제 active 모델 버전과 같은 `Chunk.embedding_model_version`을 가진 청크가 하나 이상 있는 프로젝트다.
- v1에서 `ROLLBACK_EXCLUDED`는 검색 제외와 신규 video ingest 일시 차단을 함께 의미한다.
- rollback 중에는 problem model 기준 embedding/vector upsert를 계속 수행하지 않는다.
- rollback의 우선 과제는 snapshot에 정의된 마지막 정상 active model/index 조합으로 최대한 빠르게 복구하는 것이다.
- rollback restore는 아래 조건이 모두 충족된 뒤에만 수행한다.
  - rollback target model readiness 통과
  - snapshot active index restore 완료
- restore 시 serving 필드는 snapshot active 조합만 복원한다.
- restore 시 `previous_model_version`과 `previous_index_name`은 snapshot에서 복원하지 않는다.
- restore가 완료되면 `candidate_model_version`, `candidate_index_name`, `candidate_ready_at`는 null로 정리한다.
- `ROLLBACK_EXCLUDED` 프로젝트는 restored active 모델 기준 재임베딩과 vector 반영이 끝난 뒤에만 `SERVABLE`로 되돌리고 ingest 제한도 해제한다.
- 동일 rollback 요청이 중복 전달되어도 이미 `ROLLBACK_PREPARING`이거나 이미 snapshot 조합으로 복원된 상태면 no-op로 처리한다.
