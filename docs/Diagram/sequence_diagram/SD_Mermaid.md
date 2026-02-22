# 1. local video upload

```mermaid
sequenceDiagram
autonumber
participant C as Client (User Web UI)
participant EG as Edge Gateway
participant API as Core API Server
participant DB as Metadata DB (SOT)
participant OS as Object Storage
participant MQ as Message Broker

C->>EG: 업로드 시작 요청<br/>{title, category, input_type=LOCAL_FILE,<br/>Authorization}
EG->>EG: JWT 1차 검증 + 스푸핑 방지<br/>trace_id 없으면 생성
EG->>API: 요청 전달<br/>{Authorization 그대로 전달,<br/>trace_id}
API->>API: Authorization 재검증<br/>→ requester_user_id 추출

API->>DB: Video 생성<br/>{video_id, user_id=requester_user_id,<br/>title, category, input_type,<br/>status=PENDING}
DB-->>API: video_id

API->>OS: Presigned URL 발급 요청<br/>{object_key=videos/{video_id}/source}
OS-->>API: presigned_url

API->>DB: Video.storage_path 저장<br/>{video_id, storage_path=object_key}
DB-->>API: OK

API-->>C: Presigned URL + video_id 반환<br/>{video_id, presigned_url}

C->>OS: 원본 영상 업로드(binary)<br/>via presigned_url
OS-->>C: 200 OK

C->>EG: 업로드 완료 신호<br/>{video_id, Authorization}
EG->>EG: JWT 1차 검증 + 스푸핑 방지<br/>trace_id 없으면 생성
EG->>API: 신호 전달<br/>{video_id, Authorization,<br/>trace_id}
API->>API: Authorization 재검증<br/>→ requester_user_id 추출<br/>+ video_id 소유권 검증

API->>DB: Video.status=UPLOADED 갱신<br/>{video_id}
DB-->>API: OK

API->>MQ: PREPROCESS_REQUEST 발행<br/>MessageEnvelope{message_type=PREPROCESS_REQUEST,<br/>payload_version=v1, trace_id,<br/>attempt=1, video_id, issued_at}
MQ-->>API: Ack

API-->>C: 202 Accepted<br/>{video_id, status=UPLOADED}

Note over DB: Status: PENDING → UPLOADED
```


# 2. url video uplaod

```mermaid
sequenceDiagram
autonumber
participant C as Client (User Web UI)
participant EG as Edge Gateway
participant API as Core API Server
participant DB as Metadata DB (SOT)
participant MQ as Message Broker
participant MP as Media Processor
participant OS as Object Storage

C->>EG: 외부 URL 업로드 시작<br/>{title, category, input_type=EXTERNAL_URL,<br/>source_url, Authorization}
EG->>EG: JWT 1차 검증 + 스푸핑 방지<br/>trace_id 없으면 생성
EG->>API: 요청 전달<br/>{Authorization 그대로 전달,<br/>trace_id}
API->>API: Authorization 재검증<br/>→ requester_user_id 추출
API->>DB: Video 생성<br/>{video_id, user_id=requester_user_id,<br/>title, category, input_type, source_url,<br/>status=PENDING}
DB-->>API: video_id

API->>MQ: DOWNLOAD_REQUEST 발행<br/>MessageEnvelope{message_type=DOWNLOAD_REQUEST,<br/>payload_version=v1, trace_id,<br/>attempt=1, video_id, issued_at}
MQ-->>API: Ack
API-->>C: 202 Accepted<br/>{video_id, status=PENDING}

MQ-->>MP: DOWNLOAD_REQUEST 전달<br/>MessageEnvelope{..., video_id, trace_id, attempt}
MP->>DB: Video.source_url 조회<br/>{video_id}
DB-->>MP: source_url

alt 다운로드 성공
    MP->>OS: source_url 다운로드<br/>→ 원본 저장<br/>{storage_path(object key)}
    OS-->>MP: OK
    MP->>DB: Video 갱신<br/>{video_id, storage_path 저장,<br/>status=UPLOADED}
    DB-->>MP: OK

    MP->>MQ: PREPROCESS_REQUEST 발행<br/>MessageEnvelope{message_type=PREPROCESS_REQUEST,<br/>payload_version=v1, trace_id,<br/>attempt=1, video_id, issued_at}
    MQ-->>MP: Ack

    Note over DB: Status: PENDING → UPLOADED
else 다운로드 실패
    MP->>DB: Video.status=FAILED 갱신<br/>{video_id, failed_stage=DOWNLOAD}
    DB-->>MP: OK
    Note over DB: Status: PENDING → FAILED
end
```

# 3. Media Processing

```mermaid
sequenceDiagram
autonumber
participant MQ as Message Broker
participant MP as Media Processor
participant DB as Metadata DB (SOT)
participant OS as Object Storage

MQ-->>MP: PREPROCESS_REQUEST 전달<br/>MessageEnvelope{message_type=PREPROCESS_REQUEST,<br/>payload_version=v1, trace_id,<br/>attempt, video_id, issued_at}

MP->>DB: Video.status=PROCESSING 갱신<br/>{video_id}
DB-->>MP: OK

MP->>DB: Video.storage_path 조회<br/>{video_id}
DB-->>MP: storage_path

MP->>OS: 원본 영상 로드<br/>{storage_path}
OS-->>MP: video file

MP->>OS: 오디오 추출 저장<br/>{audio_storage_path}
MP->>OS: 키프레임 추출 저장<br/>{keyframe_storage_path[], timestamp_ms[]}
OS-->>MP: OK

MP->>DB: Asset 저장<br/>{video_id, type=AUDIO, storage_path=audio_storage_path}<br/>{video_id, type=KEYFRAME, storage_path=..., timestamp_ms=...}*
DB-->>MP: OK

MP->>MQ: PREPROCESS_COMPLETED 발행<br/>MessageEnvelope{message_type=PREPROCESS_COMPLETED,<br/>payload_version=v1, trace_id,<br/>attempt=1, video_id, issued_at}
MQ-->>MP: Ack

Note over DB: Status: UPLOADED → PROCESSING


```

# 4. AI Analysis & Indexing(STT→청킹→임베딩→적재→READY)

```mermaid
sequenceDiagram
autonumber
participant MQ as Message Broker
participant W as AI Pipeline Worker
participant DB as Metadata DB (SOT)
participant OS as Object Storage
participant GW as AI Model Gateway
participant STT as External AI Adapters (STT)
participant EMB as Managed Embedding Endpoint
participant VS as Vector Store (ANN)

MQ-->>W: PREPROCESS_COMPLETED 전달<br/>MessageEnvelope{message_type=PREPROCESS_COMPLETED,<br/>payload_version=v1, trace_id,<br/>attempt, video_id, issued_at}

W->>DB: Asset + Video 조회(video_id)<br/>→ AUDIO/KEYFRAME storage_path<br/>+ Video.user_id
DB-->>W: {audio_storage_path,<br/>keyframe_storage_path[],<br/>user_id=Video.user_id}

W->>OS: 오디오 로드<br/>{audio_storage_path}
OS-->>W: audio bytes
W->>OS: 키프레임 로드<br/>{keyframe_storage_path[]}
OS-->>W: images

W->>GW: transcribe(audio)<br/>{video_id, trace_id}
GW->>STT: 외부 STT API 호출/예외처리
STT-->>GW: transcript segments + timestamps
GW-->>W: transcript segments + timestamps(표준 포맷)

W->>DB: TranscriptSegment 적재*<br/>{video_id, start_ms, end_ms, text,<br/>stt_model_version}
DB-->>W: OK

W->>W: Semantic chunking + keyframe alignment<br/>→ Chunk{chunk_id, start_ms, end_ms,<br/>text, keyframe_asset_id}

W->>GW: embed(chunks + keyframes)<br/>{chunk_id[], trace_id}
GW->>EMB: 임베딩 요청
EMB-->>GW: embedding_vector[]
GW-->>W: embedding_vector[]

W->>DB: Chunk 적재*<br/>{chunk_id, video_id, start_ms, end_ms,<br/>text, keyframe_asset_id,<br/>chunking_version,<br/>embedding_model_version}
DB-->>W: OK

W->>VS: Vector Upsert*<br/>{chunk_id, user_id=Video.user_id,<br/>video_id, embedding_vector,<br/>embedding_model_version}
VS-->>W: OK

alt DB+VectorStore 반영 완료
  W->>DB: Video.status=READY 갱신<br/>{video_id}
  DB-->>W: OK
else 실패
  W->>DB: Video.status=FAILED 갱신<br/>{video_id,<br/>failed_stage=(STT|CHUNKING|EMBEDDING|VECTOR_UPSERT),<br/>error_message?}
  DB-->>W: OK
end

Note over DB: Status: PROCESSING → READY (or FAILED)
```

# 5. Search & RAG Serving

```mermaid
sequenceDiagram
autonumber
participant C as Client (User Web UI)
participant EG as Edge Gateway
participant SS as Search Service
participant Cache as Cache
participant GW as AI Model Gateway
participant EMB as Managed Embedding Endpoint
participant FTS as Metadata DB (FTS)
participant VS as Vector Store (ANN)
participant SOT as Metadata DB (SOT Validation)
participant LLM as External AI Adapters (LLM)

C->>EG: 검색 요청<br/>{query_text, scope, Authorization}
EG->>EG: JWT 1차 검증 + 스푸핑 방지<br/>trace_id 없으면 생성
EG->>SS: 요청 전달<br/>{Authorization 그대로 전달, trace_id}
SS->>SS: Authorization 재검증<br/>→ requester_user_id 추출

SS->>Cache: Get(cache_key)<br/>{requester_user_id + query_text + scope}
alt Cache HIT
  Cache-->>SS: cached_response<br/>{answer, timestamps, topk_chunk_ids, cited_chunk_ids}
  SS-->>C: 캐시 응답 반환<br/>{answer, timestamps, topk_chunk_ids, cited_chunk_ids}
else Cache MISS
  SS->>GW: embed(query_text)<br/>{trace_id}
  GW->>EMB: 임베딩 요청
  EMB-->>GW: query_embedding_vector
  GW-->>SS: query_embedding_vector

  SS->>FTS: 키워드 후보 조회(Top-K)<br/>{query_text, requester_user_id 필터, scope}
  FTS-->>SS: keyword_candidates<br/>{chunk_id[], score[]}

  SS->>VS: 벡터 후보 조회(Top-K)<br/>{query_embedding_vector, user_id=requester_user_id 필터, scope}
  VS-->>SS: vector_candidates<br/>{chunk_id[], score[]}

  SS->>SS: 후보 병합(RRF)<br/>→ topk_chunk_ids

  SS->>SOT: 최종 서빙 검증 + 컨텍스트 로드<br/>{topk_chunk_ids, check: 권한/존재 여부/READY}<br/>→ {chunk_text, start_ms, end_ms, keyframe_asset_id}
  SOT-->>SS: contexts + timestamps

  SS->>GW: generate_answer<br/>{query_text + contexts}<br/>→ cited_chunk_ids
  GW->>LLM: 외부 LLM API 호출
  LLM-->>GW: answer (+ cited_chunk_ids)
  GW-->>SS: answer + cited_chunk_ids

  SS->>Cache: Set(cache_key, response)<br/>{answer, timestamps, topk_chunk_ids, cited_chunk_ids}
  Cache-->>SS: OK
  SS-->>C: 최종 응답 반환<br/>{answer, timestamps, topk_chunk_ids, cited_chunk_ids}
end

Note over SS: Tenancy: requester_user_id 기반<br/>(Cache Key + FTS Filter + ANN Filter + SOT Validation)
```