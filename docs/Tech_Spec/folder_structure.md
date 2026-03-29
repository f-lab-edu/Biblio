# 프로젝트 폴더 구조

> 각 컴포넌트의 권장 폴더 구조를 정의한다. 파일명은 구현 시 상황에 따라 조정 가능하다.

---

## Core API Server

```
services/core-api/
├── alembic/
│   └── versions/         — Alembic 마이그레이션
├── src/
│   ├── api/v1/
│   │   ├── router.py     — v1 라우터 조립
│   │   └── routers/      — videos 엔드포인트
│   ├── common/           — 공통 로깅
│   ├── core/             — Settings, DI dependencies
│   ├── infra/
│   │   ├── db/           — Cursor, VideoRepository
│   │   ├── broker.py     — BrokerClient 인터페이스
│   │   ├── pgmq_client.py        — PGMQ 구현체
│   │   ├── inmemory_broker.py    — InMemory 브로커 구현체
│   │   ├── storage.py            — StorageClient 인터페이스
│   │   ├── gcs_client.py         — GCS 구현체
│   │   └── inmemory_storage.py   — InMemory 스토리지 구현체
│   ├── middlewares/      — auth, trace, error handler
│   ├── models/           — SQLAlchemy ORM 모델
│   ├── schemas/          — Pydantic DTO
│   ├── services/         — video_service.py
│   └── main.py           — 애플리케이션 엔트리포인트
└── tests/
    ├── api/v1/           — API 레벨 테스트
    ├── integration/      — Repository 통합 테스트
    ├── unit/             — 서비스/미들웨어/인프라 단위 테스트
    ├── conftest.py       — pytest fixture
    └── support.py        — 테스트 지원 유틸
```

---

## Pipeline Worker

```
services/pipeline-worker/
├── src/
│   ├── infra/
│   │   ├── ai/           — Embedding, STT, Vision 어댑터 및 구현체
│   │   ├── db/           — Video/Artifact Repository, DB 모델
│   │   ├── media/        — FFmpeg 어댑터 및 구현체
│   │   ├── queue/        — 브로커 인터페이스, 컨슈머, PGMQ/InMemory 구현체
│   │   └── storage/      — StorageClient 인터페이스 및 구현체
│   ├── config/           — Settings, 환경 변수 관리
│   ├── schemas/          — 메시지 스키마
│   ├── services/         — chunking_service, pipeline_orchestrator, text_normalizer
│   ├── usecases/         — process_video, delete_video
│   ├── utils/            — logging, workdir 유틸
│   ├── bootstrap.py      — 의존성 조립
│   └── main.py           — 워커 엔트리포인트
├── tests/
│   ├── integration/      — 프로세스/리포지토리 플로우 테스트
│   ├── unit/             — 어댑터/유스케이스/서비스 단위 테스트
│   ├── conftest.py       — pytest fixture
│   └── support.py        — 테스트 지원 유틸
└── tools/
    └── fake_embedding_server.py  — 로컬 테스트용 더미 임베딩 서버
```

---

## Search Service

```
services/search-service/
├── src/
│   ├── api/v1/
│   │   ├── router.py     — v1 라우터 조립
│   │   └── routers/      — search 엔드포인트
│   ├── common/           — 공통 로깅, retry 유틸
│   ├── core/             — Settings, DI dependencies
│   ├── infra/
│   │   ├── db/           — 검색 Repository, DB 세션/모델
│   │   ├── embedding/    — Managed Embedding Endpoint 클라이언트
│   │   └── llm/          — LLM 어댑터 및 인터페이스
│   ├── middlewares/      — auth, trace, error handler
│   ├── schemas/          — search DTO
│   ├── services/         — search_orchestrator, rrf, prompt_builder, used_refs_parser
│   ├── bootstrap.py      — 의존성 조립
│   └── main.py           — 애플리케이션 엔트리포인트
└── tests/
    ├── api/              — API 레벨 테스트
    ├── integration/      — search repository 통합 테스트
    └── unit/             — 미들웨어/서비스/클라이언트 단위 테스트
```

---

## Managed Embedding Endpoint

```
services/managed-embedding-endpoint/
├── src/
│   ├── api/v1/
│   │   ├── router.py     — v1 라우터 조립
│   │   └── routers/      — embed, health 엔드포인트
│   ├── schemas/          — Pydantic 요청/응답 DTO (EmbedRequest, EmbedResponse, HealthResponse)
│   ├── services/         — inference_service.py (텍스트 임베딩 추론, 입력 순서 보장)
│   ├── infra/
│   │   └── model_loader.py   — 구성된 모델 파일/디렉토리 로드, 버전 노출, 메모리 적재/해제
│   ├── middlewares/      — Trace ID 추출 및 전파 미들웨어
│   ├── observability/    — 구조화 로깅, 메트릭 (embed_request_latency_ms 등)
│   ├── core/             — settings.py, model_state.py
│   └── main.py           — 애플리케이션 엔트리포인트
└── tests/
    ├── api/              — API 레벨 테스트
    ├── integration/      — E2E 통합 테스트 (더미 모델 스텁 사용)
    └── unit/             — 더미 임베딩 모델 스텁 기반 단위 테스트
```
