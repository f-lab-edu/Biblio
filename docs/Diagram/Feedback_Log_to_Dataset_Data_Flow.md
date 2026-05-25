# Feedback Log to Dataset Data Flow

```mermaid
flowchart LR
    subgraph Client["Client"]
        Feedback["명시적 피드백 제출<br/>(응답 ID, 좋아요/싫어요)"]
    end

    subgraph CoreAPI["Core API Server"]
        Validate["피드백 적재 가능 여부 확인<br/>사용자 인증, 응답 존재 여부, 만료 시간"]
        BuildEvent["검색 응답 스냅샷을 붙여<br/>피드백 이벤트 생성"]
        Publish["피드백 이벤트를<br/>비동기 전달 경로로 발행<br/>HTTP endpoint로 POST"]
    end

    subgraph MetadataDB["Metadata DB"]
        SearchSnapshot["검색 응답 스냅샷(질문 응답 당시 생성)<br/>질문, 검색 결과 chunk_id, 답변에 사용된 chunk_id,<br/>당시 모델/인덱스"]
        ChunkText["Chunk text<br/>학습 데이터 후보에 붙일 본문"]
    end

    subgraph FIP["Feedback Ingestion Pipeline"]
        Receive["피드백 이벤트 비동기 수신"]
        Route{"저장 가능한 이벤트인가?<br/>(지원 schema와 필수 필드 확인)"}
    end

    subgraph Storage["Object Storage"]
        RawLog["<원본 피드백 로그><br/>append-only batch 파일<br/><br/>저장 방식: batch size(피드백 건수)가 일정 수치에 도달 하면 새 파일 추가, 파일명의 timestamp로 dataset 포함 여부 결정<br/>raw_logs: timestamp-uuid.jsonl(경로로 error log와 구분)"]
        ErrorLog["<처리 제외 로그><br/>error_logs: timestamp-uuid.jsonl"]
        DatasetRows["학습 데이터셋 artifact<br/>dataset_version/train.jsonl"]
        DatasetManifest["데이터셋 메타데이터<br/>dataset_version/manifest.json"]
    end

    subgraph Worker["Feedback Loop Pipeline"]
        Trigger["데이터셋 생성 트리거<br/>정기 스케줄(ex: 매일 kst 기준 03:00) 또는 관리자 수동 실행<br/><br/>데이터셋을 이용한 학습 및 배포와는 별도 주기로 실행"]
        FixBoundary["이번 dataset에 포함할 피드백 로그 범위 확정<br/>ex: 실행 시점 기준 최근 30일 raw log만 사용"]
        LoadLogs["범위내의<br/>log batch file 로드"]
        Dedupe["중복 피드백 제거<br/>event_id 기준으로 하나만 반영<br/>동일 event_id는 피드백 발생 시각 기준 최신 이벤트 사용"]
        KeepLike["학습 대상 선별<br/>LIKE 피드백만 dataset row 후보로 사용<br/>DISLIKE는 raw log에는 남기지만 dataset row로 만들지 않음"]
        Positive["positive 후보 생성<br/>답변에 사용된 chunk_id"]
        Negative["negative 후보 생성<br/>답변 미사용 검색결과 chunk_id<br/>+ 같은 프로젝트 random chunk_id"]
        AttachText["chunk text 추가 및 후보 확정<br/>text 없는 후보 제외"]
        BuildRows["학습 데이터셋 생성<br/>하나의 행: 질문 + positive + negative<br/>+row별 원본 envent_id"]
        Summarize["메타데이터 생성<br/>dataset 생성에 사용한 raw feedback log의 시간 범위(source window), 입력/중복제거/학습가능 건수,<br/>품질 기준 통과 여부 기록"]
    end

    Feedback --> Validate
    Validate --> SearchSnapshot
    SearchSnapshot --> BuildEvent
    BuildEvent --> Publish
    Publish --> Receive
    Receive --> Route
    Route -->|"정상"| RawLog
    Route -->|"형식 오류"| ErrorLog

    Trigger --> FixBoundary
    FixBoundary --> LoadLogs
    RawLog --> LoadLogs
    LoadLogs --> Dedupe
    Dedupe --> KeepLike
    KeepLike --> Positive
    KeepLike --> Negative
    Positive --> AttachText
    Negative --> AttachText
    ChunkText --> AttachText
    AttachText --> BuildRows
    BuildRows --> Summarize
    BuildRows --> DatasetRows
    Summarize --> DatasetManifest
```

