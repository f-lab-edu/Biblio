# Biblio

> 긴 영상에서도 `Ctrl+F`처럼 필요한 내용을 찾을 수 있도록, 영상을 분석해 근거 타임스탬프가 포함된 검색·질의응답을 제공하는 멀티모달 RAG 서비스입니다.

Biblio는 영상 업로드부터 미디어 전처리, 음성·장면 분석, 임베딩 생성, 검색, 답변 생성까지 이어지는 전체 흐름을 Python 백엔드로 구현한 프로젝트입니다.

단순히 LLM API를 호출하는 데서 끝내지 않고, 비동기 영상 처리, 하이브리드 검색, 사용자별 접근 제어, 장애 복구, 피드백 수집과 임베딩 모델 배포·롤백까지 실제 서비스 운영에 필요한 경계를 함께 다뤘습니다.

## 해결하려는 문제

강의, 회의, 뉴스처럼 긴 영상은 문서와 달리 키워드 검색이 어렵습니다. 원하는 내용을 다시 찾으려면 타임라인을 반복해서 이동하거나 영상을 처음부터 재생해야 합니다.

Biblio는 다음 흐름으로 이 문제를 해결합니다.

1. 로컬 파일 또는 YouTube URL로 영상을 등록합니다.
2. 영상에서 오디오와 핵심 장면을 추출합니다.
3. 음성 인식 결과와 장면 설명을 시간 구간별 검색 문맥으로 만듭니다.
4. 키워드 검색과 벡터 검색 결과를 결합합니다.
5. 검색된 구간만 LLM에 전달해 답변과 근거 타임스탬프를 반환합니다.

## 주요 기능

### 영상 수집과 비동기 처리

- 로컬 영상 업로드와 YouTube URL 등록
- GCS 서명 URL을 이용한 클라이언트 직접 업로드
- `다운로드 → 오디오·키프레임 추출 → STT → 청킹 → 임베딩 → 저장` 파이프라인
- PGMQ 기반 비동기 작업 처리와 API·워커 책임 분리
- 처리 단계별 상태 및 실패 지점 기록
- 중간 산출물을 재사용하는 멱등적 재시도와 실패 지점 복구

### 멀티모달 RAG 검색

- Google Speech-to-Text를 이용한 음성 전사
- Gemini Vision을 이용한 핵심 장면 설명
- `BAAI/bge-m3` 임베딩 전용 서빙 서비스
- PostgreSQL 전문 검색과 pgvector 검색을 RRF로 결합한 하이브리드 검색
- 검색 결과에 근거 구간과 영상 타임스탬프 제공
- 검색 근거가 없을 때 LLM 호출을 생략하는 검색 게이트
- 사용자와 프로젝트 소유권을 모든 검색 단계에서 검증

### 피드백과 모델 수명주기

- 검색 응답 단위의 좋아요·싫어요 피드백 수집
- 원본 피드백 로그와 검색 당시 문맥을 학습 데이터로 고정
- 데이터셋 생성, 후보 모델 학습·평가, 배포, 기존 영상 재임베딩 흐름
- `active / previous / candidate` 모델 상태 관리
- 문제 발생 시 이전 모델로 전환하고 데이터 정합성을 복구하는 롤백 흐름

### 운영과 검증

- JWT 기반 인증·인가와 프로젝트 단위 데이터 격리
- 구조화 로그, trace ID 전파, 파이프라인 단계별 실행 시간 기록
- 단위·통합·API·E2E 테스트
- Docker Compose 기반 로컬 통합 환경
- Terraform 기반 GCP 인프라 구성
- GitHub Actions와 SonarCloud를 이용한 자동 검증

## 시스템 구성

![Biblio 클라우드 아키텍처](./docs/Diagram/biblio%20cloud%20architecture.png)

| 컴포넌트 | 책임 |
| --- | --- |
| `core-api` | 인증, 프로젝트·영상 관리, 업로드 URL 발급, 비동기 작업 요청, 피드백 검증 |
| `pipeline-worker` | 영상 다운로드, FFmpeg 전처리, STT·Vision 호출, 청킹, 임베딩과 적재 |
| `search-service` | 프로젝트 범위 검증, 하이브리드 검색, RAG 답변과 타임스탬프 생성 |
| `managed-embedding-endpoint` | 임베딩 모델 로드, 버전별 추론, 모델 전환 |
| `feedback-ingestion-pipeline` | 검증된 피드백 이벤트를 원본 로그로 저장 |
| `feedback-loop-pipeline` | 데이터셋 생성, 학습·평가, 모델 배포, 재임베딩과 롤백 복구 |
| `frontend` | 업로드, 처리 상태, 검색, 영상 재생과 피드백 UI |

자세한 설계는 [시스템 설계 문서](./docs/system-design.md)에서 확인할 수 있습니다.

## 주요 설계 결정

### 키워드 검색과 벡터 검색을 함께 사용

벡터 검색만 사용하면 고유명사나 정확한 용어 검색이 약해질 수 있습니다. PostgreSQL 전문 검색과 pgvector 검색에서 각각 후보를 구한 뒤 RRF로 합쳐 의미 유사성과 정확한 단어 일치를 함께 반영했습니다.

- [ADR-004: 영상 검색 전략](./docs/ADR/ADR-004-video-search-retrieval-strategy.md)

### 임베딩 서빙을 API 서비스와 분리

모델의 메모리 사용량과 버전 전환을 API 서버의 수명주기에서 분리했습니다. 모델 원본은 GCS에 두고 전용 VM의 영속 디스크를 실행 캐시로 사용합니다. Cloud Run 서비스는 VPC 내부 주소로만 임베딩 엔드포인트를 호출합니다.

- [ADR-014: 임베딩 엔드포인트 VM 배포 전략](./docs/ADR/ADR-014-임베딩%20엔드포인트%20VM%20기반%20배포%20전략.md)
- [ADR-013: Cloud Run과 VM의 사설 네트워크](./docs/ADR/ADR-013-Cloud%20Run과%20VM%20간%20사설%20네트워크%20구성.md)

### 모델 교체보다 롤백 가능성을 먼저 설계

새 모델 배포 시 기존 벡터를 즉시 삭제하지 않습니다. 현재와 이전 모델의 검색 경로를 함께 유지하고 문제가 생기면 서빙 포인터를 이전 버전으로 전환한 뒤 누락 데이터를 복구하도록 구성했습니다.

- [ADR-012: 임베딩 모델 롤백 스냅샷 전략](./docs/ADR/ADR-012-embedding-model-rollback-snapshot-strategy.md)
- [ADR-015: 모델 학습 자동화보다 배포·롤백 검증 우선](./docs/ADR/ADR-015-모델%20학습%20자동화보다%20배포%20롤백%20검증%20우선.md)


## 기술 스택

| 구분 | 기술 |
| --- | --- |
| Backend | Python 3.11, FastAPI, Pydantic, SQLAlchemy Async, Alembic |
| AI | Google Speech-to-Text, Gemini on Vertex AI, BAAI/bge-m3, RAG |
| Search & Data | PostgreSQL, pgvector, Full-Text Search, RRF, PGMQ |
| Frontend | Next.js, React, TypeScript, Tailwind CSS |
| Infrastructure | GCP Cloud Run, Compute Engine, GCS, VPC, Secret Manager, Terraform, Docker |
| Test & CI | pytest, Testcontainers, Vitest, GitHub Actions, SonarCloud |

## 검증 범위

`scripts/e2e`에는 다음 백엔드 전체 흐름을 순서대로 검증하는 시나리오가 있습니다.

1. 영상 등록부터 검색 준비 완료
2. 하이브리드 검색과 RAG 답변
3. 피드백 전달과 원본 로그 적재
4. 학습 데이터셋 생성
5. 후보 모델 학습·배포와 기존 영상 재임베딩
6. 모델 롤백과 데이터 복구

서비스별로 단위·통합 테스트를 분리하고, DB 통합 테스트는 PostgreSQL·pgvector 환경을 Testcontainers로 실행합니다.

## 저장소 구조

```text
Biblio/
├── frontend/                         # Next.js 사용자 UI
├── services/
│   ├── core-api/                     # 인증, 프로젝트·영상 관리 API
│   ├── pipeline-worker/              # 비동기 미디어·AI 처리
│   ├── search-service/               # 하이브리드 검색과 RAG
│   ├── managed-embedding-endpoint/   # 임베딩 모델 서빙
│   ├── feedback-ingestion-pipeline/  # 피드백 원본 로그 수집
│   └── feedback-loop-pipeline/       # 학습·배포·롤백 파이프라인
├── packages/                         # 서비스 간 공유 패키지
├── infra/terraform/                  # GCP Infrastructure as Code
├── scripts/e2e/                      # 백엔드 E2E 시나리오
└── docs/                             # PRD, 설계, ADR, 운영 문서
```

## 문서

- [PRD](./docs/PRD.md)
- [System Design](./docs/system-design.md)
- [Folder Structure](./docs/folder_structure.md)
- [Architecture Decision Records](./docs/ADR)
- [GCP Performance Deployment Runbook](./docs/runbooks/gcp-performance-deployment.md)
- [Git Naming Convention](./docs/git-naming-convention.md)

> PRD에 적힌 성능 수치는 제품 목표입니다. 측정이 끝난 지표와 목표 지표를 구분하기 위해, 검증되지 않은 수치는 README의 구현 성과로 사용하지 않았습니다.
