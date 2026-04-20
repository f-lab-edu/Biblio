# [Observability and Ops Standards] Common Spec

**메타 정보 (Meta)**
- Component ID: `observability-and-ops-standards`
- SOT: `docs/system-design.md`
- Related docs:
  - `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Pipeline_Worker_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Managed_Embedding_Endpoint_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Feedback_Ingestion_Pipeline_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/ML_Pipeline_Execution_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Model_Release_and_Reindex_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Admin_Control_Plane_Spec.md`
- Status: Draft

---

## 1. 목적과 범위 (Purpose and Scope)

### 1.1 한 줄 요약
- 이 문서는 Biblio 전체 backend component spec이 공통으로 따라야 하는 관측성과 운영 기준을 정의한다.

### 1.2 책임 경계
- In scope:
  - Biblio backend 컴포넌트 전반의 공통 logging, metrics, trace, alert, reconciliation 기준
  - `docs/Tech_Spec` 하위 spec들이 `Observability and Operations` 또는 동등한 섹션에 최소한으로 남겨야 할 내용
  - HTTP 요청, broker message, 내부 hand off, 외부 adapter 호출을 가로지르는 상관관계 기준
  - shared SOT와 운영 신호의 관계
- Out of scope:
  - 특정 컴포넌트 전용 메트릭 전체 목록
  - 대시보드 화면 구조, 패널 배치, 운영 툴 선택
  - 숫자 임계값 튜닝
  - logger, metrics SDK, exporter의 구현체 선택
  - 서비스별 상세 runbook

### 1.3 적용 대상
- 이 문서는 아래 계열 spec 전체에 적용된다.
  - upload and search service 계열
  - feedback loop 계열
  - admin ops 계열

- `docs/Tech_Spec/pending/` 아래 문서는 채택 전 검토 대상이므로 이 문서의 직접 적용 대상으로 보지 않는다.

- 컴포넌트별 책임과 도메인 상태는 각 spec이 정의한다.
- 관측성과 운영의 공통 정책은 이 문서가 정의한다.
- 공통 정책과 개별 spec이 충돌하면 `docs/system-design.md`를 먼저 따르고, 그 다음 이 문서를 따른다.

---

## 2. 공통 원칙 (Shared Principles)

- 운영 판단의 기준 상태는 shared SOT에 기록된 도메인 상태다.
- 로그와 메트릭은 shared SOT를 대체하지 않는다. 운영자가 상태를 해석하고 다음 액션을 정하는 보조 수단이다.
- 하나의 요청, 메시지, 내부 hand off, 후속 adapter 호출은 가능한 한 하나의 `trace_id` 체인으로 연결 가능해야 한다.
- 관측성 신호는 "무슨 일이 있었는가"보다 "지금 개입이 필요한가"를 판단할 수 있게 남겨야 한다.
- 같은 흐름을 따라가야 하는 신호는 같은 상관관계 키를 공유해야 한다.
- 원문 사용자 데이터, 대용량 payload, 평가 상세 artifact 본문은 기본 운영 신호에 포함하지 않는다.
- reconciliation과 cleanup는 해당 상태를 소유하는 컴포넌트 책임으로 정의한다.

---

## 3. Shared SOT와 운영 신호의 관계 (SOT and Operational Signals)

- 사용자 노출 정합성과 최종 상태 판단은 shared SOT를 기준으로 한다.
- 파생 저장소, queue, cache, vector index, 임시 산출물은 운영 해석의 보조 대상이지 최종 상태의 기준이 아니다.
- 운영 신호는 아래 질문에 답할 수 있어야 한다.
  - 현재 shared SOT 기준으로 정상 상태인가
  - 파생 경로가 shared SOT를 따라잡지 못하고 있는가
  - 중단되거나 정체된 실행이 있는가
  - 사람이 개입해야 하는 실패인가

- Biblio에서는 특히 아래 SOT가 운영 기준 저장소 역할을 가진다.
  - Metadata DB의 `Video`
  - Metadata DB의 `SearchResponseSnapshot`
  - Metadata DB의 `MLPipelineRun`
  - Metadata DB의 `ModelEvaluation`
  - Metadata DB의 `ModelRelease`

---

## 4. 공통 로그 기준 (Shared Logging Standards)

### 4.1 공통 필수 원칙
- 로그는 구조화된 key-value 형태를 전제로 한다.
- 성공, 실패, 재시도, 상태 전이는 서로 다른 사건으로 식별 가능해야 한다.
- 상태 전이가 일어난 경우, 어떤 리소스가 어떤 이유로 어떤 상태로 움직였는지 추적 가능해야 한다.

### 4.2 공통 후보 필드
- 아래 필드는 해당 맥락이 존재하는 한 공통 후보 필드로 취급한다.
  - `trace_id`
  - `component`
  - `operation` 또는 `action`
  - `result`
  - `attempt`
  - `error_code`
  - 도메인 리소스 식별자

- 도메인 리소스 식별자는 컴포넌트에 맞게 선택한다.
  - `user_id`
  - `video_id`
  - `req_id`
  - `event_id`
  - `ml_pipeline_run_id`
  - `evaluation_id`
  - `active_model_version`
  - `candidate_model_version`
  - `candidate_index_name`
  - `operator_user_id`

- 상태 전이 로그는 가능하면 아래 정보를 함께 남긴다.
  - 전이 대상 리소스 식별자
  - 전이 전 상태
  - 전이 후 상태
  - 전이 이유 또는 직접 원인

### 4.3 기본 운영 로그에 포함하지 않는 정보
- 원문 `query_text`
- raw transcript 본문
- 임베딩 요청 원문 `texts`
- 평가 상세 artifact 본문
- 대용량 payload 전체
- 민감 원문 로그

- 이런 정보가 필요하면 각 spec이 제한된 진단 경로와 접근 제약을 따로 정의해야 한다.

---

## 5. 공통 Trace 및 상관관계 기준 (Shared Trace and Correlation Rules)

### 5.1 HTTP 경로
- HTTP 표면이 있는 컴포넌트는 수신 trace 값을 우선 사용하고, 없거나 유효하지 않으면 새 UUID4를 생성한다.
- Biblio HTTP spec의 공통 trace header 이름은 `X-Trace-Id`로 맞춘다.
- 확정된 값은 구조화 로그, 성공 응답, 에러 응답, 하위 HTTP 호출에 같은 의미로 유지한다.
- 에러 응답이 `trace_id` 필드를 포함하는 계약을 가지면, 그 값은 같은 요청의 `X-Trace-Id`와 일치해야 한다.

### 5.2 비동기 메시지
- broker message는 발행자와 소비자가 같은 `trace_id`를 유지해야 한다.
- 재전달과 재시도는 같은 상관관계 체인을 유지해야 한다.
- `attempt`는 재시도 횟수 추적에 사용하고, `trace_id`를 대체하지 않는다.
- payload에 세부 문맥을 싣지 않는 경우에도 `trace_id`와 핵심 식별자는 유지해야 한다.

### 5.3 내부 hand off와 외부 adapter 호출
- 내부 직접 호출 hand off도 broker를 쓰지 않더라도 같은 `trace_id`를 이어받아야 한다.
- 외부 adapter 또는 외부 AI provider 호출도 가능하면 같은 `trace_id`를 전달하거나, 최소한 로컬 구조화 로그에서 같은 `trace_id`를 유지해야 한다.
- shared SOT를 다시 읽는 단계는 hand off payload의 메모리 문맥보다 SOT 식별자와 `trace_id`를 우선 신뢰한다.

### 5.4 보조 상관관계 키
- `trace_id` 외에도 아래 식별자는 보조 상관관계 키로 유지한다.
  - `req_id`
  - `event_id`
  - `video_id`
  - `ml_pipeline_run_id`
  - `evaluation_id`

---

## 6. 공통 메트릭 기준 (Shared Metrics Standards)

### 6.1 메트릭이 우선 답해야 하는 질문
- 요청 또는 트리거가 얼마나 들어오고 있는가
- 정상 완료와 실패가 어떤 비율로 발생하는가
- 처리 시간이 어디에서 길어지는가
- backlog나 대기 실행이 쌓이고 있는가
- 장시간 진전이 없는 리소스가 존재하는가
- reconciliation이나 cleanup가 반복적으로 필요한가

### 6.2 공통 분류
- 각 spec은 아래 분류 중 자기 컴포넌트에 해당하는 것만 선택해 핵심 메트릭을 남긴다.
  - request 또는 trigger count
  - success 또는 failure count
  - latency 또는 stage duration
  - backlog 또는 queue depth
  - active 또는 in-flight state
  - stuck 또는 timeout candidate
  - reconciliation 또는 cleanup action count

### 6.3 표현 원칙
- 메트릭은 운영자가 액션을 결정할 수 있는 수준으로 남긴다.
- high-cardinality 값은 label로 남용하지 않는다.
- 개별 `trace_id`, `req_id`, `video_id`는 메트릭 label보다 로그와 trace에서 추적한다.
- 메트릭은 집계 판단에 쓰고, 개별 사건 복원은 로그와 trace에 맡긴다.

---

## 7. 공통 Alert 원칙 (Shared Alerting Principles)

- alert는 운영자 개입 필요성이 있는 조건을 우선 다룬다.
- 단건 실패보다 장시간 정체, 누적 backlog, 반복되는 발행 실패, 상태 불일치가 더 우선이다.
- 정상 재시도 범위 안의 단건 실패는 바로 alert로 승격하지 않는다.
- alert 조건은 가능하면 shared SOT 기준 상태와 함께 해석 가능해야 한다.
- 숫자 임계값, 기간, escalation 경로는 각 component plan 또는 운영 문서에서 구체화한다.

운영 개입 후보의 예:
- queue backlog 장시간 누적
- 장시간 진전이 없는 `RUNNING` 실행
- 회복되지 않는 candidate reindex 정체
- 반복되는 broker publish 실패
- rollback, delete, retrigger 같은 운영 액션의 precondition conflict 급증
- shared SOT와 실제 처리 진행의 장시간 불일치

---

## 8. 공통 Reconciliation 및 Cleanup 원칙 (Shared Reconciliation and Cleanup Principles)

- reconciliation은 중단되거나 지연된 상태를 식별하고, 다시 이어받을 수 있는지 판단하는 책임이다.
- cleanup는 운영적으로 더 이상 필요 없는 중간 상태와 부산물을 정리하는 책임이다.
- 두 책임은 해당 상태를 소유하는 컴포넌트가 정의한다.
- shared SOT에 남아 있어야 하는 이력과 진단 증거는 임의로 삭제하지 않는다.
- 다른 단계가 이어서 읽어야 하는 상태는 cleanup보다 먼저 사라지면 안 된다.
- shared SOT를 다시 읽어 복구 가능한 흐름은, 과도한 hand off payload보다 최소 식별자 중심으로 설계한다.

---

## 9. 개별 Spec이 여전히 적어야 하는 것 (What Each Component Spec Must Still Declare)

- 공통 문서가 있어도 각 spec의 `Observability and Operations` 또는 동등한 섹션은 삭제하지 않는다.
- 각 spec은 최소한 아래 네 가지를 남긴다.
  - 그 컴포넌트에서만 중요한 추가 로그 필드
  - 그 컴포넌트에서만 중요한 핵심 메트릭과 alert 후보
  - 공통 trace 규칙에 대한 컴포넌트별 nuance
  - 그 컴포넌트가 직접 책임지는 reconciliation 또는 cleanup 항목

예:
- Core API Server
  - `user_id`, `video_id`, `message_type`, 큐 발행 실패, cursor decode 실패
- Search Service
  - `req_id`, retrieval path, SOT gate 이후 final-empty, LLM/Embedding downstream 실패
- Pipeline Worker
  - `video_id`, `failed_stage`, 단계별 처리 시간, resume/skip 판단 근거
- Managed Embedding Endpoint
  - `path`, `text_count`, `payload_size`, `model_version`, admission control 실패
- Feedback Ingestion Pipeline
  - `event_id`, `req_id`, raw log 적재 성공/실패, error sink 적재 건수
- ML Pipeline Execution
  - `ml_pipeline_run_id`, `dataset_version`, `evaluation_id`, stuck run 식별
- Model Release and Reindex
  - `release_status`, `candidate_index_name`, cutover backlog, rollback restore 상태
- Admin Control Plane
  - `operator_user_id`, `admin_action`, `target_id`, precondition conflict와 publish 실패

---

## 10. 하위 문서 적용 규칙 (How Child Specs Should Use This Document)

- 공통 기준은 이 문서에 한 번만 정의한다.
- 개별 spec은 이 문서를 반복 설명하지 않는다.
- 개별 spec은 공통 기준을 전제로 한 자기 책임만 적는다.
- 개별 spec이 예외를 둬야 하면 그 예외를 명시적으로 적고, 공통 기준을 그대로 따르지 않는 이유를 함께 적는다.
- 구현 plan은 이 공통 기준을 더 구체화할 수는 있지만 의미를 바꾸면 안 된다.

---

## 11. 인수 기준 (Acceptance Criteria)

- [ ] Biblio 전체 backend component spec이 공통 `trace_id` 상관관계 기준을 공유한다.
- [ ] upload/search service와 feedback loop/admin ops spec이 generic observability 설명을 반복하지 않고, 컴포넌트별 차이만 남길 수 있다.
- [ ] shared SOT 기준 상태 판단과 로그/메트릭의 보조 역할 구분이 문서상 명확하다.
- [ ] HTTP 표면, broker message, 내부 hand off, 외부 adapter 호출 사이의 상관관계 기준이 한 문서에서 일관되게 설명된다.
- [ ] 하위 spec 작성자가 무엇을 공통 문서로 올리고 무엇을 각 spec에 남겨야 하는지 판단할 수 있다.

---
