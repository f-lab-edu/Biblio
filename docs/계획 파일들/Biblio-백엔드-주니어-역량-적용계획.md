# 「백엔드 주니어 신입 쓸 것들」 Biblio 적용 보고서

- 작성일: 2026-07-13
- 원본 이미지:
  - `C:\Users\ASUS\Downloads\KakaoTalk_20260713_115226883.jpg`
  - `C:\Users\ASUS\Downloads\KakaoTalk_20260713_115226883_01.jpg`
- 적용 대상: Biblio의 Python 3.11/3.12, FastAPI, PostgreSQL, PGMQ, GCP 구조
- 연계 보고서: `/home/artyom9/project/agent_memory/Biblio-2026-Python-백엔드-취업경쟁력-보완계획.md`

## 1. 결론

이미지의 항목을 전부 구현할 필요는 없다. MySQL, Tomcat, Netty, JVM처럼 Java 생태계에 맞춘 표현이 섞여 있기 때문이다. Biblio에서는 같은 문제를 Python 백엔드 방식으로 바꿔야 한다.

가장 가치가 높은 Biblio 버전은 다음 여섯 가지다.

1. PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)`를 이용한 FTS·벡터 검색 개선
2. k6로 같은 조건의 성능 기준선과 전후 비교 만들기
3. `asyncio`, thread offload, admission control을 이용한 외부 호출 경합 해결
4. Cloud Run 인스턴스 수와 PostgreSQL connection pool을 함께 설계
5. 로컬 캐시의 중복 호출 방지와 다중 인스턴스 정합성 검증
6. 핵심 계약 중심의 CI·E2E·장애 주입 자동화

반대로 Redis, `tcpdump`, MCP, skills, hooks는 사용했다는 사실만 만들면 안 된다. 실제 문제와 측정값이 생길 때만 사용한다. Netty, Tomcat, JVM GC는 Biblio 이력서에서 빼고 ASGI 이벤트 루프, GIL, 프로세스 경계, RSS 메모리, Uvicorn·Cloud Run 동시성으로 바꾼다.

이 이미지의 핵심을 한 문장으로 바꾸면 다음과 같다.

> 도구 이름을 많이 적는 것이 아니라, 실제 문제를 같은 조건에서 재현하고 원인·대안·전후 수치·회귀 검증을 프로젝트 코드로 보여준다.

## 2. 이미지 항목의 Biblio 번역표

| 이미지 항목 | Biblio 버전 | 현재 상태 | 판단 |
|---|---|---|---|
| MySQL `EXPLAIN` | PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` | FTS·pgvector 검색은 있으나 실행 계획 산출물 없음 | 최우선 |
| 로컬 캐시 | 모델 서빙 대상 캐시, Gemini Vision 단일 호출 병합, 모델 artifact 로컬 캐시 | 이미 구현됨. 다중 인스턴스·메모리 수명 검증 부족 | 높은 가치 |
| Redis 성능·정합성 | 분산 캐시가 실제 필요한지 먼저 판단 | Redis 의존성 없음 | 현재 도입 보류 |
| 동기·비동기 외부 호출 | `httpx.AsyncClient`, `asyncio.gather`, `asyncio.to_thread`, `Semaphore` | 여러 경로에 구현됨. 혼합 부하 수치 부족 | 핵심 사례 |
| `tcpdump` 패킷 분석 | GCP 로그 → 연결 단계 시간 → DB·VM 상태 → 필요 시 제한적 패킷 캡처 | 사용 근거 없음 | 조건부 |
| connection pool 설정 | SQLAlchemy·asyncpg pool과 Cloud Run 동시성의 연결 예산 | PGMQ 풀만 명시적. SQLAlchemy 풀은 기본값 | 높은 가치 |
| k6 성능 테스트 | 검색·임베딩·API의 고정 부하와 통과 기준 | 현재 없음 | 최우선 |
| thread·Tomcat·Netty | ASGI 이벤트 루프, Uvicorn, `asyncio`, thread offload, worker concurrency | 실제 코드 근거 있음 | Python 표현으로 교체 |
| JVM GC·ratio tuning | Python RSS, `/tmp` 메모리, GIL, native library, subprocess, 동시성·batch 조절 | 실제 OOM 사례 있음 | 기존 강점 확장 |
| Claude token/context 절감 | 개발 생산성 보조 자료 | 제품 성과가 아님 | 이력서 본문 제외 |
| MCP 서버 적용 | 지식 그래프를 코드 탐색에 사용 | Biblio 제품 기능이 아니며 직접 만든 MCP도 아님 | 개발 방식 한 줄만 |
| skills·hooks 권한 가드 | 저장소 소유 CI, IAM, 테스트·배포 안전장치 | 로컬 설정은 공개 저장소 산출물이 아님 | 제품 성과로 주장 금지 |
| 모든 방면 테스트 자동화 | 상태 전이·테넌트·메시지·migration·모델 rollback 같은 핵심 계약 자동화 | 테스트는 많지만 CI 공백 존재 | 범위를 정확히 써야 함 |

## 3. Biblio에 이미 있는 근거

### 3.1 쿼리 개선의 출발점

Search Service의 현재 검색은 다음 흐름이다.

```text
query embedding
→ PostgreSQL FTS + vector distance query 병렬 실행
→ RRF 병합
→ PostgreSQL SOT gate
→ Gemini 답변
→ 검색 snapshot·conversation 저장
```

확인한 코드:

- `services/search-service/src/infra/db/search_repository.py:194` — `fts_search`
- `services/search-service/src/infra/db/search_repository.py:240` — `ann_search`
- `services/search-service/src/services/search_orchestrator.py:198` — FTS와 vector query를 `asyncio.gather`로 실행
- `services/core-api/alembic/versions/0003_pgvector_vector_index_entry.py` — pgvector 컬럼 migration

현재 FTS는 요청마다 `to_tsvector(...)`를 계산한다. 벡터 쿼리는 user·project·index·READY 조건에 맞는 행을 `<=>` 거리로 정렬한다. 저장소 migration에서는 HNSW·IVFFlat 인덱스 생성이 발견되지 않았다. 다만 운영 DB에 수동으로 만든 인덱스가 있는지는 `pg_indexes`로 별도 확인해야 한다. 따라서 이미지의 MySQL 예시는 Biblio에서 가장 자연스럽게 PostgreSQL 실행 계획 개선으로 바뀐다.

단, 데이터가 작으면 인덱스가 오히려 필요 없을 수 있다. 반드시 1천·1만·5만 청크 기준선을 먼저 만든다.

### 3.2 이미 구현된 로컬 캐시

Biblio에는 Redis보다 먼저 설명할 수 있는 로컬 캐시가 세 가지 있다.

#### Gemini Vision 중복 호출 병합

`extract_with_fallback`은 caption, OCR, scene tag를 동시에 요청한다. 세 protocol method는 결국 같은 keyframe의 `_analyze`를 호출한다. `GeminiVisionAdapter`는 keyframe별 lock과 cache를 이용해 세 요청을 실제 Gemini 호출 한 번으로 합친다.

- `services/pipeline-worker/src/infra/ai/vision_adapter.py:57`
- `services/pipeline-worker/src/infra/ai/gemini_vision_adapter.py:44`
- `services/pipeline-worker/src/infra/ai/gemini_vision_adapter.py:80`

이는 단순 조회 캐시보다 `single-flight`, 즉 같은 요청을 한 번만 실행하고 결과를 공유하려는 사례에 가깝다. 현재는 성공 경로에서만 호출 한 번으로 수렴한다.

남은 공백도 있다. cache dictionary가 worker 수명 동안 계속 남기 때문에 영상이 누적되면 key도 계속 늘 수 있다. 또한 Gemini 호출이 실패하면 성공 경로의 `_locks.pop()`까지 도달하지 않아 대기 중인 caption·OCR·tag가 provider를 다시 호출할 수 있다. 영상 처리 종료 때 cache와 실패한 lock을 정리할지, 최대 크기를 제한할지, in-flight `Task`를 공유하는 진짜 single-flight로 바꿀지는 호출 수와 메모리를 측정해 결정한다.

#### 모델 서빙 대상 캐시

`ServingSearchTargetProvider`는 DB의 active/previous 모델 대상을 한 번 읽고 메모리에서 반환한다. load와 reload는 `asyncio.Lock`으로 보호한다.

- `services/search-service/src/services/serving_targets.py:7`

문제는 gcp-perf Terraform의 Search Service 기본 최대 인스턴스가 3이라는 점이다. 실제 적용값은 state에서 다시 확인해야 한다. `/internal/reload-serving-targets` 요청이 한 인스턴스만 갱신하면 다른 인스턴스가 이전 모델 대상을 계속 사용할 가능성이 있다. 먼저 두 인스턴스에서 이 현상을 재현해야 한다.

이 사례는 `Redis를 써봤다`보다 `로컬 캐시의 다중 인스턴스 정합성 문제를 재현하고 해결했다`로 만드는 편이 강하다.

#### 모델 artifact 캐시

Embedding Endpoint는 GCS의 모델 artifact를 로컬 디스크에 materialize하고 같은 버전이 있으면 재사용한다. 임시 경로에서 다운로드를 끝낸 뒤 최종 경로로 바꿔 불완전한 artifact 노출을 막는다.

- `services/managed-embedding-endpoint/src/core/artifact_resolver.py`

cold/warm 기동 시간과 GCS 전송량을 측정하면 별도 보조 사례가 된다.

### 3.3 Python 동시성·외부 호출

Biblio는 이미지의 동기·비동기 항목을 이미 실제 코드로 보여줄 수 있다.

| 문제 종류 | 현재 처리 방식 | 코드 근거 |
|---|---|---|
| FTS와 여러 vector query | `asyncio.gather` | `search_orchestrator.py:198` |
| Vision caption·OCR·tag | `asyncio.gather` + key별 lock | `vision_adapter.py:57`, `gemini_vision_adapter.py:80` |
| 청크별 keyframe·Vision 처리 | `Semaphore`와 작은 batch | `pipeline_orchestrator.py:269` |
| ffmpeg 같은 동기 작업 | `asyncio.to_thread` | `pipeline_orchestrator.py:269` |
| 동기 `urlopen` 기반 feedback·reload | `asyncio.to_thread` | `feedback_delivery/http.py:35`, `release/model_reload.py:33` |
| 검색 임베딩 HTTP | `httpx.AsyncClient` + timeout·retry | `search-service/src/infra/embedding/client.py:35` |
| 장시간 CPU 추론 | Core·Search API 경로와 분리한 Embedding Endpoint, admission limit | `managed-embedding-endpoint/src/core/settings.py:43` |

현재 active와 previous 모델 query embedding은 순차 호출한다.

- `services/search-service/src/services/search_orchestrator.py:177`

이를 바로 `gather`로 바꾸면 안 된다. 7월 10일 실제 환경에서는 Embedding Endpoint의 슬롯이 1이었고, 긴 영상 임베딩이 10.463초 동안 점유해 검색 요청이 503을 받았다. 임베딩 서버의 용량을 그대로 둔 채 병렬 호출하면 오류만 늘 수 있다.

좋은 실험은 다음과 같다.

1. sequential과 concurrent query embedding을 비교한다.
2. Embedding Endpoint `MAX_CONCURRENCY` 1·2를 비교한다.
3. 검색과 영상 임베딩 혼합 부하를 고정한다.
4. 검색 p95, 503 비율, 영상 처리시간, VM CPU·메모리를 함께 본다.
5. 온라인·배치 용량 분리가 필요한지 판단한다.

이것이 Biblio 버전의 `thread와 Netty를 안다`에 해당한다.

### 3.4 connection pool

현재 SQLAlchemy engine은 대부분 아래처럼 생성된다.

```python
create_async_engine(database_url, future=True)
```

즉 pool 크기와 대기 timeout을 코드에서 명시하지 않고 라이브러리 기본값에 맡긴다.

반면 PGMQ를 소비하는 worker의 asyncpg pool은 애플리케이션 동시성과 연결돼 있다.

```text
Pipeline Worker: min_size = 2, max_size = WORKER_CONCURRENCY + 2
Feedback Loop Pipeline: min_size = 1, max_size = WORKER_CONCURRENCY + 2
```

Core API의 PGMQ 발행 경로는 주입된 연결이 없으면 메시지를 보낼 때마다 `asyncpg.connect()`와 close를 수행한다. 이 짧은 연결도 전체 예산과 연결 churn 측정에 포함해야 한다.

- `services/pipeline-worker/src/bootstrap.py:92`
- `services/feedback-loop-pipeline/src/bootstrap.py:455`
- `services/core-api/src/core/dependencies.py:32`
- `services/search-service/src/infra/db/session.py:4`

좋은 pool 사례는 값을 크게 바꾸는 것이 아니다. PostgreSQL 연결 예산을 먼저 계산하는 것이다.

```text
예상 최대 연결 수
= Σ(서비스 max instance × instance당 pool 상한)
+ worker별 PGMQ pool
+ Core API의 짧은 publish 연결
+ migration·운영 예약 연결
```

`pg_stat_activity`, pool checkout 대기, timeout, Cloud Run instance 수를 함께 측정한다. 연결 포화가 확인되기 전에는 PgBouncer도 넣지 않는다.

### 3.5 Java 런타임 항목의 Python 대체

| Java 중심 표현 | Biblio에서 설명할 내용 |
|---|---|
| Netty event loop | ASGI·`asyncio` 이벤트 루프 |
| Tomcat thread pool | Uvicorn process, thread offload, Cloud Run request concurrency |
| JVM thread 수 | event loop lag, thread 작업 수, worker concurrency |
| JVM GC | Python RSS, native library, `/tmp` 메모리 파일, subprocess 메모리 |
| JVM ratio tuning | `Semaphore`, batch, pool, CPU thread, instance 수의 균형 |

Core API, Search Service, Embedding Endpoint 컨테이너는 현재 각자 Uvicorn 단일 process로 실행된다. 무거운 임베딩 추론은 Core·Search API 경로에서 전용 Embedding Endpoint로 격리했고, ffmpeg는 subprocess와 thread offload 경계로 분리했다. Embedding Endpoint 내부 추론은 Uvicorn process에서 `to_thread`로 실행되므로, `CPU 작업이 API process에 전혀 없다`고 표현하면 안 된다.

이미 강한 메모리 사례도 있다. Pipeline Worker는 512MiB 제한에서 실제 535MiB를 사용해 OOM이 났다. `/tmp`에 원본 영상이 남아 있는 구조와 worker concurrency가 메모리를 결정한다는 점을 분석했다. 이 경험은 JVM GC를 억지로 학습해 붙이는 것보다 Biblio의 실제 Python 메모리 모델을 설명하는 데 유리하다.

### 3.6 `tcpdump`는 언제 쓰는가

Biblio에는 아직 `tcpdump`로 장애를 해결한 근거가 없다. 이력서에 도구 이름부터 넣으면 안 된다.

네트워크 진단 순서는 다음처럼 잡는다.

1. Cloud Logging에서 DNS, connect, read, provider status를 분리한다.
2. `curl -w`, 애플리케이션 단계 시간, `pg_stat_activity`, VM 상태를 확인한다.
3. 방화벽, private IP, Direct VPC egress, 서비스 계정 설정을 확인한다.
4. 그래도 TCP 가설이 남을 때 테스트 VM에서 포트와 대상 IP를 제한해 packet을 수집한다.

실제로 PostgreSQL VM이 정지해 Search Service 배포가 `asyncpg TimeoutError`로 실패한 적이 있다. 이 사건은 packet capture보다 VM 상태 확인이 먼저였다. `tcpdump를 할 줄 안다`보다 `가설의 층위를 좁혀 가장 싼 증거부터 확인한다`가 좋은 설명이다.

YouTube GCP IP 차단과 WARP proxy 경로는 더 현실적인 네트워크 사례다. 2026-07-12 E2E에서 direct GCP IP의 `yt-dlp`는 403이었고, WARP egress에서는 실제 영상이 READY까지 도달했다. 전체 처리시간은 66.922초였고 download 10.235초, STT 36.564초였다. 다만 관련 인프라 변경은 아직 커밋 전이고 무료 WARP endpoint의 수명도 보장되지 않는다. 이력서에 쓰기 전 재현 명령, 로그, 인프라 diff, 한계를 하나의 공개 가능한 사례 문서로 고정한다.

## 4. Redis를 넣을지 판단하는 기준

현재 Biblio에는 Redis가 없다. PostgreSQL이 SOT이고 PGMQ가 queue를 맡는다. 이 상태에서 Redis를 추가하면 비용과 장애 지점만 늘 수 있다.

아래 조건 중 하나가 실제 측정될 때만 작은 비교 실험을 한다.

- 여러 Search Service 인스턴스가 공유해야 하는 hot state가 있다.
- 반복 DB 조회가 검색 p95의 의미 있는 비중을 차지한다.
- 분산 rate limit 또는 lock이 실제로 필요하다.
- 허용 가능한 stale 시간과 invalidation 규칙을 먼저 정의할 수 있다.

Search target cache 문제의 첫 해결책을 Redis로 고정하지 않는다. 후보는 다음과 같다.

1. 요청마다 DB SOT 조회
2. 짧은 TTL + release revision 확인
3. 주기적 refresh와 장애 시 기존 값 유지
4. Redis에 version만 공유하고 DB를 최종 기준으로 사용

두 Search Service 인스턴스로 stale cache를 재현한 뒤 가장 단순한 방법을 고른다. Redis를 기각해도 ADR에 측정값과 이유가 있으면 좋은 설계 사례다.

## 5. AI 활용 항목의 Biblio 버전

이력서에서 강조할 AI는 Claude 사용법이 아니라 Biblio 제품 안의 AI 운영이다.

- Google STT의 submit·operation timeout 분리와 retryable 오류 처리
- Gemini Vision의 성공 경로 single-flight, 재시도, 빈 결과 fallback
- Gemini 답변 생성의 timeout·재시도·500·503 오류 매핑
- BGE-M3 Embedding Endpoint의 admission control
- active·previous·candidate 모델 runtime 관리
- Recall@5·MRR@5·nDCG@5 기반 offline evaluation
- candidate release, rollback, legacy re-index, re-embedding

강한 문장은 다음과 같다.

> 외부 AI를 호출했다가 아니라, 외부 AI가 느리거나 실패하거나 모델 버전이 바뀌어도 서비스 상태와 검색 정합성이 무너지지 않게 계약을 설계하고 검증했다.

### MCP·skills·hooks를 쓰는 정직한 방법

공개 `AGENTS.md`에는 codebase-memory-mcp를 코드 탐색에 사용한다는 규칙이 있다. 하지만 로컬 `.agents`, `.claude`, agentmemory, logging hook은 Biblio 공개 제품 기능이 아니다. 커밋된 `.mcp.json`이나 저장소 소유 AI hook도 없다.

따라서 다음 정도만 개발 방식에 한 줄로 쓴다.

> 코드 지식 그래프와 AI 도구를 영향 범위 탐색·초안 작성에 사용하고, 최종 판단은 ADR, 코드 리뷰, 테스트, CI 결과로 검증했습니다.

이 문장은 이력서 본문보다 README의 개발 방식이나 면접 답변에 적합하다.

다음 표현은 쓰지 않는다.

- Biblio에 MCP 서버를 구축했다.
- 팀 공용 skills·hooks 체계를 만들었다.
- Claude token을 줄여 성능을 개선했다.
- AI가 대부분의 코드를 자동으로 만들었다.

개인 개발 자동화를 성과로 보여주고 싶다면 Biblio와 분리된 공개 저장소에 secret·절대 경로 없는 설치법과 예제를 만들어야 한다.

## 6. 테스트 자동화의 현재 수준과 공백

현재 공개 저장소에는 다음 자동화가 있다.

- Core API PR test, Alembic migration, coverage 80% gate
- Search Service PR test와 coverage 80% gate
- Managed Embedding Endpoint PR test와 coverage 80% gate
- Pipeline Worker unit·integration·coverage 실행
- SonarCloud 비차단 자동 분석
- 업로드→READY, 검색, 피드백, 데이터셋, 후보 배포, rollback recovery의 6단계 backend E2E 실행기

대표 테스트도 좋다.

- 사용자·프로젝트 테넌트 격리
- cookie 인증과 CSRF
- PGMQ 중복·visibility·stale claim
- PostgreSQL migration 계약
- 중간 artifact 재사용과 pipeline resume
- 모델 release·rollback·re-index
- rollback 제외 프로젝트를 SOT·FTS·vector 전 경로에서 차단
- Embedding 503 재시도·응답 shape, LLM retryable·terminal 500/503 매핑, Vision fallback

하지만 `모든 방면 테스트 자동화`라고 쓰면 과장이다.

현재 공백:

- Feedback Loop Pipeline CI 없음
- Feedback Ingestion Pipeline CI 없음
- Frontend Vitest CI 없음
- root scripts와 backend E2E CI 미연결
- Pipeline Worker에 공통 coverage threshold 없음
- SonarCloud는 `continue-on-error: true`라 병합 차단 gate가 아님
- 현재 로컬 E2E report는 dry-run이므로 live GCP E2E 완료 근거가 아님

정확한 표현은 다음과 같다.

> 주요 4개 Python 서비스의 PR 테스트 자동화와 3개 서비스의 80% coverage gate를 구성하고, 테넌트 격리·migration·모델 rollback 계약을 PostgreSQL 통합 테스트로 검증했습니다.

6단계 backend E2E 실행기와 JSON 보고 기능은 구현돼 있다. 하지만 저장된 결과는 dry-run이므로, CI 공백을 닫고 실제 live E2E artifact를 남긴 뒤에만 운영 검증 성과로 범위를 넓혀 쓴다.

## 7. 7주 집중 계획

모든 키워드를 한 번에 구현하지 않는다. 취업용 대표 사례를 완성하기 위해 `검색 성능 1건`과 `신뢰성·동시성 1건`에 집중한다. 두 번째 사례는 4주차에 기준선을 보고 임베딩 경합과 worker crash 복구 중 하나만 고른다.

| 주차 | 집중 작업 | 완료 증거 |
|---|---|---|
| 1주차 | 검색 단계 시간, DB pool checkout, experiment id를 계측한다. 1천·1만·5만 청크 fixture와 고정 query set을 만든다. mock embedding·LLM을 쓰는 로컬 Search API 부하와 실제 외부 AI E2E를 분리한다. | dataset manifest, hash, 실행 명령, 원본 timing |
| 2주차 | k6와 `EXPLAIN (ANALYZE, BUFFERS)`로 FTS·vector 기준선을 만든다. 운영 DB의 인덱스도 `pg_indexes`로 확인한다. | p50·p95, 지속 RPS, 오류율, 실행 계획 원본 |
| 3주차 | 가장 큰 검색 병목 하나만 고친다. GIN·생성 tsvector·vector ANN 같은 해법은 실행 계획을 본 뒤 고른다. 같은 부하를 3회 재실행하고 검색 품질 회귀를 확인한다. | 전후 수치, buffer 변화, Recall/MRR/nDCG, 한계 |
| 4주차 | 임베딩 혼합 부하 또는 worker crash를 각각 짧게 재현한다. 사용자 영향과 재현성이 더 큰 한 사례만 선택한다. | 선택 근거, 기준선, 실패 시간 순서 |
| 5주차 | 선택한 두 번째 사례를 수정한다. 임베딩이면 admission·용량 분리를, crash면 ack·stale reclaim·idempotency를 검증한다. | 전후 p95·503·처리량 또는 복구율·MTTR·부작용 |
| 6주차 | 두 사례의 raw data, ADR, 테스트, 재현 스크립트, 그래프를 정리하고 이력서·포트폴리오 문장을 확정한다. | 공개 가능한 사례 문서 2개, 숫자 출처 링크 |
| 7주차 | 환경 변동으로 실패한 run만 다시 실행하고 제3자가 문서대로 재현하는지 확인한다. | 3회 반복 결과, 재현 피드백, 최종 문장 |

connection pool, 캐시 정합성, CI 확대는 후속 backlog로 둔다. 기준선이 실제 병목을 가리킬 때만 대표 사례로 승격한다. Redis와 `tcpdump`는 필요 조건이 생기지 않으면 구현하지 않는다.

### 대표 사례 합격 기준

#### 쿼리

- 2주차 기준선 후 primary metric을 고정한다.
- latency 병목이면 retrieval p95 30% 이상 감소를 목표로 한다.
- capacity 병목이면 최대 지속 RPS 2배를 목표로 한다.
- 선택하지 않은 성능 지표는 10% 넘게 악화되지 않아야 한다.
- vector ANN 적용 시 exact search 대비 Recall@5 0.95 이상을 확인한다.
- 사용자 정답 query set의 MRR@5·nDCG@5는 1%p 넘게 하락하지 않아야 한다.

#### 두 번째 사례

- 임베딩 경합을 고르면 검색 p95·503 비율·영상 처리시간을 함께 측정한다.
- worker crash를 고르면 crash 시점부터 stale reclaim, redelivery, 최종 상태까지 시간 순서를 남긴다.
- redelivery 횟수와 중복 감지율을 기록하고, 중복 재전달로 인한 DB·artifact 부작용은 0건이어야 한다.
- 선택하지 않은 경로의 처리량이나 오류율이 10% 넘게 나빠지면 개선으로 판정하지 않는다.

### 후속 backlog 합격 기준

#### 캐시

- 성공 경로에서 caption·OCR·tag 동시 요청이 keyframe당 Gemini 호출 한 번으로 수렴한다.
- hard failure 때 provider 호출 수 상한과 실패한 lock 정리를 테스트한다.
- Vision cache의 key 수와 메모리가 영상 누적에 따라 무제한 증가하지 않는다.
- Search Service 두 인스턴스에서 release 전환 후 정의한 stale SLO 안에 같은 target을 반환한다.
- Redis를 쓰면 Redis 장애·notification 유실 때 DB SOT로 복구한다.

#### connection pool·runtime

- pool checkout timeout 0건
- 유효 요청 5xx·timeout 1% 미만
- PostgreSQL 실제 연결 수가 예약분을 제외한 상한 안에 있음
- event loop lag와 thread queue가 부하 종료 후 정상으로 돌아옴
- OOM·비정상 restart 0건

#### 테스트 자동화

- Feedback Loop, FIP, Frontend, root scripts CI 연결
- 현재 coverage baseline을 측정한 뒤 내려가지 않는 ratchet threshold 적용
- Sonar를 안정화한 뒤 비차단 설정 제거 여부 결정
- live E2E는 `workflow_dispatch` 또는 예약 실행으로 돌리고 민감값을 제거한 JSON을 artifact로 보관
- 외부 비용이 큰 STT·Gemini 실험은 모든 PR에서 돌리지 않음

## 8. 이력서 문장

### 지금 쓸 수 있는 문장

#### Python 비동기 처리

> Search Service에서 FTS와 vector 조회를 `asyncio.gather`로 실행하고, Pipeline Worker에서 ffmpeg 호출을 `asyncio.to_thread`로 넘기며 `Semaphore`로 청크 동시성을 제한했습니다.

이 문장에는 아직 개선 퍼센트를 붙이지 않는다. 4~5주차 실험 후 p95·처리량을 추가한다.

#### AI 외부 의존성

> LLM·Embedding에는 timeout·재시도·오류 매핑을, Vision에는 성공 경로 single-flight·재시도·빈 결과 fallback을 적용하고 trace를 전파했습니다.

#### 모델 운영 정합성

> active·previous·candidate 임베딩 runtime과 candidate index 전환 조건을 설계하고, rollback 복구 중인 프로젝트를 PostgreSQL SOT·FTS·vector 전 검색 경로에서 차단하는 통합 테스트를 구축했습니다.

#### 테스트 자동화

> 주요 4개 Python 서비스의 PR 테스트 자동화와 3개 서비스의 80% coverage gate를 구성하고, 테넌트 격리·migration·모델 rollback 계약을 PostgreSQL 통합 테스트로 검증했습니다.

6단계 backend E2E 실행·JSON 보고 기능은 `구현한 도구`로 따로 설명할 수 있다. 현재 live E2E 완료 증거는 없으므로 `운영 E2E 완주`라고 바꾸면 안 된다.

### 실험 후 쓸 문장

#### PostgreSQL

> 5만 청크 환경에서 `EXPLAIN (ANALYZE, BUFFERS)`와 k6로 `[병목]`을 확인하고 `[변경]`을 적용해 retrieval p95를 `A→B`, 최대 지속 RPS를 `C→D`로 개선했습니다. 검색 품질 `[지표]`는 기준선 대비 `E%p` 이내로 유지했습니다.

#### connection pool

> Cloud Run 최대 인스턴스와 PostgreSQL 연결 상한으로 서비스별 pool 예산을 계산하고 `[설정]`을 적용해 pool 대기 p95를 `A→B`, 연결 timeout을 `C→D`로 줄였습니다.

#### 캐시 정합성

> Search Service 다중 인스턴스에서 model release 후 구·신 target이 섞이는 캐시 문제를 재현하고 `[TTL/revision/공유 version]` 계약으로 stale window를 `A→B`로 줄였습니다. cache 장애 시 PostgreSQL SOT 복구 경로도 검증했습니다.

#### 임베딩 경합

> CPU 임베딩 서버의 단일 admission slot을 영상 batch가 10.463초 점유해 검색 503이 발생하는 경합을 추적하고 `[용량 분리/우선순위/동시성]`을 적용해 검색 오류율을 `A→B`, p95를 `C→D`로 개선했습니다.

숫자를 측정하기 전에는 A~E를 채우지 않는다.

## 9. 포트폴리오 구성

이미지의 기술 목록을 한 페이지에 나열하지 않는다. 사례별로 다음 다섯 장을 사용한다.

1. 문제와 사용자 영향
2. 재현 조건과 기준선
3. 실행 계획·로그·메트릭으로 좁힌 원인
4. 대안과 선택 이유
5. 같은 조건의 전후 결과, 회귀 검증, 남은 한계

현재 바로 쓸 수 있는 정량·문제 해결 사례:

1. 분석 대상 디스크 예상 월 유지비 38,213원→10,088원, 74% 절감
2. direct GCP egress의 YouTube 403을 WARP private proxy 경로로 우회해 실제 영상 READY 확인, 총 66.922초
3. 512MiB 제한에서 535MiB 사용으로 난 OOM과 3개 `PROCESSING` stuck의 원인 분석
4. 모델 rollback 복구 중인 프로젝트를 SOT·FTS·vector 전 경로에서 차단한 정합성 테스트

다음으로 완성할 대표 사례:

1. PostgreSQL 검색 실행 계획과 p95 개선
2. 온라인 검색·영상 batch 임베딩 경합 또는 worker crash 복구 중 한 건

추천 보조 사례:

- Vision single-flight cache와 호출 비용
- 모델 release·rollback·re-index 정합성
- connection pool과 Cloud Run 연결 예산

## 10. 쓰지 말아야 할 문장

- Redis를 활용해 성능을 개선했습니다. — 현재 Redis도 전후 수치도 없음
- `tcpdump`로 네트워크 장애를 해결했습니다. — 사용 근거 없음
- Netty·Tomcat·JVM을 이해합니다. — Biblio 기술이 아님
- Claude token과 context를 절약해 서비스 성능을 높였습니다. — 개발 도구와 제품 성능을 혼동
- MCP 서버와 skills·hooks 체계를 구축했습니다. — 공개 Biblio 산출물이 아님
- 모든 테스트를 자동화했습니다. — CI 공백과 dry-run E2E가 있음
- ANN 검색을 구현했습니다. — 현재 vector ANN index 근거가 없음
- 운영 환경 E2E를 완주했습니다. — 현재 저장된 report는 dry-run
- stale reclaim으로 crash 자동 복구를 보장했습니다. — 실제 crash 시간 순서 검증 전

## 11. 바로 시작할 일

1. 이전 취업 경쟁력 보고서와 이 문서를 하나의 실행 backlog로 사용한다.
2. Search Service 단계 시간과 DB pool checkout 시간을 먼저 추가한다.
3. 1천·1만·5만 청크 fixture와 고정 query set을 만든다.
4. mock embedding·LLM을 쓰는 로컬 Search API 시나리오 또는 repository benchmark harness와 실제 Gemini E2E를 분리한다. 현재 `retrieval-only` 전용 API는 없으므로 있다고 가정하지 않는다.
5. FTS와 vector query의 `EXPLAIN (ANALYZE, BUFFERS)` 원본을 저장한다.
6. 기준선이 나온 뒤 검색 병목 하나만 고친다.
7. 임베딩 경합과 worker crash를 짧게 재현한 뒤 두 번째 사례 하나만 고른다.
8. connection pool과 cache 정합성은 기준선이 병목을 가리킬 때 후속 작업으로 올린다.
9. Redis와 `tcpdump`는 측정 결과가 필요성을 가리킬 때만 검토한다.

## 12. 내부 근거

- `docs/system-design.md`
- `docs/Tech_Spec/Observability_and_Ops_Standards.md` — Draft이므로 구현 완료 근거가 아니라 기준 문서
- `docs/runbooks/gcp-performance-deployment.md`
- `services/search-service/src/infra/db/search_repository.py`
- `services/search-service/src/services/search_orchestrator.py`
- `services/search-service/src/services/serving_targets.py`
- `services/pipeline-worker/src/services/pipeline_orchestrator.py`
- `services/pipeline-worker/src/infra/ai/gemini_vision_adapter.py`
- `services/pipeline-worker/src/bootstrap.py`
- `services/managed-embedding-endpoint/src/services/model_reloader.py`
- `services/feedback-loop-pipeline/tests/integration/test_rollback_end_to_end.py`
- `.github/workflows/core-api-ci.yml`
- `.github/workflows/search-service-ci.yml`
- `.github/workflows/python-tests.yml`
- `.github/workflows/managed-embedding-endpoint-ci.yml`
- `.github/workflows/sonarcloud-analyze.yml`
- `scripts/e2e/run_all_backend_e2e.py`
- `/home/artyom9/project/agent_memory/biblio_work_log/별도 정리 로그/7_10_지인테스트_결과.md`

## 13. 최종 판단

이미지의 조언은 기술 키워드 목록으로 보면 Biblio와 절반 정도만 맞는다. 문제 해결 방식으로 번역하면 대부분 활용할 수 있다.

Biblio에서 지금 이력서에 먼저 보여줄 순서는 다음과 같다.

1. 디스크 비용 74% 절감처럼 이미 계산 근거가 있는 운영 개선
2. YouTube 403→READY와 OOM 535MiB처럼 원인·영향이 확인된 장애 분석
3. 모델 수명주기와 rollback 정합성 테스트

앞으로 완성할 순서는 다음과 같다.

1. PostgreSQL 실행 계획과 k6 전후 수치
2. 임베딩 경합 또는 worker crash 복구 중 한 건
3. 측정 결과가 가리킬 때만 connection pool이나 캐시 정합성 확장

Redis, `tcpdump`, MCP, skills, hooks는 이 순서를 완성한 뒤 실제 필요가 생겼을 때만 추가한다. 사용한 기술 수보다 같은 조건의 전후 수치와 정합성 검증이 훨씬 중요하다.
