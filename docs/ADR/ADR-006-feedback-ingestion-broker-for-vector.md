# [ADR-006] feedback-ingestion-broker-for-vector

* **상태 (Status):** Superseded(대체됨)
* **날짜 (Date):** 2026-04-22
* **대체됨:** [ADR-007 feedback-ingestion-vector-http-source](./ADR-007-feedback-ingestion-vector-http-source.md)

## 1. 배경 및 문제 상황 (Context and Problem Statement)

* **상황:** Biblio는 검색 응답 단위 사용자 피드백을 수집하고, Feedback Ingestion Pipeline이 이를 원본 로그 형태로 Object Storage에 적재하는 구조를 채택하고 있다. 이 파이프라인은 Vector를 사용해 schema routing, raw/error sink 분기, buffering, retry, observability를 구성하려 한다.
* **문제:** 기존 비동기 처리 경로는 PGMQ를 기본 broker로 사용한다. 그러나 Vector 공식 component 기준으로 PGMQ source가 없어, PGMQ를 그대로 사용하면 "Object Storage 적재 성공 이후 broker ack" 경계를 Vector만으로 보장하기 어렵다.
* **목표:** Vector를 Feedback Ingestion Pipeline의 핵심 runtime으로 사용하면서도 at-least-once delivery, 손실 최소화, append-only raw log 적재, 운영 단순성을 만족하는 broker를 선택한다.

## 2. 고려한 옵션들 (Considered Options)

### 1. PGMQ 유지 + Python consumer 직접 구현

Core API가 PGMQ queue에 feedback event를 발행하고, Python 기반 Feedback Ingestion Pipeline consumer가 PGMQ에서 읽어 Object Storage에 직접 저장한 뒤 `archive`로 ack한다.

**Pros**
* 현재 서비스의 PGMQ publish/consume 방식을 재사용할 수 있다.
* ack 경계가 명확하다.
* 새 managed broker를 추가하지 않아 인프라 비용과 설정이 작다.
* local e2e 환경이 단순하다.

**Cons**
* Vector를 Feedback Ingestion Pipeline runtime으로 쓰지 못한다.
* schema routing, sink 분기, buffer/retry 설정을 Python 코드로 직접 구현해야 한다.
* Vector 기반 observability와 pipeline configuration 학습 목표를 달성하지 못한다.

### 2. PGMQ 유지 + Python bridge + Vector HTTP source

Python bridge가 PGMQ에서 feedback event를 읽고 Vector HTTP source로 전달한다. Vector가 Object Storage sink까지 처리한 뒤 성공 응답을 반환하면 Python bridge가 PGMQ message를 ack한다.

**Pros**
* PGMQ를 유지하면서 Vector transforms와 Object Storage sink를 사용할 수 있다.
* Python bridge가 PGMQ ack 정책을 직접 제어할 수 있다.
* 기존 broker 인프라와 새 Vector runtime을 함께 사용할 수 있다.

**Cons**
* Python bridge가 실질적인 consumer가 되어 컴포넌트 책임이 나뉜다.
* Vector HTTP 응답과 storage 저장 완료 확인의 의미를 정확히 맞춰야 한다.
* 장애 지점이 PGMQ, bridge, Vector, Object Storage로 늘어난다.
* 운영 복잡도 대비 Vector 사용 이점이 제한적이다.

### 3. GCP Pub/Sub + Vector `gcp_pubsub` source

Core API가 feedback event를 GCP Pub/Sub topic에 발행하고, Vector가 `gcp_pubsub` source로 subscription을 읽는다. Vector는 schema routing 후 GCP Cloud Storage sink로 raw/error logs를 저장한다.

**Pros**
* Vector 공식 source/sink 조합을 사용한다.
* Vector end-to-end acknowledgements 모델과 잘 맞는다.
* GCS sink와 같은 GCP 권한, 네트워크, 운영 경계를 공유할 수 있다.
* Python bridge 없이 source, transform, sink를 하나의 Vector topology로 표현할 수 있다.
* Pub/Sub at-least-once delivery와 피드백 파이프라인의 중복 허용 계약이 일치한다.

**Cons**
* PGMQ 외에 새 managed broker를 추가한다.
* Core API feedback publisher가 video-processing message와 다른 broker adapter를 사용해야 한다.
* local e2e에는 Pub/Sub emulator 또는 broker test double이 필요하다.
* Pub/Sub delivery, retention, runtime, GCS operation 비용을 운영 비용으로 관리해야 한다.

### 4. Kafka + Vector source

Core API가 Kafka topic에 feedback event를 발행하고, Vector가 Kafka source로 읽어 Object Storage sink로 적재한다.

**Pros**
* Vector와 Kafka source는 대량 이벤트 처리에 적합하다.
* topic, consumer group, retention 설정을 세밀하게 제어할 수 있다.
* 고처리량 이벤트 파이프라인으로 확장하기 쉽다.

**Cons**
* 현재 프로젝트 규모와 목적 대비 운영 부담이 크다.
* broker cluster 운영, local 개발 환경, 장애 대응 비용이 증가한다.
* Feedback Ingestion Pipeline 하나를 위해 도입하기에는 과하다.

## 3. 결정 사항 (Decision Outcome)

* **3번, 즉 GCP Pub/Sub + Vector `gcp_pubsub` source를 선택한다.**
* **PGMQ는 video-processing과 control-message 경로의 기본 broker로 유지한다.**
* **PGMQ + Python consumer는 Vector 사용이 목표가 아닌 경우의 fallback 옵션으로 남긴다.**
* **PGMQ + Python bridge와 Kafka는 현 단계에서 채택하지 않는다.**

**이유**
* Feedback Ingestion Pipeline은 Vector를 runtime으로 사용한다는 목표가 있다.
* Pub/Sub는 Vector 공식 source로 사용할 수 있어 PGMQ bridge보다 source-to-sink 책임 경계가 단순하다.
* GCS sink와 같은 GCP 운영 경계를 공유하므로 IAM, region, 관측성, 배포 구성이 일관된다.
* Feedback event는 사용자 동기 경로에서 분리된 비동기 로그 수집 이벤트이며, Pub/Sub의 at-least-once delivery와 duplicate 허용 모델이 FIP 계약과 맞는다.
* Kafka는 확장성은 높지만 현재 MVP 범위에서는 운영 복잡도가 과하다.

## 4. 결정된 설계 원칙 (Decision Details)

* Core API의 feedback publish path는 Pub/Sub topic으로 validated feedback event를 발행한다.
* Feedback Ingestion Pipeline은 Vector `gcp_pubsub` source로 subscription을 읽는다.
* Vector는 `schema_version`과 필수 필드 기준으로 raw event와 error event를 분기한다.
* 정상 event는 별도 feedback log bucket의 `raw_logs/` 경로에 append-only 형태로 저장한다.
* 구조상 처리할 수 없는 event는 같은 bucket의 `error_logs/` 경로에 원본 payload와 실패 원인을 보존한다.
* Feedback event는 video-processing shared envelope와 분리된 schema를 유지한다.
* PGMQ queue 이름 `FEEDBACK_EVENT`는 더 이상 FIP broker 계약으로 사용하지 않는다. Pub/Sub topic과 subscription 이름은 FIP Spec 또는 Plan에서 확정한다.
* Pub/Sub, Vector, GCS bucket은 같은 region 또는 region group에 맞춰 불필요한 cross-region transfer를 피한다.
* 비용 관리는 Pub/Sub delivery보다 Vector runtime, GCS storage, GCS write operation, retention policy를 함께 본다.

## 5. 긍정적 효과 (Positive Consequences)

* Vector를 source부터 sink까지 일관된 topology로 사용할 수 있다.
* PGMQ custom source 또는 bridge 없이 공식 component 조합을 사용할 수 있다.
* Object Storage 적재와 source ack의 의미를 Vector acknowledgement 모델에 맞출 수 있다.
* feedback ingestion과 video-processing broker 책임이 분리되어 각 경로의 운영 목적이 명확해진다.
* GCP 기반 Object Storage와 broker 권한 구성이 같은 운영 경계 안에 놓인다.

## 6. 부정적 효과 및 위험 요소 (Negative Consequences)

* broker가 PGMQ와 Pub/Sub로 나뉘어 Core API publisher 구성이 복잡해진다.
  * **대응:** feedback publisher adapter를 video-processing broker adapter와 분리하고, route별 broker 선택을 명시적으로 구성한다.
* Pub/Sub와 Vector runtime 비용이 새로 발생한다.
  * **대응:** topic retention을 짧게 유지하고, Vector runtime resource request와 GCS lifecycle policy를 MVP 규모에 맞게 작게 시작한다.
* local 개발과 e2e 테스트가 Pub/Sub emulator 또는 test double에 의존한다.
  * **대응:** local profile에서는 Pub/Sub emulator 또는 in-memory publisher double을 사용하고, staging에서 실제 Pub/Sub smoke를 수행한다.
* Pub/Sub message가 재전달되면 duplicate raw event가 생길 수 있다.
  * **대응:** raw log는 duplicate를 보존하고, downstream dataset generation은 `event_id` 기준 dedupe를 수행한다.
* 기존 Feedback Ingestion Pipeline Spec과 Plan의 PGMQ 관련 문구가 이 결정과 충돌한다.
  * **대응:** ADR 승인 후 FIP Spec과 Plan을 Pub/Sub + Vector 기준으로 개정한다.

## 7. 결정 이후 후속 결과 (Consequences)

* 2026-04-23: [ADR-007 feedback-ingestion-vector-http-source](./ADR-007-feedback-ingestion-vector-http-source.md)가 이 결정을 대체한다. Feedback ingestion은 Pub/Sub broker 경로 대신 Core API Server가 Vector `http_server` source로 직접 validated feedback event를 전달하는 방향으로 수정되었다.
