# [Core API Server] SPEC

**Meta**
* **Component ID:** core-api-server
* **Status:** Draft
* **Version:** v0.2.0
* **Last Updated:** 2026-03-03
* **SOT References:** `docs/system-design.md`, `docs/PRD.md`

---

## 1. Context & Scope

### 1.1 목적 (Purpose)

* **한 줄 요약:** Core API Server는 클라이언트의 진입점으로서 영상 메타데이터를 관리하고, Local File 업로드를 위한 Signed URL 발급 및 완료 신호 수신, 그리고 External URL 인입을 통해 비동기 파이프라인(`PREPROCESS_REQUEST`)을 트리거한다.
* **비즈니스 목표:** 1GB 이상의 대용량 영상 업로드를 안정적으로 접수하고, 후속 파이프라인을 즉시 연결하여 PRD 목표인 "업로드 완료 후 10분 이내 READY 상태 달성"의 첫 단계를 책임진다.

### 1.2 요구 기술 스택 및 환경 변수 (Tech Stack & Configs)

* **언어 및 프레임워크:** Python 3.11+, FastAPI (Async)
* **ORM 및 DB:** SQLAlchemy 2.0 (AsyncSession), PostgreSQL
* **의존성 (External Libs):** `google-cloud-storage` v2.x, `aio-pika` 9.x, `pydantic` v2.x, `apscheduler` 3.x, `alembic` 1.x, `asyncpg` 0.29+, `pydantic-settings` 2.x, `PyJWT` 2.x
* **필수 환경 변수 (`Settings`):** `GCP_PROJECT_ID`, `GCS_VIDEO_BUCKET_NAME`, `JWT_SECRET_KEY`, `DATABASE_URL`, `RABBITMQ_URL`

### 1.3 경계 (Boundaries)

* **In-Scope (책임 범위):**
  * 단일 업로드 엔드포인트에서 `input_type`에 따라 `LOCAL_FILE`과 `EXTERNAL_URL`을 분기하여 처리한다.
  * `Video` 메타데이터를 `PENDING` 상태로 저장하며, `input_type`, `source_url`, `storage_path`를 포함한다.
  * Local File 경로의 Signed URL을 발급하고, 업로드 완료 신호(`/complete`)를 처리한다.
  * 비동기 전처리 요청(`PREPROCESS_REQUEST`)을 발행하고, 누락 건을 Reconciler를 통해 재발행한다.
  * 사용자별 영상 목록을 Opaque Cursor로 조회하고, 단일 영상 상태 조회 및 메타데이터 수정을 지원한다.
  * 영상 삭제 요청을 접수하여 비동기로 위임하고, 피드백 수집 기능을 제공한다(Phase 2).

* **Out-of-Scope (제외 범위):**
  * 플레이어 연동 및 스트리밍 제어는 포함하지 않는다(타임스탬프 클릭을 통한 seek 기능 포함).
  * 미디어 추출, STT, 청킹, 임베딩, Vector Store 적재는 Worker의 책임이다.
  * DB, Object Storage, Vector Store의 하드 삭제 연쇄 제어는 Pipeline Controller의 책임이다.

### 1.4 상태 라이프사이클 기준 (SOT Alignment)

Core API는 `docs/system-design.md`에 정의된 상태 전이를 기준으로 계약을 제공한다.
* 정상 전이: `PENDING -> UPLOADED -> PROCESSING -> READY`
* 예외 전이: `FAILED` (실패 시 `failed_stage`를 기록한다)

---

## 2. Contracts (Interface & Data)

### 2.1 API / Message Endpoint

#### [HTTP API]

* **Auth / Tenancy:** 모든 요청에는 JWT Authorization 헤더가 필수이며, `Depends(get_current_user)`를 통해 추출한 `requester_user_id`를 모든 조회 및 변경 쿼리에 강제 적용한다.

| HTTP Method | Endpoint (URI) | Request | Success Response | Notes |
| --- | --- | --- | --- | --- |
| **POST** | `/api/v1/videos` | `input_type` 판별 유니온<br>`LOCAL_FILE`: `{"title","category","input_type":"LOCAL_FILE","extension"}`<br>`EXTERNAL_URL`: `{"title","category","input_type":"EXTERNAL_URL","source_url"}` | `LOCAL_FILE`: **201** `{"video_id","status":"PENDING","signed_url","expires_at"}`<br>`EXTERNAL_URL`: **202** `{"video_id","status":"PENDING"}` | 단일 엔드포인트에서 분기 처리 |
| **POST** | `/api/v1/videos/{id}/complete` | Optional metadata (`etag`, `size_bytes`) | 최초 성공: **202**<br>이미 `UPLOADED/PROCESSING/READY`: **200** | 중복 완료 신호는 부작용 없이 성공 처리한다 |
| **GET** | `/api/v1/videos` | Query: `?cursor={opaque_token}&limit=20` | **200** `{"items":[...], "next_cursor":"opaque_or_null"}` | 정렬 기준 고정: `(created_at DESC, id DESC)` |
| **GET** | `/api/v1/videos/{id}` | Path `id` | **200** `{"video_id","status","failed_stage",...}` | 테넌시를 강제 적용한다 |
| **PATCH** | `/api/v1/videos/{id}` | `{"title"?, "category"?}` | **200** 갱신된 메타데이터 | 제목 및 카테고리를 수정한다 |
| **DELETE** | `/api/v1/videos/{id}` | Empty | **202** `{"video_id","delete_requested":true}` | Core API는 요청 접수와 비동기 트리거만 수행한다 |
| **POST** | `/api/v1/feedbacks` | Phase 2: `{"video_id","rating","query_text","topk_chunk_ids","cited_chunk_ids"}` | **201** | 피드백을 적재한다 |

* **스키마 제약 조건 (Pydantic 기준):**
  * `video_id`: UUID4 포맷 필수
  * `title`: 1~255자 제한
  * `category`: `GENERAL | IT | MEDICAL | LEGAL` 중 택 1
  * `input_type`: `LOCAL_FILE | EXTERNAL_URL` 중 택 1
  * `source_url`: `input_type=EXTERNAL_URL`일 때 필수이며, `http/https` URL 형식만 허용
  * `extension`: `input_type=LOCAL_FILE`일 때 필수이며, 화이트리스트에 등록된 확장자만 허용
  * `cursor`: Base64URL로 인코딩된 Opaque 토큰 (`{"created_at":"ISO8601","id":"UUID4"}`)
  * `limit`: 기본값 20, 최대 50

#### [Message Broker / 비동기 큐]

* **Broker 인프라:** RabbitMQ
* **Exchange / Topic:** `video_events.topic`, Routing Key: `video.preprocess.requested`, Queue: `PREPROCESS_REQUEST`
* **Message Contract:** `docs/system-design.md` 3.7 MessageEnvelope와 동일한 필드를 사용한다.

```json
{
  "message_type": "PREPROCESS_REQUEST",
  "payload_version": "v1",
  "trace_id": "string (UUID4)",
  "attempt": 1,
  "video_id": "string (UUID4)",
  "issued_at": "ISO8601_TIMESTAMP"
}
```

* `PREPROCESS_REQUEST`는 Envelope 외에 추가 payload 필드가 없다.
* `trace_id`는 API → MQ → Worker 전 구간에서 동일한 값을 유지하여 전달한다.

### 2.2 Data Access (Reads & Writes)

| Type | Store | Entity/Table | Key/Filter | Mutation/Action | Notes |
| --- | --- | --- | --- | --- | --- |
| Read | Metadata DB | Video | `video_id`, `user_id` | SELECT | 테넌시 검증 필수 |
| Read | Metadata DB | Video | `user_id`, `(created_at,id)`, `limit` | Keyset SELECT | Opaque cursor를 decode한 후 복합키를 적용한다 |
| Write | Metadata DB | Video | `video_id` | INSERT | `status=PENDING`, `input_type`, `source_url`, `storage_path`를 저장한다 |
| Write | Object Storage | GCS Bucket | `videos/{user_id}/{video_id}/...` | Signed URL 생성 | `LOCAL_FILE` 요청에만 적용한다 |
| Write | Metadata DB | Video | `video_id`, `user_id` | UPDATE | `/complete` 성공 시 `status=UPLOADED`로 변경한다 |
| Write | Message Broker | PREPROCESS_REQUEST | `video_id` | Publish | 인라인 발행 후 실패 시 재시도한다 |
| Read/Write | Metadata DB + MQ | Video + PREPROCESS_REQUEST | `status=UPLOADED` AND `updated_at <= now()-grace` | Reconciler re-publish | 처리 시작 신호(`PROCESSING`)가 없는 건을 재발행한다 |
| Read/Write | Metadata DB + MQ | Video + PREPROCESS_REQUEST | `input_type=EXTERNAL_URL` AND `status=PENDING` AND `updated_at <= now()-grace` | Reconciler re-publish | External URL의 초기 발행 누락 건을 보정한다 |

### 2.3 SLA & Constraints

* **GCS Signed URL TTL:** 30분
* **최대 파일 크기:** 2GB
* **지원 확장자:** `.mp4`, `.webm`, `.mov`, `.mkv`, `.avi`, `.wmv`
* **목록 페이지 크기:** 기본 20, 최대 50
* **파일 크기 강제 방식 (이중 검증):**
  * 업로드 전: Signed URL 생성 시 `content-length-range` 조건을 설정하여 2GB 초과 업로드를 차단한다.
  * 업로드 후: `/complete`에서 `blob.exists()`와 `blob.size <= 2GB` 조건을 재검증한다.
* **Reconciler 재발행 기준 (grace period):** `updated_at <= NOW() - INTERVAL '5 minutes'` 조건으로 정체 건 판정. 실행 주기는 기본 1분.

### 2.4 Error Contract & Messaging Semantics

| HTTP Status | Error Code | 발생 조건 (When) | 재시도 가능 여부 |
| --- | --- | --- | --- |
| 400 | INVALID_ARGUMENT | 미지원 확장자, 잘못된 `input_type`, 유효하지 않은 `source_url`, cursor decode 실패, 2GB 초과 파일, `/complete` 시 오브젝트 미존재 | N |
| 401 | UNAUTHENTICATED | JWT 미제공 또는 서명/만료 검증 실패 | N |
| 403 | FORBIDDEN | 타 사용자의 `video_id`에 접근한 경우 (Tenancy 위반) | N |
| 404 | NOT_FOUND | 존재하지 않는 `video_id`를 요청한 경우 | N |
| 409 | CONFLICT | 허용되지 않은 상태에서 변경을 요청한 경우 (예: 삭제 진행 중 수정 요청) | N |
| 500 | INTERNAL_ERROR | DB 오류, GCS 호출 오류, MQ 발행이 재시도 후에도 실패한 경우 | Y |

* **에러 응답 바디:** `{"code": "ERROR_CODE", "message": "설명 문자열", "trace_id": "UUID4"}`

* **`/complete` 멱등 응답 규칙:**
  * 최초 유효 처리 시 `202 Accepted`를 반환한다.
  * 동일 `video_id`로 재요청하여 이미 `UPLOADED/PROCESSING/READY` 상태인 경우, `200 OK`를 반환하며 추가 부작용은 발생하지 않는다.

* **MQ 발행 실패 처리 규칙:**
  * 인라인 발행이 실패하면 제한 횟수만큼 재시도한 후 500을 반환한다.
  * Local File 업로드 완료 건은 `status=UPLOADED` 상태를 유지하며 롤백하지 않는다.
  * Reconciler가 주기적으로 누락된 `PREPROCESS_REQUEST`를 재발행하여 최종 정합성을 보정한다.

### 2.5 스키마 (DDL)

> Core API Server가 Alembic으로 관리하는 SOT 테이블. Search Service는 읽기 전용으로 참조한다.

```sql
CREATE TABLE video (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID        NOT NULL,
    title        VARCHAR(255) NOT NULL,
    category     TEXT        NOT NULL CHECK (category IN ('GENERAL','IT','MEDICAL','LEGAL')),
    input_type   TEXT        NOT NULL CHECK (input_type IN ('LOCAL_FILE','EXTERNAL_URL')),
    source_url   TEXT,                         -- EXTERNAL_URL 인입 시만 값 존재
    storage_path TEXT,                         -- GCS 내 객체 키 (videos/{user_id}/{video_id}/original.{ext})
    status       TEXT        NOT NULL DEFAULT 'PENDING'
                             CHECK (status IN ('PENDING','UPLOADED','PROCESSING','READY','FAILED')),
    failed_stage TEXT,                         -- DOWNLOAD|EXTRACT|STT|CHUNKING|EMBEDDING|VECTOR_UPSERT
    deleted      BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_video_user_created ON video(user_id, created_at DESC, id DESC);
CREATE INDEX idx_video_user_status  ON video(user_id, status);
```

---

## 3. Core Design & Logic

### 3.1 주요 흐름 (Sequence)

#### A. Local File Ingest

1. Client가 `POST /api/v1/videos` (`input_type=LOCAL_FILE`) 요청을 보낸다.
2. Core API가 JWT를 검증한 후 `Video(status=PENDING, input_type=LOCAL_FILE)`를 DB에 INSERT한다.
3. Core API가 Signed URL을 발급하고 `201 Created` 응답을 반환한다.
4. Client가 발급받은 Signed URL을 사용하여 GCS에 직접 업로드한다.
5. Client가 업로드 완료 후 `POST /api/v1/videos/{id}/complete`를 호출한다.
6. Core API가 현재 상태를 확인한다:
   * 이미 `UPLOADED/PROCESSING/READY` 상태이면 `200`을 반환한다(멱등 처리).
   * 그 외의 경우 `blob.exists`와 `blob.size`를 검증한다.
7. 검증을 통과하면 `status=UPLOADED`로 UPDATE한다.
8. Core API가 `PREPROCESS_REQUEST` 메시지를 인라인으로 발행 시도한다.
9. 발행에 성공하면 `202 Accepted`를 반환하고, 실패하면 재시도 후 `500`을 반환한다.
10. Reconciler가 `UPLOADED` 상태에서 정체된 건을 주기적으로 감지하여 재발행함으로써 누락을 보정한다.

#### B. External URL Ingest

1. Client가 `POST /api/v1/videos` (`input_type=EXTERNAL_URL`, `source_url` 포함) 요청을 보낸다.
2. Core API가 `Video(status=PENDING, input_type=EXTERNAL_URL, source_url)`를 DB에 INSERT한다.
3. Core API가 `PREPROCESS_REQUEST` 메시지를 즉시 발행 시도한다.
4. 발행에 성공하면 `202 Accepted`를 반환한다(Worker가 해당 URL에서 다운로드를 수행한다).
5. 발행에 실패하면 재시도 후 `500`을 반환하며, Reconciler가 `EXTERNAL_URL + PENDING` 상태의 정체 건을 재발행한다.

#### C. DELETE

1. Client가 `DELETE /api/v1/videos/{id}` 요청을 보낸다.
2. Core API가 JWT 검증 후 `video_id` 테넌시 확인 (타인 소유 → 403, 미존재 → 404).
3. Core API는 `202 Accepted` `{"video_id": "...", "delete_requested": true}`를 반환한다.
4. 실제 연쇄 삭제(DB, GCS, Vector Store)는 Pipeline Controller가 담당한다. Core API는 삭제 트리거 이벤트만 발행한다.

#### D. PATCH

1. Client가 `PATCH /api/v1/videos/{id}` `{"title"?, "category"?}` 요청을 보낸다.
2. Core API가 JWT 검증 후 테넌시 확인 (타인 소유 → 403, 미존재 → 404).
3. `deleted=true` 또는 삭제 진행 중 상태인 경우 → `409 CONFLICT`를 반환한다.
4. `title` 및/또는 `category`를 UPDATE하고 `200 OK`와 갱신된 메타데이터를 반환한다.

### 3.2 상태 전이 (State Machine)

> Core API가 직접 트리거하는 전이만 구현 대상이다. ★ 표시 행은 Worker 주도 전이로, Core API는 DB 상태를 읽을 뿐 이를 직접 트리거하지 않는다.

| From Status | To Status | Actor | Trigger | Guard (조건) | Side Effects |
| --- | --- | --- | --- | --- | --- |
| PENDING | UPLOADED | **Core API** | Local: `/complete` 성공 | `blob.exists && blob.size<=2GB` 검증 필수 | `PREPROCESS_REQUEST` 발행 |
| PENDING | UPLOADED ★ | Worker | External URL 다운로드 완료 | — | Worker가 DB 갱신 |
| UPLOADED | PROCESSING ★ | Worker | PREPROCESS_REQUEST 소비 | Worker가 메시지를 정상 소비한다 | `failed_stage` 초기화 |
| PROCESSING | READY ★ | Worker | 파이프라인 완료 | STT/Chunk/Embedding/DB/Vector 적재 모두 성공 | 검색 가능 상태 |
| PROCESSING | FAILED ★ | Worker | 단계 실패 | 실패를 감지한다 | `failed_stage`, `error_message` 기록 |
| UPLOADED | FAILED ★ | Worker | 초기 단계 실패 | 실패를 감지한다 | `failed_stage` 기록 |
| FAILED | PROCESSING ★ | Worker | 재처리 요청 후 재시작 | 멱등성 체크 통과 | 실패 지점부터 Resume |

### 3.3 멱등성 및 복구 (Resilience)

* **`/complete` 멱등성:**
  * 중복 호출은 200으로 수용하며, DB 상태 변경이나 큐 재발행을 수행하지 않는다.
* **메시지 중복 허용 정책:**
  * Reconciler의 재발행으로 인해 동일 `video_id`에 대한 메시지가 중복될 수 있다.
  * Worker는 상태 및 산출물의 존재 여부를 기준으로 중복 메시지를 안전하게 스킵해야 한다.
* **인라인 발행 + Reconciler 보정:**
  * 발행에 실패하면 즉시 재시도한다(예: 지수 백오프로 최대 3회).
  * 재시도 후에도 실패한 건은 메트릭으로 집계하고, Reconciler가 주기적으로 보정 발행한다(`attempt`를 1 증가시킨다).

### 3.4 Data Consistency & Orphan Prevention

* **트랜잭션 경계:** `Video`의 최초 INSERT와 상태 전이는 각 요청 단위의 트랜잭션으로 커밋한다.
* **업로드 무결성:** Local File은 완료 신호(`/complete`) 시점에 오브젝트 존재 여부와 크기를 재검증한다.
* **Orphan 방지:** 임시 업로드 경로와 중간 산출물은 GCS Lifecycle 정책(연령 기반 자동 정리)을 통해 정리한다.
* **삭제 책임 분리:** Core API는 삭제 요청만 접수(202)하고, 실제 연쇄 삭제는 Pipeline Controller가 수행한다.

---

## 4. Observability & Ops

* **Logging:**
  * 모든 API 요청 및 응답 로그에 `trace_id`, `user_id`, `video_id`(해당하는 경우)를 포함한다.
  * MQ 발행 및 재발행 로그에 `message_type`, `attempt`, `trace_id`를 포함한다.

* **Metrics:**
  * `gcs_signed_url_latency_ms` — Signed URL 발급 지연 시간 (p95 기준)
  * `mq_publish_fail_count` — MQ 인라인 발행 실패 횟수
  * `reconciler_republish_count` — Reconciler 재발행 횟수
  * `complete_idempotent_hit_count` — `/complete` 멱등 처리 횟수
  * `cursor_decode_fail_count` — 커서 디코드 실패 횟수

* **Alerts:** 임계치 정의는 SPEC §4 메트릭 기준으로 인프라팀에 위임한다. 주요 감시 대상: `mq_publish_fail_count` 급증, `reconciler_republish_count` 이상 급증, `/complete` 5xx 비율.

---

## 5. Acceptance Criteria (DoD)

### 5.1 시나리오 검증

* [ ] **Local File 준비 성공:** `POST /videos (LOCAL_FILE)` 호출 시 201 응답과 함께 Signed URL이 반환되고, DB에 `PENDING` 상태의 레코드가 생성되어야 한다.
* [ ] **External URL 준비 성공:** `POST /videos (EXTERNAL_URL)` 호출 시 202 응답이 반환되고, DB에 `PENDING` 상태로 저장되며, `PREPROCESS_REQUEST`가 즉시 발행되어야 한다.
* [ ] **`/complete` 최초 호출:** 오브젝트 존재 여부와 크기 검증을 통과한 경우 202를 반환하고, 상태가 `UPLOADED`로 전이되며 큐 발행이 성공해야 한다.
* [ ] **`/complete` 중복 호출:** 동일 `video_id`로 재요청 시 200을 반환하고, 상태는 변경되지 않으며 메시지가 중복 발행되지 않아야 한다.
* [ ] **MQ 실패 보정:** 인라인 발행이 실패한 이후 Reconciler가 재발행하여 Worker가 처리를 시작할 수 있는 상태에 도달해야 한다.
* [ ] **파일 크기 초과 차단:** 업로드 전후 검증 중 하나라도 실패하면 400으로 요청을 거부해야 한다.
* [ ] **커서 페이지네이션 정합성:** 연속 페이지 조회 시 결과에 중복이나 누락이 없어야 한다.
* [ ] **삭제 요청 위임:** `DELETE /videos/{id}`는 202로 접수하고 삭제 트리거만 수행하며, Core API가 연쇄 하드 삭제를 직접 수행하지 않아야 한다.
* [ ] **테넌시 위반 차단:** 타 사용자의 리소스에 접근하면 403을 반환해야 한다.
* [ ] **미존재 리소스 처리:** 존재하지 않는 `video_id`를 요청하면 404를 반환해야 한다.

### 5.2 검증을 위한 테스팅 전략 (Testing Strategy)

에이전트는 아래 가이드라인을 만족하는 자동화 테스트를 작성해야 한다.
* 테스트 프레임워크는 `pytest`, `pytest-asyncio`, `httpx`를 사용한다.
* DB 통합 테스트는 PostgreSQL 기반의 격리 환경(Testcontainers 또는 Docker Compose)을 사용한다.
* GCS 및 RabbitMQ 등 외부 I/O는 `Mock/AsyncMock` 기반의 단위 테스트로 격리한다.
* Reconciler 로직은 시간 고정(freezegun 등)과 상태별 fixture를 활용하여 재발행 조건을 검증한다.
* Cursor 계약은 encode/decode round-trip 테스트와 잘못된 토큰에 대한 예외 케이스를 포함한다.

### 5.3 산출물 (Artifacts)

* [ ] `src/api/v1/routers/videos.py` — 업로드, 완료, 조회, 수정, 삭제, 피드백을 처리하는 라우터
* [ ] `src/schemas/video_dto.py` — `input_type` 기반 판별 유니온과 cursor DTO를 포함하는 Pydantic 모델
* [ ] `src/services/video_service.py` — 상태 전이, 멱등성 보장, 인라인 MQ 발행 로직을 포함하는 서비스
* [ ] `src/services/video_reconciler.py` — `UPLOADED` 또는 `PENDING(EXTERNAL_URL)` 상태에서 정체된 건을 재발행하는 Reconciler
* [ ] `src/models/video.py` — `Video` 엔티티 및 상태, 입력 타입, 실패 단계 필드를 포함하는 모델
* [ ] `src/infra/gcs_client.py`, `src/infra/rabbitmq_client.py` — GCS 및 RabbitMQ 인프라 어댑터
* [ ] `tests/api/v1/test_videos.py`, `tests/services/test_video_reconciler.py` — 본 DoD 시나리오를 커버하는 테스트
