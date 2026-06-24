# [ADR-008] feedback-ingestion-best-effort-persistence

* **상태 (Status):** Accepted
* **날짜 (Date):** 2026-04-27

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** Biblio는 Core API Server가 검증한 feedback event를 Feedback Ingestion Pipeline으로 전달하고, Feedback Ingestion Pipeline은 이를 원본 로그 형태로 Object Storage에 적재한다.
* **문제:** Object Storage sink는 일시 장애, 지연, 권한 문제, 네트워크 오류로 실패할 수 있다. 이때 검색 서비스 가용성을 우선하면서도 feedback persistence 실패를 어떤 정책으로 처리할지 명확히 정해야 한다.
* **목표:** 검색 서비스 본경로를 보호하고 시스템 단순성을 유지하면서, feedback ingestion 실패 시의 처리 경계를 명시한다.

## 2. 고려한 옵션들 (Considered Options)

### 1. 제한된 재시도 후 폐기

Feedback Ingestion Pipeline은 제한된 횟수와 시간 동안만 sink write를 재시도하고, 그 이후에도 영속화에 실패하면 해당 feedback event를 폐기한다.

**Pros**
* 검색 서비스와 feedback ingestion 경계가 단순하게 유지된다.
* 장기 장애 시 무제한 backlog, 재처리 저장소, 별도 recovery workflow를 도입하지 않아도 된다.
* feedback persistence 실패가 검색 서비스 본경로를 오래 점유하지 않는다.

**Cons**
* 일부 feedback event 유실을 허용해야 한다.
* 장애 시간대나 특정 요청 구간의 feedback 분포가 왜곡될 수 있다.
* drop 규모를 관측하지 못하면 정책이 과도하게 낙관적으로 운영될 수 있다.

### 2. 사실상 무기한 재시도

Feedback Ingestion Pipeline은 disk buffer와 backpressure를 사용해 sink write가 성공할 때까지 장시간 재시도한다.

**Pros**
* feedback event 유실 가능성을 낮출 수 있다.
* 별도 dead-letter 저장소 없이도 보존성을 높일 수 있다.
* short outage에는 운영 개입 없이 회복될 가능성이 높다.

**Cons**
* 장기 장애 시 disk buffer 고갈과 backlog 누적 위험이 커진다.
* 장애 회복 시 flush surge가 생길 수 있다.
* 실제 한계는 disk capacity가 되어 운영 경계가 불분명해질 수 있다.

### 3. 별도 격리 저장소 또는 dead-letter 경로 추가

정상 sink write가 재시도 이후에도 실패하면, 해당 event를 별도 durable 저장소로 이동하고 이후 재처리한다.

**Pros**
* feedback event 유실을 가장 명시적으로 줄일 수 있다.
* 장애 복구와 재처리 절차를 운영적으로 설계할 수 있다.
* persistence 실패 경계가 분명해진다.

**Cons**
* 저장 경로, 재처리 절차, 운영 도구가 추가되어 구조가 커진다.
* 현재 단계의 feedback ingestion 범위 대비 운영 복잡도가 증가한다.
* 실제 failure rate를 모르는 상태에서는 과한 초기 투자일 수 있다.

## 3. 결정 사항 (Decision Outcome)

* **1번, 즉 제한된 재시도 후 폐기 방식을 선택한다.**
* **3번은 feedback 유실이 제품적으로 허용되지 않거나 sink failure rate가 높게 확인될 때의 확장 옵션으로 남긴다.**
* **2번은 장기 장애 시 운영 경계를 흐리므로 현 단계에서 채택하지 않는다.**

**이유**
* 검색 서비스 가용성이 feedback persistence보다 우선이다.
* 현 단계의 feedback ingestion은 best-effort 수집 경로로 정의하는 것이 시스템 목표와 맞다.
* managed broker, durable quarantine, replay workflow 없이 구조를 단순하게 유지하는 편이 현재 운영 성숙도에 맞다.
* 아직 sink failure rate를 모르는 상태에서 복잡한 durability machinery를 먼저 도입하는 것은 과할 수 있다.

## 4. 결정된 설계 원칙 (Decision Details)

* feedback ingestion은 가능한 한 저장하되, 실패 시 일부 유실을 허용하는 경로로 취급한다.
* Core API Server와 Feedback Ingestion Pipeline은 검색 서비스 본경로의 가용성을 우선한다.
* Feedback Ingestion Pipeline의 Object Storage sink는 bounded retry만 수행한다.
* bounded retry 이후에도 sink write가 실패하면 해당 feedback event는 폐기할 수 있다.
* 일부 feedback 유실은 현 단계에서 허용 가능한 제품 정책으로 본다.
* persistence 실패 규모와 추세를 판단할 수 있도록 failure count, retry exhaustion, sink error observability는 유지한다.
* 이 결정은 raw feedback log의 완전 보존보다 검색 서비스 보호와 구조 단순성을 우선하는 범위에서만 적용한다.

## 5. 긍정적 효과 (Positive Consequences)

* 검색 서비스 본경로가 장기 sink 장애에 덜 끌려간다.
* Feedback Ingestion Pipeline의 운영 경계가 단순하다.
* dead-letter 저장소, replay worker, 별도 복구 절차 없이도 현재 범위를 유지할 수 있다.
* 초기 운영 비용과 구조 복잡도를 낮춘다.

## 6. 부정적 효과 및 위험 요소 (Negative Consequences)

* 장기 sink 장애 시 feedback event가 유실될 수 있다.
  * **대응:** sink failure count와 retry exhaustion을 관측하고, 허용 범위를 넘는 손실이 확인되면 durable quarantine(따로저장) 또는 재처리 경로 도입을 재검토한다.
* 특정 시간대 또는 요청 구간의 feedback 분포가 치우칠 수 있다.
  * **대응:** dataset 생성 단계에서 수집량과 분포 이상을 함께 점검하고, 장애 시간대 metadata와 함께 해석한다.
* failure rate를 모르는 상태에서 정책이 과소설계일 수 있다.
  * **대응:** 운영 초기에는 failure metric과 smoke 결과를 기준으로 정책 적합성을 재평가한다.

## 7. 결정 이후 후속 결과 (Consequences)

