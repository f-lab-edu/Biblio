# Managed Embedding Endpoint SPEC

**메타 정보**
- Component ID: `managed-embedding-endpoint`
- SOT: `docs/system-design.md`
- 관련 문서:
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Pipeline_Worker_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Model_Release_and_Reindex_Spec.md`
- Status: Draft

---

## 1. 목적과 범위

### 1.1 한 줄 요약
- Managed Embedding Endpoint는 Search Service와 Pipeline Worker가 지정한 임베딩 모델 버전으로 텍스트를 벡터화하는 내부 추론 서비스다.

### 1.2 책임 경계
- 범위에 포함:
  - 내부 텍스트 임베딩 API
  - active / previous / candidate 모델의 런타임 준비 상태 유지
  - 요청 `texts` 순서를 보존한 dense embedding 반환
  - 모델 버전별 준비 상태와 trace/log 전파
- 범위에서 제외:
  - 사용자 JWT 검증과 프로젝트 테넌시 판정
  - FTS/ANN 조회, RRF, LLM 답변 생성
  - Chunk / VectorIndexEntry 저장
  - `ModelRelease` 갱신과 rollback 정책 결정
- 상위 의존성:
  - Model Release and Reindex가 제공하는 `ModelRelease` 기준 active / previous / candidate 모델 문맥
  - Model Artifact Files
- 하위 소비자:
  - Search Service의 query embedding
  - Pipeline Worker의 chunk embedding과 candidate reindex embedding

### 간단한 흐름 (Simple Flow)
1. Model Release and Reindex는 `ModelRelease`에 맞는 serving model set을 endpoint 런타임에 동기화한다.
2. Endpoint는 모델 artifact를 로드하고 준비된 모델 집합을 노출한다.
3. Search Service와 Pipeline Worker는 요청할 `model_version`과 `texts`를 전달한다.
4. Endpoint는 해당 모델 버전이 ready 상태일 때만 embedding 배열을 반환한다.

### 1.3 기술 스택 선택
| 영역 (Area) | 선택안 (Choice) | 왜 이 선택인가 |
| --- | --- | --- |
| Runtime / framework | FastAPI | 내부 HTTP API와 health/readiness 표면을 단순하게 제공한다 |
| Model runtime | FlagEmbedding / BGE-compatible runtime boundary | dense embedding 요구와 런타임 교체 가능성을 함께 만족한다 |
| Storage / DB | 직접 소유 DB 없음 | release state와 검색 projection은 다른 컴포넌트가 소유한다 |
| Messaging / async | 없음 | 호출자는 동기 HTTP로 embedding 결과를 받는다 |
| Key libraries | pydantic, httpx-compatible contract | 요청/응답 검증과 내부 호출 테스트를 명확하게 만든다 |

---

## 2. 계약 (Contracts)

### 2.1 외부 인터페이스

#### 외부 진입 인터페이스
| 인터페이스 | Method / Trigger | 입력 요약 | 출력 요약 | 인증 / 테넌시 | 비고 |
| --- | --- | --- | --- | --- | --- |
| 텍스트 임베딩 | `POST /embed` | `texts`, `model_version`, 선택적 `trace_id` | 입력 순서를 보존한 `embeddings` | 내부 서비스 인증, 사용자 테넌시 없음 | `model_version`은 ready 모델을 가리켜야 한다 |
| Health / readiness | `GET /health` | empty | 서비스 상태와 ready 모델 버전 목록 | 내부 서비스 인증 | 운영/readiness 확인 전용 |
| Serving model sync | `POST /internal/model-sync` | active 모델과 선택적 previous/candidate 모델 artifact ref | sync 접수 결과와 readiness 요약 | release/reindex 내부 전용 | `ModelRelease`가 SOT다 |

#### `/embed` request / response
```json
{
  "texts": ["string"],
  "model_version": "embedding-model-version",
  "trace_id": "UUID4"
}
```

```json
{
  "embeddings": [[0.0]]
}
```

- `texts`는 비어 있지 않은 문자열을 최소 1개 이상 포함해야 한다.
- Search Service와 Pipeline Worker는 `ModelRelease`에서 active, previous, candidate 문맥을 선택하므로 `model_version`은 필수다.
- 응답은 입력 텍스트마다 정확히 1개의 embedding을 같은 순서로 포함해야 한다.
- 응답 본문은 `model_version`의 SOT가 아니다. 호출자는 자신이 요청한 target model을 이미 알고 있어야 한다.

#### `/health` response
```json
{
  "status": "ok",
  "ready_model_versions": ["active-model-version"]
}
```

- Health는 런타임 준비 상태만 보고한다.
- serving state의 SOT는 여전히 `ModelRelease`다.

#### `/internal/model-sync` request / response
```json
{
  "active": {"model_version": "active-model-version", "artifact_ref": "artifact-uri"},
  "previous": {"model_version": "previous-model-version", "artifact_ref": "artifact-uri"},
  "candidate": {"model_version": "candidate-model-version", "artifact_ref": "artifact-uri"},
  "trace_id": "UUID4"
}
```

```json
{
  "accepted": true,
  "ready_model_versions": ["active-model-version"]
}
```

- `active`는 필수다.
- `previous`와 `candidate`는 release state가 요구할 때만 포함된다.
- sync 응답은 런타임 readiness만 보고하며, `ModelRelease`를 갱신하지 않는다.

#### 외부 연동 컴포넌트 계약
| 의존성 | 사용 목적 | 필요한 동작 / 가정 | 실패 영향 |
| --- | --- | --- | --- |
| Model Release and Reindex | serving model set sync | active는 필수이며 previous/candidate는 release state에 따라 선택적으로 전달된다 | 요청한 모델을 사용할 수 없을 수 있다 |
| Model Artifact Files | 모델 로드 | artifact ref는 로드 가능한 모델 파일을 가리킨다 | 모델 readiness가 실패한다 |
| Search Service | query embedding 호출자 | `ModelRelease`에서 읽은 active와 선택적 previous `model_version`을 전달한다 | 검색 요청 실패 또는 vector path 일부 누락 |
| Pipeline Worker | chunk embedding 호출자 | online ingest target에 맞는 active 또는 candidate `model_version`을 전달한다 | vector projection drift |

### 2.2 데이터 계약

#### 소유 데이터
| Entity / table | 목적 | 핵심 필드 / 불변조건 | 비고 |
| --- | --- | --- | --- |
| Runtime model registry | 프로세스 내부 readiness view | ready model version, role, artifact ref, loaded_at | release sync에서 파생되며 durable SOT가 아니다 |

#### 참조 데이터
| Source owner | Entity / table | 의존 필드 | 읽기 전용 가정 |
| --- | --- | --- | --- |
| Model Release and Reindex | `ModelRelease` | active / previous / candidate model versions | 호출자와 sync trigger는 같은 serving state를 사용한다 |
| Model Artifact Files | model artifact | model version, artifact ref | 한 version의 artifact 내용은 immutable하다 |

### 2.3 상태 및 비즈니스 규칙
- 항상 유지되어야 하는 불변조건:
  - Endpoint runtime state는 `ModelRelease`에서 파생되며 release record를 갱신하지 않는다.
  - 사용자 검색은 active와 previous 모델 버전만 요청할 수 있다.
  - candidate 모델 embedding은 candidate reindex와 evaluation traffic에만 허용된다.
  - `/embed`가 성공하려면 요청한 `model_version`이 ready 상태여야 한다.
  - 하나의 `/embed` 요청은 all-or-nothing으로 처리하며 부분 성공 embedding은 반환하지 않는다.
- 이 컴포넌트가 소유하는 허용 상태 전이:

| From | To | Trigger | Guard / rule | 필요한 side effect |
| --- | --- | --- | --- | --- |
| not loaded | ready | serving model sync + artifact load success | artifact ref가 유효하다 | model version이 readiness에 노출된다 |
| ready | not ready | load failure 또는 runtime eviction | model이 안전하게 서빙될 수 없다 | 해당 모델의 `/embed`는 503을 반환한다 |

- 거부되어야 하는 전이 / invalid condition:
  - 알 수 없거나 not-ready 상태인 `model_version`에 대해 embeddings를 반환하는 동작
  - candidate 모델을 사용자 검색 기본값으로 노출하는 동작
  - 요청 `texts`와 길이 또는 순서가 다른 embeddings를 반환하는 동작
- Idempotency rule:
  - 같은 `texts`와 `model_version`으로 `/embed`를 반복 호출해도 durable side effect는 없다.
  - 같은 model set에 대한 serving model sync는 반복되어도 안전하다.
- Multi-tenant / authorization rule:
  - 이 서비스는 사용자 테넌시 표면을 갖지 않는다.
  - 내부 호출자는 `/embed` 호출 전에 user/project authorization을 강제해야 한다.

### 2.4 한계와 운영 제약
- Performance / latency target:
  - 지연 시간은 model runtime과 batch size에 좌우되며, caller timeout은 Search Service와 Pipeline Worker가 소유한다.
- Payload / file size / pagination limits:
  - 설정된 최대 `texts` 개수, 개별 텍스트 길이, request payload size를 강제한다.
- Timeout / TTL / retry constraints:
  - endpoint는 일시적 runtime unavailability에 대해 retry 가능한 503을 반환한다.
  - retry budget은 caller가 소유한다.
- Security / privacy constraints:
  - 기본적으로 raw text를 로그에 남기지 않는다.
  - 로그에는 `trace_id`, `text_count`, payload size, model version, result status를 포함할 수 있다.

### 2.5 에러 계약
| 표면 | 조건 | Code / status | Retryable | 비고 |
| --- | --- | --- | --- | --- |
| `/embed` | invalid body, empty text, missing `model_version` | 400 `INVALID_ARGUMENT` | N | caller contract 위반 |
| `/embed` | payload too large | 413 `PAYLOAD_TOO_LARGE` | N | caller가 요청을 나누어야 한다 |
| `/embed` | model not ready, load in progress, admission control | 503 `SERVICE_UNAVAILABLE` | Y | caller가 retry budget 안에서 재시도할 수 있다 |
| `/embed` | unexpected invariant failure | 500 `INTERNAL_ERROR` | N | 운영자 조사가 필요하다 |
| `/health` | no ready active model | 503 `SERVICE_UNAVAILABLE` | Y | production traffic을 받으면 안 된다 |

- 표준 에러 응답 형태:
```json
{"code":"ERROR_CODE","message":"human-readable summary","trace_id":"UUID4"}
```

---

## 3. 관측성과 운영

- 필수 로그 필드:
  - `trace_id`, endpoint path, requested `model_version`, `text_count`, result status, latency
- 핵심 지표 / 알림:
  - model version별 embedding request latency
  - model readiness failure
  - model sync failure
  - reason별 503 rate
- Trace / correlation 전파 규칙:
  - caller가 제공한 trace id는 sync, embedding, error log 전 구간에서 보존한다.
- 재조정 / 정리 요구사항:
  - stale candidate model이 serving target으로 남지 않도록 runtime ready model set을 `ModelRelease`와 재조정해야 한다.

---

## 4. 인수 기준

### 4.1 반드시 통과해야 하는 시나리오
- [ ] `POST /embed`는 ready 상태인 `model_version`에 대해 입력 텍스트마다 1개의 embedding을 반환한다.
- [ ] `POST /embed`는 누락, unknown, not-ready 상태의 `model_version`을 거부한다.
- [ ] `ModelRelease` ownership을 바꾸지 않고 active, previous, candidate 모델 readiness를 표현할 수 있다.
- [ ] Search Service는 `ModelRelease`에서 읽은 version으로 active/previous query embedding을 요청할 수 있다.
- [ ] Pipeline Worker는 online ingest와 candidate reindex를 위해 active/candidate chunk embedding을 요청할 수 있다.
- [ ] Error response는 표준 `code`, `message`, `trace_id` shape를 따른다.

### 4.2 비목표 / 보류 항목
- sparse retrieval과 multi-vector output
- user authorization 또는 project-level tenancy check
- release decision, rollback decision, vector index mutation
