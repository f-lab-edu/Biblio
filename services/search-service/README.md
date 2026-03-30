# Search Service

Biblio 플랫폼의 하이브리드 검색 + RAG 응답 생성 서비스.
영상 청크에 대해 FTS(키워드) + ANN(벡터) 병렬 검색 후 RRF 병합, SOT 게이트 검증, LLM 응답 생성까지 수행한다.

**Read-only 서비스** — DB에 쓰기를 수행하지 않으며, 자체 DDL/마이그레이션을 소유하지 않는다.

## API

```
POST /api/v1/search
Authorization: Bearer <JWT>
X-Trace-Id: <UUID4>  (선택, 없으면 서버에서 생성)

{ "query": "검색어" }
```

### 응답 계약

```json
{
  "req_id": "UUID4",
  "answer": "LLM 생성 답변 (인용 [n] 포함)",
  "chunks": [
    {
      "ref": 1,
      "chunk_id": "UUID4",
      "video_id": "UUID4",
      "title": "영상 제목",
      "start_ms": 1000,
      "end_ms": 5000,
      "text": "원본 텍스트",
      "used": true
    }
  ]
}
```

- `chunks[].text`는 원본 텍스트(enriched_text가 아님)
- Client는 `chunks`에서 feedback용 `topk_ids`, `used_ids`를 파생

### Search Readiness 정책 (All-or-Nothing)

| 조건 | 응답 | 설명 |
|------|------|------|
| 업로드 영상 0개 | `409 NO_VIDEOS_UPLOADED` | Embedding/LLM 호출 없음 |
| 영상 1개 이상, READY 아닌 것 존재 | `409 SEARCH_NOT_READY` | Embedding/LLM 호출 없음 |
| 전체 영상 READY, 검색 후 결과 0건 | `200` + 빈 응답 | LLM 호출 없음 |
| 전체 영상 READY, 결과 있음 | `200` + 답변 + chunks | 전체 파이프라인 실행 |

### 에러 코드

| HTTP | Code | 조건 |
|------|------|------|
| 400 | `INVALID_ARGUMENT` | query 길이 위반, 미지원 필드 |
| 401 | `UNAUTHENTICATED` | JWT 누락/만료/위조 |
| 409 | `NO_VIDEOS_UPLOADED` | 영상 0개 |
| 409 | `SEARCH_NOT_READY` | READY 아닌 영상 존재 |
| 500 | `INTERNAL_ERROR` | LLM non-retryable 오류, ANSWER 블록 누락 |
| 503 | `SERVICE_UNAVAILABLE` | Embedding/LLM 일시 장애 |

모든 에러 응답은 `{"code", "message", "trace_id"}` 형식이며 `X-Trace-Id` 헤더를 포함한다.

## 필수 환경 변수

| 변수 | 필수 | 기본값 | 설명 |
|------|------|--------|------|
| `JWT_SECRET_KEY` | Y | - | JWT 서명 검증 키 |
| `DATABASE_URL` | Y | - | PostgreSQL asyncpg 연결 문자열 |
| `EMBEDDING_API_URL` | Y | - | Managed Embedding Endpoint 기본 URL |
| `GCP_PROJECT_ID` | N* | - | Vertex AI 프로젝트 (*gemini provider 사용 시 필수) |
| `GCP_LOCATION` | N | `us-central1` | Vertex AI 리전 |
| `LLM_PROVIDER` | N | `gemini` | `gemini` 또는 `mock` |
| `GEMINI_MODEL_NAME` | N | `gemini-2.0-flash` | Gemini 모델 이름 |
| `SEARCH_TOP_K` | N | `20` | FTS/ANN 각 후보 수 |
| `FINAL_TOP_K` | N | `5` | RRF 병합 후 최종 컨텍스트 수 |
| `RRF_K` | N | `60` | RRF 상수 k |

전체 목록은 `.env.example` 참조.

## 의존 서비스

| 서비스 | 용도 | 조건 |
|--------|------|------|
| **PostgreSQL** | `video`, `chunk`, `vector_index_entry` 읽기 | Core API/Worker가 DDL 소유. pgvector 확장 필수 |
| **Managed Embedding Endpoint** | 쿼리 임베딩 (`POST /embed`) | `{"texts":[...]}` → `{"embeddings":[...]}` 계약 |
| **Vertex AI (Gemini)** | LLM 답변 생성 | ADC 또는 서비스 계정 credential, `GCP_PROJECT_ID`/`GCP_LOCATION` 설정 |

## 로컬 실행

```bash
cd services/search-service
cp .env.example .env    # 환경 변수 편집
poetry install
poetry run uvicorn src.main:create_app --factory --reload --port 8082
```

Health check: `GET /health` → `{"status": "ok"}`

## 테스트

```bash
# 단위 + API 테스트
poetry run pytest

# 커버리지 (80% 기준)
poetry run poe cov

# 통합 테스트 (testcontainers — Docker 필요)
poetry run pytest tests/integration/
```

**통합 테스트 전제:** Docker가 실행 중이어야 하며, testcontainers가 PostgreSQL + pgvector 컨테이너를 자동 관리한다.

## 배포 전 체크

1. 필수 환경 변수 (`JWT_SECRET_KEY`, `DATABASE_URL`, `EMBEDDING_API_URL`) 설정 확인
2. `LLM_PROVIDER=gemini` 사용 시 `GCP_PROJECT_ID`, `GCP_LOCATION`, Vertex AI ADC/서비스 계정 credential 확인
3. 대상 DB에 `video`, `chunk`, `vector_index_entry` 스키마와 pgvector 확장 존재 확인
4. Embedding endpoint 도달 가능 여부 확인
5. Reverse Proxy가 `/api/v1/search`를 Search Service로 라우팅하며 `Authorization`, `X-Trace-Id` 헤더 보존 확인

## 롤백

- **애플리케이션:** 이전 컨테이너 이미지로 즉시 복귀
- **스키마:** Search Service는 DDL을 소유하지 않으므로 별도 스키마 롤백 불필요
- **주의:** 롤백 시에도 Core API feedback 계약 (`req_id`, 파생 `topk_ids`/`used_ids`)은 유지되어야 함
