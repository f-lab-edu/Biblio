# 영상 처리 경로 Deployment Diagram

- 기준일: 2026-08-02
- 대상 환경: `infra/terraform/envs/gcp-perf`

## 목적과 범위

이 문서는 영상 처리에 참여하는 **배포 단위, 실행 위치, 저장소, 서비스 간 연결**을 보여준다. 인스턴스 수와 자원처럼 운영 시 확인해야 하는 배포값도 함께 기록한다.

처리 단계별 순서·시간·대기·재시도 정책은 다루지 않는다. 해당 내용은 `Value_Stream_Map_Video_Processing_Current.md`와 코드에서 확인한다.

- 실선: 서비스 간 요청·데이터 저장 경로
- 점선: 시작 또는 모델 전환 시 읽는 설정·모델 경로

## 현재 배포 구조

```mermaid
flowchart TB
    user["사용자 브라우저"]

    subgraph gcp["GCP 프로젝트 · 서비스 리전 asia-northeast3"]
        subgraph frontend_node["Cloud Run · frontend"]
            frontend["Next.js Frontend<br/>공개 접근<br/>min 0 · max 3<br/>1 vCPU · 512MiB"]
            proxy["/api/v1/[...path] 프록시<br/>video 경로를 Core API로 전달"]
            frontend --> proxy
        end

        subgraph core_node["Cloud Run · core-api"]
            core_api["FastAPI :8080<br/>IAM 인증 필요<br/>min 0 · max 3<br/>1 vCPU · 512MiB · timeout 300초"]
            core_flow["영상 등록 → 업로드 URL 발급<br/>→ 업로드 완료 확인<br/>→ PREPROCESS_REQUEST 발행"]
            core_api --> core_flow
        end

        subgraph worker_node["Cloud Run · pipeline-worker"]
            consumer["PGMQ 폴링 루프<br/>HTTP 진입점 없음<br/>평시 min 0 · 처리 시 수동 min 1 · max 1<br/>1 vCPU · 4GiB · CPU 항상 할당<br/>동시 처리 4건"]
            orchestrator["DOWNLOAD → EXTRACT → STT<br/>→ CHUNKING(키프레임·Vision)<br/>→ EMBEDDING → VECTOR_UPSERT"]
            consumer --> orchestrator
        end

        subgraph vpc["VPC 사설 경로"]
            subgraph embedding_node["Compute Engine · 배치 임베딩 VM"]
                embedding_api["managed-embedding-endpoint :8000<br/>내부 IP 10.20.3.14<br/>e2-standard-4<br/>BGE-M3 CPU 추론"]
                admission["영상 요청 상한 4<br/>실행 슬롯 1<br/>슬롯 대기 상한 20초"]
                wireproxy["wireproxy SOCKS5 :1080<br/>YouTube 트래픽 전용"]
                embedding_api --> admission
                model_disk[("모델 캐시 디스크<br/>pd-balanced 20GiB")]
                model_disk -. 모델 로드 .-> admission
            end

            subgraph postgres_node["Compute Engine · PostgreSQL VM"]
                postgres["PostgreSQL 16 :5432<br/>내부 IP 10.20.2.3<br/>e2-standard-4<br/>pd-balanced 20GiB"]
                pgmq[("PGMQ 1.10.0<br/>PREPROCESS_REQUEST<br/>DELETE_REQUEST<br/>PROJECT_DELETE_REQUEST")]
                relational[("project · video · chunk · asset<br/>release context")]
                vector[("vector_index_entry<br/>pgvector")]
                postgres --- pgmq
                postgres --- relational
                postgres --- vector
            end
        end

        video_bucket[("Cloud Storage · video bucket<br/>원본 · audio.flac<br/>stt-input 파트 · keyframes")]
        ml_artifacts[("Cloud Storage · ML artifact bucket<br/>BGE-M3 모델 원본 · models/{version}")]
    end

    youtube["YouTube<br/>EXTERNAL_URL 소스"]
    stt["Google Speech-to-Text v2<br/>BatchRecognize · chirp_3<br/>리전 us"]
    vision["Vertex AI Gemini<br/>gemini-3.1-flash-lite<br/>location global"]
    secrets["Secret Manager<br/>DATABASE_URL · JWT secret"]

    user -- "HTTPS<br/>영상 등록·업로드 완료 통보" --> frontend
    proxy -- "HTTPS + Cloud Run IAM ID token" --> core_api
    user -- "HTTPS<br/>서명 URL 직접 업로드" --> video_bucket
    core_flow -- "TCP 5432<br/>영상 상태 기록" --> postgres
    core_flow -- "TCP 5432<br/>메시지 발행" --> pgmq

    pgmq -- "TCP 5432<br/>폴링 · 가시성 타임아웃" --> consumer
    orchestrator -- "사설 SOCKS5" --> wireproxy
    wireproxy -- "HTTPS<br/>yt-dlp 다운로드" --> youtube
    orchestrator -- "HTTPS<br/>원본 내려받기<br/>오디오·파트·키프레임 올리기" --> video_bucket
    orchestrator -- "HTTPS<br/>BatchRecognize(gs:// URI)<br/>15분 파트 · 동시 2건" --> stt
    stt -. "오디오 직접 읽기" .-> video_bucket
    orchestrator -- "HTTPS<br/>키프레임 1장씩 · 동시 2건" --> vision
    orchestrator -- "사설 HTTP<br/>POST /embed<br/>workload=video_preprocess<br/>배치 4건 · timeout 180초" --> embedding_api
    orchestrator -- "TCP 5432<br/>chunk · asset · vector upsert" --> postgres

    ml_artifacts -. "캐시에 없을 때 다운로드" .-> model_disk
    secrets -. 컨테이너 시작 시 주입 .-> core_api
    secrets -. 컨테이너 시작 시 주입 .-> consumer
    secrets -. VM 컨테이너 시작 시 주입 .-> embedding_api
```

## 배포 단위와 운영값

| 배포 단위 | 현재 값 | 운영 시 확인할 것 |
|---|---|---|
| Frontend Cloud Run | min 0, max 3, 1 vCPU, 512MiB | ready revision, 실제 인스턴스 수 |
| Core API Cloud Run | min 0, max 3, 1 vCPU, 512MiB, timeout 300초 | ready revision, 실제 인스턴스 수 |
| Pipeline Worker Cloud Run | 평시 min 0, 처리 시 수동 min 1, max 1, 1 vCPU, 4GiB, CPU 항상 할당 | 처리 전 min 1, 종료 후 min 0 |
| 배치 임베딩 VM | e2-standard-4, 실행 슬롯 1, 영상 요청 수용 상한 4, 대기 상한 20초 | VM·컨테이너 상태, queue wait, CPU |
| PostgreSQL VM | e2-standard-4, PostgreSQL 16 + PGMQ 1.10.0, pd-balanced 20GiB | VM 상태, 연결 수, 큐 깊이, CPU·IO |
| Cloud Storage | video bucket과 ML artifact bucket 분리 | 객체 경로, 모델 버전, 캐시 유무 |
| Secret Manager | Core API·Worker·임베딩 endpoint 시작 설정 주입 | secret version과 서비스 계정 접근 권한 |

## 배포 관점의 핵심 사항

- Pipeline Worker는 큐가 자동으로 기동시키지 않는다. 처리 전에 운영자가 `min 1`로 올려야 한다.
- PGMQ와 영상·검색 데이터는 물리적으로 같은 PostgreSQL VM을 사용한다.
- 배치 임베딩 VM 한 대에서 BGE-M3 endpoint와 YouTube용 wireproxy가 함께 실행된다.
- 모델은 ML artifact bucket에 보관하고, 캐시에 없을 때만 VM의 모델 디스크로 내려받는다.
- STT와 Vision은 외부 서비스이므로 해당 서비스 지연과 리전 차이가 전체 처리 시간에 포함된다.

## 문서 경계

- 단계별 처리 시간과 대기: `docs/Diagram/Value_Stream_Map_Video_Processing_Current.md`
- 영상 처리 논리 구조: `docs/Diagram/Architecture_Diagram_Video_Ingest.png`
- 이 문서의 `max`는 실행 가능한 상한이며 실제 실행 인스턴스 수를 뜻하지 않는다.

## 코드와 설정 근거

- GCP 배포와 연결: `infra/terraform/envs/gcp-perf/main.tf`
- Cloud Run 자원: `infra/terraform/modules/cloud_run_worker`, `infra/terraform/modules/cloud_run_service`
- 임베딩 VM·모델 캐시: `infra/terraform/modules/embedding_vm`
- GCS 버킷: `infra/terraform/modules/object_storage/main.tf`
- Core API 영상·큐 발행: `services/core-api/src/services/video_service.py`, `services/core-api/src/infra/pgmq_client.py`
- Worker 큐 소비: `services/pipeline-worker/src/infra/queue`
- 영상 처리 외부 연결: `services/pipeline-worker/src/services/pipeline_orchestrator.py`
- GCS 모델 materialize: `services/managed-embedding-endpoint/src/core/artifact_resolver.py`
