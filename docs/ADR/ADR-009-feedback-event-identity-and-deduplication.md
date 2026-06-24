# [ADR-009] feedback-event-identity-and-deduplication

* **상태 (Status):** Accepted
* **날짜 (Date):** 2026-04-27

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** 현재 Core API Server가 검색 응답 단위 feedback event를 생성하고, Feedback Ingestion Pipeline이 이를 append-only raw log로 저장한 뒤, 후속 ML dataset generation이 중복을 제거하는 구조를 사용하고 있다.
* **문제:** 사용자 기준으로 같은 피드백 1건이 네트워크 재시도나 HTTP 재전송으로 여러 번 전달될 수 있다. 이때 `event_id`가 매번 새로 생성되면 raw log의 중복 전달을 후속 단계에서 같은 feedback로 식별할 수 없다.
* **목표:** raw log의 append-only 정책은 유지하면서도, 사용자 기준으로 같은 피드백 1건은 같은 식별자로 추적하고 중복 제거할 수 있게 한다.

## 2. 고려한 옵션들 (Considered Options)

### 1. 안정적인 필드 조합으로 deterministic `event_id` 생성

사용자 기준으로 같은 피드백 1건을 나타내는 안정적인 필드 조합으로 `event_id`를 결정한다. 같은 입력이면 같은 `event_id`가 다시 생성된다.

**Pros**
* raw log append-only 정책과 downstream dedupe 계약을 함께 유지할 수 있다.
* 클라이언트 변경 없이 Core API 내부 규칙만으로 identity를 고정할 수 있다.
* 재시도와 중복 전달이 생겨도 사용자 기준으로 같은 피드백 1건을 같은 식별자로 추적할 수 있다.

**Cons**
* 어떤 필드를 identity에 포함할지 명확히 결정해야 한다.
* identity 규칙을 바꾸면 과거 데이터와 의미가 달라질 수 있다.

### 2. `event_id`와 별도 dedupe key를 분리

`event_id`는 전송 인스턴스를 나타내고, 별도의 dedupe key를 추가해 사용자 기준으로 같은 피드백 1건을 묶는다.

**Pros**
* 전송 인스턴스와 논리적 identity를 개념적으로 분리할 수 있다.
* raw log에서 전송 단위 추적과 논리적 dedupe를 동시에 표현할 수 있다.

**Cons**
* schema와 downstream contract가 커진다.
* 현재 단계에서는 필드와 의미를 두 개로 관리할 이유가 크지 않다.

## 3. 결정 사항 (Decision Outcome)

* **1번, 즉 안정적인 필드 조합으로 deterministic `event_id`를 생성하는 방식을 선택한다.**
* **2번은 향후 전송 인스턴스 식별과 논리적 identity를 분리해야 할 명확한 요구가 생길 때의 확장 옵션으로 남긴다.**

**이유**
* raw log는 append-only로 중복 전달 사실을 보존하되, downstream은 사용자 기준으로 같은 피드백 1건을 하나로 접을 수 있어야 한다.
* Core API, Feedback Ingestion Pipeline, ML dataset generation이 공통으로 사용할 수 있는 단일 identity 규칙이 필요하다.
* 현재 범위에서는 별도 dedupe key를 추가하는 것보다 `event_id` 자체를 논리적 feedback identity로 정의하는 편이 단순하다.

## 4. 결정된 설계 원칙 (Decision Details)

* `event_id`는 랜덤 전송 식별자가 아니라 논리적 feedback identity로 사용한다.
* 같은 `user_id`, `req_id`, `rating` 조합의 피드백 1건이 재시도되거나 중복 전달되면 같은 `event_id`를 사용한다.
* 같은 `req_id`에 대해 `LIKE`와 `DISLIKE`는 다른 feedback으로 간주한다.
* 같은 `user_id`, `req_id`, `rating` 조합으로 반복 제출된 feedback은 같은 이벤트로 간주한다.
* Core API는 `event_id`를 UUIDv5로 생성한다.
* canonical string은 `feedback:{user_id}:{req_id}:{rating}`를 사용한다.
* Feedback Ingestion Pipeline은 같은 `event_id`의 중복 전달을 허용하고 raw log에 보존할 수 있다.
* downstream dataset generation은 `event_id` 기준으로 중복 제거를 수행한다.

## 5. 긍정적 효과 (Positive Consequences)

* 재시도나 중복 전달이 생겨도 사용자 기준으로 같은 피드백 1건을 안정적으로 추적할 수 있다.
* append-only raw log 정책과 downstream dedupe 정책이 충돌하지 않는다.
* Core API, FIP, ML worker 사이의 identity 계약이 단순해진다.

## 6. 부정적 효과 및 위험 요소 (Negative Consequences)

* identity 규칙이 잘못 정의되면 서로 다른 feedback를 같은 이벤트로 취급할 수 있다.
  * **대응:** `rating`을 identity 입력에 포함해 같은 `req_id`의 `LIKE`와 `DISLIKE`를 분리한다.
* 나중에 identity 의미를 바꾸면 과거 데이터와 계약 호환성이 흔들릴 수 있다.
  * **대응:** 이 규칙을 ADR과 관련 spec에 함께 고정하고, 이후 변경은 새 ADR로 다룬다.
* 전송 인스턴스 자체를 별도로 식별해야 하는 요구가 생기면 현재 `event_id`만으로는 부족할 수 있다.
  * **대응:** 그런 요구가 생기면 별도 delivery metadata 또는 dedupe key 분리 ADR을 추가한다.

## 7. 결정 이후 후속 결과 (Consequences)
