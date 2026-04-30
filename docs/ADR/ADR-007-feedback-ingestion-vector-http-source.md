# [ADR-007] feedback-ingestion-vector-http-source

* **상태 (Status):** Accepted
* **날짜 (Date):** 2026-04-23
* **대체 대상:** [ADR-006 feedback-ingestion-broker-for-vector](./ADR-006-feedback-ingestion-broker-for-vector.md)

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** Biblio는 검색 응답 단위 사용자 피드백을 수집하고, Feedback Ingestion Pipeline이 이를 원본 로그 형태로 Object Storage에 적재하는 구조를 채택하고 있다. Core API Server는 `req_id` 기반 스냅샷 검증을 수행하고, Feedback Ingestion Pipeline은 Vector를 사용해 schema routing, raw/error sink 분기, buffering, retry, observability를 구성한다.
* **문제:** ADR-006은 GCP Pub/Sub를 feedback event broker로 선택했다. 그러나 현 단계에서는 feedback ingestion 경로의 managed broker 도입을 줄이고, Core API Server와 Vector 사이를 더 단순한 직접 전달 경계로 구성하려 한다.
* **목표:** Vector를 Feedback Ingestion Pipeline의 핵심 runtime으로 유지하면서, Core API Server가 검증한 feedback event를 Vector에 직접 전달하고 Object Storage raw log 적재까지의 책임과 리스크를 명확히 한다.

## 2. 고려한 옵션들 (Considered Options)

### 1. Core API Server -> Vector `http_server` source -> Object Storage

Core API Server가 snapshot 검증을 통과한 feedback event를 Vector의 internal HTTP source로 직접 전송한다. Vector는 공통 transform으로 schema를 검사하고, 정상 event와 error event를 Object Storage sink로 분기한다.

**Pros**
* broker 없이 Core API Server와 Vector 사이의 전달 경계가 단순하다.
* Vector의 schema routing, raw/error sink, buffer/retry 설정을 그대로 ingestion runtime의 중심에 둘 수 있다.
* Pub/Sub topic, subscription, IAM, emulator, retention 설정이 필요 없다.
* local/CI 검증은 HTTP source와 fixture/local sink 조합으로 단순화할 수 있다.
* feedback event가 video-processing shared envelope와 분리된 전용 schema를 유지한다.

**Cons**
* Vector 장애가 feedback ingestion 실패로 직접 이어진다.
* Vector 또는 Object Storage 지연이 Core API Server의 feedback 요청 처리 시간에 영향을 준다.
* broker backlog, retention, replay 기능이 사라진다.
* Core API Server 쪽 timeout, retry, observability 정책이 중요해진다.
* 향후 implicit feedback처럼 이벤트량이 커지면 별도 ingest component 또는 broker 재도입을 검토해야 한다.

### 2. 별도 Feedback Ingest API -> Vector `http_server` source -> Object Storage

Core API Server가 아닌 별도 Feedback Ingest API가 feedback event를 받아 Vector HTTP source로 전달한다. Core API Server의 핵심 요청 경로와 대량 feedback ingestion 경로를 분리한다.

**Pros**
* feedback traffic을 Core API Server와 독립적으로 scale out할 수 있다.
* rate limit, sampling, batching, admission control을 feedback 전용으로 둘 수 있다.
* implicit feedback처럼 이벤트량이 커질 때 Core API Server 보호 효과가 크다.

**Cons**
* 새 application component를 운영해야 한다.
* Vector 또는 Object Storage 장애가 Feedback Ingest API에는 여전히 직접 영향을 준다.
* 현 단계의 명시적 feedback ingestion 범위에는 구조가 커질 수 있다.

### 3. Vector sidecar -> Vector aggregator -> Object Storage

Core API Server 옆의 Vector sidecar가 event를 받고, 중앙 Vector aggregator가 Object Storage sink를 담당한다.

**Pros**
* Core API Server는 가까운 local collector만 바라볼 수 있다.
* Object Storage sink, auth, routing 설정을 중앙 aggregator에 모을 수 있다.
* 여러 application replica의 수집 경로를 일관되게 구성할 수 있다.

**Cons**
* Vector hop이 2개가 되어 배포, 모니터링, 장애 추적이 복잡해진다.
* Pub/Sub 같은 durable retention과 replay 기능은 제공하지 않는다.
* sidecar와 aggregator 양쪽의 buffer 정책을 함께 운영해야 한다.

### 4. Broker 기반 경로 유지

Core API Server 또는 별도 ingest component가 Pub/Sub/Kafka 같은 broker에 event를 발행하고, Vector가 broker source로 읽어 Object Storage에 저장한다.

**Pros**
* 피크 트래픽 흡수, redelivery, retention, replay, 장애 격리가 가장 명확하다.
* Core API Server가 Vector 또는 Object Storage 지연에서 분리된다.
* at-least-once delivery와 downstream dedupe 계약을 운영하기 쉽다.

**Cons**
* 현 단계에서 줄이려는 broker 운영 비용과 설정 복잡도가 남는다.
* Pub/Sub/Kafka provisioning, IAM, emulator 또는 integration smoke가 필요하다.
* feedback ingestion 경로가 video-processing broker 경로와 별도 운영면을 가진다.

## 3. 결정 사항 (Decision Outcome)

* **1번, 즉 Core API Server -> Vector `http_server` source -> Object Storage 방식을 선택한다.**
* **2번은 implicit feedback 또는 대량 feedback traffic이 실제 요구로 커질 때의 확장 옵션으로 남긴다.**
* **3번은 다수 application replica의 수집 경로를 중앙화해야 할 때 재검토한다.**
* **4번은 feedback event 보존성, replay, 장애 격리가 direct HTTP보다 중요해질 때 재검토한다.**

**이유**
* 현 단계의 feedback ingestion은 Core API Server가 검증한 검색 응답 단위 event를 원본 로그로 보존하는 것이 핵심이다.
* Vector를 runtime으로 유지하면서도 broker 운영면을 제거하려면, Vector HTTP source를 사용하는 직접 전달 방식이 가장 단순한 설계 방식이다
* direct HTTP 경로의 리스크는 명확하며, Spec과 Plan에서 timeout, retry, observability, dedupe 계약으로 다룰 수 있다.

## 4. 결정된 설계 원칙 (Decision Details)

* Core API Server는 `SearchResponseSnapshot` 검증을 통과한 feedback event만 Vector HTTP source로 전달한다.
* Feedback Ingestion Pipeline은 Vector `http_server` source로 feedback event를 수신한다.
* Vector는 `schema_version`과 필수 필드 기준으로 raw event와 error event를 분기한다.
* 정상 event는 별도 feedback log bucket의 `raw_logs/` 경로에 append-only 형태로 저장한다.
* 구조상 처리할 수 없는 event는 같은 bucket의 `error_logs/` 경로에 원본 payload와 실패 원인을 보존한다.
* Feedback event는 video-processing shared envelope와 분리된 schema를 유지한다.
* Core API Server는 Vector 또는 Object Storage 장애를 feedback delivery failure로 취급한다.
* downstream dataset generation은 동일 `event_id`가 중복 저장될 수 있음을 전제로 dedupe를 수행한다.

## 5. 긍정적 효과 (Positive Consequences)

* Feedback ingestion 경로에서 managed broker 운영면을 제거한다.
* Core API Server와 Feedback Ingestion Pipeline 사이의 data flow가 단순해진다.
* Vector의 transform, routing, sink 설정을 ingestion runtime의 중심에 유지한다.
* local/CI 검증에서 Pub/Sub emulator나 cloud subscription이 필요하지 않다.
* FIP의 source adapter만 바꾸고 raw/error routing과 Object Storage sink 계약은 유지할 수 있다.

## 6. 부정적 효과 및 위험 요소 (Negative Consequences)

* Vector 장애가 feedback ingestion에 직접 영향을 준다.
  * **대응:** Core API Server는 Vector delivery failure를 명확한 실패로 기록하고, 짧은 timeout과 제한된 retry를 둔다.
* Vector 또는 Object Storage 지연이 Core API Server feedback 요청 처리 시간에 영향을 준다.
  * **대응:** feedback delivery latency를 별도 metric으로 추적하고, timeout 초과 시 사용자 요청 경로를 오래 점유하지 않도록 한다.
* broker backlog, retention, replay 기능이 사라진다.
  * **대응:** Vector buffer 상태, GCS sink failure, delivery failure count를 운영 지표로 두고, 재처리는 raw/error log와 application-level retry 범위에서 다룬다.
* Core API Server retry로 duplicate raw event가 생길 수 있다.
  * **대응:** raw log는 duplicate를 보존하고, downstream dataset generation은 `event_id` 기준 dedupe를 수행한다.
* implicit feedback처럼 이벤트량이 커지면 Core API Server가 ingestion traffic의 영향을 받을 수 있다.
  * **대응:** 대량 feedback 요구가 생기면 별도 Feedback Ingest API 또는 broker 기반 경로를 재검토한다.
* Vector HTTP endpoint가 새로운 내부 network surface가 된다.
  * **대응:** endpoint는 내부 네트워크에만 노출하고, 인증 또는 mTLS 같은 보호 장치를 Spec/Plan에서 확정한다.

## 7. 결정 이후 후속 결과 (Consequences)

* ADR-006은 이 ADR로 대체된다.
* Feedback Ingestion Pipeline Spec과 Plan은 Pub/Sub source 계약을 Vector HTTP source 계약으로 개정한다.
* Core API Server Spec과 Plan은 Pub/Sub publisher 구현 계획을 Vector HTTP delivery client 계획으로 개정한다.
* Feedback Ingestion Pipeline production config는 `gcp_pubsub` source 대신 `http_server` source를 사용하도록 개정한다.
* Pub/Sub topic/subscription provisioning, IAM, emulator, backlog 지표는 feedback ingestion 범위에서 제거한다.
