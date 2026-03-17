# [Media & AI Pipeline Worker] SPEC

**Meta**
* **Component ID:** pipeline-worker
* **SOT References:** `system-design.md`, `PRD.md`, `SD-02-Media Processing & AI Indexing.png`, `ADR-003-video-search-retrieval-strategy.md`, `ADR-004-chunking-strategy.md`

---

## 1. Context & Scope

### 1.1 목적 (Purpose)
* **한 줄 요약:** Core API Server로부터 비디오 처리 이벤트를 비동기로 수신하여, 비디오 다운로드, 오디오 추출, 텍스트 변환(STT), 문장 경계+길이 제한+overlap 기반 청킹, enriched text 전처리 및 Embedding 파이프라인을 실행하고 결과를 SOT(Metadata DB) 및 Vector Store에 적재하는 백그라운드 워커 컴포넌트이다.
* **비즈니스 목표:** 업로드된 비디오의 메타데이터와 컨텍스트 기반 청크 벡터 데이터를 비동기적으로 생성하여, 하이브리드 검색의 기반 데이터를 안정적으로 공급한다. 대용량 미디어 처리 및 외부 AI API 호출에 따른 지연과 실패를 시스템 전반에 전파시키지 않는다(격리).

### 1.2 요구 기술 스택 및 환경 변수 (Tech Stack & Configs)
* **언어/프레임워크:** Python 3.11+, asyncio 기반
* **로깅 라이브러리:** `loguru`를 표준 로깅 라이브러리로 사용한다. 워커의 구조화 로그와 예외 로깅은 기본 `logging` 직접 사용보다 `loguru` 기반 설정을 우선한다.
* **메시지 브로커:** PGMQ (기본 구현체, asyncpg 기반). PostgreSQL을 큐 스토리지로 사용하며 DATABASE_URL을 공유한다. 인터페이스 추상화를 통해 다른 구현체로 교체 가능하다.
* **DB 및 벡터 저장소:** PostgreSQL (`pgvector` 확장 사용), Object Storage (GCS 기본 구현체)
* **외부 API 및 라이브러리:**
  * FFmpeg (미디어 전처리, 오디오 추출). 오디오 출력 포맷 고정: `mono, 16kHz, 16-bit PCM, FLAC`
  * External AI Adapters:
    * Google Cloud Speech-to-Text (`google-cloud-speech` SDK - STT 전용)
    * Managed Embedding Endpoint (텍스트 -> 벡터 치환)
  * **VisionAdapter** 추상 인터페이스 (Pipeline Worker 내부 인터페이스로 정의, BrokerClient/StorageClient와 동일한 DI 패턴):
    * `VisionAdapter` — 추상 클래스. `extract_caption()`, `extract_ocr()`, `extract_scene_tags()` 메서드를 정의한다.
    * `MockVisionAdapter` — 로컬/단위 테스트 전용 구현체 (고정 응답 반환)
* **필수 환경 변수:** `BROKER_TYPE`, `DATABASE_URL`, `GCP_PROJECT_ID`, `GCS_VIDEO_BUCKET_NAME`, `EMBEDDING_API_URL`
* **선택 환경 변수:** `WORKER_CONCURRENCY` (default: 4), `MAX_RETRIES` (default: 3), `DOWNLOAD_TIMEOUT_SEC` (default: 60), `STT_TIMEOUT_SEC` (default: 120), `STT_MODEL_VERSION` (default: ""), `VISION_TIMEOUT_SEC` (default: 15), `EMBEDDING_TIMEOUT_SEC` (default: 10), `EMBEDDING_BATCH_SIZE` (default: 16), `CHUNK_MAX_TOKENS` (default: 300), `CHUNK_OVERLAP_SENTENCES` (default: 1)
* **BrokerClient 인터페이스:** `consume()` 및 `ack()` 메서드를 가진 추상 클래스를 정의한다. 구현체는 의존성 주입(DI)으로 교체 가능하다.
  * `PGMQBrokerClient` — 운영 환경 구현체 (asyncpg 기반)
  * `InMemoryBrokerClient` — 로컬/단위 테스트 전용 구현체
* **StorageClient 인터페이스:** `download_object()`, `upload_object()`, `delete_object()` 메서드를 가진 추상 클래스를 정의한다.
  * `GCSStorageClient` — 운영 환경 구현체 (google-cloud-storage 기반)
  * `InMemoryStorageClient` — 로컬/단위 테스트 전용 구현체

### 1.3 경계 (Boundaries)
* **In-Scope (책임):**
  * 메시지 브로커 이벤트 구독 (비디오 프로세싱 요청)
  * 비디오 다운로드 및 임시 저장, FFmpeg 기반 오디오 추출
  * Google Cloud Speech-to-Text(`GoogleSTTAdapter`) 연동 (오디오 STT 변환)
  * 문장 경계+길이 제한+overlap 기반 청킹 
  * VisionAdapter를 통한 enriched text 전처리 — caption/OCR/scene tag 추출 및 chunk text merge 
  * Embedding Endpoint 연동 (enriched text의 Vector화)
  * 처리 결과 SOT 업데이트 (Postgres: `Video` 상태 변경, `Chunk`, `VectorIndexEntry` Insert)
  * 예외 처리, 재시도 제어 및 실패 상태 기록 정책 적용
* **Out-of-Scope (권한 밖):**
  * 사용자 UI 통신 및 동기적 에러 반환.
  * 검색을 위한 Vector Query (Search Service가 전담).
  * Video 도메인의 상태를 제외한 회원/결제 등 타 도메인 데이터 조작.

### 1.4 상태 라이프사이클 기준
파이프라인 워커는 메시지 구독 시점부터 처리 완료 시까지 SOT의 Video 상태를 전이할 의무를 가진다.
* **시작 전이:** `PENDING/UPLOADED` -> `PROCESSING`
* **정상 완료 전이:** `PROCESSING` -> `READY` (이 상태 도달 시 Search Service 노출 가능)
* **예외 롤백 전이:** `PROCESSING` -> `FAILED` (최대 재시도 초과 또는 치명적 에러 발생 시 기록 후 메시지 Ack)

---

## 2. Contracts (Interface & Data)

### 2.1 API / Message Endpoint
워커는 HTTP 서버가 아니며 메시지 브로커의 토픽/큐를 구독하는 Consumer로 동작한다.

* **Message Contract (Payload 스키마)**

| Queue/Topic | Message Type | Payload (요약) | 동작 |
| --- | --- | --- | --- |
| 고정 큐명: `PREPROCESS_REQUEST` | `PREPROCESS_REQUEST` | `{"message_type": "PREPROCESS_REQUEST", "payload_version": "v1", "trace_id": "UUID", "attempt": 1, "video_id": "UUID", "issued_at": "ISO8601"}` | 파이프라인 구동 시작 |
| 고정 큐명: `DELETE_REQUEST` | `DELETE_REQUEST` | 동일 MessageEnvelope (message_type="DELETE_REQUEST") | 연쇄 삭제 실행 |

> **참고:** `user_id`, `storage_path` 등 상세 데이터는 payload에 포함하지 않는다. 워커가 진입 시 `video_id`를 키로 Metadata DB를 직접 조회하여 획득한다. (`system-design.md §3.7`)

### 2.2 Data Access (Reads & Writes)
| Type | Store | Entity/Table | Key/Filter | Mutation/Action | Notes |
| --- | --- | --- | --- | --- | --- |
| Read | Postgres (SOT) | `Video` | `id=video_id` | SELECT | 진입 시 상태(`status`) 및 `failed_stage` 조회 — 멱등성/Resume 판단 |
| Read | Postgres (SOT) | `TranscriptSegment`, `Chunk`, `VectorIndexEntry` | `video_id`, 현재 구성된 모델 버전 | SELECT | 현재 구성된 `stt_model_version`, `embedding_model_version` 산출물 존재 여부 확인 — READY 상태 Skip/재처리 판단 |
| Read | Object Storage | N/A | `storage_path` | GET Object (Stream) | 원본 비디오 파일 로컬 임시 저장소로 다운로드 |
| Update | Postgres (SOT) | `Video` | `id=video_id` | UPDATE | `status='PROCESSING'` (진입 전이). 종료 시 `READY` 또는 `FAILED` + `failed_stage` 변경 |
| Write | Object Storage | N/A | 생성 경로 | PUT Object (Async) | 추출된 오디오/키프레임 파일 비동기 백업 적재 |
| Write | Postgres (SOT) | `Asset` | N/A | INSERT | 오디오/키프레임 Object Storage 경로 및 타입 기록 (`AUDIO`, `KEYFRAME`) |
| Write | Postgres (SOT) | `TranscriptSegment` | N/A | INSERT (Bulk) | STT 결과 원본 텍스트 및 타임스탬프 구간 영속화. `stt_model_version`을 함께 기록한다 |
| Write | Postgres (SOT) | `Chunk` | N/A | INSERT (Bulk) | 시맨틱 청킹 결과 및 keyframe 매핑 영속화. `text`(원본 청크 텍스트), `enriched_text`(합성 검색 텍스트), `stt_model_version`, `embedding_model_version`, Vision 원재료 개별 컬럼(`visual_caption TEXT`, `ocr_text TEXT`, `scene_tags TEXT`) 저장 |
| Write | Postgres (Vector) | `VectorIndexEntry` | N/A | INSERT (Bulk) | 임베딩 결과 적재 테이블 (`pgvector`). `embedding_model_version`을 함께 기록한다 |
| Read | Postgres (SOT) | `Video` | `id=video_id` | SELECT | DELETE_REQUEST 수신 시 storage_path, 연관 엔티티 확인 |
| Delete | Postgres (SOT) | `VectorIndexEntry`, `Chunk`, `TranscriptSegment`, `Asset` | `video_id` | DELETE (단일 트랜잭션) | 연쇄 삭제 순서 준수 |
| Delete | Postgres (SOT) | `Video` | `id=video_id` | hard-delete | 연쇄 삭제 완료 후 |
| Delete | Object Storage | N/A | `storage_path` (원본 영상, 오디오, 키프레임) | DELETE (비동기) | 메인 서비스와 분리하여 처리 |

### 2.3 SLA & Constraints
* **Timeout Limitation:** 미디어 다운로드/가공 및 외부 AI 어댑터 호출은 I/O Bound 작업이므로 타임아웃 처리가 매우 중요하다.
  * 미디어 다운로드: 최대 300초
  * Google Cloud STT API 호출 (`GoogleSTTAdapter`): 최대 120초 
  * VisionAdapter 호출: 최대 15초 (`VISION_TIMEOUT_SEC`, 대표 키프레임 1장 처리 기준)
  * Embedding API 호출: 최대 10초 (`EMBEDDING_TIMEOUT_SEC`, 배치 1회 요청 기준)
* **FFmpeg 오디오 포맷 제약:** 오디오 추출 출력 포맷은 `mono, 16kHz, 16-bit PCM, FLAC`으로 고정한다. 이 포맷은 `GoogleSTTAdapter` 입력 요건에 부합한다.
* **제한 사항:** 워커 노드 OOM 방지를 위해, 메모리에 로드하는 스트리밍 다운로드 버퍼 크기는 제한해야 하며 로컬 디스크를 임시 저장소로 활용한다 (완료 시 cleanup).

### 2.4 Error Contract & Messaging Semantics
* **재시도 정책 (Retryable vs Non-Retryable):**
  * *Retryable:* 네트워크 타임아웃, 브로커 일시적 연결 실패. (최대 3회 지정 백오프)
  * *Non-Retryable:* 지원하지 않는 미디어 포맷 오류, 404(스토리지 파일 유실), 악의적인 파일 구조 등. (즉시 `FAILED` 기록 후 메시지 Ack)
  * *VisionAdapter 실패 시 Fallback:* VisionAdapter는 검색 품질 향상을 위한 보조 단계이다. 일시적 오류에 대해 최대 1~2회 짧은 재시도를 허용한다. 반복 실패(예외 발생, 응답 없음, 타임아웃) 시 `visual_caption`, `ocr_text`, `scene_tags`를 빈 문자열로 처리하고, `enriched_text = chunk_text`만으로 구성한다. Vision 단계 실패는 전체 파이프라인 실패 사유로 확대하지 않으며, 파이프라인은 중단 없이 계속 진행한다.
  * *Embedding 배치 호출 정책:* 임베딩은 고정 크기 배치(`EMBEDDING_BATCH_SIZE`)로 호출한다. MVP에서는 배치 실패 시 자동 배치 분할 재시도나 동적 batch size 조정 로직을 두지 않고, 동일 배치에 대해 기존 재시도 정책만 적용한다.
  * *Embedding 입력 정규화 책임:* Managed Embedding Endpoint는 입력 텍스트를 그대로 임베딩하는 추론 컴포넌트로 유지한다. 따라서 Pipeline Worker는 embedding 직전 `enriched_text`에 대해 보수적 caller-side 정규화를 수행한다. 구체적으로 앞뒤 공백 제거, 줄바꿈/탭 등 단어 경계를 형성하는 제어문자의 공백 치환, 연속 공백 축약, Unicode NFC normalization을 적용한다. 기본 lowercasing은 수행하지 않으며, 단어 경계를 형성하지 않는 비표시 제어문자만 제거한다.
* **최종 실패 처리:** 최대 재시도 횟수를 초과한 메시지나 Non-Retryable 예외 발생 시, 워커는 SOT(Video.status)를 `FAILED`로 변경하고 `failed_stage`, `error_message`를 기록한 뒤 메시지를 Ack한다. `failed_stage`는 `DOWNLOAD / EXTRACT / STT / CHUNKING / EMBEDDING / VECTOR_UPSERT` 여섯 분류값을 사용한다. 이 값들은 실패 분류용이며, 전부가 1:1 Resume 포인터는 아니다.

---

## 3. Core Design & Logic

### 3.1 주요 흐름 (Sequence)
0. **메시지 타입 분기:** 브로커 큐에서 메시지를 수신하여 `message_type`을 확인한다.
   - `PREPROCESS_REQUEST`: 1~8번 흐름 진행.
   - `DELETE_REQUEST`: 아래 **삭제 처리 흐름**으로 분기.
1. **메시지 수신 및 진입 체크:** 브로커 큐(`PREPROCESS_REQUEST`)에서 `PREPROCESS_REQUEST` 메시지(`video_id`, `trace_id`, `attempt`)를 Pop (Ack 대기). Metadata DB에서 `Video` 레코드를 조회하여 `status` 및 `failed_stage`를 확인한다. 이어서 현재 워커가 사용 중인 STT/Embedding 모델 버전에 해당하는 산출물이 이미 존재하는지 조회한다.
   - `status = READY`이고 현재 구성된 `stt_model_version`, `embedding_model_version` 산출물이 이미 존재: 중복 수신으로 판단 → 처리 스킵 후 즉시 Ack.
   - `status = READY`이지만 현재 구성된 모델 버전 산출물이 없음: 기존 `READY` 상태는 유지한 채 신규 버전 산출물 생성 경로로 진행한다.
   - `status = DELETING`: 처리를 즉시 중단하고 아래 **삭제 처리 흐름**으로 분기.
   - `status IN (PENDING, UPLOADED)` 또는 `status = FAILED` + `failed_stage` 존재: 다음 단계로 진행한다.
   - `failed_stage` 존재 (Resume): 완료된 단계는 Skip하고, `failed_stage`와 보존 산출물을 함께 참조해 안전한 재개 지점을 결정한다 (§3.3 참조).
2. **[External URL 전용]** `status = PENDING`이고 파일이 Object Storage에 없는 경우: 외부 URL에서 파일을 다운로드하여 Object Storage에 저장, `Video.status = UPLOADED`로 갱신.
3. **트랜잭션(상태 변경 — 진입 선점 게이트):**
   - 초기 처리/실패 복구 경로(`status IN ('PENDING','UPLOADED','FAILED')`)에서는 `UPDATE Video SET status='PROCESSING' WHERE id=video_id AND status IN ('PENDING','UPLOADED','FAILED')` 를 수행하여 상태 확인과 PROCESSING 전이를 한 번에 처리한다. 업데이트가 0 rows를 반환하면(다른 워커가 먼저 `PROCESSING`으로 전이한 상태) 중복 작업으로 판단하고 즉시 Ack 후 종료한다.
   - 버전 갱신 경로(`status = READY` + 현재 구성된 모델 버전 산출물 부재)에서는 `Video.status`를 유지하고, 동일 `(video_id, stt_model_version, embedding_model_version)` 조합의 산출물이 이미 생성 중이거나 존재하는지 확인하여 중복 생성을 방지한다. 락 기반 단일 실행은 MVP 범위 밖이다.
4. **미디어 적재:** `storage_path`를 사용해 Object Storage에서 비디오 파일을 로컬 임시 스토리지로 스트리밍 다운로드 (최대 `DOWNLOAD_TIMEOUT_SEC`).
5. **미디어 가공:** FFmpeg로 오디오 트랙을 추출한다. 오디오 출력 포맷은 `mono, 16kHz, 16-bit PCM, FLAC`으로 고정한다.
   - 원본 비디오는 이후 Chunk 기준 대표 키프레임 추출에 재사용할 수 있도록 로컬 임시 스토리지에 유지한다.
6. **AI 파이프라인 진행:**
   - **STT:** 로컬 오디오 파일을 `GoogleSTTAdapter`(Google Cloud Speech-to-Text)로 전송하여 타임스탬프를 포함한 텍스트 스크립트 획득 (최대 `STT_TIMEOUT_SEC`). 결과를 `TranscriptSegment` 테이블에 Bulk Insert하며, 각 레코드에 현재 STT 모델 버전을 함께 기록한다. 현재 STT 모델 버전의 transcript가 이미 존재하고 재사용 가능하다고 판정된 경우에는 이 단계를 생략할 수 있다.
   - **청킹 (ChunkingService, ADR-003):**
     - 인접한 `TranscriptSegment`를 순서대로 읽으며 문장 경계를 복원한다.
     - 복원된 문장들을 순서대로 누적하여 최대 `CHUNK_MAX_TOKENS` 토큰까지 하나의 Chunk를 생성한다.
     - 다음 Chunk를 생성할 때는 직전 Chunk의 마지막 `CHUNK_OVERLAP_SENTENCES`문장을 overlap으로 포함한다.
     - Chunk의 `start_ms`는 포함된 첫 `TranscriptSegment`의 시작 시각, `end_ms`는 마지막 `TranscriptSegment`의 종료 시각을 사용한다.
     - 단일 문장 자체가 `CHUNK_MAX_TOKENS`를 초과하는 경우에만 예외적으로 문장 내부 분할을 허용한다.
     - 청킹 결과에 `chunking_version` 필드를 기록한다.
     - 생성되는 `Chunk`에는 현재 STT 모델 버전과 현재 Embedding 모델 버전을 함께 기록한다.
   - **대표 키프레임 추출 (Chunk 기준):**
     - 각 Chunk의 `start_ms ~ end_ms` 범위를 기준으로 대표 키프레임 1개를 추출한다.
     - 대표 키프레임은 기본적으로 Chunk 중앙 시점 `((start_ms + end_ms) / 2)`에 대응하는 프레임을 사용한다.
     - 대표 키프레임 추출에 성공하면 결과 파일을 Object Storage에 적재하고 경로를 `Asset` 테이블에 기록한다. `keyframe_asset_id` 연결은 7번 최종 적재 직전에 확정한다.
     - 대표 키프레임 추출에 실패하거나 해당 구간에서 유효 프레임을 확보하지 못하면 `keyframe_asset_id = null`로 처리한다.
   - **enriched text 구성 (VisionAdapter):**
     - `keyframe_asset_id`가 확정된 Chunk에 대해서만 `VisionAdapter`를 호출하여 `visual_caption`, `ocr_text`, `scene_tags`를 추출한다. VisionAdapter는 최대 1~2회 짧은 재시도를 허용한다 (§2.4 참조).
     - `enriched_text = f"{chunk_text} {visual_caption} {ocr_text} {scene_tags}".strip()` 형태로 합산한다.
     - **Vision 원재료 저장:** `visual_caption`, `ocr_text`, `scene_tags`를 `Chunk` 레코드에 `enriched_text`와 별도 필드로 함께 저장한다. 원재료는 디버깅, 품질 분석, 재색인, UI 근거 표시 용도로 보관한다. 저장 형식은 개별 컬럼(`visual_caption TEXT`, `ocr_text TEXT`, `scene_tags TEXT`)으로 확정되었다.
     - **Fallback:** VisionAdapter 반복 실패 또는 caption/OCR 결과 없을 경우 해당 항목을 빈 문자열로 처리하며, `enriched_text = chunk_text`만으로 구성한다. Vision 단계 실패는 전체 파이프라인 실패 사유로 확대하지 않는다.
  - **벡터화:** `enriched_text`에 caller-side 보수적 정규화를 적용한 뒤, Chunk들을 고정 크기 배치(`EMBEDDING_BATCH_SIZE`)로 묶어 Managed Embedding Endpoint에 전송하여 `embedding_vector`를 생성한다 (최대 `EMBEDDING_TIMEOUT_SEC`, 배치 1회 요청 기준). 생성된 벡터와 최종 적재 레코드에는 현재 Embedding 모델 버전을 함께 기록한다.
     - 응답 벡터는 요청한 Chunk 순서와 1:1로 매핑되어야 한다.
     - MVP에서는 배치 실패 시 배치를 더 작은 단위로 자동 분할하지 않는다. 동일 배치에 대해 기존 재시도 정책만 적용하고, 재시도 초과 시 `failed_stage = EMBEDDING`으로 처리한다.
7. **DB Insert 및 완료 처리:** `Chunk`(`text`: 원본 청크 텍스트, `enriched_text`: 합성 검색 텍스트, `stt_model_version`, `embedding_model_version`, Vision 원재료 개별 컬럼 `visual_caption TEXT`/`ocr_text TEXT`/`scene_tags TEXT`)와 `VectorIndexEntry`를 하나의 DB 트랜잭션으로 Bulk Insert. Embedding은 `enriched_text` 기준으로 수행된 결과를 저장하며 `embedding_model_version`을 함께 기록한다. 신규 모델 버전 재처리 시 기존 버전 산출물은 즉시 삭제하지 않고 공존시킨다. 초기 처리/실패 복구 경로에서는 `Video.status = READY`로 갱신하고, 이미 `READY`인 비디오의 버전 갱신 경로에서는 `Video.status = READY`를 유지한다.
8. **Cleanup 및 Message Ack:** 로컬 임시 파일 완전 삭제(성공/실패 공통). 브로커에 메시지 Ack 전송.

#### 삭제 처리 흐름 (DELETE_REQUEST 수신 또는 DELETING 감지 시)
> **DELETING 감지 시 처리 규칙:** `PREPROCESS_REQUEST` 파이프라인 진행 중 `Video.status = DELETING`을 감지한 경우, 워커는 **다음 단계 진입을 금지**하고 즉시 중단한다. 이미 진행 중인 외부 API 호출(GoogleSTTAdapter, VisionAdapter, Embedding 등)은 취소 불가로 간주한다. 삭제 감지 이후 반환된 외부 API 응답은 DB 또는 Object Storage에 저장하지 않고 폐기한다. 중단 후 아래 삭제 처리 흐름으로 전환한다.

1. Metadata DB에서 Video 레코드를 조회하여 storage_path 및 연관 엔티티를 확인한다. 대상 레코드가 이미 존재하지 않으면 중복 삭제로 간주하고 성공으로 처리한 뒤 즉시 Ack한다.
2. Metadata DB에서 VectorIndexEntry, Chunk, TranscriptSegment, Asset을 단일 트랜잭션으로 삭제한다.
3. Metadata DB에서 Video 레코드를 hard-delete한다. **이 시점이 사용자 관점의 삭제 완료 기준이다** (§3.5 참조).
4. Object Storage에서 원본 영상, 오디오, 키프레임 파일을 비동기로 삭제한다. (메인 서비스와 분리하여 처리; §3.4 삭제 완료 정합성 참조)
5. 브로커에 메시지 Ack를 전송한다.

### 3.2 상태 전이 (State Machine)
| From | To | Trigger | Guard | Side Effects |
| --- | --- | --- | --- | --- |
| PENDING/UPLOADED | PROCESSING | Worker 큐 수신 | `UPDATE ... WHERE status IN ('PENDING','UPLOADED','FAILED')` 조건부 원자적 업데이트 성공 (0 rows 반환 시 중복 작업 판단 후 Ack 종료) | 사용자 페이지 "처리 중" 상태 노출 |
| PROCESSING | READY | DB Insert 성공 | 모든 임베딩/청크 변환 정상 완료 | Search Service 검색 노출 대상 포함 |
| READY | READY | Worker 큐 수신 | 현재 구성된 모델 버전 산출물이 아직 없음 | 신규 버전 `TranscriptSegment`/`Chunk`/`VectorIndexEntry` 생성, 기존 READY 유지 |
| PROCESSING | FAILED | Worker 최대 재시도 초과 / 치명적 에러 | Non-Retryable 예외 발생 또는 Retry=Max | `failed_stage` 기록 (DOWNLOAD / EXTRACT / STT / CHUNKING / EMBEDDING / VECTOR_UPSERT), `error_message` 저장 후 Ack |
| PROCESSING (또는 임의 상태) | (연쇄 삭제 후 hard-delete) | DELETE_REQUEST 수신 또는 단계 진입 시 DELETING 감지 | Video.status = DELETING | 외부 API 추가 호출 없이 즉시 중단; 연쇄 삭제 완료 후 레코드 소멸 |

### 3.3 멱등성 및 복구 (Resilience)
* **멱등 키 조건:** `video_id`를 기본 엔티티 키로 사용하되, 모델 버전 공존 시에는 현재 구성된 `(stt_model_version, embedding_model_version)` 조합을 함께 고려해야 한다.
  * **완료 방어:** 프로세스 진입 시 `Video.status`가 이미 `READY`이더라도, 현재 구성된 모델 버전 산출물이 이미 존재하는 경우에만 메시지 중복 수신으로 간주하고 처리를 스킵(Ack)한다.
  * **Resume (부분 실패 재처리):** `Video.status = FAILED`이고 `failed_stage`가 기록된 경우, 재처리 메시지 수신 시 워커는 실제로 보존된 산출물을 기준으로 이미 완료된 무거운 작업만 Skip한다. `failed_stage`는 실패 분류값이며, 실제 재개 지점은 보존 산출물에 따라 결정한다.
    * `DOWNLOAD`면 원본 확보부터 다시 수행한다.
    * `EXTRACT`면 원본 비디오가 이미 존재함을 전제로 FFmpeg 추출부터 다시 수행한다.
    * `STT`면 오디오 산출물이 이미 존재함을 전제로 STT 호출부터 다시 수행한다. `TranscriptSegment` 적재 실패도 `STT`에 포함한다.
    * `CHUNKING`이면 `TranscriptSegment`가 이미 저장된 것을 전제로 청킹부터 다시 수행한다. Vision enrichment 처리 실패도 `CHUNKING`에 포함한다.
    * `EMBEDDING`이면 임베딩 배치 호출 실패를 의미하며, 현재 파이프라인은 임베딩 전 `Chunk`와 `enriched_text`를 별도 영속화하지 않으므로 기본값은 `CHUNKING`부터 다시 수행한다.
    * `VECTOR_UPSERT`이면 최종 `Chunk`/`VectorIndexEntry`/`Video.status=READY` 적재 실패를 의미하며, 기본값은 `CHUNKING`부터 다시 수행한다.
    * **모델 버전 변경 시 재생성 범위:** 현재 구성된 모델 버전과 저장된 산출물 버전이 다를 경우에는 실패 재시도용 `failed_stage`와 별도로 재생성을 판단한다. STT 모델 버전이 변경된 경우에는 transcript가 달라질 수 있으므로 `STT` 단계부터 끝까지 다시 수행하여 신규 버전 산출물을 저장한다. Embedding 모델 버전이 변경된 경우에는 현재 STT 모델 버전에 대응하는 텍스트 산출물을 기준으로 재임베딩을 수행하고 신규 버전 `Chunk`/`VectorIndexEntry`를 저장한다. 이미 저장된 구버전 산출물은 즉시 삭제하지 않는다.
* **보정 (Cleanup):**
  * 중간 단계 예외로 컨텍스트 강제 종료 시, 파이프라인 로컬의 `/tmp` 디렉토리에 생성되었던 해당 `video_id` 잔여 원본/가공 파일에 대한 Cleanup이 `finally` 블록 또는 Context Manager 내에서 반드시 동작해야 추후 디스크 풀(Disk Full)을 방지한다.
  * **Object Storage 중간 산출물 보존 정책:** 오디오, 키프레임 등 Object Storage에 적재된 중간 산출물은 기본 보존 대상이다. 자동 lifecycle 삭제를 기본 정책으로 두지 않으며, orphan object 정리는 별도 운영 배치/cron 태스크가 하루 1회 수행한다.

### 3.4 Data Consistency & Orphan Prevention
* **유령 데이터 방지:** 부분 처리 실패/강제 종료된 케이스의 경우, SOT `Video` 엔티티가 `PROCESSING` 채로 멈춰 있을 수 있다. 이 경우 재시도로 해소되거나 관리 툴로 보정된다.
* **부분 실패의 제어:** `Chunk`와 `VectorIndexEntry` 데이터 인서트는 물리적으로 연결되어야 정합성이 맞는다. Bulk Insert 처리 시 원자성(DB Transaction)을 가지도록 보장.

### 3.5 삭제 완료 정합성 기준
* **사용자 관점 삭제 완료 기준:** `Video`, `Chunk`, `TranscriptSegment`, `Asset`, `VectorIndexEntry` 레코드가 DB에서 hard-delete되면 사용자 관점의 삭제는 완료로 간주한다. 이 시점부터 사용자는 해당 데이터에 접근할 수 없다.
* **Object Storage Orphan Object:** DB hard-delete 이후 남아 있는 Object Storage 파일은 orphan object로 간주한다. Orphan object는 사용자 접근 경로(presigned URL 발급, 검색 결과 등)에서 절대 참조되지 않는다.
* **비동기 cleanup:** Object Storage 파일 정리(원본 영상, 오디오, 키프레임)는 주 삭제 처리 경로와 분리된 운영 배치/cron 태스크로 수행한다. 기본 실행 주기는 하루 1회이며, 삭제 대상은 DB hard-delete 이후 참조를 잃은 orphan object이다. 실패 항목은 다음 주기에 재시도한다.

---

## 4. Observability & Ops
* **Logging:** `loguru`를 사용하여 모든 로그에 원형 요청에서 넘어온 `trace_id`와 `video_id`, `user_id`를 필수 포함시킨다. 파이프라인 단계별 (Download, FFmpeg, STT, ChunkingService, VisionAdapter, Embedding, DB Insert) 진행 상황을 구조화 로깅한다.
* **Metrics:** 파이프라인 처리 시간(전체/단계별), 성공/실패율, 최종 실패 건수를 최소 단위로 수집한다. 
* **Alerts:** 최종 실패 건수 이상 증가, 에러율 급등 시 알람 기준은 구현 해본 후 임계값을 결정한다.
* **Trace Propagation:** Core API가 발급해 큐 메시지로 전파된 `trace_id`를 그대로 승계하여 외부 로깅 시스템과 연동한다.

---

## 5. Acceptance Criteria (DoD)

### 5.1 시나리오 검증

#### PREPROCESS_REQUEST 정상 흐름 (UPLOADED → READY)

**정상**
* [ ] `UPLOADED` 상태 비디오에 대해 `PREPROCESS_REQUEST` 수신 → `PROCESSING` 전이 → 다운로드 → FFmpeg 오디오 추출(`mono, 16kHz, 16-bit PCM, FLAC`) → GoogleSTTAdapter STT → 청킹(문장 경계+overlap, ChunkingService) → Chunk 기준 대표 키프레임 추출 → VisionAdapter enriched text 구성 → Embedding 배치 호출 → `Chunk`·`VectorIndexEntry` Bulk Insert → `Video.status = READY` 전이 → 메시지 Ack
* [ ] 파이프라인 완료 후 로컬 임시 파일(`/tmp/`) 완전 삭제 확인 (성공 경로)
* [ ] `Chunk`(`text`: 원본 청크 텍스트, `enriched_text`: 합성 검색 텍스트, Vision 원재료 별도 필드)와 `VectorIndexEntry` 단일 DB 트랜잭션으로 커밋, `Video.status = READY` 함께 갱신

#### 멱등성 방어 (READY 상태 동일 버전 Skip)

**정상**
* [ ] `Video.status = READY`이고 현재 구성된 STT/Embedding 모델 버전 산출물이 이미 존재하는 상태에서 `PREPROCESS_REQUEST` 수신 → 중복 수신으로 판단, 처리 스킵 후 즉시 Ack
* [ ] Skip 시 외부 API(GoogleSTTAdapter, VisionAdapter, Embedding) 호출 없음, DB 변경 없음

#### 버전 갱신 재처리 (READY 유지)

**정상**
* [ ] `Video.status = READY`이고 현재 구성된 모델 버전 산출물이 없는 상태에서 `PREPROCESS_REQUEST` 수신 → `Video.status`는 `READY`로 유지한 채 신규 버전 산출물 생성 진행
* [ ] STT 모델 버전 변경 시 신규 `TranscriptSegment`/`Chunk`/`VectorIndexEntry` 산출물이 저장되고 기존 버전 산출물은 유지됨을 확인
* [ ] Embedding 모델 버전 변경 시 현재 STT 모델 버전에 대응하는 텍스트 산출물을 기준으로 신규 버전 `Chunk`/`VectorIndexEntry`가 저장되고 기존 버전 산출물은 유지됨을 확인

#### 동시 진입 충돌 방어 (PROCESSING 상태 선점 워커 충돌)

**정상**
* [ ] 동일 `video_id`에 대해 두 워커가 거의 동시에 `PREPROCESS_REQUEST`를 수신한 경우 → 조건부 원자적 UPDATE 성공 워커 1개만 `PROCESSING` 전이 후 처리 진행
* [ ] 후행 워커: `UPDATE ... WHERE status IN ('PENDING','UPLOADED','FAILED')` 결과 0 rows → 중복 작업으로 판단, 외부 API 호출 없이 즉시 Ack 후 종료

#### Resume 분기 (FAILED + failed_stage)

**정상**
* [ ] `Video.status = FAILED` + `failed_stage` 기록된 상태에서 재처리 메시지 수신 → 실제로 보존된 산출물 기준으로 완료된 단계만 Skip하고, `failed_stage`와 산출물 보존 상태를 함께 참조해 안전한 재개 지점을 결정
* [ ] `failed_stage = STT` 예시: 오디오 산출물이 이미 존재하면 다운로드·추출은 생략하고 STT부터 재개
* [ ] `failed_stage = CHUNKING` 예시: `TranscriptSegment`가 이미 저장되어 있으면 STT는 재실행하지 않고 청킹부터 재개
* [ ] `failed_stage = EMBEDDING` 예시: 임베딩 배치 호출 실패 시 현재 문서 기준 기본값은 `CHUNKING`부터 재개
* [ ] `failed_stage = VECTOR_UPSERT` 예시: 최종 적재 실패 시 기본값은 `CHUNKING`부터 재수행
* [ ] Resume 후 전체 파이프라인 정상 완료 시 `Video.status = READY` 전이

#### Non-Retryable 예외 (Pydantic 검증 실패 → FAILED)

**예외**
* [ ] 메시지 Payload Pydantic 검증 실패(미지원 포맷, 필수 필드 누락 등) → 즉시 `Video.status = FAILED`, `failed_stage`, `error_message` 기록 후 Ack
* [ ] Object Storage 파일 유실(404) → Non-Retryable 판정, 즉시 `FAILED` 기록 후 Ack
* [ ] 지원하지 않는 미디어 포맷 오류 → 즉시 `FAILED` 기록 후 Ack
* [ ] 최종 실패 처리 후 로컬 임시 파일(`/tmp/`) Cleanup 실행 확인

#### Retryable 재시도 (503 → 지수 백오프 3회 후 FAILED)

**예외**
* [ ] Embedding API 일시적 장애(503) → 동일 임베딩 배치 요청에 대해 지수 백오프 3회 후 `failed_stage = EMBEDDING` 기록 및 Ack
* [ ] 최종 `Chunk`/`VectorIndexEntry` Bulk Insert 또는 `Video.status = READY` 갱신 트랜잭션 실패 → `failed_stage = VECTOR_UPSERT` 기록 및 Ack
* [ ] 네트워크 타임아웃 → 지수 백오프 기반 최대 3회 재시도, 3회 초과 시 `FAILED` 기록 및 Ack
* [ ] 재시도 중 성공 시 파이프라인 계속 진행
* [ ] 최종 실패 처리 후 로컬 임시 파일 Cleanup 실행 확인
* [ ] MVP 범위에서는 임베딩 실패 배치에 대한 자동 배치 분할 재시도 없이 동일 배치만 재시도함을 확인

#### enriched text Fallback (VisionAdapter 실패 → 빈 문자열 처리)

**예외**
* [ ] VisionAdapter 최대 1~2회 재시도 후 반복 실패 또는 caption/OCR 결과 없음 → `visual_caption`, `ocr_text`, `scene_tags` 빈 문자열 처리, `enriched_text = chunk_text`로 구성, 파이프라인 중단 없이 계속 진행
* [ ] embedding 직전 `enriched_text`에 대해 caller-side 보수적 정규화(앞뒤 공백 제거, 제어문자 공백 치환, 연속 공백 축약, Unicode NFC) 적용
* [ ] Fallback 발생 시 구조화 로그 기록 확인
* [ ] Fallback 시에도 `Chunk`와 `VectorIndexEntry`가 정상적으로 DB에 Insert됨을 확인
* [ ] Vision 원재료(`visual_caption`, `ocr_text`, `scene_tags`)가 `Chunk` 레코드에 별도 필드로 저장되는지 확인 (Fallback 시에는 빈 문자열로 저장)

#### DELETE_REQUEST 정상 처리 (연쇄 삭제 완료 후 hard-delete)

**정상**
* [ ] `DELETE_REQUEST` 수신 → `VectorIndexEntry` → `Chunk` → `TranscriptSegment` → `Asset` 단일 DB 트랜잭션 삭제
* [ ] DB 트랜잭션 완료 후 `Video` 레코드 hard-delete
* [ ] Object Storage 원본 영상·오디오·키프레임 파일 비동기 삭제 (메인 서비스와 분리 처리)
* [ ] 모든 삭제 완료 후 메시지 Ack
* [ ] DELETE_REQUEST 중복 수신 또는 대상 Video 레코드가 이미 없는 경우 → 성공으로 간주하고 즉시 Ack

#### 파이프라인 중 DELETING 감지 → 즉시 중단

**예외**
* [ ] `PREPROCESS_REQUEST` 처리 중 임의 단계에서 `Video.status = DELETING` 감지 → **다음 단계 진입 금지** (현재 실행 중인 단계는 완료 또는 취소 불가로 처리)
* [ ] 이미 시작된 외부 API 호출(GoogleSTTAdapter, VisionAdapter, Embedding) 결과가 반환되어도 DB/Storage에 저장하지 않고 폐기
* [ ] 삭제 감지 이후 외부 API 추가 호출 없음
* [ ] 즉시 중단 후 연쇄 삭제 흐름(DELETE 처리 유스케이스)으로 전환
* [ ] 로컬 임시 파일 Cleanup 실행 확인

### 5.2 검증을 위한 테스팅 전략 (Testing Strategy)

에이전트는 아래 가이드라인을 만족하는 자동화 테스트를 작성해야 한다.
* 테스트 프레임워크는 `pytest`, `pytest-asyncio`를 사용한다.
* **커버리지 목표:** 단위·통합 테스트 합산 80% 이상을 달성한다 (`pytest-cov` 기준).
* DB 통합 테스트는 PostgreSQL 기반의 격리 환경 (Testcontainers 또는 Docker Compose)을 사용한다.
* **외부 의존성 격리 전략:**
  * Object Storage(GCS) → `InMemoryStorageClient` (Test Double): 실제 GCS 없이 동작, 내부 dict로 파일 적재·삭제 상태 관리. `InMemoryStorageClient`를 통해 다운로드(`download_object`), 업로드(`upload_object`), 삭제(`delete_object`) 시나리오를 실제 GCS 호출 없이 검증한다.
  * Message Broker(PGMQ) → `InMemoryBrokerClient` (Test Double): 실제 MQ 없이 동작, 수신 메시지를 메모리 리스트로 관리. `InMemoryBrokerClient`를 통해 `PREPROCESS_REQUEST`/`DELETE_REQUEST` 소비(consume) 및 Ack 시나리오를 검증한다.
  * Google Cloud STT (`GoogleSTTAdapter`) → `MockSTTAdapter`(또는 `AsyncMock`)으로 대체하여 외부 호출 없이 동작한다. 정상 응답·503·429·타임아웃 시나리오를 각각 세팅하여 재시도/최종 `FAILED` + Ack 분기 검증.
  * VisionAdapter → `MockVisionAdapter`로 대체하여 외부 호출 없이 동작한다. 정상 응답·1~2회 재시도 후 실패(예외 발생)·빈 응답 시나리오를 세팅하여 enriched text Fallback 분기 검증. Vision 원재료(`visual_caption`, `ocr_text`, `scene_tags`) Chunk 별도 저장 검증 포함.
  * ChunkingService (문장 경계+overlap 청킹) → 실제 `TranscriptSegment` 목록을 입력으로 한 순수 유닛 테스트. `CHUNK_MAX_TOKENS`/`CHUNK_OVERLAP_SENTENCES` 환경 변수 조합별 청킹 결과(`start_ms`, `end_ms`, `chunking_version`, overlap 포함 여부) 검증. 단일 문장이 `CHUNK_MAX_TOKENS` 초과 시 문장 내부 분할 동작 검증.
  * Embedding API → `AsyncMock`으로 대체하여 외부 호출 없이 동작한다. 여러 Chunk를 `EMBEDDING_BATCH_SIZE` 기준으로 묶어 호출하는지, 응답 벡터가 요청 순서와 1:1 매핑되는지, 503 시나리오에서 동일 배치 기준 지수 백오프 3회 재시도 후 `failed_stage = EMBEDDING`으로 종료하는지 검증한다.
  * FFmpeg 어댑터 → subprocess `AsyncMock` 또는 더미 파일을 활용하여 실제 FFmpeg 바이너리 없이 오디오 추출과 Chunk 기준 대표 키프레임 추출 결과를 시뮬레이션한다.
* Resume 로직(`failed_stage` 기반 Skip)은 Testcontainers 환경에서 DB `Video.status = FAILED` + `failed_stage` 상태를 직접 세팅하여 검증한다.
* DELETING 감지 분기는 Testcontainers 환경에서 파이프라인 진행 중 `Video.status = DELETING`으로 DB를 직접 변경하여 즉시 중단 여부를 검증한다.

### 5.3 산출물 (Artifacts)

폴더 구조는 `docs/Tech_Spec/folder_structure.md`를 참조한다.

* [ ] 메시지 브로커 컨슈머 — PREPROCESS_REQUEST 수신, Ack 및 최종 실패 처리
* [ ] 비디오 처리 유스케이스 — 상태 전이, Resume 로직, 멱등성
* [ ] FFmpeg 어댑터 — 오디오 추출(`mono, 16kHz, 16-bit PCM, FLAC`), Chunk 기준 대표 키프레임 추출
* [ ] AI 어댑터 — GoogleSTTAdapter(STT), Embedding
* [ ] VisionAdapter (enriched text 전처리, ADR-002) — 추상 인터페이스 + MockVisionAdapter
* [ ] ChunkingService (문장 경계+overlap 청킹, ADR-003) — TranscriptSegment 기반 내부 구현
* [ ] Object Storage 어댑터 — 파일 다운로드, 임시 파일 Context Manager
* [ ] Repository — Video/Chunk/Asset/VectorIndexEntry DB 접근
* [ ] 단위 테스트 / 통합 테스트
* [ ] DELETE_REQUEST 컨슈머 — DELETING 감지, 연쇄 삭제 실행
* [ ] BrokerClient 인터페이스 및 구현체 (PGMQBrokerClient + InMemoryBrokerClient)
* [ ] StorageClient 인터페이스 및 구현체 (GCSStorageClient + InMemoryStorageClient)


