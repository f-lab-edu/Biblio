# [External AI Adapters] SPEC

**Meta**
- **Component ID:** external-ai-adapters
- **SOT References:** `system-design.md`, `PRD.md`, `Search_Service_Spec.md`, `Pipeline_Worker_Spec.md`, `Managed_Embedding_Endpoint_Spec.md`

---

## 1. Context & Scope

### 1.1 목적 (Purpose)
- **한 줄 요약:** Search Service와 Pipeline Worker가 외부 상용 AI API에 직접 결합되지 않도록, LLM 및 STT 호출을 추상화하고 공통 오류 분류·타임아웃·재시도·서킷 브레이커 정책을 일관되게 적용하는 어댑터 계층이다.
- **비즈니스 목표:** 외부 AI 제공자(Gemini, Google Cloud STT 등)의 SDK 교체나 API 스펙 변경이 상위 서비스의 비즈니스 로직과 프롬프트/파이프라인 로직에 직접 전파되지 않도록 경계를 분리한다.
- **배치 형태:** 별도 네트워크 서비스가 아니라, Search Service 및 Pipeline Worker 프로세스에 in-process library/module 형태로 주입되는 공용 adapter 패키지로 구현한다.

### 1.2 요구 기술 스택 및 환경 변수 (Tech Stack & Configs)
- **언어/런타임:** Python 3.11+, asyncio 기반
- **외부 SDK:**
  - Gemini 계열 LLM SDK
  - `google-cloud-speech` SDK (Google Cloud Speech-to-Text)
- **운영 구현체:**
  - `GeminiLLMAdapter`
  - `GoogleSTTAdapter`
- **테스트 구현체:**
  - `MockLLMAdapter`
  - `MockSTTAdapter`
- **필수 환경 변수:**
  - `GEMINI_API_KEY`
  - `GEMINI_MODEL_NAME`
  - `GCP_PROJECT_ID`
- **설정 주입 및 소유권 정책:**
  - V1에서 provider 선택은 Search Service와 Pipeline Worker가 서비스 wiring 단계에서 직접 수행한다.
  - Search Service는 LLM generation policy(예: `temperature`, `max_output_tokens`)를 소유하며, adapter 생성 시 provider-neutral config/profile 형태로 주입한다.
  - 서비스 wiring 시 주입되는 shared config는 LLM safety setting을 소유하며, adapter는 이를 provider SDK 필드로 매핑만 수행한다.
  - Pipeline Worker는 STT transcription policy(예: `language_hint`, `punctuation`, `diarization`)를 소유하며, adapter 생성 시 provider-neutral config/profile 형태로 주입한다.
  - adapter는 provider SDK 호출 방식, provider별 필드명/enum 매핑, 안전한 fallback default, timeout/retry/circuit breaker 적용 메커니즘만 소유한다.
  - V1의 request method 시그니처는 단순하게 유지하며, per-request profile 선택이나 raw provider 파라미터 pass-through는 허용하지 않는다.
- **호출자 바인딩 정책:**
  - Search Service가 `GeminiLLMAdapter`를 사용할 때의 timeout/retry/circuit breaker 값은 `Search_Service_Spec.md`를 따른다.
  - Pipeline Worker가 `GoogleSTTAdapter`를 사용할 때의 timeout/retry 값은 `Pipeline_Worker_Spec.md`를 따른다.
  - 즉, 이 컴포넌트는 공통 정책 적용 메커니즘을 제공하되, 호출자별 수치는 상위 컴포넌트 스펙이 SOT이다.

### 1.3 경계 (Boundaries)
- **In-Scope:**
  - 외부 LLM/STT 제공자별 SDK 호출 추상화
  - 공통 오류 분류 (`retryable` / `non-retryable`) 및 예외 래핑
  - 호출자별 timeout / retry / circuit breaker 정책 적용
  - `trace_id` 기반 구조화 로깅 및 provider 호출 correlation
  - 로컬/단위 테스트용 mock adapter 제공
- **Out-of-Scope:**
  - 프롬프트 템플릿 작성, `ContextBlock` 조립, `used_refs` 파싱
    - 이는 Search Service의 `prompt_builder` 책임이다.
  - 음성 파일 추출, 오디오 포맷 변환, 청킹, enriched text 생성
    - 이는 Pipeline Worker 책임이다.
  - 임베딩 추론
    - Managed Embedding Endpoint가 별도 책임을 가진다.
  - Vision caption / OCR / scene tag 호출
    - V1에서는 Pipeline Worker 내부 `VisionAdapter` 책임으로 유지한다.
  - 도메인 카테고리 기반 STT adaptation hint 또는 per-request domain hint DTO
    - V1에서는 category를 메타데이터로만 유지하며 adapter 입력 계약에 포함하지 않는다.
  - DB 영속화 및 Object Storage 쓰기
    - 상위 서비스가 책임진다.

### 1.4 상태 라이프사이클 기준
이 컴포넌트는 도메인 엔티티 상태를 직접 소유하지 않는다. 다만 각 provider binding의 adapter runtime instance는 프로세스 로컬 circuit breaker 상태를 가질 수 있다 (§3.2 참조).

---

## 2. Contracts (Interface & Data)

### 2.1 Adapter Interface

#### [LLMAdapter]

Search Service는 `ContextBlock` 목록과 자연어 지침을 조합해 최종 prompt 문자열을 만든 뒤, 이 adapter에 전달한다.  
즉, `ContextBlock` 타입과 `video_title`/`video_id` 라벨 삽입은 Search Service 소유 계약이며, External AI Adapters는 이미 직렬화된 prompt를 입력으로 받는다.

```python
@dataclass
class TokenUsageDTO:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class LLMGenerationResult:
    text: str
    provider_request_id: str | None = None
    token_usage: TokenUsageDTO | None = None
    finish_reason: str | None = None


class LLMAdapter(Protocol):
    async def generate(
        self,
        *,
        prompt: str,
        trace_id: str,
    ) -> LLMGenerationResult:
        """
        외부 LLM API를 호출하여 provider-neutral 결과 객체를 반환한다.
        `text`에는 답변 본문과 structured {"used_refs": [...]} 블록이 포함될 수 있다.
        adapter는 이 값을 파싱하거나 수정하지 않는다.
        """
```

- **입력 제약:**
  - `prompt`는 Search Service가 완성한 provider-neutral prompt 문자열이다.
  - adapter는 prompt 내용을 재작성하지 않는다.
  - adapter는 query rewrite, citation 복구, post-processing을 수행하지 않는다.
- **출력 제약:**
  - `LLMGenerationResult.text`는 provider raw text를 최대한 보존한 문자열이어야 하며, 빈 문자열 또는 공백-only 문자열이면 안 된다.
  - `{"used_refs": [...]}` 블록이 포함되어 있으면 `text` 안에 그대로 유지되어 Search Service가 후속 파싱할 수 있어야 한다.
  - `provider_request_id`, `token_usage`, `finish_reason`은 운영/관측용 선택 메타데이터이며, provider가 값을 주지 않으면 `None`을 허용한다.
  - adapter는 형식 안정성을 위해 provider의 structured output / JSON mode / response schema 기능을 내부 구현으로 사용할 수 있으나, 호출자에게 provider-specific 모드를 노출하지 않는다.

#### [STTAdapter]

```python
@dataclass
class TranscriptSegmentDTO:
    text: str
    start_ms: int
    end_ms: int


@dataclass
class STTTranscriptionResult:
    segments: list[TranscriptSegmentDTO]
    stt_model_version: str


class STTAdapter(Protocol):
    async def transcribe(
        self,
        *,
        audio_path: str,
        trace_id: str,
    ) -> STTTranscriptionResult:
        """
        로컬 오디오 파일을 외부 STT API로 전송하여
        타임스탬프를 포함한 세그먼트 목록과 STT 모델 버전을 반환한다.
        """
```

- **입력 제약:**
  - `audio_path`는 Pipeline Worker가 생성한 로컬 파일 경로이다.
  - V1에서 입력 오디오 포맷은 `mono, 16kHz, 16-bit PCM, FLAC`으로 고정한다.
  - adapter는 오디오 포맷 변환을 수행하지 않는다. 입력 검증 실패는 `INVALID_REQUEST`로 처리한다.
  - V1에서 `category` 또는 별도 domain hint는 adapter request contract에 포함하지 않는다.
- **출력 제약:**
  - `STTTranscriptionResult.segments`는 `start_ms ASC`로 정렬되어야 한다.
  - 각 항목은 `0 <= start_ms <= end_ms`를 만족해야 한다.
  - 빈 세그먼트 목록은 허용된다. 후속 청킹/실패 판단은 Pipeline Worker가 담당한다.
  - `stt_model_version`은 provider가 실제로 사용한 모델 식별자의 공식 모델명 이어야 한다.
  - Pipeline Worker는 이 값을 `TranscriptSegment.stt_model_version`에 그대로 영속화한다.

### 2.2 Data Access (Reads & Writes)

| Type | Store | Entity/Table | Key/Filter | Mutation/Action | Notes |
| --- | --- | --- | --- | --- | --- |
| Read | Local Filesystem | 로컬 오디오 파일 | `audio_path` | READ | `GoogleSTTAdapter` 입력 파일. 파일 존재 여부와 접근 가능성만 확인한다. |
| Call | External LLM API | Gemini provider | model name, prompt | GENERATE | 네트워크 호출. DB 영속화 없음. |
| Call | External STT API | Google Cloud Speech-to-Text | project context, audio bytes | RECOGNIZE | 네트워크 호출. DB 영속화 없음. |

> 이 컴포넌트는 SOT 테이블을 소유하지 않으며, Metadata DB / Vector Store / Object Storage에 직접 쓰지 않는다.

### 2.3 SLA & Constraints

#### LLM 호출 바인딩 (Search Service)

| Caller | Adapter | Timeout | Retryable 조건 | Max Retries | Circuit Breaker | 최종 실패 |
| --- | --- | --- | --- | --- | --- | --- |
| Search Service | `GeminiLLMAdapter` | `LLM_TIMEOUT_SEC` (default: `3`) | timeout, 429, 503 | `LLM_MAX_RETRIES` (default: `1`) | `LLM_CB_FAILURE_THRESHOLD` / `LLM_CB_RECOVERY_SEC` (default: `3` / `30`) | retryable 최종 실패는 Search `503`, non-retryable 최종 실패는 Search `500` |

- `GeminiLLMAdapter`는 자체 대기열을 두지 않는다.
- retry와 circuit breaker는 adapter 내부에서 적용하되, 값의 SOT는 Search Service spec이다.
- Search Service는 배포 단위 고정 generation profile을 wiring 시점에 주입하며, V1에서는 per-request profile switching을 하지 않는다.
- final failure 시 adapter는 retryable 여부가 보존된 예외를 호출자에게 전달한다.
- 성공 시 adapter는 `LLMGenerationResult`를 반환하며, Search Service는 `text`를 기능 처리에 사용하고 선택 메타데이터는 로깅/메트릭에만 사용한다.

#### STT 호출 바인딩 (Pipeline Worker)

| Caller | Adapter | Timeout | Retryable 조건 | Max Retries | Circuit Breaker | 최종 실패 |
| --- | --- | --- | --- | --- | --- | --- |
| Pipeline Worker | `GoogleSTTAdapter` | `STT_TIMEOUT_SEC` (default: `120`) | timeout, 429, 503, 일시적 네트워크 오류 | `MAX_RETRIES` (default: `3`) | V1에서는 필수 아님 | Pipeline Worker가 `FAILED`/재시도 정책으로 해석 |

- `GoogleSTTAdapter`는 caller가 지정한 retry budget 안에서만 재시도한다.
- V1에서는 STT용 별도 circuit breaker를 필수 계약으로 두지 않는다. 필요한 경우 호출자 wiring에서 옵션으로 추가할 수 있다.
- provider raw transcript를 그대로 노출하지 않고, `STTTranscriptionResult`로 정규화된 결과만 반환한다.
- `stt_model_version`은 실제 사용된 모델의 공식 식별자여야 하며, 값을 모를 경우 빈 문자열이나 `unknown` 같은 임시값으로 대체하지 않는다. 확인할 수 없으면 `INTERNAL_ERROR`로 처리한다.
- Pipeline Worker는 배포 단위 고정 transcription profile을 wiring 시점에 주입하며, V1에서는 category 기반 STT adaptation을 사용하지 않는다.

#### 공통 제약

- adapter는 입력 텍스트 정규화를 수행하지 않는다.
  - query normalization은 Search Service 책임이다.
  - `enriched_text` 정규화는 Pipeline Worker 및 Managed Embedding Endpoint 호출자 책임이다.
- V1 adapter request 시그니처에는 raw provider 파라미터, absolute deadline, cancellation token을 포함하지 않는다.
- adapter는 응답 원문을 일반 운영 로그에 기록하지 않는다.
- adapter는 내부 큐 기반의 hidden buffering을 두지 않는다. 과부하/불능 상태는 빠르게 surface한다.

### 2.4 Error Contract & Messaging Semantics

이 컴포넌트는 외부 공개 HTTP API를 직접 노출하지 않는다. 따라서 상위 서비스의 `{"code","message","trace_id"}` HTTP envelope를 직접 생성하지 않는다. 대신 아래의 구조화된 내부 예외 계약을 제공하며, 호출자가 이를 HTTP/MQ 오류 의미로 매핑한다.

```python
@dataclass
class ExternalAIAdapterError(Exception):
    code: str
    message: str
    trace_id: str
    provider: str
    retryable: bool
```

| Error Code | 발생 조건 | Retryable | Caller-side 예시 매핑 |
| --- | --- | --- | --- |
| `INVALID_REQUEST` | caller가 adapter contract를 위반한 입력, 파일 없음, 로컬 입력 검증 실패 | N | Worker `FAILED`, Search는 내부 오류로 취급 금지 |
| `AUTH_ERROR` | provider 인증 실패, credential/project/model 접근 권한 오류 | N | Search `500 INTERNAL_ERROR`, Worker `FAILED` |
| `TIMEOUT` | provider 응답 timeout | Y | Search `503`, Worker 재시도 |
| `RATE_LIMITED` | provider 429 | Y | Search 재시도 후 `503`, Worker 재시도 |
| `UNAVAILABLE` | provider 503/일시적 장애 | Y | Search `503`, Worker 재시도 |
| `CIRCUIT_OPEN` | circuit breaker open 상태 | Y | Search `503`, Worker 재시도 가능 |
| `INTERNAL_ERROR` | adapter 코드 결함, invariant 위반, 비정상 provider 응답, 내부 policy/config 매핑 오류 | N | Search `500 INTERNAL_ERROR`, Worker `FAILED` |

- `GeminiLLMAdapter`는 `used_refs` 파싱 실패를 이 컴포넌트 오류로 간주하지 않는다. 파싱은 Search Service 책임이다.
- `GeminiLLMAdapter`는 `used_refs`의 semantic correctness를 검증하지 않으며, citation integrity 관측은 Search Service 책임이다.
- `GoogleSTTAdapter`는 provider 응답을 `TranscriptSegmentDTO`로 해석할 수 없는 경우 `INTERNAL_ERROR`를 raise한다.
- provider가 4xx를 반환하더라도 원인이 adapter 내부 policy/config 매핑 결함이면 `INVALID_REQUEST`가 아니라 `INTERNAL_ERROR`로 분류한다.
- 에러 상세(`provider_status`, `raw_reason`, `failed_index` 등)는 API 계약으로 노출하지 않고 구조화 로그에만 남긴다.

---

## 3. Core Design & Logic

### 3.1 주요 흐름 (Sequence)

#### Search Service -> `GeminiLLMAdapter`

1. Search Service `prompt_builder`가 최종 prompt 문자열을 조립한다.
2. `GeminiLLMAdapter.generate(prompt, trace_id)`를 호출한다.
3. adapter는 timeout / retry / circuit breaker 정책을 적용하여 Gemini API를 호출한다.
4. 성공 시 `LLMGenerationResult`를 반환한다. `text`에는 raw provider text가 보존되며, 선택 메타데이터(`provider_request_id`, `token_usage`, `finish_reason`)는 제공 가능할 때만 채워진다.
5. Search Service가 `llm_result.text`에서 `used_refs`를 파싱하고 `citations`, `used_chunk_ids`를 생성한다. 선택 메타데이터는 caller-side logging/metrics에만 사용한다.

#### Pipeline Worker -> `GoogleSTTAdapter`

1. Pipeline Worker가 로컬 오디오 파일을 준비한다.
2. `GoogleSTTAdapter.transcribe(audio_path, trace_id)`를 호출한다.
3. adapter는 파일 존재 여부와 기본 접근 가능성을 확인한다.
4. timeout / retry 정책을 적용하여 Google Cloud STT API를 호출한다.
5. provider 응답을 `STTTranscriptionResult`로 정규화하고, 세그먼트를 `start_ms ASC`로 정렬하며 `stt_model_version`을 채운다.
6. Pipeline Worker가 `segments`를 `TranscriptSegment` 테이블에 bulk insert하고, `stt_model_version`을 함께 영속화한 뒤 후속 청킹을 수행한다.

### 3.2 상태 전이 (State Machine)

> 도메인 엔티티 상태가 아니라 adapter 인스턴스의 circuit breaker 상태를 설명한다.

| From | To | Actor | Trigger | Guard | Side Effects |
| --- | --- | --- | --- | --- | --- |
| `CLOSED` | `OPEN` | Adapter runtime | 연속 실패 임계치 초과 | caller binding에 circuit breaker가 configured | 신규 호출 즉시 `CIRCUIT_OPEN` 예외 |
| `OPEN` | `HALF_OPEN` | Adapter runtime | recovery time 경과 | caller binding에 circuit breaker가 configured | probe 호출 1건 허용 |
| `HALF_OPEN` | `CLOSED` | Adapter runtime | probe 성공 | N/A | 연속 실패 카운터 초기화 |
| `HALF_OPEN` | `OPEN` | Adapter runtime | probe 실패 | N/A | recovery timer 재시작 |

> `GoogleSTTAdapter`는 V1에서 circuit breaker가 필수가 아니므로, 이 기능이 설정된 경우에만 위 상태 전이를 적용한다.
> V1 circuit breaker 상태는 프로세스 로컬 상태이며, replica 간 공유 breaker는 범위 밖이다.

### 3.3 멱등성 및 복구 (Resilience)

- 이 컴포넌트는 DB에 쓰지 않으므로, 자체 멱등 키나 resume state를 소유하지 않는다.
- retry는 adapter 내부의 provider 호출 재시도만 의미한다. 상위 서비스의 HTTP 재시도/MQ 재처리와는 별개다.
- `GeminiLLMAdapter`는 overload 상황을 숨기기 위한 내부 대기열을 두지 않는다.
- `GoogleSTTAdapter`는 오디오 파일을 수정하거나 재인코딩하지 않는다.
- `MockLLMAdapter`와 `MockSTTAdapter`는 같은 입력에 대해 항상 같은 테스트 응답을 반환해야 하며, 네트워크를 사용하지 않는다.
- `video_id` 단위의 영속 재시도 총량 제한은 adapter가 아니라 caller/SOT 레이어의 책임이며, V1 adapter contract에는 포함하지 않는다.

### 3.4 Data Consistency & Orphan Prevention

- 이 컴포넌트는 외부 provider 호출 결과를 직접 영속화하지 않는다.
- 로컬 오디오 파일의 생성/삭제 책임은 Pipeline Worker에 있다.
- LLM 결과 `text`의 저장/파싱 책임은 Search Service에 있다.
- 따라서 orphan data 방지는 상위 서비스 경계에서 수행하며, 이 컴포넌트는 불완전한 외부 응답을 즉시 예외로 승격하여 잘못된 데이터 적재를 방지한다.

---

## 4. Observability & Ops

- **Logging:**
  - 모든 구조화 로그에 `trace_id`, `adapter_type`, `provider`, `latency_ms`, `retry_count`, `final_error_code`를 포함한다.
  - LLM adapter는 `model_name`을 로그에 포함하며, 값이 있으면 `provider_request_id`, `finish_reason`, `token_usage` 요약도 함께 기록한다.
  - STT adapter는 `audio_path` 원문 대신 파일 존재 여부, 파일 크기, `audio_duration_sec`, 호출 결과 세그먼트 수를 기록한다.
  - prompt 원문, transcript 원문, provider raw payload는 일반 운영 로그에 기록하지 않는다.
- **Metrics:**
  - `external_ai_adapter_call_latency_ms{adapter_type,provider}`
  - `external_ai_adapter_call_count{adapter_type,provider,status}`
  - `external_ai_adapter_retry_count{adapter_type,provider}`
  - `external_ai_adapter_circuit_breaker_state{adapter_type,provider}`
  - `external_ai_adapter_llm_input_tokens_total{provider,model_name}`
  - `external_ai_adapter_llm_output_tokens_total{provider,model_name}`
  - `external_ai_adapter_llm_total_tokens_total{provider,model_name}`
  - `external_ai_adapter_stt_audio_duration_sec_total{provider}`
  - `external_ai_adapter_stt_billed_audio_sec_total{provider}` (provider가 billed duration을 제공하는 경우에만)
- **Alerts:** 임계치는 인프라/운영 환경에서 결정한다. 최소 감시 대상은 timeout 급증, retryable failure rate 급증, circuit breaker open 지속 시간이다.
- **Trace Propagation:**
  - adapter는 호출자에게서 받은 `trace_id`를 변경하지 않는다.
  - provider SDK가 사용자 정의 correlation field를 허용하면 동일 `trace_id`를 함께 전달한다.
  - provider가 이를 지원하지 않더라도 local logs와 caller logs에서 같은 `trace_id`를 유지해야 한다.
- **Caller-side Observability Boundary:**
  - `used_refs` 파싱 성공 여부와 citation integrity 저하 관측은 이 컴포넌트 책임이 아니며, Search Service가 caller-side observability로 수집한다.
- **Credentials & Rotation:**
  - API key와 provider credential은 프로세스 시작 시 로드되며, rotation 반영에는 재배포 또는 재시작이 필요하다.
  - hot reload는 V1 범위에 포함하지 않는다.

---

## 5. Acceptance Criteria (DoD)

### 5.1 시나리오 검증

#### `GeminiLLMAdapter.generate()`

**정상**
* [ ] Search Service가 조립한 prompt를 입력받아 Gemini API 호출 후 `LLMGenerationResult`를 반환한다.
* [ ] `LLMGenerationResult.text`는 non-empty 문자열이며, raw text 안의 `{"used_refs": [1, 2]}` JSON 블록을 adapter가 손상시키지 않고 그대로 전달한다.
* [ ] `provider_request_id`, `token_usage`, `finish_reason`은 provider가 값을 주지 않으면 `None`이어도 성공으로 처리한다.
* [ ] 일반 운영 로그에 prompt 원문이 기록되지 않는다.

**예외**
* [ ] timeout 발생 시 Search binding 기준으로 최대 `LLM_MAX_RETRIES`회 재시도 후 실패 시 retryable `ExternalAIAdapterError(code="TIMEOUT")`를 raise한다.
* [ ] 429 발생 시 retryable `ExternalAIAdapterError(code="RATE_LIMITED")`를 raise한다.
* [ ] 503 발생 시 retryable `ExternalAIAdapterError(code="UNAVAILABLE")`를 raise한다.
* [ ] circuit breaker open 상태에서는 provider 호출 없이 즉시 `CIRCUIT_OPEN` 예외를 raise한다.
* [ ] provider 인증 실패, credential/project/model 접근 권한 오류는 `AUTH_ERROR`를 raise한다.
* [ ] provider 응답 구조를 해석할 수 없거나 `text`가 빈 문자열/공백-only 문자열이면 `INTERNAL_ERROR`를 raise한다.
* [ ] provider가 4xx를 반환해도 원인이 내부 policy/config 매핑 결함이면 `INTERNAL_ERROR`를 raise한다.

#### `GoogleSTTAdapter.transcribe()`

**정상**
* [ ] 로컬 FLAC 오디오 파일을 입력받아 `STTTranscriptionResult`를 반환한다.
* [ ] 반환 결과는 `STTTranscriptionResult`이며, `stt_model_version`이 비어 있지 않다.
* [ ] 반환 세그먼트는 `start_ms ASC`로 정렬되어 있다.
* [ ] 일반 운영 로그에 transcript 원문이나 오디오 바이트가 기록되지 않는다.

**예외**
* [ ] `audio_path`가 없거나 읽을 수 없으면 `INVALID_REQUEST` 예외를 raise한다.
* [ ] timeout 발생 시 Pipeline Worker binding 기준으로 재시도 후 retryable `TIMEOUT` 예외를 raise한다.
* [ ] 429 또는 503 발생 시 retryable `RATE_LIMITED` / `UNAVAILABLE` 예외를 raise한다.
* [ ] provider 응답의 타임스탬프를 `TranscriptSegmentDTO`로 변환할 수 없으면 `INTERNAL_ERROR`를 raise한다.
* [ ] `stt_model_version`을 확정할 수 없으면 `INTERNAL_ERROR`를 raise한다.

#### Mock adapters

**정상**
* [ ] `MockLLMAdapter`는 지정된 고정 `LLMGenerationResult`를 deterministic하게 반환한다.
* [ ] `MockSTTAdapter`는 지정된 세그먼트 목록과 `stt_model_version`을 deterministic하게 반환한다.
* [ ] mock 구현체는 네트워크나 provider SDK를 호출하지 않는다.

### 5.2 검증을 위한 테스팅 전략 (Testing Strategy)

에이전트는 아래 가이드라인을 만족하는 자동화 테스트를 작성해야 한다.
* 테스트 프레임워크는 `pytest`, `pytest-asyncio`를 사용한다.
* **커버리지 목표:** 단위 테스트 중심으로 adapter 예외 분류와 retry/circuit breaker 분기를 충분히 커버한다.
* **외부 의존성 격리 전략:**
  * Gemini SDK 호출은 `AsyncMock` 또는 provider client test double로 대체한다.
  * Google Cloud STT SDK 호출은 `AsyncMock` 또는 provider client test double로 대체한다.
  * mock adapter 테스트는 실제 네트워크 호출 없이 수행한다.
* **필수 분기:**
  * LLM 선택 메타데이터 누락(`provider_request_id=None`, `token_usage=None`, `finish_reason=None`) 성공 처리
  * LLM `text` 빈 문자열/공백-only 처리
  * provider 인증 실패(`AUTH_ERROR`)와 retryable 오류 구분
* Search Service와 Pipeline Worker는 이 spec을 소비하는 쪽이므로, cross-component 통합 테스트에서는 각 caller가 adapter 예외를 자신의 오류 계약으로 올바르게 변환하는지 별도 검증한다.

### 5.3 산출물 (Artifacts)

폴더 구조는 `docs/Tech_Spec/folder_structure.md`를 참조한다.

* [ ] `LLMAdapter`, `STTAdapter` 추상 인터페이스
* [ ] `LLMGenerationResult`, `TokenUsageDTO`
* [ ] `ExternalAIAdapterError` 및 공통 오류 코드 정의
* [ ] `GeminiLLMAdapter`
* [ ] `GoogleSTTAdapter`
* [ ] `MockLLMAdapter`, `MockSTTAdapter`
* [ ] adapter runtime policy 적용 레이어 (timeout / retry / circuit breaker)
* [ ] 단위 테스트
