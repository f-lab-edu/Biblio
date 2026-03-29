# [Search Service] SPEC

**Meta**
* **Component ID:** search-service
* **SOT References:** `docs/system-design.md`, `docs/PRD.md`
---

## 1. Context & Scope

### 1.1 목적 (Purpose)

* **한 줄 요약:** Search Service는 사용자의 자연어 질의를 수신하여 하이브리드 검색 파이프라인(임베딩 변환 → FTS 키워드 검색 + ANN 벡터 검색 → RRF 병합 → SOT 서빙 검증 → LLM 답변 생성)을 즉시 실행하고, 요청 단위 `req_id`와 타임스탬프가 포함된 검색 결과를 반환하는 검색 전담 서비스이다. 검색은 요청자 소유 영상이 1개 이상 존재하고 그 전체가 `READY`일 때만 시작되며, 영상이 0개면 `409 NO_VIDEOS_UPLOADED`, 1개 이상이지만 하나라도 미준비 상태면 `409 SEARCH_NOT_READY`를 반환한다.
* **비즈니스 목표:** PRD 목표인 "사용자의 자연어 질의에 대한 시맨틱 검색 결과를 5초 이내에 반환"하며, 최종 응답에 포함된 청크 타임스탬프를 통해 사용자가 해당 지점부터 영상을 검증하고 재생할 수 있도록 지원한다.

### 1.2 요구 기술 스택 및 환경 변수 (Tech Stack & Configs)

* **구현 계약 기준:** 본 컴포넌트의 계약은 비동기 HTTP API, DB 접근 계층, Embedding HTTP 클라이언트, Search Service 내부 LLM 인터페이스로 정의된다.
* **Reference Implementation:** Python 3.11+, FastAPI (Async), SQLAlchemy 2.0 Async, `httpx.AsyncClient`, `google-genai` SDK (Vertex AI backend)
* **필수 환경 변수:**
  * `JWT_SECRET_KEY` — JWT 서명 검증 키
  * `DATABASE_URL` — Metadata DB 연결 문자열
  * `EMBEDDING_API_URL` — Managed Embedding Endpoint 주소
* **선택 환경 변수 (초기 기본값):**
  * `LLM_PROVIDER=gemini` — `gemini | mock`
  * `GCP_PROJECT_ID` — `LLM_PROVIDER=gemini`일 때 필수
  * `GCP_LOCATION=us-central1` — `LLM_PROVIDER=gemini`일 때 사용할 Vertex AI 리전
  * `GEMINI_MODEL_NAME` — `LLM_PROVIDER=gemini`일 때 필수
  * `SEARCH_TOP_K=20` — FTS/ANN 각 후보 조회 수
  * `FINAL_TOP_K=5` — RRF 병합 후 SOT 게이트에 전달할 최종 후보 수
  * `RRF_K=60` — RRF 상수
  * `EMBEDDING_TIMEOUT_SEC=2`
  * `EMBEDDING_MAX_RETRIES=1`
  * `LLM_TIMEOUT_SEC=3`
  * `LLM_MAX_RETRIES=1`

> **Gemini 인증:** `LLM_PROVIDER=gemini`일 때 Search Service는 API Key가 아니라 GCP ADC(Application Default Credentials) 또는 서비스 계정 자격 증명을 사용해 Vertex AI에 인증한다.

> **Notes:** Search Service는 메시지 브로커를 구독하지 않는다.

### 1.3 경계 (Boundaries)

* **In-Scope (책임 범위):**
  * JWT 검증 및 `requester_user_id` 추출
  * 질의 정규화 및 검색 요청 검증
  * `trace_id` 확정 및 하위 호출 전파 (`X-Trace-Id`)
  * 요청 단위 `req_id` 생성
  * 요청자 소유 영상의 존재 여부와 전체 `READY` 상태를 확인하는 all-or-nothing gate
  * 질의 텍스트를 Managed Embedding Endpoint에 직접 전송하여 벡터 변환
  * Metadata DB(FTS)에서 키워드 후보 조회
  * Vector Store(ANN)에서 벡터 후보 조회
  * RRF 기반 후보 병합
  * Metadata DB(SOT)를 서빙 게이트로 활용한 최종 서빙 검증
  * Search Service 내부 `LLMAdapter` 구현체를 통한 최종 답변 생성 및 structured `used_refs` 파싱
  * 검색 결과(`req_id`, `answer`, `chunks`) 반환

* **Out-of-Scope (제외 범위):**
  * 영상 업로드, 메타데이터 관리, 처리 상태 조회
  * 피드백 저장 및 영구 보관
  * 청크 임베딩 생성 및 Vector Store 적재
  * 번역, query rewrite, 다국어 질의 보정
  * 카테고리별 프롬프트 분기, 카테고리 가중치 조정, 랭킹 보정
  * Managed Embedding Endpoint 내부 추론 로직
  * STT, 청킹, 파이프라인 오케스트레이션

### 1.4 상태 라이프사이클 기준 (SOT Alignment)

Search Service는 Video 상태를 직접 변경하지 않는다. 검색 범위를 제한하는 상태 기준만 참조한다.

* 검색 가능 상태: 요청자 소유 검색 범위에 영상이 1개 이상 존재하고 그 전체가 `Video.status = READY`
* 검색 제외 상태: `PENDING`, `UPLOADED`, `PROCESSING`, `FAILED`, `DELETING`

---

## 2. Contracts (Interface & Data)

### 2.1 API / Message Endpoint

#### [HTTP API]

* **Auth / Tenancy:** 모든 요청에는 JWT Authorization 헤더가 필수이다. Search Service는 JWT를 직접 검증하고 `requester_user_id`를 추출한다.
* **Trace Header:** `X-Trace-Id` 요청 헤더가 있으면 우선 사용하되 UUID 형식이 아니면 무시하고 새 UUID4를 생성한다. 헤더가 없을 때도 새 UUID4를 생성한다. 확정된 값은 성공/에러 응답 헤더 `X-Trace-Id`, 에러 응답 바디 `trace_id`, Embedding/LLM 하위 호출에 동일하게 전파한다.

| HTTP Method | Endpoint (URI) | Request | Success Response | Notes |
| --- | --- | --- | --- | --- |
| **POST** | `/api/v1/search` | `{"query": "자연어 질의"}` | **200** `{"req_id","answer","chunks"}` | 하이브리드 검색 + LLM 답변 생성. 검색 범위는 항상 요청자 소유의 전체 영상이며, 영상이 0개면 `409 NO_VIDEOS_UPLOADED`, 1개 이상이지만 하나라도 `READY`가 아니면 `409 SEARCH_NOT_READY`를 반환한다. 모든 영상이 `READY`일 때만 검색을 허용한다. 성공 시 `X-Trace-Id` 응답 헤더를 echo한다 |

* **질의 정규화 및 검증:**
  * `query`는 정규화 후에 검증한다.
  * 정규화 규칙: 앞뒤 공백 제거, 연속 공백 축약, ASCII 영문 소문자화, 제어문자 제거
  * 정규화 후 길이 제약: 최소 2자, 최대 1,000자
  * 정규화 후 길이가 2자 미만이면 `400 INVALID_ARGUMENT`

* **검색 범위 규칙:**
  * 검색 범위는 항상 `requester_user_id`가 소유한 전체 영상이다.
  * 요청 바디에는 `scope`를 두지 않는다.
  * Search Service는 특정 `video_id` 집합 또는 카테고리로 검색 범위를 축소하는 기능을 지원하지 않는다.
  * 검색 범위 내 영상이 0개면 검색을 시작하지 않고 명시적 오류(`409 NO_VIDEOS_UPLOADED`)를 반환한다.
  * 검색 범위 내 영상이 1개 이상이며 하나라도 `READY`가 아니면 검색을 시작하지 않고 명시적 오류(`409 SEARCH_NOT_READY`)를 반환한다.
  * 검색 실행이 시작된 이후의 실질 검색 대상은 전체 `READY` 영상이며, 이 정책은 “사용자 전체 영상이 준비 완료된 이후에만 검색 허용”을 보장하기 위한 gate 역할을 한다.

* **응답 스키마:**
  ```json
  {
    "req_id": "UUID4",
    "answer": "LLM이 생성한 자연어 답변 [1]",
    "chunks": [
      {
        "ref": 1,
        "chunk_id": "UUID4",
        "video_id": "UUID4",
        "title": "영상 제목",
        "start_ms": 12000,
        "end_ms": 24000,
        "text": "청크 텍스트 원문",
        "used": true
      }
    ]
  }
  ```

* **응답 필드 상세:**
  * `req_id`: 요청 단위로 생성되는 UUID4. Search Service는 이를 영속 저장하지 않으며, 피드백 귀속 키로 클라이언트와 Core API에 전달한다. 별도 검색 응답 저장소가 없으므로 이후 Core API는 이를 서버-사이드 조회키가 아니라 상관관계용 opaque ID로 취급한다.
  * `answer`: 사용자에게 노출되는 자연어 답변 본문이다. Search Service는 LLM raw output에서 `<ANSWER>...</ANSWER>` 블록만 추출해 이 필드에 넣으며, `<USED_REFS_JSON>` metadata 블록은 절대 포함하지 않는다.
  * `chunks`: SOT 서빙 게이트를 통과해 실제 최종 응답 생성에 사용된 canonical 컨텍스트 배열이다. 배열 순서는 `ref ASC`이며, Search Service는 별도의 `topk_ids`/`citations`/`used_ids` 배열을 반환하지 않는다. Empty Result는 retrieval이 실제로 시작된 이후 최종 컨텍스트가 0개인 경우에만 적용된다.
  * `chunks[].ref`: 최종 컨텍스트에 대해 LLM 프롬프트에 사용할 내부 순서 기준으로 `1..N`을 부여한 요청 단위 citation 번호이다. 답변 본문의 `[n]` 인라인 인용은 이 번호를 따른다.
  * `chunks[].text`: 사용자 노출용 원문 `text`를 사용한다. `enriched_text`는 내부 검색 품질 향상 및 LLM 컨텍스트 조립에만 사용한다.
  * `chunks[].used`: LLM이 structured `used_refs`로 보고한 citation 번호를 Search Service가 `ref -> chunk_id`로 해석한 결과이다. `true`인 항목만 실제 답변 근거로 사용된 청크이다.
  * 피드백 적재가 필요할 때 클라이언트는 `chunks`의 전체 `chunk_id` 목록을 `topk_ids`로, `used=true`인 항목의 `chunk_id` 목록을 `used_ids`로 파생해 Core API에 전달한다.
  * Empty Result 시 `answer`는 고정 문자열 `"검색 결과가 없습니다"`를 반환한다. 이 값은 retrieval이 시작된 뒤 최종 컨텍스트가 0개인 경우에만 사용한다.

#### [Managed Embedding Endpoint 연동]

* **호출 방식:** `POST {EMBEDDING_API_URL}` (`httpx.AsyncClient` 비동기 호출)
* **요청:** `{"texts": ["정규화된 질의 텍스트"]}`. Search Service는 V1에서 항상 정규화된 단일 질의만 `texts`에 담아 전송한다.
* **응답:** `{"embeddings": [[float, ...]]}`. 응답의 `embeddings` 길이는 요청 `texts` 길이와 같아야 하며, Search Service는 첫 번째 벡터만 사용한다.
* **타임아웃:** `EMBEDDING_TIMEOUT_SEC` (default: `2`)
* **재시도:** timeout/503 시 최대 `EMBEDDING_MAX_RETRIES`회, 200ms 지수 백오프
* **비정상 응답 처리:** `embeddings`가 비어 있거나, 요청 `texts` 길이와 일치하지 않거나, 첫 번째 벡터가 숫자 배열 형식이 아니면 임베딩 호출 실패로 간주한다.
* **최종 실패 시:** degraded mode 없이 `503 SERVICE_UNAVAILABLE`

#### [Search Service 내부 LLMAdapter]

* **인터페이스:** `LLMAdapter` 추상 클래스는 Search Service 내부 계약으로 정의하며, 구현체는 서비스 bootstrap/wiring 단계에서 의존성 주입(DI)한다.
  * `LLM_PROVIDER=gemini` — 운영 기본 구현체 `GeminiLLMAdapter`
  * `LLM_PROVIDER=mock` — 로컬/단위 테스트 전용 구현체 `MockLLMAdapter`
* **구현체 조립 방식:** Pipeline Worker의 `bootstrap.py`와 동일한 패턴으로, Search Service는 프로세스 시작 시 `LLM_PROVIDER` 값을 해석해 provider 선택과 설정 주입을 완료한 concrete 구현체를 조립하고 Search Orchestrator에 주입한다. 현재 기본값은 `gemini`이며, `mock`은 테스트 wiring 전용이다. 오케스트레이터는 provider SDK에 직접 의존하지 않는다.
* **Gemini 구현 계약:** `GeminiLLMAdapter`는 `google-genai` SDK를 사용하되 backend는 Vertex AI를 사용한다. 인증은 API Key가 아니라 GCP ADC 또는 서비스 계정 자격 증명으로 수행하며, `GCP_PROJECT_ID`, `GCP_LOCATION`, `GEMINI_MODEL_NAME`를 bootstrap 단계에서 주입한다.
* **반환 계약:** `generate(prompt, trace_id)`는 `LLMGenerationResult`를 반환한다.
  * `text`는 non-empty raw 문자열이며, 정확히 하나의 `<ANSWER>...</ANSWER>` 블록과 하나의 `<USED_REFS_JSON>...</USED_REFS_JSON>` 블록을 포함해야 한다.
* **설정 소유권:** Search Service는 generation policy(`temperature`, `max_output_tokens`)를 소유하고, shared config가 safety setting을 소유한다. 이 설정은 adapter 생성 시 주입되며 V1 요청 경로에서는 raw provider 파라미터나 per-request profile을 노출하지 않는다.
* **타임아웃:** `LLM_TIMEOUT_SEC` (default: `3`)
* **재시도:** Retryable 오류(타임아웃, 429, 503) 시 최대 `LLM_MAX_RETRIES`회, 200ms 지수 백오프
* **최종 실패 시:**
  * Retryable `LLMAdapter` 오류(`TIMEOUT`, `RATE_LIMITED`, `UNAVAILABLE`)는 degraded mode 없이 `503 SERVICE_UNAVAILABLE`
  * Non-retryable `LLMAdapter` 오류(`AUTH_ERROR`, `INTERNAL_ERROR`)는 `500 INTERNAL_ERROR`
* **ContextBlock 타입 정의:**
  * `ContextBlock`은 사용자 응답용 구조가 아니라 LLM 입력용 최소 컨텍스트 구조이다.
  * `text`에는 사용자 응답의 `chunks[].text`를 그대로 재사용하는 것이 아니라, `enriched_text`가 있으면 이를 우선 사용하고 없으면 원문 `text`를 사용한다.
  * `title`은 멀티 비디오 검색 시 청크 출처를 LLM에 라벨링하기 위한 필수 메타데이터이다.

```python
@dataclass
class ContextBlock:
    ref: int
    chunk_id: str
    title: str
    text: str
    start_ms: int
    end_ms: int
```

### 2.2 Data Access (Reads & Writes)

| Type | Store | Entity/Table | Key/Filter | Mutation/Action | Notes |
| --- | --- | --- | --- | --- | --- |
| Read | Metadata DB (SOT) | `video` | `video.user_id`, `video.status!=READY` | EXISTS / `SELECT 1 LIMIT 1` | readiness gate: 요청자 소유 전체 영상 중 `READY`가 아닌 영상이 하나라도 존재하는지 확인한다. 존재하면 Embedding/FTS/ANN/LLM을 수행하지 않고 `409 SEARCH_NOT_READY`를 즉시 반환한다 |
| Read | Metadata DB (SOT) | `video` | `video.user_id` | EXISTS / `SELECT 1 LIMIT 1` | 검색 범위에 영상이 하나도 없는지 확인한다. 없으면 Embedding/FTS/ANN/LLM을 수행하지 않고 `409 NO_VIDEOS_UPLOADED`를 즉시 반환한다 |
| Read | Metadata DB (FTS) | `chunk` JOIN `video` | `video.user_id`, FTS 인덱스 | SELECT | `chunk`에는 `user_id`가 없으므로 `chunk.video_id = video.id` 조인으로 테넌시를 강제한다. FTS 기준 텍스트는 `COALESCE(chunk.enriched_text, chunk.text)` |
| Read | Vector Store (ANN) | `vector_index_entry` | `vector_index_entry.user_id`, 벡터 거리 | SELECT | ANN 후보는 `user_id` 기준으로만 필터링한다. 조인 비용 없이 Vector Store 메타데이터만 사용한다 |
| Read | Metadata DB (SOT) | `chunk` JOIN `video` | 병합된 후보 `chunk_id` 목록 | SELECT | 서빙 게이트: `video.status = READY`, `video.user_id = requester_user_id`, 레코드 존재 여부 검증. 응답용 `text`와 LLM 컨텍스트용 `enriched_text`, 타임스탬프, `title`을 함께 로드한다 |

> Search Service는 read-only 컴포넌트이다. `req_id` 역시 Search Service 내부에서 영속 저장하지 않는다.

### 2.3 SLA & Constraints

* **전체 응답 SLA:** 5초 이내 (PRD §4.1)
* **검색 실패율(Zero Result Rate):** 5% 미만 유지 (PRD §4.2)
* **질의 길이:** 정규화 후 2자 이상 1,000자 이하
* **후보 조회 수:** `SEARCH_TOP_K` (초기값 `20`)
* **최종 컨텍스트 크기:** `FINAL_TOP_K` (초기값 `5`)
* **병합 전략:** RRF 사용, `RRF_K=60` 초기값
* **다국어 정책:** Search Service는 번역 또는 query rewrite를 수행하지 않는다. 한국어 질의-영어 영상 검색은 임베딩 모델의 cross-lingual 성능에 기반한 best-effort로 지원한다.
* **검색 범위 제한:** 사용자별 READY 영상 수 또는 총 청크 수에 따른 별도 hard limit는 두지 않는다. 대규모 사용자에서의 지연 증가는 운영 지표로 관측하고, 필요 시 후속 정책으로 soft limit를 도입할 수 있다.

### 2.4 Error Contract & Messaging Semantics

| HTTP Status | Error Code | 발생 조건 | Retryable |
| --- | --- | --- | --- |
| 400 | `INVALID_ARGUMENT` | 정규화 후 `query` 길이 2자 미만 또는 1,000자 초과, 지원하지 않는 요청 필드 포함 | N |
| 401 | `UNAUTHENTICATED` | JWT 미제공 또는 서명/만료 검증 실패 | N |
| 409 | `NO_VIDEOS_UPLOADED` | 검색 범위(요청자 소유 전체 영상)에 영상이 1개도 없어 검색을 시작할 수 없는 경우 | N |
| 409 | `SEARCH_NOT_READY` | 검색 범위(요청자 소유 전체 영상) 내 `READY`가 아닌 영상이 1개 이상 존재하여 사용자 전체 영상 준비가 완료되지 않은 경우 | N |
| 500 | `INTERNAL_ERROR` | DB 조회 오류, 내부 처리 오류, non-retryable `LLMAdapter` 오류(`AUTH_ERROR`, `INTERNAL_ERROR`), LLM 응답 형식 오류(필수 `<ANSWER>` 블록 누락 또는 공백) | N |
| 503 | `SERVICE_UNAVAILABLE` | Embedding API 최종 실패, retryable `LLMAdapter` 최종 실패 | Y |

* **에러 응답 바디:** `{"code": "ERROR_CODE", "message": "설명 문자열", "trace_id": "UUID4"}`
* **에러 응답 헤더:** 모든 에러 응답은 바디의 `trace_id`와 동일한 `X-Trace-Id` 헤더를 포함한다.
* **Empty Result 처리:** retrieval이 실제로 시작된 뒤에만 발생할 수 있으며, 최종 통과 청크가 0개일 때만 `200`으로 반환한다. 응답은 일반 성공 응답 스키마를 그대로 따르며, `req_id`는 유지되고 `answer="검색 결과가 없습니다"`, `chunks=[]`이다.
* **근거 부족 처리:** 최종 통과 청크가 존재하더라도 답변 근거가 충분하지 않은 경우, LLM은 추측으로 메우지 않고 근거 부족을 명시해야 한다.

### 2.5 스키마 (DDL)

> Search Service는 Metadata DB를 읽기 전용으로 참조한다. DDL 소유권은 Core API Server와 Pipeline Worker에 있다.

**참조 스키마 (읽기 전용):**

```sql
-- video: id, user_id, title, category, status, created_at, updated_at, ...
-- chunk: id, video_id, start_ms, end_ms, text, enriched_text, visual_caption, ocr_text, scene_tags, ...
-- vector_index_entry: chunk_id, user_id, video_id, embedding_vector, embedding_model_version, ...
```

---

## 3. Core Design & Logic

### 3.1 주요 흐름 (Sequence)

#### Search & RAG Serving (단일 요청 처리 흐름)

1. **요청 수신 및 Trace ID 확정:** `POST /api/v1/search` 요청을 수신한다. `X-Trace-Id` 헤더가 없거나 UUID 형식이 아니면 새 UUID4를 생성하고, 유효한 값이 있으면 그대로 사용한다.
2. **JWT 검증:** Search Service 내부 미들웨어가 JWT를 검증하고 `requester_user_id`를 추출한다. 실패 시 `401`.
3. **질의 정규화 및 요청 검증:** `query`를 정규화하고 요청 바디에 지원하지 않는 필드가 포함되지 않았는지 검증한다. 미지원 필드가 있으면 `400`.
4. **`req_id` 생성:** 요청 단위 UUID4를 생성한다.
5. **검색 범위 비어 있음 확인:** 요청자 소유 전체 영상이 0개인지 Metadata DB에서 먼저 확인한다. 0개면 `409 NO_VIDEOS_UPLOADED`를 반환한다.
6. **사용자 전체 영상 readiness gate:** 요청자 소유 전체 영상 중 `video.status != READY`인 항목이 하나라도 존재하는지 Metadata DB에서 확인한다. 존재하면 Embedding/FTS/ANN/LLM을 수행하지 않고 `409 SEARCH_NOT_READY`를 반환한다.
7. **질의 임베딩 변환:** 정규화된 질의를 Managed Embedding Endpoint로 전송한다. timeout/503은 재시도 정책을 적용하며, 최종 실패 시 `503`.
8. **FTS/ANN 후보 조회:** FTS와 ANN 조회를 병렬 실행한다.
   * FTS는 `chunk JOIN video`로 테넌시를 강제한다.
   * ANN은 `vector_index_entry`를 기준으로 조회하고 `user_id` 필터만 반영한다.
9. **RRF 병합:** FTS와 ANN 후보를 RRF로 병합하여 relevance 순위가 있는 후보 목록을 만든다.
10. **SOT 서빙 검증:** 병합된 후보를 Metadata DB(SOT)에서 다시 검증하여 `READY` 상태이며 `requester_user_id` 소유인 청크만 남긴다. 이 단계의 결과가 최종 컨텍스트 집합이다.
11. **Empty Result 처리:** 최종 집합이 0개면 LLM을 호출하지 않고 `"검색 결과가 없습니다"`를 반환한다. 이 분기는 retrieval이 실제로 시작된 이후에만 도달할 수 있다.
12. **Citation 번호 부여:** 최종 집합에 이후 LLM 프롬프트에 사용할 내부 순서 기준으로 `ref=1..N`을 부여한다. 이 번호 체계가 답변 본문 인라인 인용과 `chunks[].ref`의 기준이 된다.
13. **응답용 `chunks` 조립:** 최종 집합을 `ref ASC` 순서의 canonical 배열로 직렬화하여 `chunks`를 만든다. 각 항목에는 `ref`, `chunk_id`, `video_id`, `title`, `start_ms`, `end_ms`, `text`, `used=false` 초기값을 담는다.
14. **프롬프트 조립 및 LLM 호출:** 최종 집합을 relevance 순서 그대로 `ContextBlock`에 넣고 LLM을 호출한다. 프롬프트는 검색으로 회수된 청크에 직접 뒷받침되는 내용만 답변하도록 강제하며, 각 사실 주장마다 `[n]` 인라인 인용을 붙이도록 지시한다. 답변은 정확히 하나의 `<ANSWER>...</ANSWER>` 블록과 하나의 `<USED_REFS_JSON>...</USED_REFS_JSON>` 블록으로 출력하도록 지시하며, 후자에는 `{"used_refs":[...]}`만 포함하도록 강제한다. 근거가 부족하면 이를 명시하도록 지시한다. 내부 `LLMAdapter` 구현체는 `LLMGenerationResult`를 반환한다.
15. **응답 분리 및 `used_refs` 해석:** `llm_result.text`에서 `<ANSWER>` 블록과 `<USED_REFS_JSON>` 블록을 각각 추출한다. `<ANSWER>` 블록은 최종 `answer`로 사용한다. `<ANSWER>` 블록이 없거나 공백이면 `500 INTERNAL_ERROR`로 처리한다. `<USED_REFS_JSON>` 블록에서 `used_refs`를 파싱하고, 정수 아님/범위 밖/중복 값을 제거한 뒤 `ref -> chunk_id` 내부 매핑으로 `chunks[].used`를 갱신한다.
16. **응답 반환:** `req_id`, `answer`, `chunks`를 반환하고, 성공 응답 헤더에 `X-Trace-Id`를 echo한다. `answer`에는 `<ANSWER>` 블록 내부 자연어 답변만 포함되며 metadata 블록은 포함되지 않는다. 클라이언트는 `chunks`에서 `topk_ids`와 `used_ids`를 파생해 피드백 전송에 사용할 수 있다.

### 3.2 상태 전이 (State Machine)

> Search Service는 Video 상태를 직접 변경하지 않는다. 아래는 Search Service의 서빙 게이트가 참조하는 상태 기준이다.

| From | To | Actor | Trigger | Guard | Side Effects |
| --- | --- | --- | --- | --- | --- |
| 임의 상태 ★ | 검색 노출 | Search Service | SOT 서빙 게이트 통과 | `video.status = READY` AND `video.user_id = requester_user_id` AND 레코드 존재 | 검색 결과에 포함 |
| 임의 상태 ★ | 검색 제외 | Search Service | SOT 서빙 게이트 미통과 | status가 READY가 아니거나 소유권 불일치 또는 hard-delete | 검색 결과에서 필터링 |

> ★ 표시 행은 참조 목적이다. Search Service는 상태 전이를 트리거하지 않는다.

### 3.3 Search Orchestrator 핵심 모듈

#### RRF 병합 (Reciprocal Rank Fusion)

```
RRF_score(d) = Σ 1 / (k + rank(d))
```

* `k`: `RRF_K` (초기값 `60`)
* FTS와 ANN 각각의 순위를 기준으로 합산한다.
* 병합 결과는 relevance 순서를 갖는 후보 목록이며, Search Service는 이 내부 순서를 LLM 프롬프트 조립과 `ref` 번호 부여에만 사용한다.

#### 프롬프트 빌더 (prompt_builder)

* LLM 컨텍스트는 SOT 서빙 게이트를 통과한 최종 집합만 사용한다.
* `ContextBlock.text`에는 `enriched_text`가 있으면 이를 사용하고, 없으면 `text`를 사용한다.
* 각 청크는 단순 텍스트가 아니라 최소 `ref`, `title`, `start_ms`, `end_ms`와 함께 라벨링하여 직렬화한다. 이는 멀티 비디오 검색 시 LLM이 청크 출처와 비디오 경계를 구분하고 안정적인 citation 번호를 사용하도록 하기 위함이다.
* `chunk_id`는 서버 내부 매핑용이며, LLM에게는 citation용 식별자로 직접 복사하게 하지 않는다.
* LLM은 모든 사실 주장마다 하나 이상의 `[n]` 인라인 인용을 포함해야 하며, 하나의 문장이 여러 청크에 근거하면 관련 citation을 모두 표기해야 한다.
* LLM 출력 형식은 아래를 정확히 따라야 한다.

```text
<ANSWER>
사용자에게 보여줄 자연어 답변. 모든 사실 주장에는 `[n]` citation 포함.
</ANSWER>
<USED_REFS_JSON>
{"used_refs":[1,2]}
</USED_REFS_JSON>
```

* 시스템 프롬프트는 다음 정책을 강제한다:
  * 검색으로 회수된 청크에 의해 직접 뒷받침되는 내용만 답변한다.
  * 근거가 불충분하면 추론으로 메우지 않고 근거 부족을 명시한다.
* 파싱 전략:
  * `llm_result.text`에서 `<ANSWER>...</ANSWER>` 블록과 `<USED_REFS_JSON>...</USED_REFS_JSON>` 블록을 각각 추출한다.
  * `answer` 필드는 `<ANSWER>` 내부 텍스트만 사용한다.
  * `used_refs`는 `<USED_REFS_JSON>` 내부 문자열만 JSON으로 파싱한다.
  * 답변 본문 안에 포함된 일반 JSON 유사 문자열은 citation metadata로 해석하지 않는다.
  * `used_refs`는 citation 해석의 authoritative source이며, 답변 본문의 `[n]` 표기는 display-only로 취급한다.
  * 파싱 실패 시 모든 `chunks[].used=false`로 처리한다.
  * 답변 본문을 재스캔하여 citation 번호를 복구하려고 시도하지 않는다.

#### `used_refs` 정제 및 citation 해석

* 파싱된 `used_refs`에서 정수가 아닌 값은 제거한다.
* `1..N` 범위를 벗어난 `ref`는 제거한다.
* 중복 `ref`는 첫 등장만 유지한다.
* 정제된 `used_refs`를 `ref -> chunk_id` 내부 매핑으로 해석하여 해당 `chunks[].used=true`로 표시한다.
* 피드백 적재 및 내부 품질 분석이 필요할 때 `topk_ids`와 `used_ids`는 `chunks` 배열에서 파생한다.

### 3.4 멱등성 및 복구 (Resilience)

* **검색 요청 멱등성:** 각 검색 요청은 독립적인 읽기 전용 트랜잭션이다.
* **Embedding API 실패 처리:** timeout/503은 최대 `EMBEDDING_MAX_RETRIES`회 재시도 후 실패 시 `503`.
* **LLM API 실패 처리:** Retryable `LLMAdapter` 오류는 최대 `LLM_MAX_RETRIES`회 재시도 후 실패 시 `503`. Non-retryable `LLMAdapter` 오류(`AUTH_ERROR`, `INTERNAL_ERROR`)는 `500`.
* **사용자 전체 영상 readiness gate:** 요청자 소유 전체 영상 중 `video.status != READY`가 하나라도 있으면 expensive retrieval 단계를 수행하지 않고 즉시 `409 SEARCH_NOT_READY`를 반환한다.
* **검색 범위 비어 있음 처리:** 요청자 소유 영상이 1개도 없으면 expensive retrieval 단계를 수행하지 않고 즉시 `409 NO_VIDEOS_UPLOADED`를 반환한다.
* **장애 시 fallback 정책:** Embedding 또는 LLM 실패 시 degraded mode로 청크만 반환하지 않고, 요청 전체를 실패 처리한다.
* **Empty Result와 근거 부족 구분:**
  * Empty Result: 시스템이 판정하며 LLM을 호출하지 않는다. retrieval이 시작된 뒤 최종 컨텍스트가 0개인 경우만 포함한다.
  * 근거 부족: LLM이 비추론 정책에 따라 명시한다.

### 3.5 Data Consistency & Orphan Prevention

* **SOT 서빙 게이트의 역할:** Vector Store는 Metadata DB의 파생 Projection이므로, 최종 정합성은 SOT 게이트가 보장한다.
* **테넌시 이중 검증:** 후보 조회 단계와 SOT 게이트 단계에서 모두 `requester_user_id`를 검증한다.
* **Search Service는 DB에 쓰지 않으므로 Orphan Data를 생성하지 않는다.**
* **`req_id`는 응답 단위 식별자이며 Search Service 내부에 영속 저장하지 않는다.**

---

## 4. Acceptance Criteria (DoD)

### 4.1 시나리오 검증

#### POST /api/v1/search

**정상**
* [ ] 유효한 JWT + 유효한 `query` + 요청자 소유 전체 영상이 모두 `READY` → 사용자 전체 영상 대상 하이브리드 검색 수행 → 200 + `req_id` + `answer` + `chunks`
* [ ] 요청자 소유 영상이 0개이면 Embedding/FTS/ANN/LLM 호출 없이 `409 NO_VIDEOS_UPLOADED`를 반환함을 확인
* [ ] `chunks`가 최종 컨텍스트의 canonical 배열이며 `ref ASC` 순서로 반환됨을 확인
* [ ] `chunks[].ref`가 답변 본문의 `[n]` 인라인 인용과 동일 번호 체계를 사용함을 확인
* [ ] `chunks[].used=true`인 항목만 실제 답변 근거로 사용되었음을 확인
* [ ] `answer` 필드에는 `<USED_REFS_JSON>` metadata 블록이 포함되지 않음을 확인
* [ ] `ContextBlock.text`가 `enriched_text` 우선, 없으면 원문 `text` fallback으로 조립됨을 확인
* [ ] 최종 통과 청크가 0개인 경우 → retrieval 이후 LLM 호출 없이 `200`, `answer="검색 결과가 없습니다"`
* [ ] 최종 통과 청크가 있으나 답변 근거가 부족한 경우 → LLM이 추론으로 메우지 않고 근거 부족을 명시함을 확인
* [ ] 성공 응답 헤더에 `X-Trace-Id`가 echo됨을 확인

**예외**
* [ ] JWT 미제공 → 401
* [ ] JWT 서명 오류 또는 만료 → 401
* [ ] 정규화 후 `query` 길이 2자 미만 → 400
* [ ] `query` 1,000자 초과 → 400
* [ ] 요청 바디에 `scope` 또는 기타 미지원 필드 포함 → 400
* [ ] 잘못된 `X-Trace-Id` 헤더 → 요청은 계속 처리하되 새 UUID4를 생성하여 응답 헤더/에러 바디에 사용
* [ ] 요청자 소유 전체 영상 중 `READY`가 아닌 영상이 1개 이상 존재하면 Embedding/FTS/ANN/LLM 호출 없이 `409 SEARCH_NOT_READY`를 반환함을 확인
* [ ] Embedding API 최종 실패 → 503
* [ ] Embedding API가 `embeddings=[]`, 요청 길이 불일치, 비숫자 배열 등 비정상 shape를 반환하면 → `503`
* [ ] retryable `LLMAdapter` 최종 실패 → 503
* [ ] `LLMAdapter`의 non-retryable 오류(`AUTH_ERROR`, `INTERNAL_ERROR`) → 500
* [ ] LLM 응답에 필수 `<ANSWER>` 블록이 없거나 공백이면 → `500`

#### RRF 병합 로직

**정상**
* [ ] FTS 결과와 ANN 결과가 있을 때 RRF 점수 기준으로 올바르게 통합 순위가 산출됨을 확인
* [ ] 동일 `chunk_id`가 FTS/ANN 양쪽에 등장 시 중복 없이 합산 점수로 처리됨을 확인
* [ ] 결과가 `FINAL_TOP_K`를 초과하지 않음을 확인

#### SOT 서빙 게이트

**정상**
* [ ] `DELETING` 상태 영상의 Chunk가 후보에 포함되더라도 SOT 게이트에서 제거됨을 확인
* [ ] hard-delete된 Chunk가 SOT 게이트에서 자동 제거됨을 확인
* [ ] 타인 소유 영상의 Chunk가 후보 단계와 SOT 게이트 단계 모두에서 차단됨을 확인

#### `used_refs` 파싱 및 citation 해석

**정상**
* [ ] LLM 응답 `llm_result.text`에서 `used_refs` 파싱 성공 → 대응하는 `chunks[].used`가 올바르게 `true`로 설정됨을 확인
* [ ] `used_refs=[2,2,99,"x"]`가 들어오면 중복/범위 밖/비정수 값이 제거됨을 확인
* [ ] `answer` 본문에 `[1]`, `[2]`가 포함된 경우 `chunks[].ref`가 동일 번호를 가리킴을 확인
* [ ] 본문 안의 일반 JSON 유사 문자열은 citation metadata로 오인 파싱하지 않음을 확인

**예외**
* [ ] `llm_result.text`에서 `used_refs` 파싱 실패 → 모든 `chunks[].used=false`, 응답 자체는 200 성공

#### 프롬프트 조립

**정상**
* [ ] 멀티 비디오 검색 시 각 청크가 `ref`, `title`, `start_ms`, `end_ms`를 함께 포함한 형태로 직렬화됨을 확인
* [ ] 모든 사실 주장마다 최소 하나 이상의 `[n]` citation이 포함됨을 확인
* [ ] 하나의 문장이 여러 청크에 근거하면 관련 citation이 함께 표기됨을 확인

### 4.2 검증을 위한 테스팅 전략 (Testing Strategy)

* 단위 테스트와 통합 테스트는 최소 아래 항목을 포함해야 한다.
  * 질의 정규화 및 미지원 요청 필드 검증
  * 검색 범위 비어 있음 확인, readiness gate, final-empty result
  * FTS/ANN 병합 및 RRF 순위 결정
  * SOT 서빙 게이트의 READY/DELETING/hard-delete 필터링
  * `chunks`가 최종 컨텍스트의 canonical 배열이며 `ref ASC` 순서를 유지함
  * `ref` 번호 부여와 답변 본문 인라인 인용의 일치
  * `<ANSWER>` / `<USED_REFS_JSON>` 출력 경계와 `answer`/metadata 분리
  * 임베딩 응답 비정상 shape(`embeddings=[]`, 길이 불일치, 비숫자 배열) 처리
  * `ContextBlock.text`의 `enriched_text` 우선 / `text` fallback 규칙
  * 멀티 비디오 프롬프트 직렬화 시 `ref`, `title`, 타임스탬프 라벨링
  * Empty Result와 근거 부족 응답 구분
  * 모든 사실 주장에 대한 citation 강제
  * 답변 본문 내 일반 JSON 유사 문자열을 metadata로 오인 파싱하지 않음
  * `used_refs` 파싱, 정제, `chunks[].used` 해석
  * Embedding/LLM timeout, retry 분기
  * `X-Trace-Id` 수신, invalid 값의 재발급, 성공/에러 응답 echo 전파

### 4.3 산출물 (Artifacts)

폴더 구조는 `docs/Tech_Spec/folder_structure.md`를 참조한다.

* [ ] HTTP 라우터 — `POST /api/v1/search`
* [ ] 요청/응답 DTO — `SearchRequest`, `SearchResponse`, `ChunkResult`
* [ ] 검색 오케스트레이터 — 하이브리드 검색 파이프라인 전체 흐름
* [ ] RRF 병합 모듈
* [ ] 프롬프트 빌더 — 프롬프트 조립 및 `used_refs` 파싱
* [ ] DB Repository — FTS 조회, ANN 조회, SOT 서빙 게이트 쿼리
* [ ] Embedding HTTP 클라이언트
* [ ] LLMAdapter 추상 클래스 및 구현체
* [ ] JWT 인증 미들웨어
* [ ] 단위 테스트 / 통합 테스트
