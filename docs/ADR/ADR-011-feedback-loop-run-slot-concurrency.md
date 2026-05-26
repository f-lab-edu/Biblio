# [ADR-011] feedback-loop-run-slot-concurrency

* **상태 (Status):** Accepted
* **날짜 (Date):** 2026-05-12

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** Feedback Loop Pipeline은 `MLPipelineRun`을 사용해 학습 실행을 제어한다. 정책상 동시에 `RUNNING` run은 하나만 존재해야 하고, 다음 실행 대기용 `PENDING` run도 하나만 존재해야 한다.
* **문제:** `TRAINING_REQUEST`는 at-least-once로 재전달될 수 있다. 두 요청이 동시에 들어오면 둘 다 "현재 RUNNING이 없다"고 판단한 뒤 `RUNNING` 생성을 시도할 수 있다. 애플리케이션의 선조회 로직만으로는 empty-set race를 막기 어렵다.
* **목표:** 기존 schema 제약을 활용하면서 중복 요청이 DB 에러로 끝나지 않고 하나의 run slot 상태로 수렴하게 한다.

## 2. 고려한 옵션들 (Considered Options)

### 1. Unique index 충돌을 잡고 현재 slot을 재조회

`MLPipelineRun.status`에 대한 partial unique index로 `RUNNING`과 `PENDING`을 각각 최대 하나로 제한한다. 동시 insert가 충돌하면 `IntegrityError`를 잡고 현재 `RUNNING` / `PENDING` row를 다시 읽어 domain result로 반환한다.

**Pros**
* 이미 Core API migration에 존재하는 production invariant를 그대로 활용한다.
* schema를 새로 추가하지 않는다.
* 중복 요청을 실패가 아니라 같은 run으로 수렴시키는 멱등 처리가 가능하다.
* 변경 범위가 `DbRunSlotStore`와 local schema projection에 집중된다.

**Cons**
* `IntegrityError`를 정상 동시성 경로로 다뤄야 한다.
* savepoint 또는 transaction 상태 관리가 필요하다.
* local test schema가 production partial unique index를 반영하지 않으면 같은 위험을 테스트하지 못한다.

### 2. PostgreSQL advisory lock 사용

`request_training_run` 시작 시 feedback-loop training slot 전용 transaction-level advisory lock을 잡고, slot 판단과 run 생성을 직렬화한다.

**Pros**
* empty-set race를 명확하게 막을 수 있다.
* 애플리케이션 로직이 직관적이다.
* unique 충돌 복구 경로가 단순해진다.

**Cons**
* PostgreSQL 전용 동작에 코드가 직접 의존한다.
* SQLite 기반 테스트와 운영 DB 동작 차이가 커진다.
* lock key 관리와 lock 경합 관찰이 추가로 필요하다.

### 3. 별도 run slot table 도입

`training_run_slot` 같은 singleton table을 두고 현재 `running_run_id`, `pending_run_id`를 관리한다. 이 row를 `SELECT ... FOR UPDATE`로 잠근 뒤 run 상태를 갱신한다.

**Pros**
* 항상 잠글 수 있는 row가 있어 empty-set race가 없다.
* run slot 상태 모델이 명시적이다.
* 향후 slot 정책이 복잡해질 때 확장성이 좋다.

**Cons**
* 새 table과 migration이 필요하다.
* `MLPipelineRun`과 slot table이라는 두 상태 원장을 동기화해야 한다.
* 장애 시 reconciliation 대상이 늘어난다.
* 현재 문제를 해결하기에는 변경 범위가 크다.

## 3. 결정 사항 (Decision Outcome)

* **1번, unique index 충돌을 잡고 현재 slot을 재조회하는 방식을 선택한다.**
* **2번 advisory lock은 PostgreSQL 전용 직렬화가 꼭 필요해질 때 재검토한다.**
* **3번 별도 run slot table은 run slot 정책이 더 복잡해질 때 확장 옵션으로 남긴다.**

**이유**
* production schema는 이미 `RUNNING`과 `PENDING` partial unique index를 통해 핵심 invariant를 표현하고 있다.
* 현재 결함은 DB가 중복 생성을 막는 순간을 애플리케이션이 정상 멱등 결과로 번역하지 않는 데 있다.
* 새 lock 체계나 새 table을 도입하지 않아도 목표를 달성할 수 있다.
* local schema projection에도 같은 partial unique index를 반영하면 테스트가 production invariant를 더 잘 따른다.

## 4. 결정된 설계 원칙 (Decision Details)

* `RUNNING` run은 DB unique constraint 기준으로 최대 하나만 허용한다.
* `PENDING` run도 DB unique constraint 기준으로 최대 하나만 허용한다.
* `DbRunSlotStore`는 run insert/update 중 unique 충돌이 나면 실패로 끝내지 않고 현재 slot 상태를 다시 읽는다.
* 충돌 후 같은 `dataset_version`의 active run이 있으면 해당 run을 반환한다.
* 충돌 후 다른 `RUNNING`이 있으면 그 run을 반환하고 `should_execute_now=True`로 둔다.
* 충돌 후 `PENDING`만 있으면 그 run을 반환하고 `should_execute_now=False`로 둔다.
* local SQLAlchemy projection은 Core API migration의 partial unique index를 mirror해서 integration test가 weaker schema 위에서 통과하지 않게 한다.
* 이 결정은 run slot claim에 한정한다. release transition race는 별도 결정 또는 후속 수정에서 다룬다.

## 5. 긍정적 효과 (Positive Consequences)

* 동시에 들어온 중복 `TRAINING_REQUEST`가 DB 에러 대신 하나의 slot 상태로 수렴한다.
* 기존 production schema invariant를 재사용하므로 변경 범위가 작다.
* local integration test가 `RUNNING` / `PENDING` uniqueness를 더 현실적으로 검증한다.
* 새 관리 자원 없이 현재 `MLPipelineRun` 중심 상태 모델을 유지한다.

## 6. 부정적 효과 및 위험 요소 (Negative Consequences)

* unique 충돌이 정상 제어 흐름의 일부가 된다.
  * **대응:** insert/update 구간을 nested transaction 또는 savepoint로 감싸고, 충돌 후 현재 slot을 재조회한다.
* DB별 partial index 지원 차이가 테스트 신뢰도에 영향을 줄 수 있다.
  * **대응:** local projection에 SQLite/PostgreSQL partial index 조건을 모두 명시한다.
* 충돌 후 현재 slot을 찾지 못하는 비정상 상태가 생길 수 있다.
  * **대응:** 이 경우 domain decision을 만들지 않고 명시적 오류를 발생시켜 reconciliation 또는 운영 알림 대상으로 남긴다.

## 7. 결정 이후 후속 결과 (Consequences)


