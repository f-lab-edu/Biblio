# Biblio Sequence Diagrams

- 기준일: 2026-08-05
- 기준 브랜치: `feat/104-search-load-test-baseline`
- 이 문서는 **현재 코드가 실제로 실행하는 순서**를 그린다. 컴포넌트 구성과 데이터 모델은 `docs/system-design.md`를 따른다.
- 같은 폴더의 `SD-01`~`SD-06` PNG는 이전 설계 시점 그림이며 이 문서가 최신이다.

**읽는 법**

- 화살표 옆에는 그 호출로 오가는 데이터를 적었다. 무엇이 무엇으로 바뀌는지가 드러나게 입력 → 출력 형태로 쓴다.
- `Note`는 저장 위치와 상태 전이를 표시한다.
- 각 다이어그램 아래 표에 "무엇이 어디에 남는가"를 정리했다.

**공통 참여자**

| 표기 | 뜻 |
|---|---|
| `FE` | Frontend. 화면과 백엔드 프록시를 겸한다 |
| `API` | Core API |
| `SS` | Search Service |
| `PW` | Pipeline Worker |
| `EMB` | Managed Embedding Endpoint |
| `DB` | Metadata DB (SOT) |
| `VS` | Vector Store. 물리적으로는 `DB`와 같은 인스턴스의 벡터 테이블 |
| `MQ` | Message Broker. 물리적으로는 `DB` 안의 큐 |
| `OS` | Object Storage |

---

# 1. 영상 등록

로컬 파일과 외부 URL은 큐를 발행하는 시점이 다르다. 로컬 파일은 업로드가 끝난 뒤, 외부 URL은 즉시 발행한다.

```mermaid
sequenceDiagram
autonumber
participant C as 브라우저
participant FE as Frontend (프록시)
participant API as Core API
participant DB as Metadata DB (SOT)
participant OS as Object Storage
participant MQ as Message Broker

C->>FE: POST /api/v1/projects/{project_id}/videos<br/>{title, category, input_type, source_url?}<br/>+ 인증 쿠키 또는 Bearer 토큰
FE->>API: 같은 경로로 전달<br/>인증 정보 원본 유지 + 백엔드 호출 토큰 추가
API->>API: JWT 검증 → requester_user_id 추출<br/>쿠키 인증이면 CSRF 쿠키·헤더 일치 확인
API->>DB: 프로젝트 조회 {project_id}
DB-->>API: {user_id, lifecycle_state, search_serving_state}
API->>API: 소유권 확인<br/>DELETING 또는 ROLLBACK_EXCLUDED면 409로 중단
API->>API: video_id(UUID) 생성<br/>→ storage_path 확정
API->>DB: Video 1행 INSERT (단일 트랜잭션)<br/>{video_id, project_id, user_id, title, category,<br/>input_type, source_url, storage_path, status=PENDING}
DB-->>API: OK

alt input_type = LOCAL_FILE
    API->>OS: 업로드용 서명 URL 발급<br/>{object_name=storage_path, 크기 상한}
    OS-->>API: {url, expires_at, 필수 헤더}
    API-->>C: 201 {video_id, status=PENDING, signed_url, upload_headers}
    C->>OS: PUT 원본 영상(binary)
    OS-->>C: 200
    C->>FE: POST /api/v1/videos/{video_id}/complete
    FE->>API: 전달
    API->>OS: 업로드된 객체 크기 조회 {storage_path}
    OS-->>API: size_bytes
    alt 크기 상한 초과
        API->>DB: Video.status=DELETING
        API->>MQ: DELETE_REQUEST 발행<br/>{video_ids:[video_id], trace_id, attempt:1}
        API-->>C: 413 파일 크기 초과
    else 정상
        API->>DB: Video.status=UPLOADED
        API->>MQ: PREPROCESS_REQUEST 발행<br/>{video_ids:[video_id], trace_id, attempt:1}
        API-->>C: 202 {video_id, status=UPLOADED}
    end
else input_type = EXTERNAL_URL
    API->>MQ: PREPROCESS_REQUEST 즉시 발행<br/>(다운로드를 워커에 위임)
    API-->>C: 202 {video_id, status=PENDING}
end

Note over DB: 이 시점 상태 — LOCAL_FILE은 UPLOADED, EXTERNAL_URL은 PENDING
```

**이미 처리 중인 영상에 완료 신호가 다시 오면** 오류 대신 현재 상태를 200으로 돌려준다. 큐 발행이 실패한 업로드를 사용자가 다시 눌러 복구할 수 있게 하기 위해서다.

| 데이터 | 어디에 남는가 |
|---|---|
| 원본 영상 파일 | Object Storage `storage_path` |
| 영상 메타데이터와 `status` | Metadata DB `video` |
| 처리 요청 | Message Broker `PREPROCESS_REQUEST` 큐 |

---

# 2. 영상 처리와 색인

워커 프로세스 하나가 다운로드부터 적재까지 끝까지 들고 간다. 단계 사이마다 처리 시각을 갱신하고 삭제 요청을 다시 확인한다.

```mermaid
sequenceDiagram
autonumber
participant MQ as Message Broker
participant PW as Pipeline Worker
participant DB as Metadata DB (SOT)
participant OS as Object Storage
participant STT as 음성 인식
participant VIS as Vision
participant EMB as Managed Embedding Endpoint
participant VS as Vector Store

MQ-->>PW: PREPROCESS_REQUEST 수신<br/>{video_ids, trace_id, attempt}
PW->>DB: 영상과 기존 산출물 상태 조회 {video_id}
DB-->>PW: {status, input_type, source_url, storage_path,<br/>has_audio_asset, has_transcript}
Note over PW: status=DELETING이면 삭제 흐름(5장)으로 넘어감<br/>이미 READY이고 현재 모델 기준 산출물이 있으면 건너뜀
PW->>DB: 처리권 확보 (status=PROCESSING, processing_claimed_at=now)
DB-->>PW: 확보 성공 / 실패(다른 워커 처리 중이면 물러남)

rect rgb(235, 243, 255)
Note over PW, OS: DOWNLOAD — 원본을 로컬 작업 폴더로
alt EXTERNAL_URL
    PW->>PW: 원본 링크에서 영상 다운로드<br/>(길이·용량·화질 상한 적용)
    PW->>OS: 원본 영상 업로드 {storage_path}
else LOCAL_FILE
    PW->>OS: 원본 영상 다운로드 {storage_path}
    OS-->>PW: source 파일
end
end

rect rgb(235, 250, 240)
Note over PW, OS: EXTRACT — 영상 → 오디오(FLAC)
PW->>PW: 오디오 추출 + 길이 측정<br/>상한 초과면 SOURCE_LIMIT_EXCEEDED
PW->>OS: 오디오 업로드 {artifacts/{video_id}/audio.flac}
PW->>DB: Asset UPSERT {asset_type=AUDIO, storage_path, start_ms=0, end_ms=길이}
end

rect rgb(255, 250, 235)
Note over PW, STT: STT — 오디오 → 텍스트 + 타임스탬프
alt 오디오가 길이 상한 초과
    PW->>PW: 오디오를 겹침 구간 포함해 여러 조각으로 분할
    PW->>OS: 조각 업로드
    PW->>STT: 조각별 인식 요청 (동시 실행 제한 적용)
    STT-->>PW: 조각별 segments
    PW->>PW: 겹침 구간 기준으로 병합 + 타임스탬프 보정
else 상한 이내
    PW->>STT: 인식 요청 {audio object uri}
    STT-->>PW: segments{text, start_ms, end_ms} + stt_model_version
end
PW->>DB: TranscriptSegment 일괄 저장<br/>{segment_index, text, start_ms, end_ms, stt_model_version}
end

rect rgb(250, 240, 255)
Note over PW, VIS: CHUNKING — segments → chunk + 화면 정보 보강
PW->>DB: 현재 적재 대상 조회 (ModelRelease)
DB-->>PW: {active_model_version, active_index_name}<br/>ROLLBACK_PREPARING이면 적재 중단
PW->>PW: 문장 경계를 지키며 토큰 상한까지 묶어 chunk 생성<br/>(앞 문장 일부를 겹쳐 문맥 유지)
loop chunk마다 (동시 실행 제한 적용)
    PW->>PW: chunk 중간 지점의 키프레임 추출
    PW->>OS: 키프레임 업로드 {artifacts/{video_id}/keyframes/{i}.jpg}
    PW->>DB: Asset UPSERT {asset_type=KEYFRAME, start_ms, end_ms}
    DB-->>PW: keyframe_asset_id
    PW->>VIS: 키프레임 해석 요청
    VIS-->>PW: {visual_caption, ocr_text, scene_tags}<br/>실패 시 빈 값으로 진행
    PW->>PW: enriched_text = 정규화(chunk.text + caption + ocr + tags)
end
end

rect rgb(255, 240, 240)
Note over PW, EMB: EMBEDDING — enriched_text → 벡터
loop 배치 단위
    PW->>EMB: POST /embed<br/>{texts:[enriched_text...], model_version=active}<br/>헤더 X-Embedding-Workload: video_preprocess
    EMB-->>PW: {embeddings[]} 또는 503(대기열/슬롯 초과 시 재시도)
end
end

rect rgb(240, 240, 240)
Note over PW, VS: VECTOR_UPSERT — 단일 트랜잭션 적재
PW->>DB: Chunk 저장<br/>{chunk_index, text, enriched_text, start_ms, end_ms,<br/>keyframe_asset_id, chunking_version, stt/embedding_model_version,<br/>visual_caption, ocr_text, scene_tags}
PW->>VS: VectorIndexEntry UPSERT<br/>{index_name=active_index_name, chunk_id, user_id,<br/>project_id, video_id, embedding_vector}
PW->>DB: Video.status=READY
Note over VS: 적재 대상은 active 인덱스 하나뿐이다.<br/>후보 모델이 준비 중이어도 이중으로 쓰지 않는다
end
```

| 변환 | 결과가 남는 곳 |
|---|---|
| 영상 → 오디오 | Object Storage + `asset`(AUDIO) |
| 오디오 → 대본 구간 | `transcript_segment` |
| 대본 → 청크 | `chunk.text` |
| 청크 + 키프레임 해석 → 검색용 텍스트 | `chunk.enriched_text` (키워드 검색과 임베딩의 입력) |
| 키프레임 이미지 | Object Storage + `asset`(KEYFRAME) |
| 검색용 텍스트 → 벡터 | `vector_index_entry.embedding_vector` (active 인덱스) |

---

# 3. 처리 실패와 재시도

실패는 단계와 코드로 분류해 남긴다. 재시도는 사용자가 `FAILED` 상태에서만 걸 수 있고, 워커는 남아 있는 산출물을 보고 건너뛴다.

```mermaid
sequenceDiagram
autonumber
participant C as 브라우저
participant FE as Frontend (프록시)
participant API as Core API
participant DB as Metadata DB (SOT)
participant MQ as Message Broker
participant PW as Pipeline Worker

rect rgb(255, 240, 240)
Note over PW, DB: 실패 기록
PW->>PW: 예외를 실패 단계와 실패 코드로 분류<br/>DOWNLOAD/EXTRACT/STT/CHUNKING/EMBEDDING/VECTOR_UPSERT<br/>× YOUTUBE_BLOCKED / SOURCE_UNAVAILABLE / SOURCE_LIMIT_EXCEEDED /<br/>AUDIO_EXTRACTION_FAILED / STT_FAILED / EMBEDDING_FAILED /<br/>INDEX_WRITE_FAILED / INTERNAL_PROCESSING_ERROR
PW->>DB: Video 갱신<br/>{status=FAILED, failed_stage, failure_code, failure_trace_id}
Note over DB: 중간 산출물(오디오·키프레임·대본)은 지우지 않는다
PW->>MQ: 메시지 Ack (별도 실패 큐로 옮기지 않음)
end

rect rgb(235, 243, 255)
Note over C, PW: 사용자 재시도
C->>FE: GET /api/v1/videos/{video_id}
FE->>API: 전달
API->>DB: 조회
DB-->>API: {status=FAILED, failed_stage, failure_code}
API-->>C: 실패 상태와 사유 코드
C->>FE: POST /api/v1/videos/{video_id}/retry
FE->>API: 전달
API->>DB: 상태 확인 — FAILED가 아니면 409
API->>DB: Video 갱신<br/>{status=PENDING, failed_stage=null,<br/>failure_code=null, failure_trace_id=null}
API->>MQ: PREPROCESS_REQUEST 재발행 {attempt 증가}
Note over API: 큐 발행이 실패하면 이전 실패 메타데이터를 되돌린다
API-->>C: 202 {video_id, status=PENDING}
MQ-->>PW: PREPROCESS_REQUEST
PW->>DB: 기존 산출물 확인
DB-->>PW: has_audio_asset, has_transcript
PW->>PW: 오디오가 있으면 추출 건너뜀<br/>대본이 있으면 음성 인식 건너뜀<br/>청킹부터 다시 수행
end
```

`failed_stage`는 분류값이며 재개 지점과 1:1이 아니다. 청크와 검색용 텍스트는 임베딩 전에 따로 저장하지 않으므로, 임베딩·적재 실패는 청킹부터 다시 한다.

**워커가 죽은 경우** — 처리 시각이 기준 시간보다 오래 멈춘 항목은 다른 워커가 처리권을 다시 가져간다. 이 기준 시간은 큐 가시성 타임아웃보다 짧아야 하며 워커 시작 시 검증한다.

---

# 4. 검색과 답변 생성

```mermaid
sequenceDiagram
autonumber
participant C as 브라우저
participant FE as Frontend (프록시)
participant SS as Search Service
participant EMB as Managed Embedding Endpoint
participant DB as Metadata DB (SOT)
participant VS as Vector Store
participant LLM as LLM

C->>FE: POST /api/v1/search {project_id, query}
FE->>FE: 첫 경로 조각이 search → Search Service로 분기
FE->>SS: 전달 (인증 정보 원본 유지 + 백엔드 호출 토큰)
SS->>SS: JWT 검증 → requester_user_id<br/>쿠키 인증이면 CSRF 확인
SS->>SS: 질의 정규화 → 2~1,000자 검사

SS->>DB: 코퍼스 준비 상태 1회 조회<br/>{user_id, project_id, lifecycle_state=ACTIVE}
DB-->>SS: {total_videos, non_ready_count}
Note over SS: total_videos=0 → "업로드된 영상 없음"<br/>non_ready_count>0 → "아직 검색 준비 중"

SS->>SS: 검색 대상 조회 (프로세스 캐시)<br/>→ active [+ previous] {model_version, index_name}

par 대상별 질의 임베딩 (동시)
    SS->>EMB: POST /embed {texts:[query], model_version=active}<br/>헤더 X-Embedding-Workload: search
    EMB-->>SS: {embeddings:[active_vector]}
and
    SS->>EMB: POST /embed {texts:[query], model_version=previous}
    EMB-->>SS: {embeddings:[previous_vector]}
end
Note over EMB: 검색 요청은 대기열에서 먼저 꺼낸다.<br/>접수 상한이나 대기 상한을 넘으면 503

par 후보 생성 (동시)
    SS->>DB: 키워드 검색 Top-20<br/>to_tsvector(COALESCE(enriched_text, text)) @@ plainto_tsquery(query)<br/>+ 소유권·프로젝트·READY·SERVABLE 조건
    DB-->>SS: [{chunk_id, rank}]
and
    loop 대상 인덱스마다 (동시)
        SS->>VS: 벡터 검색 Top-20<br/>embedding_vector와 질의 벡터의 코사인 거리 오름차순<br/>+ index_name 및 같은 범위 조건
        VS-->>SS: [{chunk_id, rank}]
    end
end

SS->>SS: RRF 병합 (k=60) → 최종 Top-5 chunk_id
SS->>DB: SOT 서빙 게이트 조회 {chunk_ids}<br/>소유권 · 프로젝트 범위 · video READY ·<br/>project SERVABLE/ACTIVE · 프로젝트 내 전 영상 READY
DB-->>SS: 통과한 {chunk_id, video_id, title, text, start_ms, end_ms}

alt 통과 청크 0건
    SS->>DB: SearchConversation 저장 {answer="검색 결과가 없습니다"}
    SS-->>C: {req_id, answer, chunks: []}
else 통과 청크 있음
    SS->>SS: RRF 순위대로 정렬 → ref 번호 1..N 부여<br/>→ 프롬프트 구성
    SS->>LLM: {system, user(query + 번호 붙은 문맥)}
    LLM-->>SS: 답변 본문 + used_refs 블록
    SS->>SS: 답변 추출 + used_refs 파싱<br/>→ 각 chunk의 used 플래그 결정
    SS->>DB: SearchResponseSnapshot 저장<br/>{req_id, query_text, topk_chunk_ids, used_chunk_ids,<br/>active_model_version, active_index_name,<br/>served_vector_paths, expires_at}
    SS->>DB: SearchConversation 저장 {query, answer, sources}
    Note over SS: 두 저장이 실패해도 검색 응답은 그대로 반환한다
    SS-->>C: {req_id, answer, chunks:[{ref, chunk_id, video_id,<br/>title, start_ms, end_ms, text, used}]}
end
```

| 데이터 | 어디에 남는가 | 쓰임 |
|---|---|---|
| 검색 응답 스냅샷 | `search_response_snapshot` (만료 시각 있음) | 피드백 검증의 기준 |
| 검색 대화 기록 | `search_conversation` (만료 없음) | 검색 기록 화면 |
| 답변 본문 | 저장하지 않음 | 대화 기록의 `answer`로만 남는다 |

**검색 기록 조회**는 `GET /api/v1/search/history?project_id=...`로 `search_conversation`을 그대로 읽는다. 검색을 다시 실행하지 않는다.

---

# 5. 삭제

영상 삭제와 프로젝트 삭제 모두 API가 상태만 바꾸고 큐를 발행한다. 실제 정리는 워커가 한다.

```mermaid
sequenceDiagram
autonumber
participant C as 브라우저
participant API as Core API
participant DB as Metadata DB (SOT)
participant MQ as Message Broker
participant PW as Pipeline Worker
participant OS as Object Storage

rect rgb(235, 243, 255)
Note over C, MQ: 요청 접수
alt 영상 삭제 (단건 또는 여러 건)
    C->>API: DELETE /api/v1/videos/{id}<br/>또는 POST /api/v1/videos:batch-delete {video_ids}
    API->>DB: 소유권 확인 후 대상 전부 status=DELETING
    Note over DB: 이 시점부터 프로젝트가 "전 영상 READY" 조건을 잃어<br/>검색에서 즉시 빠진다
    API->>MQ: DELETE_REQUEST {video_ids, trace_id}
    Note over API: 발행 실패 시 이전 상태로 되돌린다
    API-->>C: 202 {video_ids, delete_requested:true}
else 프로젝트 삭제
    C->>API: DELETE /api/v1/projects/{id}
    API->>DB: 소유권 확인 후 Project.lifecycle_state=DELETING
    API->>MQ: PROJECT_DELETE_REQUEST {project_id, trace_id}
    API-->>C: 202
end
end

rect rgb(255, 245, 235)
Note over MQ, OS: 워커 정리
alt DELETE_REQUEST
    MQ-->>PW: DELETE_REQUEST {video_ids}
else PROJECT_DELETE_REQUEST
    MQ-->>PW: PROJECT_DELETE_REQUEST {project_id}
    PW->>DB: 프로젝트에 속한 video_id 전부 조회
    DB-->>PW: video_ids
end
PW->>DB: 대상 영상 조회
DB-->>PW: 존재하는 영상 목록
Note over PW: 하나도 없으면 중복 삭제로 보고 성공 처리
PW->>DB: 처리 중 표시가 아직 살아 있는지 확인
Note over PW: 아직 처리 중이면 메시지를 Ack하지 않고 미룬다<br/>(진행 중인 파이프라인이 스스로 멈출 때까지)
PW->>DB: 자산 저장 경로 수집 (원본 · 오디오 · 키프레임)
DB-->>PW: storage_paths
PW->>OS: 객체 일괄 삭제 {storage_paths}
PW->>DB: VectorIndexEntry · Chunk · TranscriptSegment · Asset 삭제
PW->>DB: Video 레코드 hard-delete
opt PROJECT_DELETE_REQUEST
    PW->>DB: Project 레코드 삭제
end
end

Note over DB: 이미 수집된 피드백 이벤트와 이미 만들어진 데이터셋은 남는다.<br/>새 데이터셋을 만들 때 삭제된 청크는 다시 쓰지 않는다
```

**파이프라인이 진행 중인 경우** — 워커는 각 단계 진입 전에 상태를 다시 읽는다. `DELETING`을 보면 그 자리에서 파이프라인을 멈추고 처리권을 놓은 뒤 삭제를 수행한다.

---

# 6. 피드백 수집

```mermaid
sequenceDiagram
autonumber
participant C as 브라우저
participant FE as Frontend (프록시)
participant API as Core API
participant DB as Metadata DB (SOT)
participant FIP as Feedback Ingestion Pipeline
participant OS as Object Storage

C->>FE: POST /api/v1/feedbacks {req_id, rating}
FE->>API: 전달
API->>API: JWT 검증 → requester_user_id
API->>DB: SearchResponseSnapshot 조회 {req_id}
DB-->>API: {user_id, project_id, query_text, topk_chunk_ids,<br/>used_chunk_ids, active_model_version, active_index_name,<br/>served_vector_paths, expires_at}
Note over API: 스냅샷 없음 → 404 · 다른 사용자 → 403 · 만료됨 → 404

API->>API: event_id = uuid5(user_id, req_id, rating)<br/>같은 입력이면 항상 같은 값 → 중복 제거 기준
API->>API: 스냅샷 값을 이벤트에 고정<br/>검색 시점의 질의·근거·모델·인덱스를 그대로 담는다

loop 재시도 (상한까지)
    API->>FIP: POST 피드백 이벤트(JSON)<br/>{schema_version, event_id, req_id, user_id, project_id,<br/>trace_id, query_text, rating, topk_ids, used_ids,<br/>active_model_version, active_index_name,<br/>served_vector_paths, response_snapshot_ref, created_at}
    FIP-->>API: 202
end
Note over API: 재시도 후에도 실패하면 실패 카운터를 남기고 사용자에게 오류 반환
API-->>C: 201

FIP->>FIP: 수집 시각 부여 → JSON 파싱<br/>필수 필드와 스키마 버전 검사
alt 검사 통과
    FIP->>OS: 원본 로그 적재<br/>feedback/raw_logs/schema_version=..<br/>/ingest_date=../hour=../
else 파싱 실패 또는 필드 누락 또는 미지원 버전
    FIP->>OS: 오류 로그 적재<br/>feedback/error_logs/... {error_code, error_reason,<br/>original_payload}
end
Note over FIP: 전송 장애에 대비해 디스크 버퍼와 재시도를 둔다
```

| 데이터 | 어디에 남는가 |
|---|---|
| 정상 피드백 이벤트 | Object Storage `feedback/raw_logs/` (학습 데이터셋의 입력) |
| 형식이 깨진 이벤트 | Object Storage `feedback/error_logs/` (원본 payload 보존) |

---

# 7. 데이터셋 생성 · 재학습 · 서빙 전환

역할이 다른 워커 세 개가 큐로 이어진다. 스케줄러는 시각만 보고 요청을 넣는다.

```mermaid
sequenceDiagram
autonumber
participant SCH as scheduler
participant MQ as Message Broker
participant DSW as dataset-worker
participant TRW as train-release-worker
participant OS as Object Storage
participant DB as Metadata DB (SOT)
participant EMB as Managed Embedding Endpoint (배치·검색 2대)
participant SS as Search Service
participant VS as Vector Store

rect rgb(235, 250, 240)
Note over SCH, OS: 데이터셋 생성
SCH->>MQ: 매일 정해진 시각 → DATASET_GENERATION_REQUEST
MQ-->>DSW: 수신
DSW->>OS: 대상 기간(기본 최근 30일)의 피드백 원본 로그 읽기
OS-->>DSW: 이벤트 목록
DSW->>DSW: event_id 기준 중복 제거 (최신만 남김)
DSW->>DB: 이벤트가 참조한 chunk 본문 조회<br/>+ 같은 프로젝트 안의 랜덤 네거티브 후보 조회
DB-->>DSW: {chunk_id → text}
DSW->>DSW: 질의별 학습 그룹 구성<br/>{query_text, positives[], negatives[],<br/>source_event_ids, 검색 시점 모델·인덱스}
DSW->>OS: 데이터셋 적재<br/>{prefix}/{dataset_version}/train.jsonl<br/>{prefix}/{dataset_version}/manifest.json
end

rect rgb(255, 250, 235)
Note over SCH, DB: 학습과 평가
SCH->>MQ: 매주 정해진 요일·시각 → TRAINING_REQUEST
MQ-->>TRW: 수신
TRW->>OS: 최신 데이터셋 manifest 선택
OS-->>TRW: {dataset_version, rows_uri}
TRW->>DB: 실행 슬롯 확보 → MLPipelineRun 생성 (status=RUNNING)
Note over DB: RUNNING은 동시에 하나, PENDING도 동시에 하나만 허용<br/>더 최신 데이터셋이 오면 기존 PENDING은 SUPERSEDED

TRW->>OS: 현재 서빙 모델 아티팩트 복제 → 후보 버전으로
TRW->>OS: train.jsonl 다운로드
TRW->>TRW: 후보 모델 학습
TRW->>OS: 후보 아티팩트 저장<br/>model_manifest.json · training_metadata.json · scoring_artifact.json
TRW->>DB: MLPipelineRun에 후보 모델 버전 기록

TRW->>OS: 평가용 데이터셋 읽기 (학습셋과 분리된 고정 산출물)
OS-->>TRW: 평가 질의와 정답 기준
TRW->>TRW: 후보 모델 vs 기준 모델 검색 품질 비교
TRW->>DB: ModelEvaluation 저장<br/>{quality_metrics, pass_criteria, overall_decision}
TRW->>OS: 질의별 상세 결과 JSONL 저장

alt overall_decision = FAIL 또는 실행 중 오류
    TRW->>DB: MLPipelineRun {status=FAILED, failed_stage,<br/>failure_type=FAIL 또는 ERROR, failure_reason}
    Note over DB: 기존 서빙은 그대로 유지된다
else overall_decision = PASS
    TRW->>DB: MLPipelineRun status=READY_FOR_RELEASE
end
end

rect rgb(240, 240, 255)
Note over TRW, SS: 후보 배포
TRW->>DB: ModelRelease 상태 확인 (STABLE이어야 함)
TRW->>DB: candidate 열기<br/>{release_status=CANDIDATE_REINDEXING,<br/>candidate_model_version, candidate_index_name, candidate_opened_at}
TRW->>EMB: POST /internal/reload-models (배치 VM · 검색 VM 둘 다)
EMB->>OS: 후보 모델 아티팩트 다운로드
EMB-->>TRW: {ready_model_versions}
Note over TRW: 후보 버전이 목록에 없으면 배포 시도 실패로 기록<br/>시도 횟수가 상한을 넘으면 DEPLOYMENT_BLOCKED

TRW->>DB: 오래된 세대에만 존재하는 영상 수 확인
alt 남아 있음
    TRW->>DB: LegacyReindexItem 등록 (PENDING)
    Note over TRW: 전환을 막고 재색인이 끝난 뒤 다시 시도한다 (9장)
else 없음
    TRW->>DB: ModelRelease 갱신<br/>{active←candidate, previous←직전 active,<br/>candidate=null, release_status=STABLE, switched_at}
    TRW->>DB: 커밋
    TRW->>SS: POST /internal/reload-serving-targets
    SS->>DB: ModelRelease 재조회 → 프로세스 캐시 교체
    TRW->>DB: MLPipelineRun status=DEPLOY_COMPLETED
end
end

Note over VS: 전환 후 previous 인덱스는 남아 있고,<br/>검색은 active와 previous 두 세대까지 함께 조회한다
```

| 산출물 | 어디에 남는가 |
|---|---|
| 학습 데이터셋 | Object Storage `{prefix}/{dataset_version}/train.jsonl` + `manifest.json` |
| 후보 모델 | Object Storage 모델 아티팩트 3종 |
| 평가 집계 | Metadata DB `model_evaluation` |
| 평가 상세 | Object Storage JSONL |
| 실행 이력 | Metadata DB `ml_pipeline_run` |
| 서빙 조합 | Metadata DB `model_release` (한 행) |

**Pipeline Worker에는 전환을 알리지 않는다.** 다음 영상을 처리할 때 릴리스 레코드를 다시 읽는다.

---

# 8. 롤백과 복구

롤백 요청은 Core API가 발행만 하고, 실행은 롤백 워커가 한다. 복구 재임베딩은 롤백 완료와 분리된 후속 작업이다.

```mermaid
sequenceDiagram
autonumber
participant OP as 운영자
participant API as Core API
participant MQ as Message Broker
participant RBW as rollback-worker
participant DB as Metadata DB (SOT)
participant EMB as Managed Embedding Endpoint
participant SS as Search Service
participant VS as Vector Store
participant SCH as scheduler
participant RMW as reembedding-worker

rect rgb(255, 240, 240)
Note over OP, MQ: 요청
OP->>API: POST /api/v1/admin/model-release/rollback<br/>(role=ADMIN 토큰)
API->>DB: 현재 ModelRelease 조회
DB-->>API: {active_model_version, switched_at, release_status}
API->>MQ: ROLLBACK_REQUEST 발행<br/>{expected_active_model_version, expected_switched_at,<br/>trace_id, attempt}
API-->>OP: 202
end

rect rgb(245, 240, 255)
Note over MQ, SS: 롤백 실행
MQ-->>RBW: ROLLBACK_REQUEST
RBW->>DB: 현재 ModelRelease + 복원 대상 스냅샷 조회
DB-->>RBW: 현재 조합 / {snapshot_model_version, snapshot_index_name}
alt 요청의 expected 값이 현재와 다름
    RBW->>RBW: 오래된 요청으로 보고 종료 (아무것도 바꾸지 않음)
else 이미 스냅샷 상태로 복원됨
    RBW->>RBW: 종료
else 조건 일치
    RBW->>DB: 문제 모델 기간 데이터가 있는 프로젝트<br/>search_serving_state=ROLLBACK_EXCLUDED
    Note over DB: 이 프로젝트들은 검색 후보 조건에서 빠진다
    RBW->>DB: ModelRelease.release_status=ROLLBACK_PREPARING
    Note over DB: 이 동안 Pipeline Worker의 신규 적재도 막힌다
    RBW->>EMB: 복원 대상 모델 준비 확인
    EMB-->>RBW: ready 여부
    RBW->>VS: 스냅샷 인덱스 복원
    VS-->>RBW: 복원 여부
    Note over RBW: 둘 중 하나라도 준비되지 않으면 여기서 멈추고<br/>다음 요청 때 같은 지점부터 이어서 한다
    RBW->>DB: ModelRelease 복원<br/>{active←스냅샷, candidate=null,<br/>release_status=STABLE, switched_at}
    RBW->>DB: 커밋
    RBW->>SS: POST /internal/reload-serving-targets
    SS->>DB: 재조회 → 캐시 교체
end
end

rect rgb(235, 250, 240)
Note over SCH, VS: 복구 후처리 (스케줄러 주기 실행)
SCH->>DB: release_status=STABLE 이고<br/>ROLLBACK_EXCLUDED 프로젝트가 있는지 확인
DB-->>SCH: 대상 프로젝트와 영상 목록
loop 영상마다
    SCH->>MQ: REEMBEDDING_REQUEST<br/>{video_id, target_model_version, target_index_name}
end
MQ-->>RMW: REEMBEDDING_REQUEST
RMW->>DB: 해당 영상의 chunk 조회
DB-->>RMW: chunk 목록
RMW->>RMW: 임베딩 입력 텍스트 구성
loop 배치 단위
    RMW->>EMB: POST /embed {texts, model_version=복원된 active}
    EMB-->>RMW: {embeddings[]}
    RMW->>RMW: 벡터 차원이 대상 인덱스 정의와 맞는지 검증
    RMW->>VS: VectorIndexEntry UPSERT {index_name=복원된 active}
end
Note over RMW: chunk 행은 건드리지 않는다. 벡터만 채워 넣는다
SCH->>DB: 복구가 끝난 프로젝트 search_serving_state=SERVABLE
end
```

**핵심** — 롤백은 벡터를 되살리는 작업이 아니라 **서빙 기준을 되돌리는 작업**이다. 문제 모델이 활성일 때 업로드된 영상은 복원된 인덱스에 아예 없으므로, 그 빈자리는 후속 재임베딩이 채운다. 채워지기 전까지 해당 프로젝트는 검색에서 빠져 있다.

---

# 9. 오래된 세대 재색인

두 세대만 검색에 쓰므로, 그보다 오래된 인덱스에만 있는 영상은 최신 active 인덱스로 옮긴다. 이 작업이 남아 있으면 다음 모델 전환이 막힌다.

```mermaid
sequenceDiagram
autonumber
participant LRW as legacy-reindex-worker
participant DB as Metadata DB (SOT)
participant EMB as Managed Embedding Endpoint (배치)
participant VS as Vector Store

loop 스캔 주기마다
    LRW->>DB: 잠금 확보 시도 (여러 워커 중 하나만 진행)
    DB-->>LRW: 확보 / 실패 시 이번 회차 건너뜀
    LRW->>DB: 현재 ModelRelease 조회 → 목표 인덱스·모델 확정
    LRW->>DB: LegacyReindexItem 중 PENDING 항목 조회
    DB-->>LRW: {video_id, source_index_name, target_index_name, ...}

    loop 항목마다 (1회 실행당 개수 상한)
        LRW->>DB: 항목 status=RUNNING, started_at 기록
        LRW->>DB: 해당 영상의 chunk 조회
        DB-->>LRW: chunk 목록 (총 개수를 항목에 기록)
        LRW->>DB: 목표 인덱스의 벡터 차원 조회 (인덱스 대장)
        loop 배치 단위
            LRW->>EMB: POST /embed {texts, model_version=목표 모델}
            EMB-->>LRW: {embeddings[]}
            LRW->>LRW: 개수와 차원 검증<br/>어긋나면 항목을 FAILED로 기록하고 중단
            LRW->>VS: VectorIndexEntry UPSERT {index_name=목표 인덱스}
            LRW->>DB: completed_chunk_count 갱신
            LRW->>LRW: 지정된 시간만큼 쉬어 부하 조절
        end
        LRW->>DB: 목표 인덱스의 벡터 수와 chunk 수 일치 확인
        alt 일치
            LRW->>DB: 항목 status=SUCCEEDED, completed_at 기록
        else 불일치
            LRW->>DB: 항목 status=FAILED<br/>{failed_stage=CONSISTENCY_CHECK, retry_count 증가}
        end
    end
    LRW->>DB: 잠금 해제
end
```

**항목은 언제 생기나** — 후보 배포 중 전환 직전 검사에서, 오래된 세대에만 존재하는 영상을 찾아 `LegacyReindexItem`으로 등록한다(7장). 같은 영상·출발 인덱스·도착 인덱스 조합은 하나만 존재한다.

| 상태 | 뜻 |
|---|---|
| `PENDING` | 등록됐고 아직 시작 전 |
| `RUNNING` | 재색인 진행 중 |
| `SUCCEEDED` | 목표 인덱스의 벡터 수가 chunk 수와 일치 |
| `FAILED` | 조회·임베딩·적재·일관성 확인 중 실패. 실패 단계와 횟수를 남긴다 |
| `SKIPPED` | 대상에서 제외된 항목 |
