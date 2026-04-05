# 프로젝트 폴더 구조

> 이 저장소는 서비스별 구현을 `services/` 아래에 두는 모노레포를 기준으로 한다.
> 각 컴포넌트의 내부 폴더 구조는 서비스 루트 기준이며, 파일명은 구현 시 상황에 따라 조정 가능하다.

---

## Top-Level Layout

```text
services/
├── core-api/
│   ├── pyproject.toml
│   ├── poetry.lock
│   ├── .venv/            — 로컬 전용 가상환경 (커밋 제외)
│   ├── alembic.ini
│   ├── alembic/
│   ├── src/
│   └── tests/
├── pipeline-worker/
│   ├── pyproject.toml
│   ├── poetry.lock
│   ├── .env.example
│   ├── .venv/            — 로컬 전용 가상환경 (커밋 제외)
│   ├── src/
│   └── tests/
├── search-service/
└── managed-embedding-endpoint/

docs/
└── ...

.github/
└── workflows/
```

---

## Core API Server

> 서비스 루트: `services/core-api/`

```text
alembic/                 — DB 마이그레이션
src/
├── api/v1/
│   ├── router.py         — API v1 라우터 엔트리
│   └── routers/          — HTTP 라우터 (영상 업로드, 완료, 조회, 수정, 삭제)
├── common/               — 로깅/메트릭 유틸
├── schemas/              — Pydantic 요청/응답 DTO
├── services/             — 비즈니스 로직 (상태 전이, 업로드 완료, 삭제, 재시도)
├── models/               — SQLAlchemy ORM 모델
├── infra/
│   ├── db/               — Repository, cursor 유틸
│   ├── broker.py         — BrokerClient 인터페이스
│   ├── pgmq_client.py    — PGMQ 구현체
│   ├── inmemory_broker.py
│   ├── storage.py        — StorageClient 인터페이스
│   ├── gcs_client.py
│   └── inmemory_storage.py
├── middlewares/          — Auth, trace, error handler
└── core/                 — Settings, dependency wiring

tests/
├── api/v1/               — FastAPI 라우터/API 테스트
├── unit/                 — 서비스/미들웨어/인프라 단위 테스트
├── integration/          — Repository 중심 DB 통합 테스트
├── conftest.py
└── support.py            — 테스트 fixture 및 helper
```

---

## Pipeline Worker

> 서비스 루트: `services/pipeline-worker/`

```text
src/
├── config/               — Settings, 환경 변수 관리
├── usecases/             — 비디오 처리 유스케이스 (상태 전이, Resume 로직)
├── services/             — Chunking, text normalization, 파이프라인 오케스트레이션
├── infra/
│   ├── queue/            — 메시지 브로커 인터페이스, consumer, PGMQ/InMemory 구현체
│   ├── db/               — Video/Asset/Chunk/Transcript/Vector Repository 및 모델
│   ├── storage/          — StorageClient 인터페이스 및 구현체 (GCS, InMemory)
│   ├── ai/               — Google STT, Embedding, Vision 어댑터
│   └── media/            — FFmpeg 미디어 처리 어댑터 (오디오 추출, 키프레임 추출)
├── schemas/              — 이벤트 메시지 스키마 (Pydantic)
└── utils/                — 로깅, 작업 디렉토리 관리 유틸

tests/
├── unit/                 — infra/service/usecase 단위 테스트
├── integration/          — in-memory DB/스토리지 기반 흐름 테스트
├── conftest.py
└── support.py            — 테스트용 factory, mock transport, helper
```

---

## Search Service

> 서비스 루트: `services/search-service/`

```text
src/
├── api/v1/routers/       — HTTP 라우터 (/api/v1/search)
├── api/v1/schemas/       — 요청/응답 스키마
├── services/             — 검색 오케스트레이터, RRF 병합, 프롬프트 빌더
├── infra/
│   ├── db/               — Chunk/Video Repository (pgvector 포함)
│   └── ai_adapters/      — Embedding, Gemini 어댑터
├── middlewares/          — JWT 인증, 에러 핸들러
└── observability/        — 구조화 로깅, 메트릭

tests/
├── unit/                 — AsyncMock 기반 단위 테스트
├── integration/          — Testcontainers 기반 DB 통합 테스트
└── perf/                 — Locust 성능 테스트
```

---

## Managed Embedding Endpoint

> 서비스 루트: `services/managed-embedding-endpoint/`

```text
src/
├── api/v1/
│   ├── router.py         — API v1 라우터 엔트리
│   └── routers/          — HTTP 라우터 (POST /embed, GET /health)
├── schemas/              — Pydantic 요청/응답 DTO (EmbedRequest, EmbedResponse, HealthResponse)
├── services/             — 텍스트 임베딩 추론 서비스
├── infra/
│   ├── model_loader.py   — 구성된 모델 파일/디렉토리 로드, 버전 노출, 메모리 적재/해제
│   └── runtime.py        — 임베딩 런타임 추상화 및 실제 모델 어댑터 경계
├── middlewares/          — Trace ID 추출 및 전파 미들웨어
├── observability/        — 구조화 로깅 유틸
└── core/
    ├── settings.py       — pydantic-settings 기반 환경변수 관리
    └── model_state.py    — 모델 readiness / model_version 상태 저장

tests/
├── api/                  — API 계약 테스트
└── unit/                 — DTO / middleware / state / runtime 단위 테스트
```
