# [Media & AI Pipeline Worker] SPEC

**메타 정보**
- Component ID: `pipeline-worker`
- SOT: `docs/system-design.md`
- 관련 문서:
  - `docs/PRD.md`
  - `docs/Tech_Spec/upload_search_Service/Pipeline_Worker_Plan.md`
  - `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Managed_Embedding_Endpoint_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Model_Release_and_Reindex_Spec.md`
- Status: Draft

---

## 1. 목적과 범위

### 1.1 한 줄 요약
- Media & AI Pipeline Worker는 프로젝트 하위 영상 처리 메시지를 소비하여 미디어 전처리, STT, 청킹, 임베딩, vector projection 적재, 영상 삭제 연쇄 처리를 수행하는 비동기 워커다.

### 1.2 책임 경계
- 범위에 포함:
  - `PREPROCESS_REQUEST`, `DELETE_REQUEST` 소비와 at-least-once 중복 처리 방어
  - `video_id`로 Metadata DB의 `Video`를 조회하고 `project_id`, `user_id`, storage/source 정보를 복원
  - 외부 URL 다운로드, Object Storage 원본 확보, FFmpeg 전처리, STT, 청킹, keyframe/vision enrichment, embedding 호출
  - `TranscriptSegment`, `Asset`, `Chunk`, `VectorIndexEntry` 산출물 생성
  - `Video.status`, `failed_stage`, `error_message` 갱신
  - `VectorIndexEntry`에 `user_id`, `project_id`, `video_id`, model/index metadata를 포함
  - `DELETE_REQUEST` 또는 처리 중 `DELETING` 감지 시 관련 DB 레코드 hard-delete와 Object Storage cleanup 요청
- 범위에서 제외:
  - project 생성, project ownership API, project 하위 video API
  - search API 실행, FTS/ANN 조회, RAG 답변 생성, 검색 응답 snapshot 생성
  - project readiness gate 판정과 search error response 반환
  - rollback 중 `Project.search_serving_state` 전이와 프로젝트 검색 제외/재편입 결정
  - ML 학습, 평가, model release cutover, rollback orchestration
- 상위 의존성:
  - Core API Server가 만든 `Project` / `Video` SOT와 video-processing messages
  - Object Storage의 원본 영상 또는 외부 URL
  - `ModelRelease`의 active model-index context
- 하위 소비자:
  - Search Service의 project-scoped FTS/ANN/SOT gate
  - Admin Control Plane의 영상 처리 상태 조회
  - Model Release and Reindex의 active 전환 및 rollback 복구 흐름

### 간단한 흐름 (Simple Flow)
1. Worker는 video-processing message를 받고 `video_id`로 `Video`를 조회한다.
2. `Video.project_id`와 `user_id`를 포함한 처리 문맥을 확정하고 상태 기반 멱등성 guard를 통과한다.
3. 미디어 확보, STT, 청킹, enrichment, embedding을 수행한다.
4. 청크 SOT와 vector projection을 저장하고 검색 스코프 필터에 필요한 metadata를 함께 기록한다.
5. 성공 시 `Video.status=READY`, 최종 실패 시 `FAILED`, 삭제 요청 시 관련 DB 레코드 hard-delete로 종료한다.

### 1.3 기술 스택 선택
| 영역 (Area) | 선택안 (Choice) | 왜 이 선택인가 |
| --- | --- | --- |
| Runtime / framework | Python 3.11+, asyncio worker | 장시간 I/O와 외부 AI 호출을 API request path에서 분리한다 |
| Storage / DB | PostgreSQL, SQLAlchemy async, pgvector-compatible vector store, GCS | 영상 상태 SOT와 검색 projection을 같은 도메인 키로 연결한다 |
| Messaging / async | PGMQ 기본, broker adapter 추상화 | video-processing 요청을 at-least-once로 처리하고 test double을 둘 수 있다 |
| Media / AI | FFmpeg, STT adapter, Vision adapter, Managed Embedding Endpoint | 미디어 전처리와 모델 추론 경계를 Worker 밖/안으로 명확히 나눈다 |
| Key libraries | loguru, httpx, google-cloud-storage, google-cloud-speech | 구조화 로그, HTTP inference, GCS, STT 연동에 사용한다 |

---

## 2. 계약 (Contracts)

### 2.1 외부 인터페이스

#### 메시지 / 이벤트 계약
- Queue:
  - `PREPROCESS_REQUEST`
  - `DELETE_REQUEST`
- Producer 책임:
  - Core API Server는 project ownership과 video membership을 검증한 뒤 허용된 상태 전이에 대해서만 video-processing message를 발행한다.
- Consumer 책임:
  - Worker는 message payload의 `video_id`만 신뢰하고, user/project/storage/model 문맥은 공유 SOT에서 다시 읽는다.
  - Worker는 message를 정상 완료, 최종 실패 기록, 중복 안전 skip, 중복 삭제 success 중 하나로 닫은 뒤 Ack한다.
- 전달 의미론: at-least-once
- Payload versioning 규칙:
  - video-processing message는 `docs/system-design.md` 3.13 shared envelope를 따른다.
  - 지원하지 않는 `payload_version`은 처리하지 않고 운영자 관측 가능한 실패로 남긴다.

```json
{
  "message_type": "PREPROCESS_REQUEST",
  "payload_version": "v1",
  "trace_id": "UUID4",
  "attempt": 1,
  "video_id": "UUID4",
  "issued_at": "ISO8601_TIMESTAMP"
}
```

#### 외부 연동 컴포넌트 계약
| 의존성 | 사용 목적 | 필요한 동작 / 가정 | 실패 영향 |
| --- | --- | --- | --- |
| Metadata DB `Video` | processing context, state transition | `Video.project_id`와 `user_id`를 최신 SOT로 읽을 수 있어야 한다 | vector metadata 누락 또는 잘못된 검색 scope |
| Metadata DB artifacts | transcript/chunk/vector metadata persistence | `Chunk`와 `VectorIndexEntry`가 같은 video/project 문맥으로 원자적 저장되어야 한다 | partial search projection 또는 false empty |
| Object Storage | original video, audio, keyframe artifacts | streaming download/upload/delete를 제공해야 한다 | processing failure 또는 orphan object 발생 |
| Managed Embedding Endpoint | chunk embedding | Worker가 전달한 target `model_version`에 맞는 embedding vector를 반환해야 한다 | vector projection 생성 실패 |
| `ModelRelease` | active model-index context | 현재 online ingest 대상 active model/index를 읽을 수 있어야 한다 | active serving projection drift |
| Message Broker | async delivery and ack | at-least-once delivery와 Ack를 제공해야 한다 | duplicate processing 또는 processing delay |

### 2.2 데이터 계약

#### 소유 데이터
| 엔터티 / 테이블 | 목적 | 핵심 필드 / 불변조건 | 비고 |
| --- | --- | --- | --- |
| `TranscriptSegment` | STT 결과의 시간 구간 SOT | `video_id`, `start_ms`, `end_ms`, `text`, `stt_model_version` | 실패 재개와 재청킹의 입력이다 |
| `Asset` | 파생 미디어 파일 포인터 | `video_id`, `asset_type`, `storage_path` | audio/keyframe object를 추적한다 |
| `Chunk` | 검색 근거 텍스트 SOT | `video_id`, `text`, `enriched_text`, time range, `embedding_model_version` | Search Service 최종 context의 SOT다 |
| `VectorIndexEntry` | ANN 검색 projection | `index_name`, `chunk_id`, `user_id`, `project_id`, `video_id`, `embedding_model_version` | FTS/ANN/SOT gate가 같은 project scope를 공유하게 하는 필수 metadata다 |

#### 참조 데이터
| SOT 소유자 | 엔터티 / 테이블 | 의존 필드 | 읽기 전용 가정 |
| --- | --- | --- | --- |
| Core API | `Video` | `id`, `project_id`, `user_id`, `status`, `input_type`, `source_url`, `storage_path`, `failed_stage` | Worker는 영상의 소속 project를 `Video.project_id`에서 복원한다 |
| ML ops | `ModelRelease` | active model version and index name | online ingest는 현재 active target 한 곳만 사용한다 |

### 2.3 상태 및 비즈니스 규칙
- 항상 유지되어야 하는 불변조건:
  - `VectorIndexEntry`는 대응 `Chunk`, `Video`, `Project` 문맥을 잃지 않아야 하며 `project_id` 없이 저장될 수 없다.
  - Worker가 `READY`로 완료한 영상은 Search Service의 project-internal readiness gate 입력이 된다.
  - project 내부 all-or-nothing 검색 가능 여부는 Search Service가 `Project`와 project 하위 `Video` 상태를 읽어 판단한다.
  - rollback 중 프로젝트 검색 제외/재편입 상태는 Model Release and Reindex가 관리하고 Worker는 영상 산출물만 갱신한다.
  - `CANDIDATE_REINDEXING` 중에도 online ingest는 기존 active model/index 한 곳에만 기록하며, candidate는 cutover 후 active가 된 뒤부터 신규 데이터를 받는다.
- 이 컴포넌트가 소유하는 허용 상태 전이:
  - 초기/재개 processing: `PENDING` / `UPLOADED` / `FAILED` -> `PROCESSING`
  - 성공한 processing: `PROCESSING` -> `READY`
  - 최종 processing failure: `PROCESSING` -> `FAILED`
  - delete processing: `DELETING` 또는 `DELETE_REQUEST` -> related artifact row와 `Video` hard-delete
- 거부되어야 하는 조건:
  - `Video`가 없거나 `project_id`가 없는 preprocess message
  - current target model/index 산출물이 이미 존재하는 중복 preprocess message
  - `DELETING` 감지 이후 외부 AI 호출 결과를 새 산출물로 저장하는 동작
  - `Project.search_serving_state`를 Worker가 직접 변경하는 동작
- 멱등성 규칙:
  - 기본 멱등 키는 `video_id`이며, model/index 공존 시 target `embedding_model_version`과 `index_name`을 함께 고려한다.
  - 같은 target 산출물이 이미 완성된 `READY` 영상의 preprocess message는 skip 후 Ack한다.
  - 삭제 대상 `Video`가 이미 없으면 duplicate delete success로 Ack한다.
- 멀티테넌트 / 인가 규칙:
  - Worker는 public user auth surface가 아니며, tenancy enforcement는 upstream Core API와 downstream Search Service가 수행한다.
  - Worker가 만드는 모든 searchable projection은 downstream tenancy filter를 위해 `user_id`와 `project_id`를 보존한다.

### 2.4 한계와 운영 제약
- 성능 / 지연 목표:
  - Worker processing은 비동기 batch/background latency로 취급하며 user request SLA와 분리한다.
- Throughput / rate / concurrency 한계:
  - `WORKER_CONCURRENCY`, embedding batch size, external AI timeout은 설정값으로 운영한다.
- Payload / 파일 크기 한계:
  - 사용자-facing file limit은 Core API가 검증한다. Worker는 실제 storage object와 media decoder failure를 최종 확인한다.
- Timeout / retry 제약:
  - network/provider/storage transient failure는 제한된 retry 후 실패 상태로 닫는다.
  - Vision enrichment failure는 검색 품질 보조 단계 failure로 취급하고 text-only chunk로 계속 진행할 수 있다.
- 보안 / 개인정보 제약:
  - source URL, transcript, chunk text는 사용자 데이터로 취급한다.
  - logs는 `trace_id`, `user_id`, `project_id`, `video_id`, stage 중심으로 남기고 원문 transcript/chunk는 최소화한다.

### 2.5 에러 계약
| 표면 | 조건 | 코드 / 상태 | 재시도 가능 | 비고 |
| --- | --- | --- | --- | --- |
| Message validation | unsupported payload version 또는 malformed envelope | terminal message failure | N | 운영 로그에 `trace_id`와 reason을 남긴다 |
| Metadata lookup | preprocess 대상 `Video` 누락 | safe skip 또는 terminal failure | N | 삭제 메시지에서는 duplicate delete success다 |
| Metadata invariant | `Video.project_id` 누락 | row가 존재하면 `Video.status=FAILED` | N | searchable projection을 만들 수 없다 |
| Media/storage | object 누락, unsupported media, corrupt file | `FAILED`와 `failed_stage` 기록 | N | 요청자 또는 데이터 문제로 취급한다 |
| External dependency | transient storage/STT/embedding/broker failure | retry 후 `FAILED` | Y | retry exhaustion 후 Ack한다 |
| Vision enrichment | caption/OCR/tag extraction failure | fallback | Y | 전체 pipeline 실패로 확대하지 않는다 |
| Final persistence | chunk/vector/status transaction failure | retry 후 `FAILED` | Y | partial projection을 남기지 않아야 한다 |

---

## 3. 관측성과 운영

- 필수 log field:
  - `trace_id`, `message_type`, `video_id`, `project_id`, `user_id`, `stage`, `failed_stage`, `embedding_model_version`, `index_name`
- 추적할 핵심 metric / alert:
  - `pipeline_message_count` by message type and result
  - `pipeline_processing_latency_ms` by stage
  - `pipeline_failed_count` by `failed_stage`
  - `pipeline_vector_upsert_count` by `index_name`
  - `pipeline_project_id_missing_count`
  - `pipeline_delete_success_count`
  - `pipeline_vision_fallback_count`
- Trace / correlation 전파 규칙:
  - message `trace_id`는 지원되는 범위에서 storage, embedding, STT, DB, structured log로 전파한다.
- Reconciliation / cleanup 요구사항:
  - DB transaction failure는 같은 target에 대한 vector metadata 없이 chunk row만 남기면 안 된다.
  - DB hard-delete가 사용자에게 보이는 delete 완료 지점이며, object storage orphan cleanup은 비동기로 재시도할 수 있다.
  - Search Service가 반복적인 vector/SOT mismatch를 관측하면 projection drift로 취급하고 reprocessing 또는 reindex tooling으로 조정한다.

---

## 4. 인수 기준

### 4.1 반드시 통과해야 하는 시나리오
- [ ] Project video에 대한 `PREPROCESS_REQUEST`는 transcript/chunk/vector artifact를 생성하고 `VectorIndexEntry.user_id`, `project_id`, `video_id`를 저장한다.
- [ ] 성공한 processing은 target `Video`만 `READY`로 만들며, project search readiness는 Search Service가 project state와 project video state에서 도출한다.
- [ ] Candidate 준비 중 online ingest는 기존 active target에만 기록하고, cutover 후에는 새 active target에 기록한다.
- [ ] Processing failure는 Ack 전에 `Video.status=FAILED`, `failed_stage`, 운영자가 볼 수 있는 error context를 기록한다.
- [ ] Duplicate preprocess와 duplicate delete message는 중복 searchable artifact 없이 안전하게 종료된다.
- [ ] `DELETE_REQUEST` 또는 `DELETING` 감지 시 관련 DB row를 hard-delete하여 해당 video가 FTS, ANN, SOT-gated search result에 나타나지 않게 한다.
- [ ] Worker는 `Project.search_serving_state`를 변경하지 않으며, rollback project exclusion과 re-inclusion은 admin/model-release 책임으로 유지된다.

### 4.2 비목표 / 보류 항목
- Search API project scope, readiness error response, FTS/ANN retrieval, `SearchResponseSnapshot` 생성은 Search Service가 소유한다.
- Project 생성/목록/수정과 project 하위 video upload API는 Core API Server가 소유한다.
- Rollback 영향 선택, project exclusion, restored-model reembedding orchestration, project re-inclusion은 Model Release and Reindex가 소유한다.
