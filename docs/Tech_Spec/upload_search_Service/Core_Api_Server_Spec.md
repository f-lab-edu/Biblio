# [Core API Server] SPEC

**메타 정보**
- Component ID: `core-api-server`
- SOT: `docs/system-design.md`
- 관련 문서:
  - `docs/PRD.md`
  - `docs/Tech_Spec/upload_search_Service/Core_Api_Server_Plan.md`
  - `docs/Tech_Spec/upload_search_Service/Search_Service_Spec.md`
  - `docs/Tech_Spec/upload_search_Service/Pipeline_Worker_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Feedback_Ingestion_Pipeline_Spec.md`
  - `docs/Tech_Spec/feedback_loop_&_admin_ops/Admin_Control_Plane_Spec.md`
- Status: Draft

---

## 1. 목적과 범위

### 1.1 한 줄 요약
- Core API Server는 인증된 사용자의 프로젝트와 프로젝트 하위 영상 메타데이터를 관리하고, 업로드/삭제/재처리/피드백 요청을 후속 비동기 컴포넌트로 연결하는 사용자-facing API다.

### 1.2 책임 경계
- 범위에 포함:
  - 사용자 소유 `Project` 생성, 조회, 수정
  - 프로젝트 하위 `Video` 생성, 업로드 완료 접수, 조회, 수정, 삭제 요청, 재처리 요청
  - 로컬 파일 업로드용 Signed URL 발급과 외부 URL 영상 처리 요청 접수
  - `PREPROCESS_REQUEST`, `DELETE_REQUEST`, validated feedback event 발행
  - 검색 응답 피드백 요청의 `SearchResponseSnapshot` 기반 검증
- 범위에서 제외:
  - 검색 실행, RAG 답변 생성, 검색 응답 스냅샷 생성
  - 미디어 다운로드, STT, 청킹, 임베딩, Vector Store 적재, 연쇄 hard-delete
  - admin 전용 조회/운영 액션의 세부 HTTP 계약
  - 모델 릴리스, rollback 복구, 프로젝트 검색 제외 상태 전이
- 상위 의존성:
  - Client Web UI
  - JWT issuer
  - Search Service가 저장한 `SearchResponseSnapshot`
- 하위 소비자:
  - Media & AI Pipeline Worker
  - Feedback Ingestion Pipeline
  - Admin Control Plane

### 간단한 흐름 (Simple Flow)
1. 사용자는 프로젝트를 만들고, 해당 프로젝트 안에 로컬 파일 또는 외부 URL 영상을 추가한다.
2. Core API는 JWT를 검증하고 프로젝트 소유권을 확인한 뒤 영상 메타데이터와 초기 상태를 저장한다.
3. 로컬 파일은 Signed URL 업로드 완료 신호 이후, 외부 URL은 접수 직후 video-processing 메시지를 발행한다.
4. 삭제와 재처리 요청은 상태 guard를 통과한 경우에만 상태를 갱신하고 video-processing 메시지를 발행한다.
5. 피드백 요청은 `req_id`로 검색 응답 스냅샷을 검증한 뒤 feedback event로 발행한다.

### 1.3 기술 스택 선택
| 영역 (Area) | 선택안 (Choice) | 왜 이 선택인가 |
| --- | --- | --- |
| Runtime / framework | Python 3.11+, FastAPI async | repo의 서비스 구조와 JWT/API middleware 계약을 재사용한다 |
| Storage / DB | PostgreSQL, SQLAlchemy 2 async, Alembic | 프로젝트/영상 상태와 검색 응답 스냅샷 검증에 ACID SOT가 필요하다 |
| Object storage | GCS Signed URL | 대용량 영상 업로드를 API 서버 경유 없이 처리한다 |
| Messaging / async | PGMQ 기본, broker adapter 추상화 | video-processing 요청과 feedback event를 사용자 동기 경로에서 분리한다 |
| Key libraries | Pydantic settings, PyJWT, google-cloud-storage | 설정, 인증, Signed URL 발급을 명시적 계약으로 관리한다 |

---

## 2. 계약 (Contracts)

### 2.1 외부 인터페이스

#### 외부 진입 인터페이스
| 인터페이스 | 메서드 / 트리거 | 입력 요약 | 출력 요약 | 인증 / 테넌시 | 비고 |
| --- | --- | --- | --- | --- | --- |
| `/api/v1/projects` | `POST` | `title`, 선택적 `description` | `201`, project metadata | JWT 사용자가 생성된 project를 소유 | 기본 검색 노출 상태는 `SERVABLE` |
| `/api/v1/projects` | `GET` | `cursor?`, `limit?` | `200`, project page | JWT 사용자의 project만 반환 | keyset pagination |
| `/api/v1/projects/{project_id}` | `GET`, `PATCH` | path project, 수정 가능한 metadata | `200`, project metadata | `Project.user_id=requester_user_id` | 사용자 경로는 검색 노출 상태를 변경하지 않는다 |
| `/api/v1/projects/{project_id}/videos` | `POST` | `LOCAL_FILE` metadata 또는 `EXTERNAL_URL` metadata | local: `201` + Signed URL, external: `202` | project ownership required | 영상은 프로젝트 하위에 생성된다 |
| `/api/v1/projects/{project_id}/videos/{video_id}/complete` | `POST` | upload completion metadata | 첫 성공 `202`, 멱등 성공 `200` | project ownership + video membership | local file upload completion only |
| `/api/v1/projects/{project_id}/videos` | `GET` | `cursor?`, `limit?` | `200`, video page | project ownership 필요 | 프로젝트 내부 영상 목록 |
| `/api/v1/projects/{project_id}/videos/{video_id}` | `GET`, `PATCH`, `DELETE` | path video, 수정 가능한 metadata 또는 delete request | `200` 또는 `202` | project ownership + video membership | delete는 요청 접수 후 async 처리 |
| `/api/v1/projects/{project_id}/videos/{video_id}/retry` | `POST` | 빈 body | `202`, retry requested | project ownership + video membership | `FAILED` 영상만 허용 |
| `/api/v1/projects/{project_id}/videos/{video_id}/playback-url` | `POST` | 빈 body | `200`, Signed URL | project ownership + video membership | `READY` local file 영상만 허용 |
| `/api/v1/feedbacks` | `POST` | `req_id`, `rating` | `201`, feedback accepted | snapshot user가 requester와 일치해야 함 | 검색 문맥은 snapshot에서 복원한다 |

#### 메시지 / 이벤트 계약
- Queue:
  - `PREPROCESS_REQUEST`
  - `DELETE_REQUEST`
  - feedback event queue는 `Feedback_Ingestion_Pipeline_Spec.md`가 정한다.
- Producer 책임:
  - Core API는 허용된 video 상태 전이 이후 video-processing 메시지를 발행한다.
  - Core API는 `SearchResponseSnapshot` 검증을 통과한 피드백만 feedback event로 발행한다.
- Consumer 책임:
  - Pipeline Worker는 `video_id`로 Metadata DB를 조회하여 처리 문맥을 복원한다.
  - Feedback Ingestion Pipeline은 validated feedback event를 append-only raw log로 저장한다.
- 전달 의미론: at-least-once
- Payload versioning 규칙:
  - video-processing 메시지는 `docs/system-design.md` 3.13의 shared envelope를 따른다.
  - feedback event payload는 `docs/system-design.md`의 Feedback Event와 FIP spec을 따른다.

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
| Metadata DB | project/video 상태 저장, snapshot 검증 | project ownership과 video membership을 같은 트랜잭션 경계에서 검증할 수 있어야 한다 | 잘못된 테넌시 허용 또는 상태 불일치 |
| Object Storage | local file upload/playback URL | 객체 존재 여부와 크기 메타데이터를 조회할 수 있어야 한다 | upload completion 검증 실패 |
| Message Broker | async processing/event publish | at-least-once 발행과 trace propagation을 지원해야 한다 | 후속 처리 지연 또는 사용자 요청 실패 |
| Search Service | `SearchResponseSnapshot` 생성 | `req_id`, 사용자, 프로젝트, 질의/청크/모델 문맥을 TTL 동안 보존해야 한다 | 피드백 검증 불가 |

### 2.2 데이터 계약

#### 소유 데이터
| 엔터티 / 테이블 | 목적 | 핵심 필드 / 불변조건 | 비고 |
| --- | --- | --- | --- |
| `Project` | 사용자 소유 업로드/검색 단위 | `id`, `user_id`, `title`, `description`, `search_serving_state` | 사용자 경로의 권한 기준은 `Project.user_id` |
| `Video` | 프로젝트 하위 영상 메타데이터와 처리 상태 | `id`, `project_id`, `user_id`, `status`, `input_type`, `source_url`, `storage_path`, `failed_stage` | `Video.project_id`는 요청 path의 project와 일치해야 한다 |

#### 참조 데이터
| SOT 소유자 | 엔터티 / 테이블 | 의존 필드 | 읽기 전용 가정 |
| --- | --- | --- | --- |
| Search Service | `SearchResponseSnapshot` | `req_id`, `user_id`, `project_id`, `query_text`, `topk_chunk_ids`, `used_chunk_ids`, active model/index fields, `expires_at` | Core API는 snapshot을 생성하지 않고 피드백 검증에만 사용한다 |
| Admin / ML ops | `Project.search_serving_state` | `SERVABLE`, `ROLLBACK_EXCLUDED` | 사용자 경로는 운영 상태를 직접 전이하지 않는다 |

### 2.3 상태 및 비즈니스 규칙
- 항상 유지되어야 하는 불변조건:
  - 사용자 경로는 JWT의 requester가 소유한 프로젝트와 그 하위 영상에만 접근한다.
  - 영상 경로는 project ownership 확인 후 `Video.project_id` 일치를 추가로 검증한다.
  - 피드백 이벤트의 검색 문맥은 클라이언트 입력이 아니라 `SearchResponseSnapshot`에서 복원한다.
- 이 컴포넌트가 소유하는 허용 상태 전이:
  - local upload completion: `PENDING -> UPLOADED`
  - 사용자 delete request: 모든 video state -> `DELETING`
  - 사용자 retry request: `FAILED -> PENDING`
- 거부되어야 하는 조건:
  - 다른 사용자의 프로젝트 또는 프로젝트 밖의 영상 접근
  - `DELETING` 영상 수정
  - `FAILED`가 아닌 영상 재처리 요청
  - 만료, 미존재, 다른 사용자 소유의 `SearchResponseSnapshot` 기반 feedback 요청
- 멱등성 규칙:
  - `/complete`는 이미 `UPLOADED`, `PROCESSING`, `READY`인 영상에 대해 추가 publish 없이 성공으로 처리한다.
  - async message와 feedback event는 at-least-once 전달을 전제로 downstream 중복 처리를 허용한다.
- 멀티테넌트 / 인가 규칙:
  - 사용자 경로는 `Project.user_id`를 기본 테넌시 SOT로 삼는다.
  - admin 경로의 별도 권한 규칙은 Admin Control Plane spec이 소유한다.

### 2.4 한계와 운영 제약
- 성능 / 지연 목표:
  - 영상 업로드/삭제/재처리 요청은 후속 작업을 비동기로 넘기고 접수 응답을 반환한다.
- Payload / 파일 크기 / pagination 한계:
  - local file 최대 크기: 2GB
  - supported upload extensions: `.mp4`, `.webm`, `.mov`, `.mkv`, `.avi`, `.wmv`
  - project/video list page size: default 20, max 50
- Timeout / TTL / retry 제약:
  - Signed URL TTL: 30분
  - `/complete`는 객체 존재와 크기를 재검증한다.
  - `SearchResponseSnapshot`은 `expires_at`이 지나면 feedback 검증에 사용할 수 없다.
- 보안 / 개인정보 제약:
  - `source_url`, query text, feedback event는 사용자 데이터로 취급하고 trace 가능한 최소 필드만 로그에 남긴다.

### 2.5 에러 계약
| 표면 | 조건 | 코드 / 상태 | 재시도 가능 | 비고 |
| --- | --- | --- | --- | --- |
| User API | invalid input, unsupported extension, bad cursor, object missing/too large, bad rating | `400 INVALID_ARGUMENT` | N | 요청자가 수정해야 한다 |
| User API | missing/invalid JWT | `401 UNAUTHENTICATED` | N | 인증 실패 |
| User API | project ownership or video membership violation | `403 FORBIDDEN` | N | 테넌시 위반 |
| User API | unknown project, video, or snapshot | `404 NOT_FOUND` | N | 존재성 검증 실패 |
| User API | invalid state transition | `409 CONFLICT` | N | 상태 guard 실패 |
| Internal dependency | DB, storage, broker failure after retry | `500 INTERNAL_ERROR` | Y | 운영자 관측 대상 |

- 표준 에러 응답 형태:
```json
{"code":"ERROR_CODE","message":"human-readable summary","trace_id":"UUID4"}
```

---

## 3. 관측성과 운영

- 필수 log field:
  - `trace_id`, `user_id`, 해당하는 경우 `project_id`, 해당하는 경우 `video_id`, feedback용 `req_id`
- 추적할 핵심 metric / alert:
  - `gcs_signed_url_latency_ms`
  - `mq_publish_fail_count`
  - `complete_idempotent_hit_count`
  - `cursor_decode_fail_count`
  - `feedback_publish_fail_count`
- Trace / correlation 전파 규칙:
  - HTTP request trace id는 video-processing message와 feedback event로 전파한다.
- Reconciliation / cleanup 요구사항:
  - Core API는 media pipeline cleanup과 hard-delete reconciliation을 Pipeline Worker에 맡긴다.
  - 만료된 `SearchResponseSnapshot` cleanup은 Search Service 또는 해당 storage policy가 소유한다.

---

## 4. 인수 기준

### 4.1 반드시 통과해야 하는 시나리오
- [ ] Project API는 requester가 소유한 프로젝트만 생성, 조회, 수정할 수 있게 한다.
- [ ] Project 하위 video API는 project ownership과 video membership을 모두 강제한다.
- [ ] Local file ingest는 Signed URL 발급, upload completion 검증, `PREPROCESS_REQUEST` 발행까지 계약대로 동작한다.
- [ ] External URL ingest, delete, retry는 허용 상태에서만 상태를 갱신하고 적절한 async message를 발행한다.
- [ ] Feedback API는 `req_id`로 snapshot을 검증하고 snapshot 문맥을 feedback event로 매핑한다.
- [ ] 선언된 에러 계약과 `/complete` 멱등 규칙이 외부에서 관찰 가능하게 유지된다.

### 4.2 비목표 / 보류 항목
- Search Service의 project-scoped retrieval과 `SearchResponseSnapshot` 생성은 이 spec의 구현 대상이 아니다.
- Pipeline Worker의 미디어 처리, 임베딩, Vector Store 적재, hard-delete는 이 spec의 구현 대상이 아니다.
- Admin 전용 운영 액션과 rollback project exclusion 전이는 admin-ops 계열 spec이 다룬다.
