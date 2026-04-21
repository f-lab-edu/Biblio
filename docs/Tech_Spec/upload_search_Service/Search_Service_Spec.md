# [Search Service] SPEC

**메타 정보**
- Component ID: `search-service`
- SOT: `docs/system-design.md`
- 관련 문서:
  - `docs/PRD.md`
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Plan.md`
  - `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Pipeline_Worker_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Managed_Embedding_Endpoint_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Feedback_Ingestion_Pipeline_Spec.md`
- Status: Draft

---

## 1. 목적과 범위

### 1.1 한 줄 요약
- Search Service는 인증된 사용자의 단일 프로젝트 범위에서 하이브리드 검색과 RAG 답변 생성을 수행하고, 피드백 검증에 필요한 검색 응답 스냅샷을 단기 보존하는 검색 전담 API다.

### 1.2 책임 경계
- 범위에 포함:
  - JWT 검증, requester 추출, 프로젝트 소유권 확인
  - 프로젝트 검색 노출 상태와 프로젝트 내부 영상 readiness gate 확인
  - query validation, trace id 확정, `req_id` 생성
  - Managed Embedding Endpoint 호출
  - Metadata DB FTS 후보 조회, Vector Store ANN 후보 조회, RRF 병합
  - Metadata DB SOT 게이트를 통한 최종 권한/존재/상태 검증
  - LLM 답변 생성, citation/used chunk 해석, search response 반환
  - `SearchResponseSnapshot` 생성 및 TTL 기반 단기 보존
- 범위에서 제외:
  - 프로젝트/영상 생성, 업로드, 삭제, 재처리
  - 청킹, 임베딩 벡터 생성, Vector Store 적재
  - feedback API 수신, feedback event 발행, raw feedback log 저장
  - rollback 복구 상태 전이와 재임베딩 실행
  - query rewrite, 번역, reranking model, 카테고리별 ranking policy
- 상위 의존성:
  - Client Web UI
  - JWT issuer
  - Core API가 관리하는 project/video metadata
  - Pipeline Worker가 만든 chunk/vector projection
- 하위 소비자:
  - Client Web UI
  - Core API feedback validation path
  - Feedback Ingestion Pipeline이 소비하는 downstream event의 upstream context

### 간단한 흐름 (Simple Flow)
1. 사용자는 소유한 프로젝트를 선택하고 자연어 질의를 보낸다.
2. Search Service는 JWT와 프로젝트 소유권을 확인한다.
3. 선택된 프로젝트가 검색 가능한 상태이고 프로젝트 내부 영상이 모두 `READY`일 때만 retrieval을 시작한다.
4. FTS, ANN, RRF, SOT 게이트를 거쳐 최종 컨텍스트를 확정한다.
5. LLM 답변과 `chunks`를 반환하고, 같은 검색 문맥을 `SearchResponseSnapshot`으로 저장한다.

### 1.3 기술 스택 선택
| 영역 (Area) | 선택안 (Choice) | 왜 이 선택인가 |
| --- | --- | --- |
| Runtime / framework | Python 3.11+, FastAPI async | repo의 API 서비스 패턴과 async DB/HTTP 호출에 맞춘다 |
| Storage / DB | PostgreSQL / SQLAlchemy async | 프로젝트 소유권, FTS, SOT 게이트, snapshot 저장이 Metadata DB에 있다 |
| Vector / ANN | Vector Store projection | chunk vector 후보 조회는 파생 projection에서 수행한다 |
| External inference | Managed Embedding Endpoint, internal LLM adapter | embedding과 answer generation을 서비스 경계 밖 연산으로 분리한다 |
| Key libraries | httpx, PyJWT, google-genai | embedding HTTP 호출, JWT 검증, Vertex AI LLM 연동에 사용한다 |

---

## 2. 계약 (Contracts)

### 2.1 외부 인터페이스

#### 외부 진입 인터페이스
| 인터페이스 | 메서드 / 트리거 | 입력 요약 | 출력 요약 | 인증 / 테넌시 | 비고 |
| --- | --- | --- | --- | --- | --- |
| `/api/v1/projects/{project_id}/search` | `POST` | `query` | `200`, `req_id`, `answer`, `chunks` | JWT requester가 project를 소유해야 함 | 검색 범위는 path project 하나로 고정된다 |

응답 형태:
```json
{
  "req_id": "UUID4",
  "answer": "natural language answer with [1] citations",
  "chunks": [
    {
      "ref": 1,
      "chunk_id": "UUID4",
      "video_id": "UUID4",
      "title": "video title",
      "start_ms": 12000,
      "end_ms": 24000,
      "text": "source chunk text",
      "used": true
    }
  ]
}
```

인터페이스 규칙:
- `query`는 정규화 후 최소 2자, 최대 1,000자여야 한다.
- `X-Trace-Id`가 유효한 UUID이면 그대로 사용하고, 없거나 잘못된 값이면 새 UUID4를 생성한다.
- `chunks`는 SOT 게이트를 통과해 실제 LLM 컨텍스트로 사용된 최종 청크 배열이다.
- `chunks[].ref`는 답변 본문의 `[n]` citation과 같은 요청 단위 번호 체계다.
- `chunks[].used`는 LLM의 structured `used_refs`를 `ref -> chunk_id`로 해석한 결과다.
- Core API feedback 경로는 `SearchResponseSnapshot`에 저장된 서버 측 검색 문맥을 기준으로 검증한다.

#### 외부 연동 컴포넌트 계약
| 의존성 | 사용 목적 | 필요한 동작 / 가정 | 실패 영향 |
| --- | --- | --- | --- |
| Metadata DB `Project` / `Video` | ownership, project serving state, readiness gate | project owner와 project 내부 video 상태를 최신 SOT로 읽을 수 있어야 한다 | 잘못된 검색 허용 또는 검색 차단 |
| Metadata DB `Chunk` | FTS 후보와 최종 SOT 게이트 | `video_id`, text, enriched text, timestamp를 조회할 수 있어야 한다 | false empty 또는 잘못된 근거 반환 |
| Vector Store `VectorIndexEntry` | ANN 후보 조회 | `user_id`, `project_id`, `video_id`, `index_name`, model version metadata를 필터링할 수 있어야 한다 | scope leakage 또는 model/index mismatch |
| Metadata DB `ModelRelease` | serving model/index 조합 결정 | active and optional previous model/index identifiers를 읽을 수 있어야 한다 | 잘못된 인덱스 조회 또는 snapshot context 오류 |
| Managed Embedding Endpoint | query embedding | Search Service가 `ModelRelease`에서 읽은 active/previous `model_version`을 전달하면 해당 버전 기준 query vector를 반환해야 한다 | 검색 요청 실패 |
| LLM provider | final answer generation | provided context 안에서 citation을 포함한 답변을 반환해야 한다 | 답변 생성 실패 |

### 2.2 데이터 계약

#### 소유 데이터
| 엔터티 / 테이블 | 목적 | 핵심 필드 / 불변조건 | 비고 |
| --- | --- | --- | --- |
| `SearchResponseSnapshot` | feedback 검증과 운영 추적용 검색 응답 스냅샷 | `req_id`, `user_id`, `project_id`, `query_text`, `topk_chunk_ids`, `used_chunk_ids`, active model/index fields, `served_vector_paths`, `project_serving_state`, `expires_at` | Search Service가 생성하고 TTL 기반으로 단기 보존한다 |

#### 참조 데이터
| SOT 소유자 | 엔터티 / 테이블 | 의존 필드 | 읽기 전용 가정 |
| --- | --- | --- | --- |
| Core API | `Project` | `id`, `user_id`, `search_serving_state` | `SERVABLE` 프로젝트만 검색 가능하다 |
| Core API / Pipeline Worker | `Video` | `id`, `project_id`, `status`, `title` | 프로젝트 내부 모든 영상이 `READY`일 때만 검색 가능하다 |
| Pipeline Worker | `Chunk` | `id`, `video_id`, `text`, `enriched_text`, `start_ms`, `end_ms` | final context와 사용자 노출 근거의 SOT다 |
| Pipeline Worker | `VectorIndexEntry` | `chunk_id`, `user_id`, `project_id`, `video_id`, `index_name`, `embedding_model_version` | Vector Store는 Metadata DB에서 파생된 projection이다 |
| ML ops | `ModelRelease` | `active_model_version`, `active_index_name`, `previous_model_version`, `previous_index_name` | Search Service는 현재 serving 조합에 맞는 vector paths를 조회한다 |

### 2.3 상태 및 비즈니스 규칙
- 항상 유지되어야 하는 불변조건:
  - 검색 스코프는 `requester_user_id`와 path `project_id`의 교집합이다.
  - FTS, ANN, SOT 게이트는 모두 `user_id`와 `project_id` 필터를 적용한다.
  - 최종 사용자 노출 정합성은 Metadata DB SOT 게이트가 보장한다.
  - 성공 응답은 feedback 검증에 사용할 `SearchResponseSnapshot` 저장과 함께 완료된다.
- 검색 시작 전 gate:
  - project가 없거나 requester 소유가 아니면 검색을 시작하지 않는다.
  - project가 `ROLLBACK_EXCLUDED`이면 검색을 시작하지 않고 복구 중임을 에러 메시지로 고지한다.
  - project 내부 영상이 0개이면 검색을 시작하지 않는다.
  - project 내부 영상 중 하나라도 `READY`가 아니면 검색을 시작하지 않는다.
- 멱등성 규칙:
  - 검색 요청은 read-mostly 독립 요청이며, 매 성공 응답마다 새 `req_id`와 snapshot을 만든다.
  - 동일 query 재요청은 캐시나 dedupe 없이 별도 응답 단위로 취급한다.
- 멀티테넌트 / 인가 규칙:
  - path project의 소유권 검증 실패는 테넌시 위반으로 처리한다.
  - ANN 후보가 Vector Store에서 반환되더라도 SOT 게이트가 project/user scope를 다시 검증한다.

### 2.4 한계와 운영 제약
- 성능 / 지연 목표:
  - PRD 기준 자연어 검색 응답 목표는 5초 이내다.
- Throughput / rate / concurrency 한계:
  - `SEARCH_TOP_K`, `FINAL_TOP_K`, `RRF_K`는 설정값으로 운영한다.
- Payload / 입력 한계:
  - normalized query length: 2..1,000 chars
- Timeout / TTL / retry 제약:
  - embedding/LLM timeout과 retry는 제한된 횟수만 허용하며, degraded answer mode는 두지 않는다.
  - `SearchResponseSnapshot` TTL은 feedback 허용 시간 창보다 짧아서는 안 된다.
- 보안 / 개인정보 제약:
  - query text와 answer context는 사용자 데이터로 취급한다.
  - logs는 `trace_id`, `user_id`, `project_id`, `req_id` 중심으로 남기고 원문 query/answer는 최소화한다.

### 2.5 에러 계약
| 표면 | 조건 | 코드 / 상태 | 재시도 가능 | 비고 |
| --- | --- | --- | --- | --- |
| Search API | invalid query or unsupported body | `400 INVALID_ARGUMENT` | N | retrieval 시작 전 거부 |
| Search API | missing/invalid JWT | `401 UNAUTHENTICATED` | N | 인증 실패 |
| Search API | project ownership violation | `403 FORBIDDEN` | N | 테넌시 위반 |
| Search API | unknown project | `404 NOT_FOUND` | N | 존재하지 않는 프로젝트 |
| Search API | no videos in project | `409 NO_VIDEOS_UPLOADED` | N | project 내부 영상 없음 |
| Search API | project rollback-excluded or any project video not ready | `409 SEARCH_NOT_READY` | N | message에 복구/준비 상태를 구분해 고지 |
| Search API | embedding or retryable LLM failure after retry | `503 SERVICE_UNAVAILABLE` | Y | 외부 inference 일시 장애 |
| Search API | DB error, non-retryable LLM error, invalid required LLM answer block | `500 INTERNAL_ERROR` | N | 운영자 관측 대상 |

- 표준 에러 응답 형태:
```json
{"code":"ERROR_CODE","message":"human-readable summary","trace_id":"UUID4"}
```

---

## 3. 관측성과 운영

- 필수 log field:
  - `trace_id`, `user_id`, `project_id`, `req_id`, `active_model_version`, `active_index_name`
- 추적할 핵심 metric / alert:
  - `search_request_latency_ms`
  - `search_not_ready_count`
  - `search_snapshot_write_fail_count`
  - `embedding_call_fail_count`
  - `llm_call_fail_count`
  - `sot_gate_filtered_count`
- Trace / correlation 전파 규칙:
  - 확정된 `X-Trace-Id`는 Embedding/LLM 호출로 전파하고 성공/에러 응답에도 포함한다.
- Reconciliation / cleanup 요구사항:
  - 만료된 snapshot은 TTL cleanup 또는 storage policy로 제거해야 한다.
  - 반복되는 SOT gate false empty는 projection drift 신호로 취급한다.

---

## 4. 인수 기준

### 4.1 반드시 통과해야 하는 시나리오
- [ ] Search API는 requester 소유 project 하나만 검색 범위로 사용한다.
- [ ] Project 내부 영상이 0개, 미준비 영상 포함, 또는 rollback-excluded 상태이면 retrieval을 시작하지 않고 선언된 409를 반환한다.
- [ ] FTS, ANN, SOT gate는 모두 `user_id`와 `project_id` scope를 강제한다.
- [ ] 성공한 search는 `req_id`, citation-aware `answer`, canonical `chunks`를 반환한다.
- [ ] 성공한 search는 project, query, final chunk ids, used chunk ids, model/index context, expiry를 담은 `SearchResponseSnapshot`을 저장한다.
- [ ] retrieval 이후 결과가 비어 있으면 `200`, `answer="검색 결과가 없습니다"`, `chunks=[]`, snapshot을 반환한다.
- [ ] Embedding/LLM 실패와 잘못된 LLM answer block은 선언된 error contract를 따른다.

### 4.2 비목표 / 보류 항목
- Multi-project search는 이 spec 범위가 아니다.
- Feedback context source는 server-side `SearchResponseSnapshot`이다.
- Search Service는 project/video readiness 또는 rollback exclusion state를 변경하지 않는다.
