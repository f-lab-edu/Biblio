# [ADR-010] feedback-dataset-retrieval-group-and-decoupled-training

* **상태 (Status):** Accepted
* **날짜 (Date):** 2026-05-17

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** Feedback Loop Pipeline은 사용자 feedback log를 학습 dataset artifact로 만들고, 이후 training scheduler가 dataset을 선택해 retrieval 모델 학습을 실행한다.
* **문제:** 기존 dataset 구조는 raw feedback event를 triplet row(질문쿼리/긍정/부정)로 바로 변환한다. 이 구조는 응답 청크(topk_ids) 에서 llm이 인용한 청크(used_ids) 를 제외한 청크를 강한 negative처럼 다루고, 같은 query-positive 조합을 여러 row로 반복해 학습 신호를 과장할 수 있다.
* **목표:** 학습 dataset의 기준 구조를 retrieval 학습에 더 적합한 형태로 바꾼다

## 2. 고려한 옵션들 (Considered Options)

### 1. 현상 유지: Triplet row를 dataset 기준 구조로 유지

 기존 방식으로 raw feedback event에서 query, positive text, negative text를 한 줄 단위 triplet row로 만든다.

**Pros**
* 기존 smoke flow와 trainer 입력 구조를 크게 바꾸지 않아도 된다.
* 구현 범위가 작다.
* 한 row 단위 검증이 단순하다.

**Cons**
* `topk_ids - used_ids`를 실제보다 강한 negative로 취급하기 쉽다.
* 같은 query-positive 조합이 negative 개수만큼 반복되어 특정 positive가 과하게 반영될 수 있다.
* source, confidence, lineage를 group 단위로 보존하기 어렵다.
* MNRL 같은 multi-negative contrastive 학습 입력 구조와 맞지 않는다.

### 2. Triplet row 대신 query별 positive/negative group을 사용

query 하나를 기준으로 positive 후보 배열과 negative 후보 배열을 한 group에 담는다.  각 후보에는 왜 positive/negative로 봤는지와 신뢰도를 기록한다. group에는 어떤 feedback
event에서 만들어졌는지 추적할 수 있는 정보를 기록한다. Dataset artifact는 `retrieval-group-v1` generation rule로 생성한다

**Pros**
* 한 query의 positive/negative 후보를 group 단위로 표현할 수 있다.
* `exposed_unused`(topk_ids - used_ids)와 `random_same_project`(project 안의 무관한 청크)를 서로 다른 약한 negative source로 구분할 수 있다.
* 각 후보가 왜 positive/negative로 분류됐는지와 신뢰도를 남기고, group이 어떤 feedback event 에서 만들어졌는지도 추적할 수 있어 후속 분석과 튜닝이 가능하다.
* MNRL 계열 retrieval training 입력으로 확장하기 쉽다.
* triplet 반복으로 생기는 positive 과대표현을 줄인다.

**Cons**
* materializer, manifest, trainer, fixture 구조를 함께 바꿔야 한다.
* 데이터셋 검증 기준을 새로 정의해야 한다.
* `topk_ids - used_ids`를 여전히 negative 후보로 사용하므로, 답변에 쓰이지 않았을 뿐 실제로는 관련 있는 chunk가 negative로 들어갈 수 있다.
* confidence(신뢰도) 값은 초기 임의 설정이라 실제 학습 품질과 맞지 않을 수 있다.

### 3. 비슷한 query를 묶어 feedback을 누적 집계(aggregation)

Raw feedback을 누적 집계하고, 의미적으로 비슷한 query를 묶어 더 강한 dataset candidate를 만든다.

**Pros**
* 장기적으로 더 안정적인 label 후보를 만들 수 있다.
* 비슷한 query의 feedback을 하나의 묶음으로 누적해, 개별 event만 볼 때보다 더 많은 학습 후보를 만들 수 있다.
* 같은 의미지만 표현이 다른 query들을 묶어 학습 신호가 흩어지는 것을 줄일 수 있다

**Cons**
* v1 범위에 비해 설계와 운영 복잡도가 크다.
* clustering 품질 검증과 재현성 관리가 추가로 필요하다.
* feedback 누적을 위해 query 정규화 기준과 유사도 threshold를 정해야 한다.
* 잘못 묶인 query는 positive/negative 후보를 오염시킬 수 있다.
* 트래픽과 feedback 양이 적으면 clustering을 도입해도 충분한 묶음이 만들어지지 않을 수 있다.

## 3. 결정 사항 (Decision Outcome)

* **2번, Triplet row 대신 query별 positive/negative group을 사용을 기준 구조로 채택한다.**
* **Triplet row 생성 경로와 `triplet-v1` artifact 선택 경로는 active pipeline에서 제거한다.**
* **3번 aggregation은 v1 범위에서 제외하고 향후 확장 옵션으로 남긴다.**

**이유**
* Raw feedback을 그대로 triplet dataset으로 만들면, 확실하지 않은 feedback 신호를 정답처럼 학습할 위험이 있다.
* 2번 방식은 같은 positive가 여러 번 반복 학습되는 문제를 줄이고, 답변에 쓰이지 않은 chunk 를 강한 negative가 아니라 약한 negative 후보로 다룰 수 있다.
* Aggregation과 semantic query clustering은 장기적으로 유용할 수 있지만, v1에서 검증하려는 pipeline 작동 여부, 가용성, 안전성에 비해 복잡도가 크다.

## 4. 결정된 설계 원칙 (Decision Details)

* Active dataset artifact는 retrieval training group 구조만 사용한다.
* `train.jsonl` 경로는 유지하되 파일 내부 row 구조를 group 구조로 바꾼다.
* 각 group은 query, positive 후보, negative 후보, source event 추적 정보, project context, generation metadata를 보존한다.
* Positive source `liked_response_used_chunk`의 confidence는 `0.8`로 둔다.
* Negative source `exposed_unused`의 confidence는 `0.4`로 둔다.
* Negative source `random_same_project`의 confidence는 `0.2`로 둔다.
* Same-project random negative 수는 `exposed_unused` negative 수 기준 target ratio `0.5`로 계산한다.
* Same-project random negative는 group별 minimum `1`, maximum `3`을 적용한다. 후보 pool이 없으면 `0`개를 사용한다.
* Dataset generation은 최근 30일 raw feedback log를 읽어 artifact와 manifest를 만든다.
* Dataset generation은 매일 03:00 KST에 실행하며, admin/manual trigger도 허용한다.
* Training은 매주 월요일 04:00 KST에 실행하며, admin/manual trigger도 허용한다.
* Training scheduler는 최신의 검증된 dataset만 선택한다.
* Dataset generation은 training request를 트리거 하지 않는다.(별도로 동작)
* v1 eligibility는 `training_group_count >= 10`이고 `negative_count >= 20`일 때 `eligible=true`로 둔다.
* 기준에 못 미친 artifact는 보존하되 `eligible=false`와 `ineligible_reasons`를 manifest에 기록한다.
* Confidence 정책을 바꾸면 dataset 의미가 바뀌므로 `generation_rule_version` 갱신을 함께 검토한다.

## 5. 긍정적 효과 (Positive Consequences)

* Dataset의 기준 구조가 MNRL 계열 retrieval training에 맞아진다.
* Negative source별 신뢰도를 분리해 false negative 위험을 더 명확히 다룰 수 있다.
* Dataset row가 어떤 feedback event와 생성 규칙에서 만들어졌는지 남기므로, 나중에 분석하거나 같은 dataset을 재현하기 쉬워진다.
* Feedback이 적은 상황에서도 artifact 생성과 training 사용 가능 여부를 따로 판단할 수 있다.

## 6. 부정적 효과 및 위험 요소 (Negative Consequences)

* Trainer와 fixture가 새 group 구조를 읽도록 함께 바뀌어야 한다.
  * **대응:** Materializer, artifact writer, trainer, integration fixture를 같은 generation rule 기준으로 갱신한다.
* `random_same_project` negative도 실제로는 관련 chunk일 수 있다.
  * **대응:** confidence를 낮게 두고, positive 및 exposed-unused 후보와 중복되지 않게 한다.
* 서비스 초기처럼 feedback이 적은 시기에는 기준을 만족하는 dataset이 자주 만들어지지 않을 수 있다
  * **대응:** Artifact는 보존하고 `eligible=false` 이유를 manifest에 남긴다. Training scheduler는 eligible artifact가 있을 때만 실행한다.


## 7. 결정 이후 후속 결과 (Consequences)


