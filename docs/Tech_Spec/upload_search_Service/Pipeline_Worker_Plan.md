# [Media & AI Pipeline Worker] PLAN

**Meta**
- **Component ID:** pipeline-worker
- **Target SPEC:** `docs/Tech_Spec/Pipeline_Worker_Spec.md`
- **SOT:** `docs/system-design.md`, `docs/Tech_Spec/Pipeline_Worker_Spec.md`, `docs/Tech_Spec/Core_Api_Server_Spec.md`, `docs/Tech_Spec/Managed_Embedding_Endpoint_Spec.md`, `docs/ADR/ADR-003-chunking-strategy.md`, `docs/ADR/ADR-004-video-search-retrieval-strategy.md`

---

> 이 PLAN은 특정 구현자나 특정 에이전트 도구에 종속되지 않는 실행 계획 문서다.
> 목표는 구현자가 `SPEC + PLAN`만 읽고도 작업 순서, 검증 기준, 통합 경계를 오해 없이 이해할 수 있게 만드는 것이다.

## 1. Goals & Strategy

### 1.1 달성 목표

- **PREPROCESS_REQUEST 파이프라인 완성:** `PENDING/UPLOADED/FAILED/READY` 분기, External URL 다운로드, FFmpeg 전처리, STT, 청킹, Vision fallback, embedding, DB 적재, `READY/FAILED` 전이를 구현한다.
- **삭제 경로 완성:** `DELETE_REQUEST` 직접 처리와 `PREPROCESS_REQUEST` 도중 `DELETING` 감지 시 즉시 중단 후 연쇄 삭제 전환을 구현한다.
- **멱등성 및 복구력:** 동일 모델 버전 중복 수신 skip, `failed_stage` 기반 Resume, 모델 버전 변경 시 재생성 범위를 SPEC 그대로 구현한다.
- **테스트:** PostgreSQL 기반 통합 테스트와 mock/in-memory double을 조합해 SPEC §5.1 시나리오를 자동화하고, 단위·통합 테스트 합산 커버리지 80% 이상을 달성한다.

### 1.2 제외 대상 (Non-Goals)

- 앱 레벨 DLQ 운영, 자동 재처리 워크플로우, dead-letter 라우팅은 이번 구현 범위에서 제외한다.
- `READY` 상태 버전 갱신 경로에 대한 분산 락 기반 단일 실행 보장은 MVP 범위에서 제외한다. SPEC의 현재 기준대로 중복 산출물 존재 여부 확인만 구현한다.
- Embedding 실패 배치에 대한 자동 batch split, 동적 batch size 조정, adaptive retry는 제외한다.
- orphan object 일일 정리 배치/cron은 이번 컴포넌트 구현 범위에서 제외한다. Worker는 주 삭제 경로의 비동기 삭제 트리거까지만 책임진다.
- Vision 모델 고도화나 별도 Vision 서비스 분리는 제외한다. V1은 SPEC에 정의된 `VisionAdapter` 인터페이스와 fallback 규칙만 구현한다.
- 구조화 메트릭, 분산 트레이싱, 알람 연동 등 본격적인 observability 작업은 핵심 파이프라인 기능 이후로 미룬다. 메시지/DB 계약상 필요한 `trace_id` 필드 파싱과 전달만 유지한다.

### 1.3 리스크 및 대응 방안 (Risk & Mitigation)

- **장시간 외부 I/O로 인한 중복 처리 위험:** `Video.status` 조건부 업데이트, 현재 모델 버전 산출물 존재 조회, 단계 진입 전 `DELETING` 재확인으로 중복 부수 효과를 제한한다.
- **로컬 임시 파일 누적으로 인한 디스크 고갈:** `video_id` 단위 임시 작업 디렉토리 Context Manager를 두고 성공/실패/삭제 경로 공통 `finally` cleanup을 강제한다.
- **부분 적재로 인한 검색 정합성 훼손:** `Chunk`와 `VectorIndexEntry`, `Video.status=READY` 반영을 하나의 DB 트랜잭션으로 묶고, 실패 시 `failed_stage=VECTOR_UPSERT`로 종료한다.
- **공유 DB 스키마 소유 경계 혼선:** 현재 저장소에는 `services/core-api/alembic`만 존재하므로, shared migration 패키지가 도입되기 전까지 Worker 관련 스키마 변경도 기존 Alembic 소유 경계에 추가한다.
- **관련 ADR 부재로 인한 Vision 구현 혼선:** `ADR-002` 문서는 현재 저장소에 없지만 `Pipeline_Worker_Spec.md`가 VisionAdapter 메서드와 fallback 계약을 inline으로 닫고 있으므로, 구현은 SPEC 기준으로 진행하고 ADR 추가는 후속 문서 작업으로 분리한다.

### 1.4 구현 전제 및 열려 있는 결정사항 (Preconditions & Open Decisions)

- **구현 전제:** `Pipeline_Worker_Spec.md`의 메시지 계약, 상태 전이, `failed_stage`, Vision fallback, 모델 버전 공존 정책을 SOT로 사용한다.
- **구현 전제:** 신규 워커 코드는 현재 저장소 레이아웃에 맞춰 `services/pipeline-worker/` 아래에 생성한다.
- **구현 전제:** DB 마이그레이션은 기존 소유 경계인 `services/core-api/alembic`에 추가하고, Worker는 그 스키마를 사용한다.
- **구현 전제:** `services/pipeline-worker`의 로컬 개발 환경은 `poetry install`로 구성하고, 표준 검증 명령은 `poetry run poe ...`를 우선 사용한다. 파일 단위의 좁은 검증만 `poetry run pytest ...`로 실행한다.
- **구현 전제:** 워커 로그 구현은 Python 기본 `logging` 직접 구성 대신 `loguru`를 표준 로깅 라이브러리로 사용한다.
- **구현 전제:** 런타임에는 FFmpeg 바이너리와 pgvector가 활성화된 PostgreSQL이 제공되어야 한다.
- **열려 있는 결정사항:** 현재 기준 구현을 막는 blocker는 없다. `GoogleSTTAdapter`는 Worker 내부 `infra/ai/`에 구현하고, 호출 계약은 `Pipeline_Worker_Spec.md`를 따른다.

### 1.5 핵심 의존성 패키지

| 패키지 | 용도 | 최소 버전 |
| --- | --- | --- |
| `pydantic-settings` | 환경 변수 로딩 | 2.x |
| `loguru` | 워커 표준 로깅 라이브러리 | 0.7+ |
| `sqlalchemy[asyncio]` | DB ORM / 트랜잭션 경계 | 2.x |
| `asyncpg` | PostgreSQL + PGMQ 비동기 드라이버 | 0.29+ |
| `pgvector` | `VectorIndexEntry` 매핑 및 벡터 컬럼 지원 | 0.2+ |
| `httpx` | Managed Embedding Endpoint 호출 | 0.27+ |
| `google-cloud-storage` | GCS 다운로드/업로드/삭제 | 2.x |
| `google-cloud-speech` | Google STT SDK | 2.x |
| `pytest-asyncio` | 비동기 테스트 | 0.23+ |
| `pytest-cov` | 커버리지 측정 | 5.x |
| `poethepoet` | 표준 검증 task runner (`poetry run poe ...`) | 0.32+ |
| `testcontainers[postgres]` | PostgreSQL 통합 테스트 격리 환경 | 4.x |

---

## 2. Implementation Phasing Strategy

- **Phase 1:** 새 워커 서비스 스캐폴딩, Settings, 메시지 스키마, 작업 디렉토리 유틸을 먼저 닫는다.
- **Phase 2:** DB migration/repository, Broker/Storage/Media/AI adapter, ChunkingService를 구현해 파이프라인이 의존할 경계를 확정한다.
- **Phase 3:** `PREPROCESS_REQUEST`와 `DELETE_REQUEST` 유스케이스를 붙이고, Resume/버전 재생성/DELETING 분기를 통합한다.
- **Phase 4:** 통합 테스트, CI 실행 경로, 배포/롤백 검증을 마무리한다.
- **병합 게이트:** 각 Phase는 독립적으로 테스트 가능해야 하며, 최소 단위 PR마다 단위 테스트 또는 통합 테스트 중 하나 이상이 녹색이어야 한다.

### 2.1 작업 분해 원칙 (Task Decomposition Rules)

- 각 Task는 하나의 구현 단위와 하나의 검증 단위를 가진다.
- DB 스키마/Repository 계약을 먼저 닫고, 그 위에 유스케이스를 얹는다.
- 외부 의존성은 모두 인터페이스와 test double을 함께 만든 뒤 유스케이스에 주입한다.
- `PREPROCESS`와 `DELETE`는 저장소 계약을 공유하지만 유스케이스 파일은 분리하여 병렬 수정 가능하게 유지한다.
- 테스트는 단위 테스트에서 분기 로직을, 통합 테스트에서 상태 전이와 DB 정합성을 검증하도록 역할을 분리한다.

### 2.2 선행 경로 및 병렬 가능 범위 (Critical Path & Parallelism)

- **Critical Path:**
  - 새 서비스 스캐폴딩과 Settings/메시지 스키마를 만든다.
  - Worker가 쓰는 테이블/컬럼/인덱스와 Repository 계약을 확정한다.
  - 임시 파일/FFmpeg/STT/Embedding/Vision/Storage/Broker 경계를 구현한다.
  - `process_video` 유스케이스에 Resume, 버전 분기, fallback, 최종 트랜잭션을 붙인다.
  - `delete_video` 유스케이스와 consumer dispatch를 연결한다.
  - SPEC §5.1 시나리오를 자동화한다.

- **Parallelizable Workstreams:**
  - FFmpeg adapter, Storage/Broker adapter, Embedding/Vision/STT adapter는 Settings와 메시지 스키마가 닫힌 뒤 병렬 구현 가능하다.
  - ChunkingService와 text normalization은 Repository와 독립적으로 병렬 구현 가능하다.
  - `delete_video` 유스케이스는 Repository delete 계약이 고정되면 `process_video`와 병렬 구현 가능하다.

- **Merge Owner / Integration Point:**
  - 최종 통합 지점은 `services/pipeline-worker/src/main.py`와 consumer loop다.
  - 유스케이스 통합 후에는 `tests/integration/test_process_flow.py`, `tests/integration/test_delete_flow.py`에서 저장소/외부 adapter/test double을 실제로 연결해 검증한다.

---

## 3. Work Breakdown Structure (WBS)

> 구현자가 그대로 실행할 수 있는 구체 작업 지시서.
> 모든 작업은 `Output / Files / Test Files / Commands / Verify / Linked AC / Depends On`를 포함한다.

### Phase 1: Skeleton & Contracts

- [ ] **Task 0: 워커 서비스 스캐폴딩 및 설정 로딩**
  - **Output:** `services/pipeline-worker/` 서비스 루트, `src/main.py`, `src/config/settings.py`, `pyproject.toml`, `.env.example`, `loguru` 기본 설정을 포함한 테스트 기본 구조.
  - **Files:** `services/pipeline-worker/pyproject.toml`, `services/pipeline-worker/src/main.py`, `services/pipeline-worker/src/config/settings.py`, `services/pipeline-worker/src/utils/logging.py`, `services/pipeline-worker/.env.example`
  - **Test Files:** `services/pipeline-worker/tests/unit/test_settings.py`
  - **Commands:** `cd services/pipeline-worker && poetry run pytest tests/unit/test_settings.py`
  - **Verify:** 필수 환경 변수 누락 시 설정 로딩이 실패하고, 정상 설정에서는 워커 프로세스가 consumer bootstrap까지 진입하며 `loguru` 초기화가 애플리케이션 시작 시점에 연결된다.
  - **Linked AC:** SPEC §1.2, §2.1의 필수 환경 변수 전제, SPEC §5.3 산출물
  - **Depends On:** 없음
  - **병렬 가능:** N

- [ ] **Task 1: 메시지 스키마와 consumer dispatch 골격**
  - **Output:** `PREPROCESS_REQUEST`/`DELETE_REQUEST` Pydantic 스키마, 공통 MessageEnvelope 검증, `trace_id` 포함 envelope 필드 보존, message_type 기반 dispatch skeleton.
  - **Files:** `services/pipeline-worker/src/schemas/messages.py`, `services/pipeline-worker/src/infra/queue/consumer.py`
  - **Test Files:** `services/pipeline-worker/tests/unit/test_message_schemas.py`, `services/pipeline-worker/tests/unit/test_consumer_dispatch.py`
  - **Commands:** `cd services/pipeline-worker && poetry run pytest tests/unit/test_message_schemas.py tests/unit/test_consumer_dispatch.py`
  - **Verify:** 유효하지 않은 payload는 즉시 검출되고, 유효한 envelope는 `message_type`에 따라 올바른 유스케이스로 라우팅되며 `trace_id` 값이 유스케이스 입력으로 유지된다.
  - **Linked AC:** SPEC §2.1, SPEC §2.4, SPEC §5.1 Non-Retryable 예외
  - **Depends On:** Task 0
  - **병렬 가능:** Y

- [ ] **Task 2: 임시 작업 디렉토리와 FFmpeg adapter**
  - **Output:** `video_id` 기준 임시 작업 디렉토리 Context Manager, 오디오 추출(`mono, 16kHz, 16-bit PCM, FLAC`)과 Chunk 기준 대표 키프레임 추출 adapter.
  - **Files:** `services/pipeline-worker/src/utils/workdir.py`, `services/pipeline-worker/src/infra/media/ffmpeg_adapter.py`
  - **Test Files:** `services/pipeline-worker/tests/unit/test_workdir.py`, `services/pipeline-worker/tests/unit/test_ffmpeg_adapter.py`
  - **Commands:** `cd services/pipeline-worker && poetry run pytest tests/unit/test_workdir.py tests/unit/test_ffmpeg_adapter.py`
  - **Verify:** 예외가 나도 임시 파일이 정리되고, FFmpeg 호출 인자가 SPEC의 오디오 포맷/키프레임 계약과 일치한다.
  - **Linked AC:** SPEC §2.3, SPEC §3.1 단계 4~5, SPEC §5.1 정상 흐름/cleanup
  - **Depends On:** Task 0
  - **병렬 가능:** Y

### Phase 2: Persistence & Adapter Boundaries

- [ ] **Task 3: Worker 산출물 스키마 마이그레이션과 Repository 구현**
  - **Output:** `TranscriptSegment`, `Asset`, `Chunk`, `VectorIndexEntry` 테이블 및 Worker가 필요한 `Video` 연계 컬럼/조회 경로, 조건부 상태 전이/버전 조회/연쇄 삭제 Repository.
  - **Files:** `services/core-api/alembic/versions/0002_pipeline_worker_artifacts.py`, `services/pipeline-worker/src/infra/db/video_repository.py`, `services/pipeline-worker/src/infra/db/artifact_repository.py`
  - **Test Files:** `services/pipeline-worker/tests/integration/test_repositories.py`
  - **Commands:** `cd services/core-api && poetry run alembic upgrade head`, `cd services/pipeline-worker && poetry run pytest tests/integration/test_repositories.py`
  - **Verify:** `PROCESSING` 선점 update, 현재 모델 버전 산출물 존재 확인, `Chunk + VectorIndexEntry + Video.status` 트랜잭션, delete cascade가 통합 테스트로 재현된다.
  - **Linked AC:** SPEC §2.2, §3.2, §3.4, §3.5, SPEC §5.1 동시 진입/버전 재처리/DELETE_REQUEST
  - **Depends On:** Task 0
  - **병렬 가능:** Y

- [ ] **Task 4: Broker / Storage / Embedding / Vision / STT adapter 경계와 test double**
  - **Output:** `BrokerClient`, `StorageClient`, `EmbeddingClient`, `VisionAdapter`, `STT` 경계와 운영/테스트 구현체. STT는 `Pipeline_Worker_Spec.md`에 정의된 `GoogleSTTAdapter` 계약을 따른다.
  - **Files:** `services/pipeline-worker/src/infra/queue/broker.py`, `services/pipeline-worker/src/infra/queue/pgmq_client.py`, `services/pipeline-worker/src/infra/queue/inmemory_broker.py`, `services/pipeline-worker/src/infra/storage/client.py`, `services/pipeline-worker/src/infra/storage/gcs_client.py`, `services/pipeline-worker/src/infra/storage/inmemory_storage.py`, `services/pipeline-worker/src/infra/ai/google_stt_adapter.py`, `services/pipeline-worker/src/infra/ai/embedding_client.py`, `services/pipeline-worker/src/infra/ai/vision_adapter.py`
  - **Test Files:** `services/pipeline-worker/tests/unit/test_broker_clients.py`, `services/pipeline-worker/tests/unit/test_storage_clients.py`, `services/pipeline-worker/tests/unit/test_embedding_client.py`, `services/pipeline-worker/tests/unit/test_google_stt_adapter.py`, `services/pipeline-worker/tests/unit/test_vision_adapter.py`
  - **Commands:** `cd services/pipeline-worker && poetry run pytest tests/unit/test_broker_clients.py tests/unit/test_storage_clients.py tests/unit/test_embedding_client.py tests/unit/test_google_stt_adapter.py tests/unit/test_vision_adapter.py`
  - **Verify:** timeout/retry/fallback 정책, 응답 shape 정규화, in-memory/mock 동작이 SPEC과 일치한다.
  - **Linked AC:** SPEC §1.2, §2.1, §2.3, §2.4, SPEC §5.2 외부 의존성 격리 전략
  - **Depends On:** Task 0, Task 1
  - **병렬 가능:** Y

- [ ] **Task 5: ChunkingService와 embedding 직전 text normalization**
  - **Output:** 문장 경계 + overlap 기반 ChunkingService, oversized sentence split, `chunking_version` 부여, `enriched_text` 보수적 정규화 유틸.
  - **Files:** `services/pipeline-worker/src/services/chunking_service.py`, `services/pipeline-worker/src/services/text_normalizer.py`
  - **Test Files:** `services/pipeline-worker/tests/unit/test_chunking_service.py`, `services/pipeline-worker/tests/unit/test_text_normalizer.py`
  - **Commands:** `cd services/pipeline-worker && poetry run pytest tests/unit/test_chunking_service.py tests/unit/test_text_normalizer.py`
  - **Verify:** `CHUNK_MAX_TOKENS`, `CHUNK_OVERLAP_SENTENCES`, oversized sentence split, Unicode NFC normalization이 SPEC 그대로 동작한다.
  - **Linked AC:** SPEC §2.4, §3.1 청킹/벡터화, SPEC §5.1 enriched text fallback
  - **Depends On:** Task 0
  - **병렬 가능:** Y

### Phase 3: Core Usecases & Integration

- [ ] **Task 6: `PREPROCESS_REQUEST` 유스케이스 구현**
  - **Output:** 상태 조회, External URL 다운로드, `PROCESSING` 선점, STT skip/resume, chunk/keyframe/Vision/embedding, 최종 `READY/FAILED` 처리, 동일 버전 skip, 신규 버전 재생성, late `DELETING` 중단 규칙이 포함된 메인 오케스트레이터.
  - **Files:** `services/pipeline-worker/src/usecases/process_video.py`, `services/pipeline-worker/src/services/pipeline_orchestrator.py`
  - **Test Files:** `services/pipeline-worker/tests/unit/test_process_video.py`
  - **Commands:** `cd services/pipeline-worker && poetry run pytest tests/unit/test_process_video.py`
  - **Verify:** `READY` 동일 버전 skip, `FAILED + failed_stage` Resume, embedding retry exhaustion, Vision fallback, 최종 Ack 전 상태 기록이 단위 테스트로 재현된다.
  - **Linked AC:** SPEC §3.1, §3.2, §3.3, SPEC §5.1 PREPROCESS/Resume/Retryable/Fallback
  - **Depends On:** Task 2, Task 3, Task 4, Task 5
  - **병렬 가능:** N

- [ ] **Task 7: `DELETE_REQUEST` 유스케이스와 DELETING 전환 구현**
  - **Output:** 중복 삭제 safe-ack, DB 연쇄 삭제, `Video` hard-delete, Object Storage 비동기 삭제 호출, `PREPROCESS` 단계 진입 전 `DELETING` 감지 시 delete flow handoff.
  - **Files:** `services/pipeline-worker/src/usecases/delete_video.py`, `services/pipeline-worker/src/usecases/process_video.py`
  - **Test Files:** `services/pipeline-worker/tests/unit/test_delete_video.py`, `services/pipeline-worker/tests/integration/test_delete_flow.py`
  - **Commands:** `cd services/pipeline-worker && poetry run pytest tests/unit/test_delete_video.py tests/integration/test_delete_flow.py`
  - **Verify:** 삭제 순서, hard-delete 완료 기준, duplicate delete ack, late response discard 규칙이 검증된다.
  - **Linked AC:** SPEC §3.1 삭제 처리 흐름, §3.5, SPEC §5.1 DELETE_REQUEST / DELETING 감지
  - **Depends On:** Task 3, Task 4
  - **병렬 가능:** Y

- [ ] **Task 8: consumer loop와 유스케이스 통합**
  - **Output:** concurrency 제한(`WORKER_CONCURRENCY`), message consume/ack, `PREPROCESS`/`DELETE` dispatch, terminal failure ack이 포함된 실행 가능한 worker loop.
  - **Files:** `services/pipeline-worker/src/main.py`, `services/pipeline-worker/src/infra/queue/consumer.py`
  - **Test Files:** `services/pipeline-worker/tests/integration/test_consumer_flow.py`
  - **Commands:** `cd services/pipeline-worker && poetry run pytest tests/integration/test_consumer_flow.py`
  - **Verify:** 한 메시지는 정확히 한 유스케이스로 전달되고, 성공/최종 실패/중복 skip/delete duplicate 시 Ack semantics가 유지된다.
  - **Linked AC:** SPEC §2.1, §2.4, §5.1 정상 흐름/최종 실패/DELETE_REQUEST
  - **Depends On:** Task 6, Task 7
  - **병렬 가능:** N

### Phase 4: Verification & Release Readiness

- [ ] **Task 9: E2E 통합 테스트, 커버리지, CI 경로 정리**
  - **Output:** SPEC §5.1 시나리오를 반영한 통합 테스트 스위트, 커버리지 설정, 기존 CI에 Worker 테스트 실행 경로 추가.
  - **Files:** `services/pipeline-worker/tests/integration/test_process_flow.py`, `services/pipeline-worker/tests/integration/test_resume_flow.py`, `services/pipeline-worker/tests/integration/test_deleting_interrupt.py`, `.github/workflows/ci.yml` 또는 기존 Python CI 워크플로우
  - **Test Files:** 전체 Worker 테스트 스위트
  - **Commands:** `cd services/pipeline-worker && poetry run poe check`, `cd services/pipeline-worker && poetry run poe coverage`
  - **Verify:** SPEC §5.1 시나리오와 §5.2 테스트 전략을 충족하고, 커버리지 80% 이상을 달성한다.
  - **Linked AC:** SPEC §5.1, §5.2, §5.3
  - **Depends On:** Task 8
  - **병렬 가능:** N

---

## 4. Integration Checklist & Done Criteria

### 4.1 통합 체크리스트 (Integration Checklist)

- [ ] `PREPROCESS_REQUEST` / `DELETE_REQUEST` envelope 필드가 `Core_Api_Server_Spec.md`와 완전히 일치한다.
- [ ] `failed_stage`, `Video.status`, `trace_id`, 모델 버전 필드 의미가 `Pipeline_Worker_Spec.md`와 일치한다.
- [ ] `GoogleSTTAdapter` 반환 shape와 retry 의미가 `Pipeline_Worker_Spec.md`와 일치한다.
- [ ] Embedding 호출 URL, 응답 길이 검증, fail-fast 처리가 `Managed_Embedding_Endpoint_Spec.md`와 일치한다.
- [ ] `Chunk`와 `VectorIndexEntry` 저장 필드가 Search Service가 읽는 `text`, `enriched_text`, `video_id`, 모델 버전 의미와 충돌하지 않는다.
- [ ] `DELETING` 감지 이후에는 외부 API 추가 호출이 발생하지 않고, 이미 완료된 늦은 응답도 저장되지 않는다.
- [ ] Worker 전용 코드가 없던 현재 저장소 구조를 고려해 신규 서비스 루트와 기존 마이그레이션 소유 경계가 문서대로 유지된다.
- [ ] Vision 원재료(`visual_caption`, `ocr_text`, `scene_tags`)는 별도 필드로 저장되고, fallback 시 빈 문자열로 저장된다.
- [ ] embedding 실패 시 batch split을 추가하지 않았고, delete path에서 사용자 관점 삭제 완료 기준을 DB hard-delete로 유지한다.
- [ ] observability는 후순위로 미뤘더라도, 공유 계약상 `trace_id` 필드는 메시지 스키마와 유스케이스 입력에서 유지된다.

### 4.2 완료 조건 (Definition of Done)

- [ ] SPEC §5.1에 정의된 시나리오 테스트가 모두 녹색이다.
- [ ] 단위·통합 테스트 합산 커버리지가 80% 이상이다 (`pytest-cov` 기준).
- [ ] Worker 테스트가 CI에서 실행된다.
- [ ] `PREPROCESS`와 `DELETE` 경로 모두에서 임시 파일 cleanup이 자동화 테스트 또는 명시적 검증으로 확인된다.

---

## 5. Rollout & Rollback Plan

### 5.1 배포 계획 (Rollout)

- **서비스 추가:** `services/pipeline-worker`를 별도 배포 단위로 추가한다.
- **환경 변수:** `BROKER_TYPE`, `DATABASE_URL`, `GCP_PROJECT_ID`, `GCS_VIDEO_BUCKET_NAME`, `EMBEDDING_API_URL`, `WORKER_CONCURRENCY`, `MAX_RETRIES`, `DOWNLOAD_TIMEOUT_SEC`, `STT_TIMEOUT_SEC`, `STT_MODEL_VERSION`, `VISION_TIMEOUT_SEC`, `EMBEDDING_TIMEOUT_SEC`, `EMBEDDING_BATCH_SIZE`, `CHUNK_MAX_TOKENS`, `CHUNK_OVERLAP_SENTENCES`를 배포 환경에 설정한다.
- **런타임 의존성:** FFmpeg 바이너리, GCS/STT 접근 권한, pgvector가 활성화된 Postgres를 준비한다.
- **스키마 반영:** 기존 DB migration 소유 경계에서 `poetry run alembic upgrade head`를 수행한 뒤 Worker를 기동한다.
- **스모크 검증:** 테스트용 `PREPROCESS_REQUEST` 1건과 `DELETE_REQUEST` 1건으로 상태 전이, artifact 적재, hard-delete를 확인한 후 concurrency를 늘린다.

### 5.2 롤백 계획 (Rollback)

- **애플리케이션 롤백:** Worker 배포를 이전 이미지/아티팩트로 되돌린다.
- **메시지 호환성:** v1 MessageEnvelope는 Core API와 Worker가 공유하므로, 애플리케이션 롤백만으로도 큐 메시지 포맷 호환성은 유지된다.
- **DB 스키마 원복:** 새 테이블/컬럼이 실제 데이터에 사용되기 시작했다면 먼저 애플리케이션만 롤백하고, DB downgrade는 데이터 보존 영향 검토 후 별도로 수행한다.
- **부분 적용 복구:** Worker만 배포 실패한 경우 Core API는 계속 메시지를 발행할 수 있으므로, 롤백 시 consumer를 멈춘 상태에서 큐 적체량과 visibility timeout을 확인한 뒤 재기동 순서를 정한다.

---

## Assumptions (확정된 사항)

- 신규 Worker 서비스 루트는 `services/pipeline-worker`로 생성한다.
- shared migration 패키지가 생기기 전까지 Worker 관련 DB 스키마는 기존 `services/core-api/alembic`가 관리한다.
- `services/pipeline-worker`의 설치/검증 표준은 `poetry install`, `poetry run poe check`, `poetry run poe coverage`를 기준으로 한다.
- `GoogleSTTAdapter`는 Worker 내부에 구현하고, 호출 계약은 `Pipeline_Worker_Spec.md`를 기준으로 유지한다.
- 통합 테스트의 PostgreSQL은 Testcontainers 또는 동등한 격리 환경을 사용하며 `pgvector`가 활성화되어 있다고 가정한다.
- `ADR-002`가 없어도 VisionAdapter 핵심 계약은 `Pipeline_Worker_Spec.md`에 inline으로 닫혀 있으므로 구현 blocker가 아니다.
