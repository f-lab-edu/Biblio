# [Feedback Ingestion Pipeline] SPEC

**메타 정보**
- Component ID: feedback-ingestion-pipeline
- SOT: `docs/system-design.md` (이 SPEC은 system design SOT와 일관되어야 한다)
- 관련 문서: `docs/ADR/ADR-007-feedback-ingestion-vector-http-source.md`, `docs/ADR/ADR-006-feedback-ingestion-broker-for-vector.md`, `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`, `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
- Status: Draft (cycle-2)

---

## 1. 목적과 범위

### 1.1 한 줄 요약
- Feedback Ingestion Pipeline(FIP)은 Core API Server가 검증 완료한 검색 응답 단위 피드백 이벤트를 내부 HTTP ingress로 수신하여, 손실을 최소화하면서 수정 불가능한 원본 로그로 Object Storage에 적재하는 컴포넌트다.

### 1.2 책임 경계
- 범위에 포함:
  - Core API가 전달한 validated feedback event 수신
  - 원본 이벤트를 append-only 로그로 Object Storage에 저장
  - 재전송/중복 수신 가능성을 전제로 손실 최소화 계약 제공
  - trace_id/event_id 기준 관측성과 운영 재조정 근거 제공
- 범위에서 제외:
  - 사용자 인증/인가, req_id 유효성 검증, 허용 시간 창 판정
  - SearchResponseSnapshot 생성/수정/만료 관리
  - Core API의 HTTP delivery retry 구현
  - 피드백 집계, 라벨 정제, 학습용 데이터셋 생성
  - ranking/학습 로직, offline analytics schema 설계
- 상위 의존성:
  - Client가 호출한 Core API Server
  - Metadata DB의 `SearchResponseSnapshot` (검증 책임은 Core API 소유)
- 하위 소비자:
  - Object Storage 원본 이벤트 로그
  - ML Lifecycle Worker의 피드백 데이터셋 생성 배치

간단한 흐름:
1. Client가 Core API Server에 검색 응답 단위 feedback를 보낸다.
2. Core API Server가 `req_id`, 사용자, 프로젝트, 허용 시간 창을 검증하고 검색 시점 문맥이 포함된 validated feedback event를 만든다.
3. Core API Server가 이 이벤트를 FIP의 Vector HTTP ingress로 전달한다.
4. Vector는 설정된 라우팅 규칙에 따라 이벤트를 Object Storage의 append-only raw log로 보낸다.
5. 구조상 처리할 수 없는 이벤트는 error log로 분기한다.
6. Vector 또는 Object Storage 일시 장애가 있으면 Vector buffer/retry와 Core API delivery failure handling 경로를 따른다.

### 1.3 기술 스택 선택
| 영역 (Area) | 선택안 (Choice) | 왜 이 선택인가 |
| --- | --- | --- |
| Ingestion runtime | Vector | `schema_version` 기반 라우팅과 멀티 sink(raw_logs/error_logs/)를 설정으로 구성할 수 있다. HTTP source에서 읽고 Object Storage로 쓰는 과정의 buffering, retry, 처리 완료 신호는 Vector source/sink 설정과 Core API delivery 정책이 함께 담당한다. Logstash 대비 경량(Rust 기반, JVM 불필요)이며, 별도 수집 프로그램을 새로 만들 필요가 적다. |
| Feedback transport | Vector internal HTTP ingress | ADR-007에 따라 Core API가 validated feedback event를 Vector `http_server` source로 직접 전달한다. PGMQ는 video-processing/control-message 경로에 유지하고, feedback ingestion은 feedback 전용 HTTP delivery 경계로 분리한다. |

---

## 2. 계약 (Contracts)

### 2.1 외부 인터페이스

#### 외부 진입 인터페이스
| 인터페이스 | 메서드 / 트리거 | 입력 요약 | 출력 요약 | 인증 / 테넌시 | 비고 |
| --- | --- | --- | --- | --- | --- |
| Vector ingestion HTTP ingress | Core API가 internal HTTP POST로 validated feedback event 전달 | 검색 응답 단위 피드백 1건 | 정상 이벤트는 raw_logs sink로 보내고, 구조상 처리할 수 없는 이벤트는 error_logs sink로 분기 | 사용자 인증은 upstream에서 완료. ingress는 내부 네트워크 경계와 service-to-service 보호를 전제로 한다 | FIP는 client-facing public HTTP API를 소유하지 않는다 |

#### 메시지 / 이벤트 계약
- Transport: Core API가 FIP 내부 Vector HTTP ingress로 validated feedback event를 전달한다.
- Routing contract:
  - Core API와 FIP는 feedback event 전용 internal HTTP endpoint 계약을 공유한다.
  - 실제 address/path/auth 방식은 FIP PLAN 및 배포 설정에서 주입하며, public client가 직접 호출할 수 없어야 한다.
- Producer / pipeline 책임:
  - Producer(Core API): `req_id` 스냅샷 검증, 사용자/시간 창/무효화 여부 검사, feedback 전용 계약으로 HTTP delivery 수행
  - FIP(Vector pipeline): 수신 이벤트를 원본 로그 형태로 Object Storage에 보내고 trace/event 기준으로 관측 가능하게 유지
- 전달 의미론: at-least-once 기준. Core API retry 또는 Vector 재처리로 인한 중복 수신 가능성을 허용하고 손실 최소화를 우선한다.
- Payload versioning 규칙:
  - feedback 전용 schema를 사용한다. 기존 video_id 중심 async contract에 끼워 넣지 않는다.
  - 버전 식별자 필드는 `schema_version`(정수)이며 반드시 포함되어야 한다. 현재 지원 버전은 `1`이다.
  - 호환되지 않는 구조 변경은 새 정수 버전으로만 도입한다. minor/patch 구분은 두지 않는다.
  - Vector 라우팅 설정은 지원하지 않는 `schema_version` 값을 정상 raw log가 아니라 error_logs sink로 보낸다.
- 최소 포함 정보:
  - 식별/추적: `event_id`, `req_id`, `user_id`, `project_id`, `created_at`, `schema_version`, `trace_id` 또는 동등한 상관관계 키
  - 피드백 본문: `rating`
  - 검색 시점 문맥: `query_text`, `topk_ids`, `used_ids`, `active_model_version`, `active_index_name`, `response_snapshot_ref`
- 필드 의미는 `docs/system-design.md`의 `Feedback Event` 및 `SearchResponseSnapshot` 정의를 따른다.

#### 외부 연동 컴포넌트 계약
| 의존성 | 사용 목적 | 필요한 동작 / 가정 | 실패 영향 |
| --- | --- | --- | --- |
| Core API Server | 검증된 피드백 이벤트 생산 및 HTTP delivery | invalid req_id/타사용자/허용 시간 초과 이벤트는 delivery 전에 차단한다 | 잘못된 upstream 검증은 잘못된 raw log로 이어진다 |
| Object Storage | append-only raw log 보관 | 원본 이벤트를 overwrite 없이 보존 가능해야 한다 | 장기 장애 시 적재 실패, Core API delivery failure, Vector buffer 누적이 발생할 수 있다 |

### 2.2 데이터 계약

#### 소유 데이터 (이 컴포넌트가 SOT인 경우)
| 엔터티 / 테이블 | 목적 | 핵심 필드 / 불변조건 | 비고 |
| --- | --- | --- | --- |
| Feedback Event raw log (Object Storage) | 검색 응답 단위 원본 피드백 보존 | append-only, 원본 의미 보존 | FIP가 원본 로그 적재 책임을 가진다 |

논리 데이터 모델 (Feedback Event: `docs/system-design.md` 3.6 기반):
- `schema_version` (정수, 현재 `1`)
- `event_id`
- `user_id`
- `project_id`
- `req_id`
- `query_text`
- `rating`
- `topk_ids`
- `used_ids`
- `active_model_version`
- `active_index_name`
- `response_snapshot_ref`
- `created_at`

`event_id` 계약:
- `event_id`는 랜덤 전송 식별자가 아니라 논리적 feedback identity다.
- 같은 `user_id`, `req_id`, `rating` 조합의 feedback submission이 재시도되거나 중복 전달되면 같은 `event_id`를 사용한다.
- Core API는 `event_id`를 UUIDv5로 생성하며, canonical string은 `feedback:{user_id}:{req_id}:{rating}`를 사용한다.

#### 참조 데이터 (다른 SOT를 읽는 경우)
| SOT 소유자 | 엔터티 / 테이블 | 의존 필드 | 읽기 전용 가정 |
| --- | --- | --- | --- |
| Search Service / Metadata DB | `SearchResponseSnapshot` | `req_id`, `user_id`, `project_id`, `query_text`, `topk_chunk_ids`, `used_chunk_ids`, `active_model_version`, `active_index_name`, `served_vector_paths`, `project_serving_state`, `created_at`, `expires_at` | FIP는 직접 조회하지 않아도 되며, upstream validation이 이 스냅샷에 기반한다고 가정한다 |

### 2.3 상태 및 비즈니스 규칙
- 항상 유지되어야 하는 불변조건:
  - FIP는 검증된 feedback event만 처리 대상으로 간주한다.
  - raw log는 append-only이며, 적재 후 의미를 바꾸는 in-place 수정 경로를 두지 않는다.
  - 한 raw event는 검색 시점의 핵심 문맥(`project_id`, `query_text`, `topk_ids`, `used_ids`, 모델/인덱스 정보)을 함께 포함해야 한다.
- 이 파이프라인이 표현하는 처리 결과:
  - `validated event received -> raw log persisted`
  - `validated event received -> temporary retry pending` (Object Storage 일시 실패 또는 Vector 내부 retry 대기 시)
    - retry pending은 저장이 아직 끝나지 않아 Vector buffer/retry 또는 upstream HTTP retry 경로에 따라 다시 처리될 수 있는 상태를 의미한다.
- 거부되어야 하는 전이 / invalid condition:
  - 지원하지 않는 버전의 데이터를 무단으로 정상 저장하는 행위
  - raw log를 후처리 결과로 overwrite 하는 동작
- Idempotency / dedupe contract:
  - `event_id`는 feedback event 단위의 전역 중복 제거 키다.
  - FIP는 at-least-once 전달로 인한 같은 `event_id`의 재수신을 허용하며, raw log에는 중복 수신 사실을 보존할 수 있다.
  - curation dataset 생성 단계는 raw log를 읽을 때 `event_id` 기준으로 중복 제거를 수행한다.
  - 최종 curation dataset에는 동일한 `event_id`가 두 번 반영되지 않아야 한다.
- 멀티테넌트 / 인가 규칙:
  - 테넌시 검증 책임은 Core API에 있다.
  - FIP는 메시지 내 `user_id`와 `project_id`를 raw log에 그대로 보존하지만 별도 권한 판정을 수행하지 않는다.
- Persistence / retry boundary:
  - FIP는 feedback event가 raw log에 영속화된 뒤에만 정상 처리 완료로 간주한다.
  - Object Storage sink write는 bounded retry만 수행한다.
  - bounded retry 이후에도 영속화에 실패하면 해당 event는 폐기될 수 있다.
  - retry/backoff/buffer/timeout의 구체 값은 FIP PLAN에서 정한다.
- Vector config contract:
  - Vector 설정은 FIP의 HTTP source, schema routing, raw/error sink 분기, retry/buffer 동작을 정의하는 버전 관리 대상이다.

### 2.4 한계와 운영 제약
- 성능 / 지연 목표:
  - feedback delivery는 Core API 요청 경로와 연결되므로, per-event 저지연과 손실 최소화 사이의 균형이 필요하다.
  - Object Storage 적재 지연이 Core API feedback 응답 지연으로 전파될 수 있음을 운영 리스크로 본다.
- Throughput / rate / concurrency 한계:
  - concurrency/batch size 구체 수치는 FIP PLAN 문서에서 확정한다.
  - Vector HTTP ingress request rate, delivery latency, rejected request count, buffer depth, ingestion lag을 운영 지표로 본다.
- Payload / 파일 크기 / pagination 한계:
  - 이벤트 payload는 단일 검색 응답 문맥만 포함하며, 대용량 본문/원본 answer 전체 장기 저장은 범위 밖이다.
- Timeout / TTL / retry 제약:
  - SearchResponseSnapshot TTL과 허용 피드백 시간 창은 upstream/system 정책이다.
  - Object Storage 일시 장애는 Vector의 buffer/retry 설정과 Core API HTTP delivery failure handling으로 처리한다.
  - 미지원 version 또는 필수 필드 누락처럼 구조상 정상 저장할 수 없는 메시지는 Vector 라우팅 설정으로 `error_logs/` 경로에 보존한다. 이 경로는 운영 알림 트리거 대상이며, schema 호환 후 수동 재처리 입력으로 사용할 수 있다.
  - retry, buffer, timeout 관련 값은 FIP PLAN 문서에서 Vector와 Core API HTTP delivery 설정값으로 확정한다.
- 보안 / 개인정보 제약:
  - Vector HTTP ingress는 내부 네트워크 경계에만 노출되어야 하며, service-to-service 보호 장치를 가져야 한다.
  - raw log에는 사용자 식별자, 프로젝트 식별자, 질의 텍스트가 포함되므로 운영 접근은 최소 권한으로 제한해야 한다.
  - 원본 로그는 데이터셋 생성 입력으로 쓰이더라도 개인정보/민감질의 처리 정책을 우회하는 저장소가 되어서는 안 된다.

### 2.5 에러 계약
| 표면 | 조건 | 코드 / 상태 | 재시도 가능 | 비고 |
| --- | --- | --- | --- | --- |
| Vector HTTP ingress | HTTP endpoint unavailable, timeout, rejected request | feedback_delivery_failure | Y | Core API가 관측하고 retry 정책을 적용한다 |
| Vector transform | 미지원 `schema_version` | unsupported_schema_version | N | `error_logs/` sink에 원본 메시지를 보존한다 |
| Vector transform | 필수 정보 누락 또는 구조 불일치 | malformed_feedback_event | N | `error_logs/` sink에 원본 메시지를 보존한다 |
| Vector sink | Object Storage 일시 실패 | transient_persistence_failure | Y | Vector buffer/retry와 Core API delivery failure handling에 따라 다시 처리될 수 있어야 한다 |
| Vector source/sink | 동일 `event_id` 재수신 | duplicate_delivery | Y | at-least-once의 정상 범주 |

- FIP는 사용자 권한, `req_id` 소유자, 피드백 허용 시간 창 같은 의미 검증을 재수행하지 않는다. 이 검증은 Core API 책임이다.
- upstream 검증 누락으로 의미상 잘못된 데이터가 들어온 경우, FIP는 원본을 보존하고 이후 품질 점검이나 운영 분석에서 탐지할 수 있게 추적 정보를 남긴다.
- FIP의 HTTP ingress는 client-facing API가 아니므로 외부 사용자용 에러 응답 바디 형식은 본 SPEC 범위가 아니다.

---

## 3. 관측성과 운영

- 필수 log field:
  - `trace_id`(또는 동등한 상관관계 키), `event_id`, `req_id`, `user_id`, `project_id`, `schema_version`, `rating`, `result`
- 추적할 핵심 metric / alert:
  - Vector HTTP ingress request count, rejected request count, delivery latency
  - 원본 로그의 저장 성공 및 실패 건수
  - 통신 오류 등으로 인한 재시도 및 재전송 발생 횟수
  - 중복 여부를 나중에 확인할 수 있도록 raw log와 Vector 운영 로그에 남는 `event_id`
  - 데이터 수집 지연 시간 (FIP가 이벤트를 수신한 시점부터 최종 저장될 때까지 걸린 시간)
  - `error_logs/` error sink에 적재된 non-retryable 메시지 건수 (알림 트리거 대상)
  - 데이터 전송 파이프라인(Vector) 내의 임시 보관량(Buffer), 재시도 현황, 처리 완료 상태를 운영자가 모니터링할 수 있어야 한다.
- Trace / correlation 전파 규칙:
  - Core API가 생성/전달한 `trace_id`를 HTTP delivery, FIP 로그, object metadata(지원 시)에 동일하게 유지한다.
  - `event_id`는 이벤트 단위 상관관계 키, `req_id`는 검색 응답 단위 상관관계 키로 함께 남긴다.
- Service level 계약:
  - validated feedback event는 정상 상태에서 p95 5분 이내에 raw log로 적재되어야 한다.
  - raw log 적재 실패율은 월간 0.1% 이하를 목표로 한다.
  - Vector HTTP ingress 지연, rejected request, buffer depth, ingestion lag은 운영자가 확인할 수 있는 지표로 노출한다.

---

## 4. 인수 기준

### 4.1 반드시 통과해야 하는 시나리오
- [ ] Core API가 `SearchResponseSnapshot` 검증을 통과한 feedback event를 Vector HTTP ingress로 전달하면, Vector 파이프라인이 이를 읽어 Object Storage raw log에 `project_id`를 포함한 필요한 문맥 필드와 함께 남긴다.
- [ ] transport는 검색/영상 처리용 기존 video_id 중심 메시지 계약과 분리된 feedback 전용 schema를 사용한다.
- [ ] duplicate delivery가 발생해도 이벤트 손실 없이 운영자가 `event_id`/`trace_id` 기준으로 추적할 수 있으며, raw log는 중복 수신 사실을 보존한다.
- [ ] Object Storage 일시 장애 시 bounded retry 경로를 따른다.
- [ ] raw log는 `docs/system-design.md`의 `Feedback Event` 논리 필드를 보존한다.
- [ ] 미지원 version 식별자 또는 필수 필드 누락 메시지 수신 시 Vector 라우팅 설정에 따라 raw log가 아니라 `error_logs/` sink에 원본 메시지를 보존하며 운영 알림이 발생한다.

---

## 5. 참고 문서
- 관련 spec:
  - `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
- Diagram 또는 schema:
  - `docs/system-design.md` §2.6 Feedback 수집, §3.6 Feedback Event, §3.7 SearchResponseSnapshot
  - `docs/Diagram/sequence_diagram/SD_Mermaid.md`
