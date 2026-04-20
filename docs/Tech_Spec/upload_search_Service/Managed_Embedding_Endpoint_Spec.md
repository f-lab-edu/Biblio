# [Managed Embedding Endpoint] SPEC

**Meta**
* **Component ID:** managed-embedding-endpoint
* **SOT References:** `docs/system-design.md`, `docs/PRD.md`, `docs/ADR/ADR-001-ai-model-serving-strategy.md`, `docs/ADR/ADR-004-video-search-retrieval-strategy.md`, `docs/Tech_Spec/folder_structure.md`

---

## 1. Context & Scope

### 1.1 목적 (Purpose)

* **한 줄 요약:** Managed Embedding Endpoint는 Search Service와 Pipeline Worker로부터 텍스트 목록을 직접 수신하여, 동일 임베딩 모델 기준의 벡터로 변환해 반환하는 텍스트 임베딩 전용 내부 HTTP 서비스이다.
* **비즈니스 목표:** 검색 질의 임베딩과 청크 색인 임베딩을 동일 모델로 일관되게 생성하고, 배포 시점에 지정된 모델 파일을 기준으로 안정적으로 서빙하여 검색 품질과 운영 단순성을 확보한다.

### 1.2 요구 기술 스택 및 환경 변수 (Tech Stack & Configs)

* **구현 계약 기준:** 본 컴포넌트의 계약은 내부 HTTP API(`POST /embed`, `GET /health`), 텍스트 임베딩 추론 런타임, 로컬 모델 파일 로더, trace 전파 규칙으로 정의된다.
* **V1 Reference Runtime:** `FlagEmbedding` 기반 `BGEM3FlagModel`을 사용하여 `BAAI/bge-m3`의 dense embedding만 서빙한다. sparse retrieval 및 multi-vector 기능은 V1 범위에 포함하지 않는다.
* **Reference Implementation 방향:** `docs/Tech_Spec/folder_structure.md` 기준 `api/v1/router.py`, `api/v1/routers`, `schemas`, `services/inference_service.py`, `infra/model_loader.py`, `middlewares`, `core`, `main.py` 구조를 따른다.
* **필수 환경 설정:**
  * 내부 HTTP 서버 listen 포트
  * 현재 서빙할 모델 파일 경로 또는 디렉토리 경로
  * 로컬 모델 캐시 또는 임시 작업 경로
* **선택 환경 설정:**
  * 단일 `/embed` 요청 허용 최대 `texts` 개수 (미설정 시 기본값 `32`)
  * 단일 텍스트 최대 길이 (미설정 시 기본값 `4096` chars)
  * 단일 요청 허용 최대 payload size (미설정 시 기본값 `262144` bytes)
  * 최대 동시 처리량 (미설정 시 기본값 `1`)

> **Notes:** 현재 architecture 문서는 구체 모델 프레임워크(`sentence-transformers` 등)와 내부 호출 인증 방식(API key, mTLS 등)을 확정하지 않는다. 다만 본 스펙은 V1 구현 기준을 명확히 하기 위해 `BAAI/bge-m3` + `FlagEmbedding` dense-only 경로를 reference runtime으로 고정한다. 또한 이 스펙은 사용자 결정에 따라 Model Registry 자동 감지/자동 교체 대신, 배포 시 지정된 모델 파일을 기동 시 로드하는 단순한 운영 방식을 따른다. 기동 시간 제한, warm-up 수행 방식, 재시작 또는 배포 실패 판정은 서비스 계약에 포함하지 않으며 운영 환경 책임으로 둔다.

### 1.3 경계 (Boundaries)

* **In-Scope (책임 범위):**
  * 내부 HTTP API로 텍스트 임베딩 요청 수신
  * 요청 `texts` 순서를 보존한 임베딩 벡터 반환
  * Search Service 단일 질의 임베딩과 Pipeline Worker 배치 임베딩을 동일 wire contract로 처리
  * 배포 시점에 지정된 모델 파일 로드
  * `/health`를 통한 현재 서빙 가능 여부와 모델 버전 노출
  * `trace_id` 전파, 구조화 로깅

* **Out-of-Scope (제외 범위):**
  * 검색 랭킹, RRF, LLM 호출, RAG 응답 생성
  * STT, 청킹, Vision enrichment
  * Metadata DB, Vector Store, Feedback 저장
  * 사용자 JWT 검증 및 테넌시 판정
  * 이미지 또는 멀티모달 임베딩 생성
  * 기동 시간 제한, warm-up 오케스트레이션, 재시작/배포 실패 판정 같은 운영 환경 제어

### 1.4 상태 라이프사이클 기준 (Serving Readiness)

Managed Embedding Endpoint는 Video 같은 도메인 엔티티 상태를 관리하지 않는다. 다만 서빙 관점에서 현재 로드된 모델이 요청을 처리할 준비가 되었는지 여부를 내부적으로 유지한다.

* **서빙 가능 상태:** 현재 모델이 메모리에 로드되어 있고 `/embed` 요청을 처리할 수 있음
* **서빙 불가 상태:** 아직 모델이 로드되지 않았거나, 지정된 모델 파일을 읽지 못했거나, 모델 로드에 실패하여 서빙 준비가 완료되지 않음

---

## 2. Contracts (Interface & Data)

### 2.1 API / Message Endpoint

#### [HTTP API]

* **Auth / Tenancy:** 외부 사용자용 JWT/테넌시 개념은 적용하지 않는다. 호출자는 Search Service와 Pipeline Worker 같은 내부 서비스만 가정한다.
* **Trace Header:** `X-Trace-Id` 헤더가 있으면 이를 그대로 사용하고, 없으면 엔드포인트가 신규 UUID4를 생성한다. 확정된 값은 응답 헤더 `X-Trace-Id`, 구조화 로그, 에러 응답 `trace_id`에 동일하게 기록한다.
* **호출자 URL 계약:** Search Service와 Pipeline Worker의 `EMBEDDING_API_URL`은 본 컴포넌트의 `/embed` 경로를 가리킨다.

| HTTP Method | Endpoint (URI) | Request | Success Response | Notes |
| --- | --- | --- | --- | --- |
| **POST** | `/embed` | `{"texts": ["string", ...]}` | **200** `{"embeddings": [[float, ...], ...]}` | 입력 `texts`와 응답 `embeddings`는 길이와 순서가 1:1로 대응해야 한다 |
| **GET** | `/health` | Empty | **200** `{"status":"ok","model_version":"string"}` | 현재 서빙 가능한 모델이 로드된 상태에서만 200을 반환한다 |

* **스키마 제약 조건 (Pydantic 기준):**
  * `texts`: 최소 1개 이상의 문자열 목록
  * 각 항목은 빈 문자열일 수 없다
  * `texts` 개수가 서버 측 최대 허용치를 넘으면 `400 INVALID_ARGUMENT`
  * 개별 `text` 길이가 서버 측 최대 허용치를 넘으면 `400 INVALID_ARGUMENT`
  * 요청 본문 크기가 서버 측 최대 payload size를 넘으면 `413 PAYLOAD_TOO_LARGE`
  * 응답 `embeddings` 길이는 요청 `texts` 길이와 같아야 한다
  * 응답 `embeddings[i]`는 요청 `texts[i]`의 임베딩 벡터다
  * 모든 벡터는 동일 차원을 가져야 한다
  * Search Service는 V1에서 항상 정규화된 단일 질의만 보낸다
  * Pipeline Worker는 `EMBEDDING_BATCH_SIZE` 단위 배치로 `enriched_text` 목록을 보낸다
  * 입력 텍스트 정규화는 endpoint 책임이 아니다. caller는 정규화된 텍스트만 전송해야 하며, endpoint는 전달받은 문자열을 그대로 임베딩한다.

#### [Local Model Files]

* **ModelLoader 인터페이스:** 배포 시점에 지정된 모델 파일 또는 디렉토리를 로컬에서 읽고, 추론 런타임에 로드/언로드하는 추상 인터페이스를 정의한다.
* **역할:**
  * 구성된 모델 파일 위치 확인
  * 모델 파일 로드 및 추론 준비
  * 현재 서빙 중인 모델의 버전 문자열 노출
* **모델 버전 식별자:** Managed Embedding Endpoint의 `model_version` SOT는 현재 프로세스가 실제로 로드한 artifact path이다. artifact path는 고정 naming convention에 따라 version string을 포함해야 하며, endpoint는 이 실제 로드 경로를 기준으로 `/health`와 구조화 로그에 동일한 `model_version`을 노출해야 한다.

### 2.2 Data Access (Reads & Writes)

| Type | Store | Entity/Table | Key/Filter | Mutation/Action | Notes |
| --- | --- | --- | --- | --- | --- |
| Read | Local Filesystem | 모델 파일/디렉토리 | configured path | READ | 현재 배포에 포함된 모델 가중치/토크나이저/전처리 리소스 확인 |
| Read | Local Filesystem | 모델 파일/디렉토리 | configured path | LOAD | 추론 런타임에 모델 로드 |

> Managed Embedding Endpoint는 Metadata DB나 Vector Store에 직접 쓰지 않는다.

### 2.3 SLA & Constraints

* **텍스트 전용 추론:** MVP에서 텍스트 임베딩만 지원한다. 이미지나 멀티모달 입력은 지원하지 않는다.
* **입력 순서 보장:** 응답 `embeddings`는 요청 `texts` 순서를 그대로 보존해야 한다.
* **일관 모델 보장:** Search Service와 Pipeline Worker는 동일 서빙 모델을 호출해야 하며, 응답 벡터는 현재 ready 상태의 단일 모델 버전 기준으로 생성되어야 한다.
* **모델 교체 방식:** 본 컴포넌트는 최신 버전을 자동 감지하거나 hot reload하지 않는다. 모델 교체는 운영 배포 절차에서 모델 파일을 교체한 뒤, 새 프로세스 기동을 통해 반영한다.
* **모델 버전 노출:** 현재 서빙 모델을 식별하는 `model_version` 문자열은 `/health`와 구조화 로그에서 일관되게 보여야 한다.
* **배포 간 버전 skew:** 멀티 인스턴스 배포 중 짧은 구버전/신버전 공존은 허용한다. 단, 각 요청은 처리 시점의 단일 ready 모델 버전 기준으로 일관되게 처리되어야 한다.
* **startup readiness:** 프로세스는 시작 시점에 로컬에서 접근 가능한 모델 파일을 로드해야 하며, 모델 로드 성공 전까지 `/health`는 ready를 보고하면 안 된다. 실제 추론 가능 여부 검증은 서비스 내부 startup smoke inference가 아니라 배포/운영 절차의 별도 smoke test로 처리한다. 기동 시간 제한, warm-up 수행 방식, 재시작 또는 배포 실패 판정은 운영 환경 책임으로 둔다.
* **admission control:** endpoint는 최대 `texts` 개수, 최대 개별 `text` 길이, 최대 payload size, 최대 동시 처리량을 서버 측 guardrail로 강제한다.
* **guardrail 초과 처리:** 최대 동시 처리량을 초과한 경우 semaphore acquire timeout 없이 즉시 fail-fast로 `503 SERVICE_UNAVAILABLE`을 반환한다. V1에서는 endpoint 내부 대기열로 요청을 흡수하지 않으며, 재시도는 호출자 정책으로 처리한다.
* **guardrail 기본값:** 별도 설정이 없으면 `max_texts_per_request=32`, `max_text_length_chars=4096`, `max_payload_bytes=262144`, `max_concurrency=1`을 기본값으로 적용한다. MVP에서는 미설정을 무제한 허용으로 해석하지 않는다.
* **부분 성공 비지원:** 하나의 `/embed` 요청 안에서 일부 텍스트만 성공하는 부분 성공 응답은 제공하지 않는다. 요청 단위로 all-or-nothing 처리한다.

### 2.4 Error Contract & Messaging Semantics

| HTTP Status | Error Code | 발생 조건 | Retryable |
| --- | --- | --- | --- |
| 400 | `INVALID_ARGUMENT` | `texts` 누락, 빈 배열, 빈 문자열 포함, `texts` 개수 초과, 개별 `text` 길이 초과, JSON 스키마 불일치 | N |
| 413 | `PAYLOAD_TOO_LARGE` | 요청 본문 크기가 서버 측 최대 payload size 초과 | N |
| 500 | `INTERNAL_ERROR` | 코드 결함, 불변식 위반, 비정상 내부 상태 등 non-retryable 내부 오류 | N |
| 503 | `SERVICE_UNAVAILABLE` | ready 모델 미존재, 지정된 모델 파일 로드 실패, admission control에 의한 일시적 수용 불가, 일시적 추론 런타임 이상 | Y |

* **에러 응답 바디:** `{"code": "ERROR_CODE", "message": "설명 문자열", "trace_id": "UUID4"}`
* **에러 상세 노출 정책:** V1에서는 `index`, `reason_code` 같은 상세 failure 정보를 API 응답 필드로 보장하지 않는다. 호출자와 테스트 코드는 `code`, `message`, `trace_id`만 신뢰해야 하며, 상세 정보는 구조화된 내부 로그로만 수집한다.
* **비정상 응답 금지:** 성공 응답에서는 반드시 `embeddings`를 반환해야 하며, 빈 배열, 길이 불일치, 비숫자 벡터는 성공 응답으로 반환하면 안 된다.
* **헬스 체크 semantics:** `/health`는 ready 모델이 존재하면 `200 {"status":"ok","model_version":"..."}`, 그렇지 않으면 `503`을 반환한다. not-ready 상태에서는 `model_version` 노출을 보장하지 않는다.
* **liveness endpoint:** V1에서는 별도 liveness endpoint를 제공하지 않는다.

### 2.5 스키마 (DDL)

> Managed Embedding Endpoint는 RDB 스키마를 직접 소유하지 않는다.

---

## 3. Core Design & Logic

### 3.1 주요 흐름 (Sequence)

#### POST /embed

1. 요청 헤더에서 `X-Trace-Id`를 수신하거나 신규 생성한다.
2. 요청 바디 `texts`의 존재 여부, 길이, 빈 문자열 여부를 검증한다. 실패 시 `400`.
3. `texts` 개수, 개별 `text` 길이, payload size가 서버 측 guardrail을 넘는지 확인한다. 입력 검증 위반은 `400`, payload size 초과는 `413`.
4. 현재 ready 모델이 존재하는지 확인한다. 없으면 `503`.
5. 최대 동시 처리량을 초과했는지 확인한다. admission control은 semaphore acquire timeout 없이 즉시 fail-fast로 동작하며, 슬롯을 즉시 확보하지 못하면 `503`을 반환한다.
6. 요청 `texts`를 현재 ready 모델에 전달하여 임베딩 벡터를 생성한다.
7. 응답 `embeddings`가 입력 `texts`와 길이/순서/형식이 1:1로 대응하는지 내부적으로 검증한다. 실패 시 `503`.
8. `{"embeddings": [[float, ...], ...]}`를 반환하고, 응답 헤더에 `X-Trace-Id`를 echo한다. 성공 응답 본문에는 `model_version`을 포함하지 않는다.

#### GET /health

1. 현재 ready 모델이 메모리에 로드되어 있는지 확인한다.
2. ready 모델이 있으면 `200 {"status":"ok","model_version":"..."}`를 반환한다.
3. ready 모델이 없으면 `503 SERVICE_UNAVAILABLE`을 반환한다.

#### 모델 파일 로드

1. 프로세스 시작 시 구성된 모델 파일 또는 디렉토리 경로를 읽는다.
2. 실제 로드할 artifact path를 기준으로 `model_version` 문자열을 확보한다.
3. 모델 파일을 메모리에 로드한다.
4. 로드에 성공하면 현재 ready 모델로 승격한다.
5. 어느 단계든 실패하면 ready 상태에 진입하지 않고 `/embed`, `/health`는 `503`을 반환한다.
6. 실제 추론 가능 여부 검증은 서비스 내부 startup 단계가 아니라 배포/운영 절차의 별도 smoke request로 확인한다.

### 3.2 상태 전이 (Serving Lifecycle)

| From | To | Actor | Trigger | Guard | Side Effects |
| --- | --- | --- | --- | --- | --- |
| 모델 없음 | Ready | Managed Embedding Endpoint | 프로세스 시작 시 모델 로드 성공 | 로컬 파일 로드 및 `model_version` 확보 성공 | `/embed` 서빙 가능 |
| 모델 없음 | 서빙 불가 | Managed Embedding Endpoint | 프로세스 시작 시 모델 로드 실패 | ready 모델 부재 또는 모델 로드 실패 | `/embed`, `/health`는 503 |

### 3.3 멱등성 및 복구 (Resilience)

* **요청 멱등성:** `/embed`는 읽기 전용 추론 요청이며, 동일 입력 반복 호출은 부작용이 없다.
* **순서 보장:** 요청 `texts`와 응답 `embeddings`의 인덱스는 항상 1:1 대응해야 한다.
* **모델 파일 로드 복구:** 지정된 모델 파일을 읽거나 로드하지 못하면 현재 프로세스는 ready 상태에 진입하지 않는다. 복구는 올바른 모델 파일을 반영한 뒤 프로세스를 다시 기동하는 운영 절차로 처리한다.
* **부분 성공 금지:** 일부 텍스트만 성공한 결과는 반환하지 않는다. 추론 중 예외가 발생하면 요청 전체를 실패 처리한다.

### 3.4 Data Consistency & Orphan Prevention

* **모델 일관성:** 하나의 `/embed` 요청은 단일 ready 모델 버전 기준으로 처리되어야 하며, 요청 중간에 모델 파일이 교체되더라도 현재 프로세스가 로드한 동일 모델 기준으로 결과를 반환해야 한다.
* **파일 교체 반영 시점:** 모델 파일 변경은 자동 반영되지 않으며, 다음 프로세스 기동부터 새 모델이 적용된다.
* **추적 정보 보존:** `model_version`은 요청 단위 구조화 로그에 `trace_id`와 함께 기록되어야 한다. `/embed` 응답 본문에는 포함하지 않는다.
* **다운스트림 일관성:** Search Service와 Pipeline Worker는 응답 벡터를 현재 모델 버전 결과로 간주하므로, 엔드포인트는 응답 순서 불일치나 부분 결과를 반환하면 안 된다.

---

## 4. Logging & Trace

* **Logging:**
  * 모든 요청/응답 로그에 `trace_id`, `path`, `text_count`, `payload_size`, 처리 시간, 결과 상태, `model_version`을 포함한다.
  * 모델 초기 로드 로그에 `model_version`, 결과(success/failure)를 포함한다.
  * `/embed` 성공 로그에는 요청 본문 원문을 남기지 않고 `text_count`, `text_length` 요약, `payload_size`만 기록한다.
  * 에러 상세는 API 응답이 아니라 구조화 로그로 남긴다. 예를 들어 `error_code`, `reason_code`, `failed_index`, `text_count`, `payload_size`, `model_version`, `trace_id`를 함께 기록할 수 있다.
  * 원문 `texts` 수집은 기본 비활성화한다. 운영자 승인 하의 임시 추적 모드에서만 허용하며, 가능하면 특정 `trace_id` 범위로 한정하고 TTL, 접근 통제, 감사 로그를 동반해야 한다.

* **Trace Propagation:** `X-Trace-Id`를 수신, 구조화 로그, 성공 응답, 에러 응답 전 구간에 동일하게 전파한다.

---

## 5. Acceptance Criteria (DoD)

### 5.1 시나리오 검증

#### POST /embed

**정상**
* [ ] 유효한 `texts=["query"]` 요청 → `200` + 길이 1의 `embeddings` 반환
* [ ] 유효한 배치 `texts=[...]` 요청 → `200` + 동일 길이의 `embeddings` 반환
* [ ] 응답 `embeddings[i]`가 요청 `texts[i]`와 같은 순서를 유지함을 확인
* [ ] 성공 응답 헤더에 `X-Trace-Id`가 echo됨을 확인
* [ ] 성공 응답 본문에 `model_version`이 포함되지 않음을 확인

**예외**
* [ ] `texts` 누락 → `400`
* [ ] `texts=[]` → `400`
* [ ] 빈 문자열 포함 → `400`
* [ ] `texts` 개수 초과 → `400 INVALID_ARGUMENT`
* [ ] 개별 `text` 길이 초과 → `400 INVALID_ARGUMENT`
* [ ] payload size 초과 → `413 PAYLOAD_TOO_LARGE`
* [ ] 최대 동시 처리량 초과 시 admission control → `503 SERVICE_UNAVAILABLE`
* [ ] ready 모델이 없는 상태에서 `/embed` 호출 → `503`
* [ ] 일시적 추론 런타임 이상 → `503`
* [ ] non-retryable 내부 오류(코드 결함, 불변식 위반, 비정상 내부 상태) → `500`
* [ ] 비정상 내부 결과(빈 `embeddings`, 길이 불일치, 비숫자 벡터) 감지 → 성공 응답 대신 `503`
* [ ] 에러 응답 바디는 `code/message/trace_id` 고정 shape만 보장하고, `index/reason_code`를 응답 필드로 기대하지 않음을 확인

#### GET /health

**정상**
* [ ] ready 모델이 로드된 상태 → `200 {"status":"ok","model_version":"..."}`

**예외**
* [ ] ready 모델이 없는 상태 → `503`

#### 모델 파일 교체 반영

**정상**
* [ ] 프로세스 시작 시 지정된 모델 파일이 정상 로드되면 ready 상태로 진입함을 확인
* [ ] 현재 서빙 모델의 `model_version`이 health/log에 동일하게 노출함을 확인
* [ ] 새 모델 교체 완료 이후의 모든 신규 요청이 새 모델을 사용함을 확인
* [ ] 배포/운영 smoke request로 실제 `/embed` 추론 성공 여부를 별도로 검증함을 확인

**예외**
* [ ] 지정된 모델 파일이 없거나 손상된 경우 ready 상태에 진입하지 못하고 `/embed`, `/health` 모두 `503`

### 5.2 검증을 위한 테스팅 전략 (Testing Strategy)

에이전트는 아래 가이드라인을 만족하는 자동화 테스트를 작성해야 한다.
* 테스트 프레임워크는 `pytest`, `pytest-asyncio`, `httpx`를 사용한다.
* **외부 의존성 격리 전략:**
  * 로컬 모델 파일 로더 → 더미 loader 또는 `AsyncMock`으로 대체하여 파일 존재/부재/로드 실패 시나리오를 재현한다.
  * 임베딩 모델 추론 런타임 → 더미 모델 또는 스텁으로 대체하여 순서 보장, 차원 일관성, 예외 케이스를 검증한다.
* `/embed` 테스트는 단일 질의와 배치 요청 모두 포함해야 한다.
* 서버 측 guardrail 테스트는 `texts` 개수 초과, 개별 길이 초과, payload size 초과, 동시 처리량 초과를 모두 포함해야 한다.
* guardrail 기본값 테스트는 관련 설정이 없을 때 기본값(`32`, `4096`, `262144`, `1`)이 실제로 적용되는지 확인해야 한다.
* 모델 파일 교체 테스트는 초기 로드 성공, 파일 교체 후 재기동 반영, 파일 로드 실패를 모두 포함해야 한다.
* readiness 테스트는 초기 로드 성공, 모델 로드 실패, not-ready 상태에서의 `/health` 응답을 포함해야 한다.
* 운영 smoke test는 배포 이후 실제 `/embed` 요청 1건으로 추론 성공 여부를 검증하는 절차를 포함해야 한다.
* Trace propagation 테스트는 `X-Trace-Id` 수신/생성/echo를 포함해야 한다.

### 5.3 산출물 (Artifacts)

폴더 구조는 `docs/Tech_Spec/folder_structure.md`를 참조한다.

* [ ] HTTP 라우터 — `POST /embed`, `GET /health`
* [ ] Pydantic DTO — `EmbedRequest`, `EmbedResponse`, `HealthResponse`
* [ ] 추론 서비스 — 텍스트 임베딩 생성 및 응답 순서 보장
* [ ] 모델 로더 — 지정된 모델 파일 로드 및 버전 노출
* [ ] Trace/로깅 미들웨어
* [ ] 단위 테스트 / 통합 테스트
