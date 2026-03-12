# [Search Service] SPEC

**Meta**
* **Component ID:** search-service
* **SOT References:** `docs/system-design.md`, `docs/PRD.md`
---

## 1. Context & Scope

### 1.1 목적 (Purpose)

* **한 줄 요약:** Search Service는 사용자의 자연어 질의를 수신하여 하이브리드 검색 파이프라인(임베딩 변환 → FTS 키워드 검색 + ANN 벡터 검색 → RRF 병합 → SOT 서빙 검증 → LLM 답변 생성)을 즉시 실행하고, 검색 응답 ID와 타임스탬프가 포함된 검색 결과를 반환하는 검색 전담 서비스이다.
* **비즈니스 목표:** PRD 목표인 "사용자의 자연어 질의에 대한 시맨틱 검색 결과를 5초 이내에 반환"하며, 최종 응답에 포함된 청크 타임스탬프를 통해 사용자가 해당 지점부터 영상을 검증하고 재생할 수 있도록 지원한다.

### 1.2 요구 기술 스택 및 환경 변수 (Tech Stack & Configs)

* **구현 계약 기준:** 본 컴포넌트의 계약은 비동기 HTTP API, DB 접근 계층, Embedding/LLM 어댑터 인터페이스로 정의된다.
* **Reference Implementation:** Python 3.11+, FastAPI (Async), SQLAlchemy 2.0 Async, `httpx.AsyncClient`, `google-generativeai` SDK
* **필수 환경 변수:**
  * `JWT_SECRET_KEY` — JWT 서명 검증 키
  * `DATABASE_URL` — Metadata DB 연결 문자열
  * `EMBEDDING_API_URL` — Managed Embedding Endpoint 주소
  * `GEMINI_API_KEY` — Gemini API 인증 키
  * `GEMINI_MODEL_NAME` — Gemini 모델명
* **선택 환경 변수 (초기 기본값):**
  * `SEARCH_TOP_K=20` — FTS/ANN 각 후보 조회 수
  * `FINAL_TOP_K=5` — RRF 병합 후 SOT 게이트에 전달할 최종 후보 수
  * `RRF_K=60` — RRF 상수
  * `EMBEDDING_TIMEOUT_SEC=1`
  * `EMBEDDING_MAX_RETRIES=1`
  * `EMBEDDING_CB_FAILURE_THRESHOLD=3`
  * `EMBEDDING_CB_RECOVERY_SEC=30`
  * `LLM_TIMEOUT_SEC=3`
  * `LLM_MAX_RETRIES=1`
  * `LLM_CB_FAILURE_THRESHOLD=3`
  * `LLM_CB_RECOVERY_SEC=30`

> **Notes:** Search Service는 메시지 브로커를 구독하지 않는다.

### 1.3 경계 (Boundaries)

* **In-Scope (책임 범위):**
  * JWT 검증 및 `requester_user_id` 추출
  * 질의 정규화 및 검색 요청 검증
  * 요청 단위 `search_response_id` 생성
  * 질의 텍스트를 Managed Embedding Endpoint에 직접 전송하여 벡터 변환
  * Metadata DB(FTS)에서 키워드 후보 조회
  * Vector Store(ANN)에서 벡터 후보 조회
  * RRF 기반 후보 병합
  * Metadata DB(SOT)를 서빙 게이트로 활용한 최종 서빙 검증
  * LLMAdapter를 통한 최종 답변 생성 및 structured `used_refs` 파싱
  * 검색 결과(`search_response_id`, `answer`, `chunks`, `topk_chunk_ids`, `citations`, `used_chunk_ids`) 반환
  * `trace_id` 전파 (`X-Trace-Id`)

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

* 검색 가능 상태: `Video.status = READY`
* 검색 제외 상태: `PENDING`, `UPLOADED`, `PROCESSING`, `FAILED`, `DELETING`

---

## 2. Contracts (Interface & Data)

### 2.1 API / Message Endpoint

#### [HTTP API]

* **Auth / Tenancy:** 모든 요청에는 JWT Authorization 헤더가 필수이다. Search Service는 JWT를 직접 검증하고 `requester_user_id`를 추출한다.
* **Trace Header:** `X-Trace-Id` 요청 헤더가 있으면 우선 사용하고, 없으면 Search Service가 UUID4를 신규 생성한다. 확정된 값은 성공 응답 헤더 `X-Trace-Id`, 에러 응답 바디 `trace_id`, Embedding/LLM 하위 호출 로그에 동일하게 전파한다.

| HTTP Method | Endpoint (URI) | Request | Success Response | Notes |
| --- | --- | --- | --- | --- |
| **POST** | `/api/v1/search` | `{"query": "자연어 질의", "scope": {...}}` | **200** `{"search_response_id","answer","chunks","topk_chunk_ids","citations","used_chunk_ids"}` | 하이브리드 검색 + LLM 답변 생성 |

* **질의 정규화 및 검증:**
  * `query`는 정규화 후에 검증한다.
  * 정규화 규칙: 앞뒤 공백 제거, 연속 공백 축약, ASCII 영문 소문자화, 제어문자 제거
  * 정규화 후 길이 제약: 최소 2자, 최대 1,000자
  * 정규화 후 길이가 2자 미만이면 `400 INVALID_ARGUMENT`

* **`scope` 규칙:**
  * `scope` 생략 또는 `{}` 빈 객체는 `{"all_my_videos": true}`와 동일하게 처리한다.
  * 허용되는 형태:
    * `{"all_my_videos": true}`
    * `{"video_ids": ["UUID4", ...]}`
    * `{"category": "GENERAL" | "IT" | "MEDICAL" | "LEGAL"}`
    * `{"video_ids": ["UUID4", ...], "category": "GENERAL" | "IT" | "MEDICAL" | "LEGAL"}`
  * 허용되지 않는 형태:
    * `all_my_videos`와 다른 필드를 함께 보낸 경우
    * `all_my_videos: false`
    * `video_ids: []`
    * 유효하지 않은 UUID 형식
  * `video_ids`와 `category`가 함께 오면 교집합으로 처리한다.
  * `category`는 검색 범위를 제한하는 필터로만 사용하며, 프롬프트/랭킹 보정에는 사용하지 않는다.
  * `scope.video_ids`가 명시된 경우 배열 내 항목 중 하나라도 미존재 또는 타인 소유이면 전체 요청을 `404 NOT_FOUND`로 실패시킨다. 이때 존재 여부와 권한 여부는 구분하여 노출하지 않는다.

* **응답 스키마:**
  ```json
  {
    "search_response_id": "UUID4",
    "answer": "LLM이 생성한 자연어 답변 [1]",
    "chunks": [
      {
        "chunk_id": "UUID4",
        "video_id": "UUID4",
        "video_title": "영상 제목",
        "start_ms": 12000,
        "end_ms": 24000,
        "text": "청크 텍스트 원문"
      }
    ],
    "topk_chunk_ids": ["UUID4", "..."],
    "citations": [
      {
        "ref_no": 1,
        "chunk_id": "UUID4"
      }
    ],
    "used_chunk_ids": ["UUID4", "..."]
  }
  ```

* **응답 필드 상세:**
  * `search_response_id`: 요청 단위로 생성되는 UUID4. Search Service는 이를 영속 저장하지 않으며, 피드백 귀속 키로 클라이언트와 Core API에 전달한다. 별도 검색 응답 저장소가 없으므로 이후 Core API는 이를 서버-사이드 조회키가 아니라 상관관계용 opaque ID로 취급한다.
  * `topk_chunk_ids`: SOT 서빙 게이트를 통과해 실제 최종 응답 생성에 사용된 청크 집합이다. `chunks`와 1:1 동일 집합이며, 순서는 relevance 순서를 유지한다.
  * `ref_no`: SOT 게이트를 통과한 최종 컨텍스트에 대해 `topk_chunk_ids`와 동일한 relevance 순서로 `1..N`을 부여한 요청 단위 citation 번호이다.
  * `chunks`: topk_chunk_ids와 동일한 청크 집합을 사용자 확인이 쉬운 순서로 재정렬한 배열이다. 같은 video_id의 청크를 먼저 모으고, 비디오 그룹은 관련도가 높은 순서로 정렬하며, 각 그룹 내부는 `start_ms` 오름차순으로 정렬한다.
  * `chunks`의 순서는 citation 번호와 독립적이다. citation 번호는 `ref_no` 기준, `chunks`는 video-grouped timeline 기준으로 해석한다.
  * `chunks[].text`: 사용자 노출용 원문 `text`를 사용한다. `enriched_text`는 내부 검색 품질 향상 및 LLM 컨텍스트 조립에만 사용한다.
  * `citations`: LLM이 structured `used_refs`로 보고한 citation 번호를 Search Service가 `ref_no -> chunk_id`로 해석한 매핑 배열이다. 각 항목은 `{ref_no, chunk_id}` 형태이며 `ref_no` 오름차순으로 반환한다.
  * `used_chunk_ids`: `citations`에서 해석된 실제 최종 참조 청크 ID 목록이다. 피드백 적재 및 품질 분석은 이 필드를 기준으로 수행한다.
  * Empty Result 시 `answer`는 고정 문자열 `"검색 결과가 없습니다"`를 반환한다.

#### [Managed Embedding Endpoint 연동]

* **호출 방식:** `POST {EMBEDDING_API_URL}` (`httpx.AsyncClient` 비동기 호출)
* **요청:** `{"texts": ["정규화된 질의 텍스트"]}`. Search Service는 V1에서 항상 정규화된 단일 질의만 `texts`에 담아 전송한다.
* **응답:** `{"embeddings": [[float, ...]]}`. 응답의 `embeddings` 길이는 요청 `texts` 길이와 같아야 하며, Search Service는 첫 번째 벡터만 사용한다.
* **타임아웃:** `EMBEDDING_TIMEOUT_SEC` (default: `1`)
* **재시도:** timeout/503 시 최대 `EMBEDDING_MAX_RETRIES`회, 200ms 지수 백오프
* **서킷 브레이커:** 연속 `EMBEDDING_CB_FAILURE_THRESHOLD`회 실패 시 open, `EMBEDDING_CB_RECOVERY_SEC`초 후 half-open 전환
* **비정상 응답 처리:** `embeddings`가 비어 있거나, 요청 `texts` 길이와 일치하지 않거나, 첫 번째 벡터가 숫자 배열 형식이 아니면 임베딩 호출 실패로 간주한다.
* **최종 실패 시:** degraded mode 없이 `503 SERVICE_UNAVAILABLE`

#### [External AI Adapters — LLMAdapter]

* **인터페이스:** `LLMAdapter` 추상 클래스 (DI로 주입). `External_AI_Adapters_Spec.md ` 참조
  * `GeminiLLMAdapter` — 운영 환경 구현체
  * `MockLLMAdapter` — 로컬/단위 테스트 전용 구현체
* **반환 계약:** `generate(prompt, trace_id)`는 `LLMGenerationResult`를 반환한다.
  * `text`는 답변 본문과 structured `used_refs`를 포함할 수 있는 non-empty 문자열이다.
  * `provider_request_id`, `token_usage`, `finish_reason`은 선택 메타데이터이며, Search Service는 이를 로깅/메트릭에만 사용한다.
* **설정 소유권:** Search Service는 generation policy(`temperature`, `max_output_tokens`)를 소유하고, shared config가 safety setting을 소유한다. 이 설정은 adapter 생성 시 주입되며 V1 요청 경로에서는 raw provider 파라미터나 per-request profile을 노출하지 않는다.
* **타임아웃:** `LLM_TIMEOUT_SEC` (default: `3`)
* **재시도:** Retryable 오류(타임아웃, 429, 503) 시 최대 `LLM_MAX_RETRIES`회, 200ms 지수 백오프
* **서킷브레이커:** 연속 `LLM_CB_FAILURE_THRESHOLD`회 실패 시 open, `LLM_CB_RECOVERY_SEC`초 후 half-open 전환
* **최종 실패 시:**
  * Retryable adapter 오류(`TIMEOUT`, `RATE_LIMITED`, `UNAVAILABLE`, `CIRCUIT_OPEN`)는 degraded mode 없이 `503 SERVICE_UNAVAILABLE`
  * Non-retryable adapter 오류(`AUTH_ERROR`, `INTERNAL_ERROR`)는 `500 INTERNAL_ERROR`
* **ContextBlock 타입 정의:**
  * `ContextBlock`은 사용자 응답용 구조가 아니라 LLM 입력용 최소 컨텍스트 구조이다.
  * `text`에는 사용자 응답의 `chunks[].text`를 그대로 재사용하는 것이 아니라, `enriched_text`가 있으면 이를 우선 사용하고 없으면 원문 `text`를 사용한다.

```python
@dataclass
class ContextBlock:
    ref_no: int
    chunk_id: str
    text: str
    start_ms: int
    end_ms: int
```

### 2.2 Data Access (Reads & Writes)

| Type | Store | Entity/Table | Key/Filter | Mutation/Action | Notes |
| --- | --- | --- | --- | --- | --- |
| Read | Metadata DB (FTS) | `chunk` JOIN `video` | `video.user_id`, `video.id`(scope), `video.category`(scope), FTS 인덱스 | SELECT | `chunk`에는 `user_id`, `category`가 없으므로 `chunk.video_id = video.id` 조인으로 테넌시와 카테고리 필터를 강제한다. FTS 기준 텍스트는 `COALESCE(chunk.enriched_text, chunk.text)` |
| Read | Vector Store (ANN) | `vector_index_entry` LEFT JOIN `video` | `vector_index_entry.user_id`, `vector_index_entry.video_id`(scope), `video.category`(scope), 벡터 거리 | SELECT | 현재 설계는 조인으로 카테고리 필터를 강제한다. 조인 비용이 병목이면 검색 전용 projection 또는 중복 컬럼 도입을 후속 최적화로 검토한다 |
| Read | Metadata DB (SOT) | `chunk` JOIN `video` | 병합된 후보 `chunk_id` 목록 | SELECT | 서빙 게이트: `video.status = READY`, `video.user_id = requester_user_id`, 레코드 존재 여부 검증. 응답용 `text`와 LLM 컨텍스트용 `enriched_text`, 타임스탬프, `video_title`을 함께 로드한다 |

> Search Service는 read-only 컴포넌트이다. `search_response_id` 역시 Search Service 내부에서 영속 저장하지 않는다.

### 2.3 SLA & Constraints

* **전체 응답 SLA:** 5초 이내 (PRD §4.1)
* **검색 실패율(Zero Result Rate):** 5% 미만 유지 (PRD §4.2)
* **질의 길이:** 정규화 후 2자 이상 1,000자 이하
* **후보 조회 수:** `SEARCH_TOP_K` (초기값 `20`)
* **최종 컨텍스트 크기:** `FINAL_TOP_K` (초기값 `5`)
* **병합 전략:** RRF 사용, `RRF_K=60` 초기값
* **다국어 정책:** Search Service는 번역 또는 query rewrite를 수행하지 않는다. 한국어 질의-영어 영상 검색은 임베딩 모델의 cross-lingual 성능에 기반한 best-effort로 지원한다.
* **검색 범위 제한:** 사용자별 READY 영상 수 또는 총 청크 수에 따른 별도 hard limit는 두지 않는다. 대규모 사용자에서의 지연 증가는 운영 지표로 관측하고, 필요 시 후속 정책으로 soft limit 또는 scope narrowing 유도를 도입할 수 있다.

### 2.4 Error Contract & Messaging Semantics

| HTTP Status | Error Code | 발생 조건 | Retryable |
| --- | --- | --- | --- |
| 400 | `INVALID_ARGUMENT` | 정규화 후 `query` 길이 2자 미만 또는 1,000자 초과, 유효하지 않은 `scope`, `video_ids=[]`, 잘못된 UUID 형식, `all_my_videos`와 다른 필드의 조합 | N |
| 401 | `UNAUTHENTICATED` | JWT 미제공 또는 서명/만료 검증 실패 | N |
| 404 | `NOT_FOUND` | `scope.video_ids` 중 하나라도 미존재 또는 타인 소유 | N |
| 500 | `INTERNAL_ERROR` | DB 조회 오류, 내부 처리 오류, non-retryable LLM adapter 오류(`AUTH_ERROR`, `INTERNAL_ERROR`) | N |
| 503 | `SERVICE_UNAVAILABLE` | Embedding API 최종 실패, Embedding Circuit Breaker 개방, retryable LLM adapter 최종 실패, LLM Circuit Breaker 개방 | Y |

* **에러 응답 바디:** `{"code": "ERROR_CODE", "message": "설명 문자열", "trace_id": "UUID4"}`
* **Empty Result 처리:** 최종 통과 청크가 0개인 경우, LLM을 호출하지 않고 `200`으로 반환한다. 응답 값은 `answer="검색 결과가 없습니다"`, `chunks=[]`, `topk_chunk_ids=[]`, `citations=[]`, `used_chunk_ids=[]`이다.
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

1. **요청 수신 및 Trace ID 확정:** `X-Trace-Id` 헤더가 있으면 사용하고, 없으면 UUID4를 생성한다.
2. **JWT 검증:** Search Service 내부 미들웨어가 JWT를 검증하고 `requester_user_id`를 추출한다. 실패 시 `401`.
3. **질의 정규화 및 요청 검증:** `query`를 정규화하고 `scope`를 파싱한다. 허용되지 않는 조합이면 `400`.
4. **스코프 사전 검증:** `scope.video_ids`가 명시된 경우, 요청된 모든 `video_id`가 `requester_user_id` 소유이며 실제로 존재하는지 Metadata DB에서 검증한다. 하나라도 미통과면 전체 요청을 `404`로 실패시킨다.
5. **`search_response_id` 생성:** 요청 단위 UUID4를 생성한다.
6. **질의 임베딩 변환:** 정규화된 질의를 Managed Embedding Endpoint로 전송한다. timeout/503은 재시도 정책과 Circuit Breaker를 적용하며, 최종 실패 시 `503`.
7. **FTS/ANN 후보 조회:** FTS와 ANN 조회를 병렬 실행한다.
   * FTS는 `chunk JOIN video`로 테넌시/카테고리/비디오 범위를 강제한다.
   * ANN은 `vector_index_entry`를 기준으로 조회하고, 카테고리 필터가 있을 때는 `video` 조인을 사용한다.
8. **RRF 병합:** FTS와 ANN 후보를 RRF로 병합하여 relevance 순위가 있는 후보 목록을 만든다.
9. **SOT 서빙 검증:** 병합된 후보를 Metadata DB(SOT)에서 다시 검증하여 `READY` 상태이며 `requester_user_id` 소유인 청크만 남긴다. 이 단계의 결과가 최종 컨텍스트 집합이며 `topk_chunk_ids`의 기준이 된다.
10. **Empty Result 처리:** 최종 집합이 0개면 LLM을 호출하지 않고 `"검색 결과가 없습니다"`를 반환한다.
11. **Citation 번호 부여:** 최종 집합에 relevance 순서 기준으로 `ref_no=1..N`을 부여한다. 이 번호 체계가 `citations`와 LLM 프롬프트의 기준이 된다.
12. **응답용 정렬:** 최종 집합을 `video_id`별로 그룹화하고, 그룹 간 순서는 각 그룹의 최고 relevance를 기준으로, 그룹 내부는 `start_ms ASC`로 정렬하여 `chunks` 배열을 만든다.
13. **프롬프트 조립 및 LLM 호출:** 최종 집합을 relevance 순서 그대로 `ContextBlock`에 넣고 LLM을 호출한다. 프롬프트는 검색으로 회수된 청크에 직접 뒷받침되는 내용만 답변하도록 강제하며, 답변 본문에는 `[n]` 인라인 인용을 넣고 별도 structured `used_refs`를 반환하도록 지시한다. 근거가 부족하면 이를 명시하도록 지시한다. LLM adapter는 `LLMGenerationResult`를 반환한다.
14. **`used_refs` 파싱 및 해석:** `llm_result.text`에서 structured `used_refs`를 파싱하고, 정수 아님/범위 밖/중복 값을 제거한 뒤 `ref_no -> chunk_id` 매핑으로 `citations`와 `used_chunk_ids`를 생성한다.
15. **응답 반환:** `search_response_id`, `answer`, `chunks`, `topk_chunk_ids`, `citations`, `used_chunk_ids`를 반환하고, 성공 응답 헤더에 `X-Trace-Id`를 echo한다. `provider_request_id`, `token_usage`, `finish_reason`은 값이 있을 때 caller-side logging/metrics에만 사용한다.

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
* 병합 결과는 relevance 순서를 갖는 후보 목록이며, 최종 `topk_chunk_ids`는 SOT 게이트 통과 후 이 순서를 유지한다.

#### 프롬프트 빌더 (prompt_builder)

* LLM 컨텍스트는 SOT 서빙 게이트를 통과한 최종 집합만 사용한다.
* `ContextBlock.text`에는 `enriched_text`가 있으면 이를 사용하고, 없으면 `text`를 사용한다.
* 각 청크는 단순 텍스트가 아니라 최소 `ref_no`, `video_title` 또는 `video_id`, `start_ms`, `end_ms`와 함께 라벨링하여 직렬화한다. 이는 멀티 비디오 검색 시 LLM이 청크 출처와 비디오 경계를 구분하고 안정적인 citation 번호를 사용하도록 하기 위함이다.
* `chunk_id`는 서버 내부 매핑용이며, LLM에게는 citation용 식별자로 직접 복사하게 하지 않는다.
* LLM은 답변 본문에 `[n]` 인라인 인용을 포함하고, 응답 마지막에 `{"used_refs": [1, 2, ...]}` JSON 블록을 포함하도록 지시한다.
* 시스템 프롬프트는 다음 정책을 강제한다:
  * 검색으로 회수된 청크에 의해 직접 뒷받침되는 내용만 답변한다.
  * 근거가 불충분하면 추론으로 메우지 않고 근거 부족을 명시한다.
* 파싱 전략:
  * `llm_result.text`에서 정규식으로 JSON 블록을 추출한다.
  * 복수 매칭 시 마지막 블록을 사용한다.
  * `used_refs`는 citation 해석의 authoritative source이며, 답변 본문의 `[n]` 표기는 display-only로 취급한다.
  * 파싱 실패 시 `citations=[]`, `used_chunk_ids=[]`로 처리한다.
  * 답변 본문을 재스캔하여 citation 번호를 복구하려고 시도하지 않는다.

#### `used_refs` 정제 및 citation 해석

* 파싱된 `used_refs`에서 정수가 아닌 값은 제거한다.
* `1..N` 범위를 벗어난 `ref_no`는 제거한다.
* 중복 `ref_no`는 첫 등장만 유지한다.
* 정제된 `used_refs`를 `ref_no -> chunk_id` 매핑으로 해석하여 `citations`와 `used_chunk_ids`를 생성한다.
* `citations`는 사용자 대상 인라인 인용 표시와 내부 품질 분석에 공통으로 사용한다.

### 3.4 멱등성 및 복구 (Resilience)

* **검색 요청 멱등성:** 각 검색 요청은 독립적인 읽기 전용 트랜잭션이다.
* **Embedding API 실패 처리:** timeout/503은 최대 `EMBEDDING_MAX_RETRIES`회 재시도 후 실패 시 `503`.
* **LLM API 실패 처리:** Retryable adapter 오류는 최대 `LLM_MAX_RETRIES`회 재시도 후 실패 시 `503`. Non-retryable adapter 오류(`AUTH_ERROR`, `INTERNAL_ERROR`)는 `500`.
* **Circuit Breaker:** Embedding/LLM 각각 독립적으로 운용한다.
* **장애 시 fallback 정책:** Embedding 또는 LLM 실패 시 degraded mode로 청크만 반환하지 않고, 요청 전체를 실패 처리한다.
* **Empty Result와 근거 부족 구분:**
  * Empty Result: 시스템이 판정하며 LLM을 호출하지 않는다.
  * 근거 부족: LLM이 비추론 정책에 따라 명시한다.

### 3.5 Data Consistency & Orphan Prevention

* **SOT 서빙 게이트의 역할:** Vector Store는 Metadata DB의 파생 Projection이므로, 최종 정합성은 SOT 게이트가 보장한다.
* **테넌시 이중 검증:** 후보 조회 단계와 SOT 게이트 단계에서 모두 `requester_user_id`를 검증한다.
* **카테고리 필터 정합성:** 현재 설계는 `video` 조인으로 카테고리 필터를 적용한다.
* **Search Service는 DB에 쓰지 않으므로 Orphan Data를 생성하지 않는다.**
* **`search_response_id`는 응답 단위 식별자이며 Search Service 내부에 영속 저장하지 않는다.**

---

## 4. Observability & Ops

* **Logging:**
  * 모든 검색 요청/응답 로그에 `trace_id`, `search_response_id`, `user_id`, `scope` 요약, 단계별 소요 시간을 포함한다.
  * 일반 운영 로그에는 `query_text` 원문을 저장하지 않는다.
  * 원문 대신 `query_length`, 결과 청크 수, 필터링된 청크 수, `citation_count`, `used_chunk_ids` 개수를 기록한다.
  * Embedding API, LLM API 호출 로그에 `trace_id`, 레이턴시(ms), 에러 코드를 포함한다.
  * LLM adapter가 값을 제공하면 `provider_request_id`, `finish_reason`, `token_usage` 요약을 함께 기록한다.
  * `used_refs` 파싱 실패 시 `used_refs_parse_failure=true` 경고 로그를 기록한다.

* **Metrics:**
  * `search_request_latency_ms`
  * `search_latency_ms{stage=embedding}`
  * `search_latency_ms{stage=fts}`
  * `search_latency_ms{stage=ann}`
  * `search_latency_ms{stage=rrf}`
  * `search_latency_ms{stage=sot_gate}`
  * `search_latency_ms{stage=llm}`
  * `search_empty_result_count`
  * `search_error_rate`
  * `embedding_api_circuit_breaker_state`
  * `llm_api_circuit_breaker_state`

* **Alerts:** 주요 감시 대상은 SLA 위반, Empty Result 급증, 5xx 급증, Embedding/LLM Circuit Breaker open 상태이다.

* **Trace Propagation:** `X-Trace-Id`를 요청 수신, 성공 응답, 에러 응답, Embedding API 호출, LLM API 호출 전 구간에 동일하게 전파한다.

---

## 5. Acceptance Criteria (DoD)

### 5.1 시나리오 검증

#### POST /api/v1/search

**정상**
* [ ] 유효한 JWT + 유효한 `query` + `scope` 생략 또는 `{}` → 사용자의 전체 READY 영상 대상 하이브리드 검색 수행 → 200 + `search_response_id` + `answer` + `chunks` + `topk_chunk_ids` + `citations` + `used_chunk_ids`
* [ ] `scope: {"video_ids": [...]}` 지정 시 해당 비디오 집합으로 검색 범위가 제한됨을 확인
* [ ] `scope: {"category": "IT"}` 지정 시 해당 카테고리 영상만 검색됨을 확인
* [ ] `scope: {"video_ids": [...], "category": "IT"}` 지정 시 교집합으로 검색됨을 확인
* [ ] `topk_chunk_ids`와 `chunks`가 동일한 최종 집합을 가리키되, `topk_chunk_ids`는 relevance 순서, `chunks`는 video-grouped timeline 순서임을 확인
* [ ] `citations.ref_no`가 `topk_chunk_ids`의 relevance 순서 기준 `1..N`에 대응하고, `chunks` 정렬과 독립적임을 확인
* [ ] `ContextBlock.text`가 `enriched_text` 우선, 없으면 원문 `text` fallback으로 조립됨을 확인
* [ ] 최종 통과 청크가 0개인 경우 → LLM 호출 없이 `200`, `answer="검색 결과가 없습니다"`
* [ ] 최종 통과 청크가 있으나 답변 근거가 부족한 경우 → LLM이 추론으로 메우지 않고 근거 부족을 명시함을 확인
* [ ] 성공 응답 헤더에 `X-Trace-Id`가 echo됨을 확인

**예외**
* [ ] JWT 미제공 → 401
* [ ] JWT 서명 오류 또는 만료 → 401
* [ ] 정규화 후 `query` 길이 2자 미만 → 400
* [ ] `query` 1,000자 초과 → 400
* [ ] `scope: {"all_my_videos": true, "video_ids": [...]}` → 400
* [ ] `scope: {"all_my_videos": true, "category": "IT"}` → 400
* [ ] `scope: {"video_ids": []}` → 400
* [ ] `scope.video_ids` 중 하나라도 타인 소유 또는 미존재 → 404
* [ ] Embedding API 최종 실패 → 503
* [ ] Embedding API가 `embeddings=[]`, 요청 길이 불일치, 비숫자 배열 등 비정상 shape를 반환하면 → `503`
* [ ] retryable LLM adapter 최종 실패 → 503
* [ ] LLM adapter의 non-retryable 오류(`AUTH_ERROR`, `INTERNAL_ERROR`) → 500
* [ ] Embedding Circuit Breaker 개방 상태 → 503
* [ ] LLM Circuit Breaker 개방 상태 → 503

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
* [ ] LLM 응답 `llm_result.text`에서 `used_refs` 파싱 성공 → `citations`와 `used_chunk_ids`가 올바르게 생성됨을 확인
* [ ] `used_refs=[2,2,99,"x"]`가 들어오면 중복/범위 밖/비정수 값이 제거됨을 확인
* [ ] `answer` 본문에 `[1]`, `[2]`가 포함된 경우 `citations.ref_no`가 동일 번호를 가리킴을 확인

**예외**
* [ ] `llm_result.text`에서 `used_refs` 파싱 실패 → `citations=[]`, `used_chunk_ids=[]`, 응답 자체는 200 성공

#### 프롬프트 조립

**정상**
* [ ] 멀티 비디오 검색 시 각 청크가 `ref_no`와 비디오 식별 정보(`video_title` 또는 `video_id`), `start_ms`, `end_ms`를 함께 포함한 형태로 직렬화됨을 확인

### 5.2 검증을 위한 테스팅 전략 (Testing Strategy)

* 단위 테스트와 통합 테스트는 최소 아래 항목을 포함해야 한다.
  * 질의 정규화 및 `scope` 검증
  * FTS/ANN 병합 및 RRF 순위 결정
  * `scope.video_ids + category` 교집합 처리
  * `scope.video_ids`의 404 은닉 정책
  * SOT 서빙 게이트의 READY/DELETING/hard-delete 필터링
  * `topk_chunk_ids`와 `chunks`의 동일 집합/상이한 순서 의미
  * `ref_no`의 relevance 순서 부여와 `chunks` 정렬과의 독립성
  * 임베딩 응답 비정상 shape(`embeddings=[]`, 길이 불일치, 비숫자 배열) 처리
  * `ContextBlock.text`의 `enriched_text` 우선 / `text` fallback 규칙
  * 멀티 비디오 프롬프트 직렬화 시 `ref_no`, 비디오 식별 정보, 타임스탬프 라벨링
  * Empty Result와 근거 부족 응답 구분
  * LLM 선택 메타데이터(`provider_request_id`, `token_usage`, `finish_reason`) 부재 시에도 성공 응답 유지
  * `used_refs` 파싱, 정제, `citations`/`used_chunk_ids` 해석
  * Embedding/LLM timeout, retry, Circuit Breaker 분기
  * `X-Trace-Id` 수신/생성/echo 전파

### 5.3 산출물 (Artifacts)

폴더 구조는 `docs/Tech_Spec/folder_structure.md`를 참조한다.

* [ ] HTTP 라우터 — `POST /api/v1/search`
* [ ] 요청/응답 DTO — `SearchRequest`, `SearchResponse`, `ChunkResult`, `CitationRef`
* [ ] 검색 오케스트레이터 — 하이브리드 검색 파이프라인 전체 흐름
* [ ] RRF 병합 모듈
* [ ] 프롬프트 빌더 — 프롬프트 조립 및 `used_refs` 파싱
* [ ] DB Repository — FTS 조회, ANN 조회, SOT 서빙 게이트 쿼리
* [ ] Embedding HTTP 클라이언트
* [ ] LLMAdapter 추상 클래스 및 구현체
* [ ] JWT 인증 미들웨어
* [ ] 단위 테스트 / 통합 테스트
