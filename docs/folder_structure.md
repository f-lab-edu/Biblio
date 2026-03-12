# 프로젝트 폴더 구조

> 각 컴포넌트의 권장 폴더 구조를 정의한다. 파일명은 구현 시 상황에 따라 조정 가능하다.

---

## Core API Server

```
src/
├── api/v1/routers/       — HTTP 라우터 (영상 업로드, 완료, 조회, 수정, 삭제)
├── schemas/              — Pydantic 요청/응답 DTO, cursor DTO
├── services/             — 비즈니스 로직 (상태 전이, 멱등성, Reconciler)
├── models/               — SQLAlchemy ORM 모델
├── infra/
│   ├── db/               — Repository, 트랜잭션 경계
│   ├── storage/          — StorageClient 인터페이스 및 구현체 (GCS, InMemory)
│   └── broker/           — BrokerClient 인터페이스 및 구현체 (RabbitMQ, InMemory)
└── core/                 — Settings, 공통 미들웨어

tests/
├── unit/                 — InMemory 구현체로 격리한 단위 테스트
└── integration/          — Testcontainers 기반 DB 통합 테스트
```

---

## Pipeline Worker

```
src/
├── config/               — Settings, 환경 변수 관리
├── usecases/             — 비디오 처리 유스케이스 (상태 전이, Resume 로직)
├── services/             — Chunking, 파이프라인 오케스트레이션
├── adapters/
│   ├── queue/            — 메시지 브로커 컨슈머, DLQ 라우터
│   ├── db/               — Video/Chunk/Asset Repository
│   ├── storage/          — StorageClient 인터페이스 및 구현체 (GCS, InMemory)
│   ├── ai/               — Gemini, Embedding 외부 AI 어댑터
│   └── media/            — FFmpeg 미디어 처리 어댑터 (오디오 추출, 키프레임 추출)
├── schemas/              — 이벤트 메시지 스키마 (Pydantic)
└── utils/                — 미디어 Context Manager 등 유틸

tests/
├── unit/                 — AsyncMock 기반 단위 테스트
└── integration/          — Testcontainers 기반 DB 통합 테스트
```

---

## Search Service

```
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

```
src/
├── api/routers/          — HTTP 라우터 (POST /embed, GET /health)
├── schemas/              — Pydantic 요청/응답 DTO (EmbedRequest, EmbedResponse, HealthResponse)
├── services/
│   └── inference_service.py   — 텍스트 임베딩 추론, 입력 순서 보장
├── infra/
│   └── model_loader.py   — 구성된 모델 파일/디렉토리 로드, 버전 노출, 메모리 적재/해제
├── middlewares/          — Trace ID 추출 및 전파 미들웨어
├── observability/        — 구조화 로깅, 메트릭 (embed_request_latency_ms 등)
└── core/
    ├── settings.py       — pydantic-settings 기반 환경변수 관리
    └── model_state.py    — 모델 로딩 상태 플래그 (로딩 중 / 완료)

tests/
├── unit/                 — 더미 임베딩 모델 스텁 기반 단위 테스트
└── integration/          — E2E 통합 테스트 (더미 모델 스텁 사용)
```
