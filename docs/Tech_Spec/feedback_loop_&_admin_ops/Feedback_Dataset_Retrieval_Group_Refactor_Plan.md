# Feedback Dataset 생성 리팩토링 계획

## 1. 목적

현재 feedback dataset 생성 방식은 raw feedback event를 바로 triplet 학습 row로 바꾼다.

이번 리팩토링의 목적은 dataset의 기준 형태를 더 안전한 형태로 바꾸는 것이다.

채택할 기준 형태는 retrieval training group이다.

한 group은 아래 정보를 함께 가진다.

- 사용자 query
- positive chunk 후보들
- negative chunk 후보들
- 이 후보들이 어떤 feedback event에서 나왔는지 추적할 수 있는 정보
- positive/negative 판단 근거와 신뢰도

멘토 확인 결과, v1에서는 이 수준의 개선으로 충분하다.

query/chunk 집계 테이블, 의미 기반 query 묶기, LLM labeling, human labeling은 v1 범위에 넣지 않는다.

---

## 2. 현재 방식

현재 dataset 생성 흐름은 아래와 같다.

1. raw feedback event를 읽는다.
2. 같은 `event_id`는 중복 제거한다.
3. `rating == LIKE`인 event만 학습 후보로 본다.
4. `used_ids`를 positive chunk로 본다.
5. `topk_ids - used_ids`를 hard negative chunk로 본다.
6. positive 1개와 negative 1개를 묶어 triplet row를 만든다.

예시 구조:

```text
query A + positive P + negative N1
query A + positive P + negative N2
query A + positive P + negative N3
```

이 방식은 smoke flow 검증에는 쓸 수 있다.

하지만 학습 데이터의 기준 형태로 두기에는 위험이 있다.

---

## 3. 왜 바꾸는가

### 3.1 `topk_ids - used_ids`는 확정 negative가 아니다

`topk_ids`는 검색 결과에 노출된 chunk 목록이다.

`used_ids`는 LLM이 실제 답변에 사용했다고 보고한 chunk 목록이다.

어떤 chunk가 `used_ids`에 없다고 해서 관련 없는 chunk라는 뜻은 아니다.

예를 들어 LLM이 rank 1 chunk만 보고 답했을 수 있다.
그 경우 rank 2 chunk는 관련 있어도 `used_ids`에 들어가지 않는다.

따라서 `topk_ids - used_ids`는 hard negative 확정값이 아니라, exposed-unused negative 후보로 다뤄야 한다.

### 3.2 같은 query-positive가 여러 번 반복된다

현재 방식은 negative 개수만큼 같은 query-positive 쌍을 반복한다.

이러면 특정 positive chunk가 실제보다 더 강하게 학습될 수 있다.

### 3.3 raw feedback은 label이 아니라 evidence다

사용자의 LIKE는 chunk relevance만 의미하지 않는다.

LIKE에는 아래 요인이 섞일 수 있다.

- 답변 문장 품질
- UI 경험
- 사용자 기대치
- LLM이 chunk를 조합한 방식
- 검색 결과 자체의 품질

그래서 raw feedback event를 바로 최종 label처럼 쓰면 안 된다.

---

## 4. 채택할 방향

Dataset artifact의 기준 형태를 retrieval training group으로 바꾼다.

각 group은 한 query에 대해 positive 후보와 negative 후보를 배열로 가진다.

예시:

```json
{
  "query_text": "semantic search",
  "positives": [
    {
      "chunk_id": "chunk-pos",
      "text": "positive chunk text",
      "source": "liked_response_used_chunk",
      "confidence": 0.8
    }
  ],
  "negatives": [
    {
      "chunk_id": "chunk-neg",
      "text": "exposed unused chunk text",
      "source": "exposed_unused",
      "confidence": 0.4
    },
    {
      "chunk_id": "chunk-random",
      "text": "same project random chunk text",
      "source": "random_same_project",
      "confidence": 0.2
    }
  ],
  "source_event_ids": ["event-1"],
  "project_id": "project-1",
  "generation_rule_version": "retrieval-group-v1"
}
```

초기 신뢰도 값은 v1 generation rule의 일부로 확정한다.

| source | confidence | 이유 |
| --- | --- | --- |
| `liked_response_used_chunk` | `0.8` | LIKE가 붙은 답변에서 실제 사용된 chunk라 가장 강한 positive 후보지만, 사용자 LIKE는 chunk 관련성만 평가한 값이 아니다. |
| `exposed_unused` | `0.4` | 검색 결과에 노출됐지만 답변에는 쓰이지 않은 chunk라 weak negative 후보로 본다. |
| `random_same_project` | `0.2` | 같은 project 안의 random chunk라 false negative 가능성이 더 높다. |

이 값을 바꾸면 dataset 의미가 바뀐다.
변경 시 `generation_rule_version` 갱신을 함께 검토한다.

이 문서에서 "관련성"은 query에 대해 해당 chunk가 답변 근거로 적합한 정도를 뜻한다.

이 형태는 MNRL 같은 multi-negative contrastive 학습에 바로 맞는다.

이번 리팩토링 이후 active dataset artifact는 이 group 형태만 사용한다.
triplet row 생성 경로는 제거한다.

---

## 5. 변경 범위 요약

| 영역 | 현재 | 변경 후 |
| --- | --- | --- |
| dataset 기준 형태 | triplet row | retrieval training group |
| positive | `LIKE + used_ids` | 동일 신호를 `positives[]`에 출처와 신뢰도와 함께 저장 |
| exposed unused chunk | hard negative | weak negative 후보 |
| random negative | 없음 | 같은 project chunk에서 추가 후보 생성 |
| manifest | triplet row count 중심 | group/positive/negative/source/drop count 중심 |
| trainer 입력 | `positive_text`, `hard_negative_text` | `positives[]`, `negatives[]` |
| aggregation | 없음 | v1에서는 계속 없음 |
| triplet | dataset 기준 | active dataset 경로에서 제거 |

---

## 6. 스케줄과 source window 결정

Dataset generation과 training은 선형으로 묶지 않는다.

채택 흐름:

```text
Feedback ingest
-> raw log 저장

Dataset generation
-> raw log를 읽어 dataset artifact 생성

Training
-> latest eligible dataset을 선택해 학습 실행
```

Dataset generation 성공은 training run을 직접 만들지 않는다.
두 흐름은 별도 scheduler와 admin/manual trigger로 실행한다.

두 흐름 사이의 계약은 아래 artifact와 metadata다.

- `train.jsonl`
- `manifest.json`
- `dataset_version`
- manifest eligibility

v1 스케줄 결정:

| 항목 | 결정 |
| --- | --- |
| dataset generation trigger | scheduler + admin/manual trigger |
| dataset generation cadence | 하루 1회 |
| dataset generation time | 매일 03:00 KST |
| source window | 실행 시점 기준 rolling last 30 days |
| training trigger | 별도 scheduler + admin/manual trigger |
| training cadence | 주 1회 |
| training time | 매주 월요일 04:00 KST |
| training dataset selection | latest eligible dataset |

Biblio의 project-level feedback은 sparse할 수 있다.
하루 단위 source window만 사용하면 학습 가능한 group이 충분히 모이지 않을 수 있다.

따라서 v1 dataset generation은 최근 30일 raw feedback log를 읽어 retrieval training group dataset을 만든다.
source window는 재현성과 lineage를 위해 manifest에 기록한다.

Dataset generation 책임:

- schedule 또는 admin/manual trigger로 실행된다.
- 최근 30일 raw feedback log를 읽는다.
- retrieval training group dataset을 만든다.
- Object Storage에 `train.jsonl`과 `manifest.json`을 쓴다.
- source window와 eligibility를 manifest에 기록한다.
- trainable data가 부족하면 `eligible=false`를 기록한다.
- training을 호출하거나 training request를 publish하지 않는다.

Training 책임:

- dataset generation과 별도 schedule 또는 admin/manual trigger로 실행된다.
- dataset artifact catalog를 읽는다.
- latest eligible dataset을 선택한다.
- 같은 `dataset_version`에 대해 successful/running run이 있으면 skip한다.
- 새 eligible dataset이 있을 때만 training run을 만든다.

---

## 7. 문서 변경 계획

### 7.1 `Feedback_Dataset_Generation_Issues_and_Alternatives.md`

=> 현재 문서로 대체가능한 내용이라 삭제 했음, 7.1 항목은 무시

### 7.2 `ML_Pipeline_Execution_Spec.md`

변경해야 할 내용:

- `TrainingDataset Artifact`의 의미를 retrieval training group으로 수정한다.
- 학습 dataset이 보존해야 하는 정보를 아래처럼 정리한다.
  - raw event 추적 정보
  - query context
  - retrieval context
  - positive candidates
  - negative candidates
  - generation metadata
- `topk_ids`와 `used_ids`는 label이 아니라 검색 시점 evidence와 추적 정보라는 점을 명시한다.

### 7.3 `Feedback_Loop_Pipeline_Plan.md`

변경해야 할 내용:

- Workstream 2의 triplet 생성 설명을 retrieval group materialization으로 바꾼다.
- same-project random negative 생성 작업을 추가한다.
- 완료 기준을 triplet row count가 아니라 group/negative/source count 기준으로 바꾼다.
- 검증 항목을 새 artifact shape 기준으로 바꾼다.

### 7.4 `학습 파이프라인 plan 작업전 결정사항정리.md`

변경해야 할 내용:

- "training example shape는 triplet row" 결정을 폐기한다.
- "기준 shape는 retrieval training group"으로 수정한다.
- active dataset 경로에서는 triplet을 만들지 않는다고 정리한다.

---

## 8. 코드 변경 계획

### 8.1 Dataset materializer

대상 파일:

- `services/feedback-loop-pipeline/src/dataset/materializer.py`

현재 책임:

- raw event를 받아 triplet row를 만든다.

변경 후 책임:

- raw event를 받아 retrieval training group을 만든다.

변경할 것:

- `TrainingTripletRow`를 대체할 group 모델을 만든다.
- positive chunk label 모델을 만든다.
- negative chunk label 모델을 만든다.
- event 하나에서 가능한 경우 group 하나를 만든다.
- positive는 `used_ids`에서 만든다.
- negative는 두 source에서 만든다.
  - `exposed_unused`
  - `random_same_project`
- positive/negative 모두 text snapshot을 포함한다.
- text가 없는 후보는 dataset row에 넣지 않고 manifest drop count에 반영한다.

### 8.2 Dataset manifest

대상 파일:

- `services/feedback-loop-pipeline/src/dataset/manifest.py`

현재 중심 필드:

- `triplet_row_count`

변경 후 중심 필드:

- `training_group_count`
- `positive_count`
- `negative_count`
- `negative_source_counts`
- `missing_text_drop_count`
- `ineligible_reasons`

변경할 것:

- generation rule version을 `retrieval-group-v1`로 바꾼다.
- eligibility policy를 group/negative 기준으로 바꾼다.
- v1 eligibility는 파이프라인 작동 여부, 가용성, 안전성을 우선한다.
- `training_group_count >= 10`이고 `negative_count >= 20`이면 `eligible=true`로 둔다.
- 기준에 못 미치면 artifact는 보존하되 `eligible=false`와 `ineligible_reasons`를 기록한다.
- 기존 `triplet-v1` manifest는 dataset selection 대상에서 제거한다.
- old manifest parsing branch는 active path에 남기지 않는다.

### 8.3 Batch service

대상 파일:

- `services/feedback-loop-pipeline/src/dataset/batch.py`

현재 책임:

- raw event에서 참조된 chunk id의 text를 가져와 materializer에 넘긴다.

변경 후 책임:

- 기존 referenced chunk text를 계속 가져온다.
- 같은 project의 random negative 후보 chunk도 가져온다.
- materializer에 두 종류의 chunk text map을 넘긴다.

필요한 port:

- chunk id 목록으로 text를 가져오는 port
- project id 목록으로 같은 project chunk pool을 가져오는 port

### 8.4 Same-project chunk source

대상 후보 파일:

- `services/feedback-loop-pipeline/src/infra/db/stores.py`
- 또는 새 파일 `services/feedback-loop-pipeline/src/dataset/chunk_pool.py`

필요한 동작:

- project id 기준으로 학습 후보 chunk pool을 읽는다.
- `READY` project/video의 chunk만 후보로 쓴다.
- positive chunk는 negative 후보에서 제외한다.
- 이미 `exposed_unused`로 들어간 chunk도 중복으로 넣지 않는다.
- group별 random negative 수는 고정 개수보다 ratio 정책으로 정한다.
- 단일 group이 너무 커지지 않도록 ratio 정책에는 per-group upper bound를 함께 둔다.

초기 정책:

- `exposed_unused` negative 수를 기준으로 `random_same_project` target ratio를 계산한다.
- target ratio는 `0.5`로 둔다.
- group별 minimum은 `1`로 둔다.
- group별 maximum은 `3`으로 둔다.
- same-project 후보 pool이 없으면 `0`개를 사용한다.

계산식:

```text
random_same_project_count = clamp(round(exposed_unused_count * 0.5), 1, 3)
```

주의:

- 같은 document sibling 제외는 v1 필수로 넣지 않는다.
- 현재 schema에서 sibling 판단 근거가 명확하지 않으면 future improvement로 둔다.

### 8.5 Triplet 생성 경로 제거

대상 후보 파일:

- `services/feedback-loop-pipeline/src/dataset/materializer.py`
- `services/feedback-loop-pipeline/src/dataset/artifacts.py`
- `services/feedback-loop-pipeline/src/training/runner.py`

필요한 동작:

- `TrainingTripletRow` 생성 모델과 writer 경로를 제거한다.
- smoke trainer와 fixture도 retrieval training group shape로 갱신한다.
- active code path에서 triplet row를 파생하지 않는다.
- `triplet-v1` artifact는 latest eligible dataset selection 대상에 포함하지 않는다.

### 8.6 Artifact writer

대상 파일:

- `services/feedback-loop-pipeline/src/dataset/artifacts.py`

현재:

- `train.jsonl`에 triplet row를 쓴다.

변경 후:

- `train.jsonl`에 retrieval group을 한 줄씩 쓴다.

경로는 유지한다.

이유:

- artifact 경로 변경은 release/run control 영향이 크다.
- 파일 내부 shape만 바꿔도 이번 목적을 달성할 수 있다.

### 8.7 Training runner

대상 파일:

- `services/feedback-loop-pipeline/src/training/runner.py`

현재:

- `positive_text`
- `hard_negative_text`

변경 후:

- `positives[]`
- `negatives[]`

local smoke training에서는 아래처럼 처리한다.

- query와 positive text가 겹치는 token은 가중치를 올린다.
- query와 negative text가 겹치는 token은 가중치를 낮춘다.
- 모든 negative 후보를 반영한다.

---

## 9. 테스트 변경 계획

### 9.1 Unit tests

대상:

- `tests/unit/test_dataset_materializer.py`
- `tests/unit/test_dataset_manifest.py`
- `tests/unit/test_settings.py`
- `tests/unit/test_model_manifest.py`

검증할 것:

- 같은 `event_id`는 한 번만 반영된다.
- `LIKE + used_ids`가 positive 후보를 만든다.
- `topk_ids - used_ids`는 `exposed_unused` negative 후보가 된다.
- 같은 project chunk가 `random_same_project` negative 후보가 된다.
- positive chunk는 negative 후보에 들어가지 않는다.
- missing chunk text는 drop count에 반영된다.
- manifest는 group/positive/negative/source count를 저장하고 다시 읽을 수 있다.
- `triplet-v1` manifest는 dataset selection 대상에 포함되지 않는다.
- local trainer fixture가 group shape를 읽을 수 있다.

### 9.2 Integration tests

대상:

- `tests/integration/test_local_smoke_flow.py`
- `tests/integration/test_training_request_handler.py`
- `tests/integration/test_training_run_flow.py`

검증할 것:

- raw feedback log에서 group dataset artifact가 생성된다.
- manifest에 `training_group_count`가 들어간다.
- `train.jsonl` row에 `positives[]`, `negatives[]`가 들어간다.
- local training, evaluation, release handoff smoke flow가 계속 동작한다.
- latest eligible dataset selector가 새 manifest 기준으로 동작한다.

---

## 10. 구현 순서

### Step 1. 문서 먼저 수정

수정 순서:

1. `Feedback_Dataset_Generation_Issues_and_Alternatives.md` => 이거 이미 지운 파일 수정 안해도 됨
2. `ML_Pipeline_Execution_Spec.md`
3. `Feedback_Loop_Pipeline_Plan.md`
4. `학습 파이프라인 plan 작업전 결정사항정리.md`

목표:

- 문서에서 dataset 기준 형태를 먼저 확정한다.
- triplet을 기준 형태로 말하는 문장을 제거한다.
- aggregation 제외 결정을 명확히 남긴다.

### Step 2. Materializer test 작성

먼저 실패하는 테스트를 만든다.

테스트 이름 예:

- `test_materializer_emits_retrieval_group_with_label_sources`
- `test_materializer_records_exposed_unused_as_weak_negative`
- `test_materializer_adds_random_same_project_negative`
- `test_materializer_counts_missing_text_drops`

### Step 3. Materializer 구현

구현 목표:

- event 하나에서 group 하나 생성
- positive/negative label source 기록
- confidence 기록
- 추적 정보 보존
- missing text drop count 집계

### Step 4. Manifest 변경

구현 목표:

- `triplet_row_count` 중심 gate 제거
- `training_group_count`, `negative_count` 중심 gate 추가
- source별 negative count 기록
- old manifest selection path 제거

### Step 5. Batch service와 chunk pool 연결

구현 목표:

- same-project random negative 후보 공급
- 고정 정렬로 테스트 안정화
- 현재 artifact path 유지

### Step 6. Trainer 변경

구현 목표:

- group shape를 읽는다.
- positive/negative arrays를 모두 반영한다.
- 기존 smoke training flow가 유지된다.

### Step 7. Integration test 갱신

구현 목표:

- local smoke flow 통과
- training request handler 통과
- run flow 통과

### Step 8. 낡은 표현 제거

아래 검색으로 남은 표현을 확인한다.

```bash
rg -n "TrainingTripletRow|triplet_row_count|MIN_TRIPLET_ROW_COUNT|hard_negative_text|triplet-v1" docs services/feedback-loop-pipeline
```

허용되는 위치:

- 결정 이력
- old review 문서

허용되지 않는 위치:

- active spec
- active plan
- materializer의 기준 생성 경로
- new manifest eligibility path

---

## 11. 검증 명령

우선 실행할 테스트:

```bash
cd services/feedback-loop-pipeline
poetry run pytest tests/unit/test_dataset_materializer.py -v
poetry run pytest tests/unit/test_dataset_manifest.py -v
poetry run pytest tests/unit/test_model_manifest.py -v
poetry run pytest tests/integration/test_local_smoke_flow.py -v
```

그 다음 실행할 테스트:

```bash
cd services/feedback-loop-pipeline
poetry run pytest tests/unit/test_training_run_flow.py tests/integration/test_training_run_flow.py tests/integration/test_training_request_handler.py -v
```

마지막 전체 확인:

```bash
cd services/feedback-loop-pipeline
poetry run pytest
```

---

## 12. 남은 결정 사항

구현 전에 확인할 추가 사용자 결정은 없다.

- old env var `MIN_TRIPLET_ROW_COUNT`는 제거한다.

---

## 13. 리스크와 대응

### 13.1 random negative도 false negative일 수 있음

같은 project 안의 random chunk도 query와 관련 있을 수 있다.

대응:

- confidence를 낮게 둔다.
- source를 `random_same_project`로 명확히 기록한다.
- positive chunk와 exposed-unused chunk는 중복 제외한다.

### 13.2 old dataset artifact 재사용 위험

기존 artifact는 `triplet-v1` manifest를 가진다.
새 학습 경로가 이를 재사용하면 dataset shape가 다시 섞인다.

대응:

- dataset selection은 `retrieval-group-v1` manifest만 대상으로 한다.
- `triplet-v1` parsing과 compatibility branch는 active path에서 제거한다.
- 필요하면 old artifact는 Object Storage 보존 데이터로만 둔다.

### 13.3 trainer가 old row shape에 묶여 있을 수 있음

현재 local trainer는 `hard_negative_text`를 읽는다.

대응:

- trainer를 group shape 기준으로 바꾼다.
- old row shape 입력 처리는 제거한다.

### 13.4 문서와 코드가 다른 말을 할 수 있음

대응:

- 문서 먼저 수정한다.
- 구현 후 낡은 표현 검색을 돌린다.
- active spec/plan에 triplet을 기준 형태로 설명하는 표현이 남지 않게 한다.

### 13.5 dataset generation과 training이 다시 선형 결합될 수 있음

Dataset generation 성공 직후 training request를 publish하면 두 흐름이 다시 선형으로 결합된다.

대응:

- dataset generation은 artifact와 manifest만 쓴다.
- training scheduler는 자기 cadence에 따라 latest eligible dataset을 선택한다.
- 두 흐름의 연결 지점은 `train.jsonl`, `manifest.json`, `dataset_version`, eligibility로 제한한다.

---

## 14. 완료 기준

리팩토링 완료 기준:

- active 문서에서 retrieval training group이 dataset 기준 shape로 설명된다.
- `DatasetMaterializer`가 group artifact를 만든다.
- manifest가 group/positive/negative/source/drop count를 기록한다.
- local trainer가 group artifact를 읽는다.
- latest eligible dataset selection이 새 manifest 기준으로 동작한다.
- dataset generation과 training이 별도 scheduler/manual trigger로 실행된다.
- dataset generation 성공이 training run을 직접 만들지 않는다.
- training scheduler가 latest eligible dataset을 선택한다.
- 같은 `dataset_version`이 duplicate training run을 만들지 않는다.
- focused unit/integration test가 통과한다.
- triplet 관련 active code path는 제거된다.

---

## 15. 근거

근거 문서:

- `docs/system-design.md`의 Feedback Event 정의
- `docs/Tech_Spec/feedback_loop_&_admin_ops/ML_Pipeline_Execution_Spec.md`의 TrainingDataset Artifact 계약
- `docs/Feedback_Dataset_Generation_Issues_and_Alternatives.md`의 dataset generation 문제 분석

사용자/멘토 결정:

- v1에서는 retrieval group 개선 수준으로 충분하다.
- 집계 테이블과 query clustering은 v1에서 제외한다.
- raw feedback은 후속 집계가 가능하도록 충분한 추적 정보를 보존한다.

---

## 16. 자체 리뷰 결과

검토 결과:

- abstraction level: plan 문서 수준에 맞게 파일/테스트/순서를 명시했다.
- SOT alignment: Feedback Event 원본 계약은 유지하고 dataset artifact 내부 shape만 바꾼다.
- scheduler boundary: dataset generation과 training은 artifact/manifest 계약으로만 연결하고, 서로 직접 호출하지 않는다.
- no invented decision: 사용자와 합의한 v1 기본값만 문서에 확정했다.
- duplication control: triplet 파생 책임을 새로 만들지 않고 active path에서 제거한다.
- guardrail check: 채택 설계를 먼저 설명하고, 이전 방식 비교는 문제/호환성 설명에만 사용했다.
