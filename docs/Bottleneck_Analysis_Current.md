# 배포 환경 병목 분석 보고서

- 기준일: 2026-08-03
- 대상 환경: `infra/terraform/envs/gcp-perf` · GCP 프로젝트 `project-ed2d3cb0-7d1e-43ef-bb6`
- 근거 문서: `docs/Diagram/Deployment_Diagram_Search_Current.md`, `docs/Diagram/Deployment_Diagram_Video_Processing_Current.md`, `docs/Diagram/Value_Stream_Map_Search_Current.md`, `docs/Diagram/Value_Stream_Map_Video_Processing_Current.md`
- 목적: 부하가 몰릴 때 먼저 무너질 지점을 순서대로 정리하고, 지점별 대응 방안과 비용을 비교한다.
- 실측 범위: 2026-08-03 배포값 조회와 PostgreSQL 실행 계획 측정을 반영했다. 배포값은 실행 중인 Cloud Run revision과 VM 메타데이터에서 직접 읽었다.

## 결론

부하보다 **데이터 증가에 먼저 무너지는 지점이 FTS**다. 현재 `chunk` 테이블에는 기본키 외의 인덱스가 없어서, 검색 한 건이 테이블 전체를 순차 스캔하며 모든 행에 `to_tsvector`를 다시 계산한다. 게다가 현재 실행 계획은 이 전체 스캔을 **프로젝트의 READY 영상 수만큼 반복**한다. 청크 148개짜리 지금 데이터에서도 영상 11개 프로젝트의 FTS가 476ms로 측정됐다(실측). 같은 조건에서 벡터 검색은 7ms다.

두 번째는 **검색 임베딩 VM의 단일 실행 슬롯**이다. `MAX_CONCURRENCY=1`이 실제 배포값으로 확인됐고, warm 추론이 평균 410.2ms이므로 순차 처리 상한은 초당 약 2.4건이다(계산값). 대기 상한 5초를 넘는 요청부터 거부된다. 데이터가 적을 때는 이쪽이 먼저 막히고, 데이터가 늘면 FTS가 훨씬 먼저 무너진다.

세 번째는 **인스턴스 하나 안에서 요청 동시성 80과 DB 커넥션 15가 어긋나는 구조**다. Cloud Run 동시성은 세 서비스 모두 80(기본값)으로 확인됐는데, SQLAlchemy 기본 풀은 인스턴스당 15개다. 인스턴스 1대가 요청 수십 건을 받는 동안 DB 커넥션은 15개뿐이라, Cloud Run이 인스턴스를 늘리기 전에 풀에서 먼저 밀린다.

가장 비용 대비 효과가 큰 조치는 FTS용 GIN 인덱스 추가다. 나머지는 실측 후 판단한다.

## 조회로 확인한 배포값

앞선 문서에서 `확인 필요`로 남겨둔 세 항목을 실제 배포에서 조회했다.

| 항목 | 확인된 값 | 확인 방법 |
|---|---|---|
| Cloud Run 요청 동시성 | frontend·search-service·core-api·pipeline-worker 모두 **80** (GCP 기본값) | `gcloud run services describe` |
| Cloud Run 인스턴스·자원 | 세 서비스 min 0 · max 3 · 1 vCPU · 512MiB · timeout 300초. pipeline-worker max 1 · 4GiB · timeout 3600초 | 같음 |
| PostgreSQL `max_connections` | **100**, `superuser_reserved_connections` 3 → 앱이 쓸 수 있는 값은 **97** | `pg_settings` 조회 |
| PostgreSQL 메모리 설정 | `shared_buffers` 128MiB, `work_mem` 4MiB, `effective_cache_size` 4GiB (모두 패키지 기본값) | 같음 |
| 임베딩 VM 환경변수 | `MAX_CONCURRENCY=1`, `SEARCH_REQUEST_LIMIT=32`, `VIDEO_PREPROCESS_REQUEST_LIMIT=4`, `SEARCH_WAIT_TIMEOUT_SEC=5`, `VIDEO_PREPROCESS_WAIT_TIMEOUT_SEC=20` | VM startup-script 메타데이터 |
| 임베딩 VM 스레드 제한 | **없음.** `OMP_NUM_THREADS` 등 스레드 제한 환경변수가 없고, docker-compose에도 CPU 제한이 없다 | 같음 |

임베딩 VM 값은 Terraform 선언값과 모두 일치했다. `max_connections`는 startup-script가 손대지 않아 PostgreSQL 패키지 기본값 100이 그대로 쓰인다.

측정 시점 데이터 규모는 청크 148개, `vector_index_entry` 168개, 영상 52개, 프로젝트 16개다.

## 현재 구조에서 부하가 흐르는 길

검색 요청 한 건은 Frontend Cloud Run → Search Service Cloud Run → 검색 임베딩 VM(질의 임베딩) → PostgreSQL(FTS·벡터 병렬) → Vertex AI Gemini(LLM) 순서로 지나간다. 영상 색인은 Core API가 PGMQ에 메시지를 넣고, Pipeline Worker(max 1)가 꺼내서 다운로드 → STT → 청킹·Vision → 배치 임베딩 VM → PostgreSQL 저장으로 처리한다.

부하 관점에서 중요한 사실은 세 가지다.

- 검색용 임베딩 VM(10.20.3.15)과 배치용 임베딩 VM(10.20.3.14)은 분리되어 있다. 검색과 색인이 임베딩 슬롯을 서로 뺏지 않는다.
- PostgreSQL VM은 분리되어 있지 않다. 검색 조회, 색인 쓰기, PGMQ 폴링이 모두 e2-standard-4 한 대로 들어간다.
- 같은 PostgreSQL을 쓰는 Cloud Run 서비스는 search-service, core-api, pipeline-worker, feedback-ingestion-pipeline과 feedback-loop 계열 워커 6종까지 모두 11개다.

## 검색 경로 병목

### 1. FTS 전체 스캔 — 데이터가 늘면 가장 먼저 무너진다

`chunk` 테이블의 인덱스는 기본키 `chunk_pkey(id)` 하나뿐이다. `video_id` 인덱스도, `to_tsvector` 식 인덱스도 없다(실측 확인). 그 결과 FTS 쿼리가 두 가지 비용을 동시에 낸다.

첫째, `to_tsvector`를 미리 저장해두지 않아서 검색할 때마다 테이블의 모든 행에 대해 다시 계산한다. 둘째, `chunk.video_id`에 인덱스가 없어서 현재 실행 계획이 영상 하나마다 `chunk` 전체를 다시 스캔한다.

실측 결과다. 영상 11개·청크 58개짜리 프로젝트를 검색했을 때 실행 계획은 다음과 같았다.

```
Seq Scan on chunk c  (actual time=43.226..43.226 rows=0 loops=11)
  Filter: (to_tsvector('simple', COALESCE(enriched_text, text)) @@ plainto_tsquery(...))
  Rows Removed by Filter: 148
Execution Time: 475.997 ms
```

`loops=11`은 프로젝트의 READY 영상 수이고, 매 반복마다 테이블 전체 148행을 스캔했다. 즉 `to_tsvector` 계산이 148 × 11 = 1,628회 실행됐다. 여기서 스캔 대상은 이 프로젝트의 청크 58개가 아니라 **테이블 전체 148개**다. 다른 프로젝트와 다른 사용자의 데이터가 늘어도 내 검색이 느려진다는 뜻이다.

VSM에 기록된 FTS 55.4ms와 이 476ms의 차이도 이 구조로 설명된다. VSM 측정 프로젝트는 READY 영상이 2개였고, 이번 측정 프로젝트는 11개다. 반복 횟수가 영상 수를 그대로 따라간다.

정리하면 FTS 시간은 대략 `(프로젝트의 READY 영상 수) × (테이블 전체 청크 수) × (청크당 to_tsvector 비용)`으로 늘어난다. 두 값이 함께 커지므로 비례가 아니라 곱으로 증가한다.

측정값에서 청크 1개당 단일 스캔 비용은 약 0.31ms다(46ms ÷ 148행). 이 값이 유지된다고 가정한 추정치다.

| 조건(영상 10개 프로젝트 가정) | 현재 실행 계획 | 반복 없이 1회 스캔할 경우 |
|---|---:|---:|
| 청크 1,000개 | 약 3.1초 | 약 0.31초 |
| 청크 10,000개 | 약 31초 | 약 3.1초 |
| 청크 50,000개 | 약 155초 | 약 15.5초 |

이 표는 청크당 비용이 일정하다는 가정 위의 계산값이다. 실제 텍스트 길이 분포에 따라 달라진다. 다만 어느 쪽이든 부하테스트 목표 규모에서 검색이 성립하지 않는다는 결론은 바뀌지 않는다.

대응 방안:

| 선택지 | 기대 효과 | 비용·조건 |
|---|---|---|
| `to_tsvector` 식에 GIN 인덱스 추가 | 전체 스캔 자체를 없앤다. 근본 해결책이다 | 쓰기 시 인덱스 갱신 비용, 저장 공간, 마이그레이션. 식 인덱스는 쿼리의 식과 정확히 일치해야 동작한다 |
| tsvector 컬럼을 만들어 저장 + GIN 인덱스 | 위와 같고 계획이 더 단순해진다 | 스키마 변경과 기존 데이터 백필이 필요하다 |
| `chunk.video_id` 인덱스 추가 | 반복 스캔을 없애 현재 기준 약 10배 단축 | 작은 변경이지만 전체 스캔 자체는 남는다. GIN 인덱스가 있으면 불필요해질 수 있다 |
| 그대로 두고 코퍼스를 작게 유지 | 변경 없음 | 부하테스트 목표 규모에서 검색이 사실상 실패한다 |

`enable_nestloop=off`로 반복을 없앤 대안 계획을 실행해보니 `chunk` 스캔이 1회(46ms)로 줄었다. 반복 제거만으로도 약 10배가 줄지만, 전체 스캔은 그대로 남는다. 그래서 권장은 GIN 인덱스다.

측정에서 확인할 값: 인덱스 추가 전후의 FTS p95, 청크 1천·1만·5만에서의 `EXPLAIN (ANALYZE, BUFFERS)`, 인덱스 추가 후 색인 쓰기 시간 변화.

### 2. 검색 임베딩 VM 단일 슬롯 — 현재 데이터에서 먼저 막히는 지점

`MAX_CONCURRENCY=1`, `SEARCH_REQUEST_LIMIT=32`, `SEARCH_WAIT_TIMEOUT_SEC=5`가 실제 배포값으로 확인됐다. warm 추론은 평균 410.2ms다.

부하 시 예상 동작(계산값):

- 지속 처리 상한은 초당 약 2.4건(1초 ÷ 0.41초)이다.
- 이 상한을 넘는 유입이 계속되면 대기열이 자라고, 대기가 5초를 넘는 요청부터 거부된다. 5초 ÷ 0.41초 ≈ 12건이 임계 대기 깊이다.
- 실행 중 추론은 선점되지 않으므로, 거부는 한꺼번에가 아니라 유입 초과분만큼 지속적으로 발생한다.

대응 방안:

| 선택지 | 기대 효과 | 비용·조건 |
|---|---|---|
| startup warm-up `encode()` 1회 | 재기동 후 첫 요청의 1.6~2.4초 cold 비용 제거 | 구현 작음. readiness 판정과 실패 정책을 함께 정해야 함 |
| `MAX_CONCURRENCY`를 2~4로 증가 | 슬롯 병렬화로 처리량 증가 가능 | 스레드 제한이 없는 현재 상태에서는 추론 1건이 이미 vCPU 4개를 나눠 쓴다. 슬롯만 늘리면 같은 코어를 더 잘게 나누게 되므로 개별 추론이 느려질 수 있다. 슬롯당 코어 수를 고정한 뒤 총처리량과 개별 지연을 함께 재야 판단할 수 있다 |
| 임베딩 VM 증설 + 분배 | 처리량이 대수에 비례해 증가 | VM 비용 증가. $300 부하테스트 예산 제약과 충돌. 분배 계층이 없어 추가 구현 필요 |
| 질의 임베딩 캐시 | 동일 질의 반복 시 추론 자체를 생략 | 효과가 실제 질의 반복률에 달려 있다. 부하테스트에서 같은 질의를 반복하면 캐시가 결과를 왜곡하므로 시나리오 설계와 함께 결정 |

측정에서 확인할 값: 동시 검색 수준별 queue wait 분포, 거부 응답 발생 시점, VM CPU 사용률.

### 3. 요청 동시성 80 대 DB 커넥션 15

Cloud Run 동시성이 80으로 확인되면서 이 항목의 성격이 바뀌었다. 인스턴스 1대가 동시 요청 80건까지 받는데, 그 인스턴스의 DB 커넥션 풀은 15개(SQLAlchemy 기본 `pool_size` 5 + `max_overflow` 10)다. 코드가 풀 크기를 지정하지 않아 기본값이 그대로 적용된다(`search-service/src/infra/db/session.py:5`).

검색 요청은 DB 커넥션을 처음부터 끝까지 쥐고 있지 않다. `search_repository.py`를 보면 FTS, 벡터 검색, SOT gate, 스냅샷 저장, 대화 저장마다 커넥션을 열고 그 호출이 끝나면 반납한다. 그래서 동시 요청 80건이 곧바로 커넥션 80개를 요구하지는 않는다.

문제는 DB 호출 시점이 겹칠 때다. 인스턴스 1대가 요청 수십 건을 처리하는 동안 그 요청들의 DB 호출 순간이 겹치면 15개를 넘어서고, 초과분은 `pool_timeout` 기본값 30초까지 기다린다. 임베딩 거부(5초)나 FTS 지연보다 훨씬 늦게 실패하므로, 사용자 입장에서는 빠른 거부와 느린 timeout이 섞여 원인 구분이 어려워진다.

FTS가 476ms처럼 길어지면 커넥션 점유 시간도 그만큼 길어져 이 문제가 함께 악화된다. 두 병목은 독립적이지 않다.

대응 방안: 풀 크기와 `pool_timeout`을 코드에 명시한다. 인스턴스당 동시 요청 수를 감당할 값으로 올릴지, 아니면 Cloud Run 동시성을 80보다 낮춰 인스턴스당 부하를 줄일지는 선택이다. 후자는 인스턴스가 더 빨리 늘어나는 대신 1 vCPU·512MiB 인스턴스가 요청을 덜 떠안게 한다. 어느 쪽이든 아래 서버 상한과 함께 정해야 한다.

### 4. PostgreSQL max_connections 100

서버 상한은 100이고 3개는 예약되어 있어 앱이 쓸 수 있는 값은 97이다. 같은 DB를 쓰는 서비스의 이론적 최대 커넥션 수를 더하면 이 값을 넘는다.

| 서비스 | 최대 인스턴스 | 인스턴스당 풀 | 합계 |
|---|---:|---:|---:|
| search-service | 3 | 15 | 45 |
| core-api | 3 | 15 | 45 |
| pipeline-worker | 1 | 15 + PGMQ 전용 6 | 21 |
| feedback-ingestion-pipeline | 3 | 15 | 45 |
| feedback-loop 워커 6종 | 각 1 | 15 | 90 |
| **합계** | | | **246** |

이 246은 모든 서비스가 동시에 최대로 커넥션을 열었을 때의 상한이며, 실제로는 그렇게 되지 않는다. SQLAlchemy 풀은 필요할 때 커넥션을 여는 방식이고 feedback 계열 워커는 평소 min 0이라 떠 있지 않다. 다만 search-service와 core-api만 최대로 늘어도 90이 되어 97에 거의 닿는다.

대응 방안: 부하테스트 시나리오에 어떤 서비스가 함께 떠 있는지 먼저 정한다. 검색 단독 시나리오라면 여유가 있고, `둘 다` 시나리오에서는 pipeline-worker와 feedback 계열까지 더해 계산해야 한다. 서비스별 풀 크기를 명시해 상한을 통제하는 쪽이 `max_connections`를 올리는 것보다 먼저다. `max_connections`를 올리면 PostgreSQL의 커넥션당 메모리 사용이 함께 늘어나는데, 지금 `shared_buffers`가 128MiB로 작아서 메모리 배분을 같이 봐야 한다.

### 5. exact KNN 벡터 검색 — 현재는 문제 없음

같은 프로젝트를 대상으로 벡터 검색 실행 계획을 측정한 결과 7.1ms였다. FTS와 달리 반복 스캔이 없다.

```
Seq Scan on vector_index_entry vie  (actual time=0.012..0.070 rows=58 loops=1)
Index Scan using video_pkey on video v  (loops=58)
Execution Time: 7.110 ms
```

`vector_index_entry`에는 `(project_id, index_name)` 인덱스가 있고 쿼리가 `project_id`로 직접 거르기 때문에, 스캔 범위가 테이블 전체가 아니라 해당 프로젝트의 168개 중 58개로 좁혀진다. FTS가 테이블 전체를 영상 수만큼 반복 스캔하는 것과 대조된다.

ANN 인덱스가 없어 여전히 exact KNN이지만, 스캔 대상이 프로젝트 단위로 제한되므로 증가 폭은 프로젝트당 청크 수에만 비례한다. exact KNN 유지는 ADR-012에서 롤백 스냅샷 정책과 함께 내린 결정이므로 ANN 전환은 그 결정의 재검토를 전제로 한다. 현재 측정값에서는 전환할 근거가 없다.

### 6. LLM 구간이 요청을 길게 붙잡는 효과

warm 검색에서 LLM이 평균 4.666초로 전체의 90.1%다. LLM 자체는 외부 서비스라 #104의 개선 대상이 아니지만, 부하 관점에서는 별도 효과가 있다. 요청 하나가 인스턴스의 요청 슬롯을 약 5초씩 점유하므로, 동시성 80짜리 인스턴스 1대에 요청이 계속 쌓이는 동안 Cloud Run은 인스턴스를 늘리지 않을 수 있다.

대응 방안: #104 주 시나리오에서는 LLM을 mock으로 분리해 검색 인프라 한계를 따로 잰다(기존 계획 유지). 사용자 체감 개선이 목표가 되면 스트리밍 응답이나 서비스 리전과 가까운 모델 배치를 검토하되, 이는 부하테스트와 별개 트랙이다.

### 7. Cold start — min 0 구조의 첫 요청 비용

Frontend·Search Service 모두 min 0으로 확인됐고, 재기동 검증에서 첫 요청은 클라이언트 기준 15.2초였다. 이 중 약 9.5초는 Search Service timing 진입 전 구간으로, 아직 프록시·Cloud Run 기동·middleware 중 어디인지 분해되지 않았다.

대응 방안: 본 측정은 min 1로 실행해 cold를 분리한다(기존 계획 유지). 첫 요청의 9.5초는 Frontend와 Search Service의 요청 시작 시각을 함께 기록해 위치를 먼저 확정한 뒤 대응을 정한다. 상시 min 1 유지는 비용이 늘므로 부하테스트 기간에만 적용한다.

## 영상 처리 경로 병목

### 8. 배치 임베딩 단일 슬롯

배치 임베딩 VM도 `MAX_CONCURRENCY=1`로 확인됐고, 영상 요청 수용 상한은 4, 대기 상한은 20초다. 표본 1건에서 이 구간이 20.829초(전체의 17.2%)였고 청크 4개가 배치 하나로 처리됐다.

앞 배치의 추론 시간(20.8초)이 대기 상한(20초)과 거의 같다는 점이 중요하다. 영상 두 건이 임베딩 단계에서 겹치기만 해도 뒤 요청이 상한 근처까지 기다리거나 거부될 수 있다(추정, 실측 필요).

대응 방안: 영상 색인은 사용자 응답 경로가 아니므로 처리량 요구가 검색보다 낮다. Worker의 임베딩 호출이 재시도를 포함하는지와 거부 시 파이프라인 실패 처리를 먼저 확인하고, 색인 동시 처리량을 늘릴 필요가 확인될 때 배치 크기 조정이나 대기 상한 완화를 검토한다. 배치 크기를 키우면 추론 1회가 길어져 검색과는 다른 트레이드오프가 생기므로, 배치 크기 대 추론 시간 관계를 실측한 뒤 정한다.

### 9. Pipeline Worker max 1

Worker는 1대, 동시 처리 4건이 상한이다. 표본 기준 영상 1건 처리에 약 121초가 걸렸고 그중 62.2%가 외부 STT 대기다. 대기 중심이라 동시 4건까지는 1 vCPU로 겹쳐 처리할 수 있지만, 그 이상 몰리면 큐에 쌓인다.

대응 방안: PGMQ 가시성 타임아웃 기반이라 Worker 수평 확장의 구조적 여지는 있으나, max를 올리기 전에 단계별 멱등성(중복 처리 안전성)을 검증해야 한다. 색인 지연이 실제 문제로 확인되기 전까지는 현재 상한을 유지하는 쪽이 비용이 낮다. STT 75초는 외부 서비스 구간이므로 개선 대상이 아니라 변동 요인으로 기록한다.

## 공통 병목

### 10. PostgreSQL VM 한 대의 세 역할과 작은 shared_buffers

검색 조회, 색인 쓰기, PGMQ 폴링이 모두 같은 e2-standard-4 한 대에서 실행된다. 여기에 더해 `shared_buffers`가 128MiB로 확인됐다. VM 메모리가 16GiB인데 PostgreSQL이 캐시로 쓰는 영역은 그중 128MiB뿐이다. 패키지 기본값이 그대로 남아 있는 상태다.

FTS 전체 스캔과 결합하면 영향이 커진다. 측정에서 `Buffers: shared hit=3387 read=47`로 대부분 캐시에서 읽혔지만, 이건 데이터가 148행뿐이라 가능했던 일이다. 청크가 수만 건으로 늘면 128MiB에 다 들어가지 않아 디스크 읽기가 늘고, pd-balanced 디스크 성능이 새 제약으로 들어온다.

대응 방안: `shared_buffers`를 올리는 것은 설정 한 줄과 재시작으로 끝나고 VM을 늘리지 않아도 된다. 일반적으로 시스템 메모리의 25% 안팎을 쓰지만, 이 프로젝트의 실제 작업 집합 크기를 모르는 상태에서 값을 확정하지는 않는다. FTS 인덱스를 먼저 넣으면 스캔량 자체가 줄어 이 항목의 우선순위가 내려간다. 세 역할 분리는 VM 비용이 늘어 예산 제약과 충돌하므로, 부하테스트에서 CPU·IO·연결 수를 검색/색인 부하와 같은 시간축으로 기록해 경합을 먼저 확인한다.

## 아직 확정할 수 없는 것

- 임베딩 슬롯을 2 이상으로 올렸을 때 총처리량이 실제로 느는지. 스레드 제한이 없는 현재 구조에서는 계산만으로 확정할 수 없다.
- 초당 2.4건, 대기 깊이 12건은 warm 표본 4회 평균에서 나온 계산값이다. p95 기준으로는 더 낮을 수 있다.
- FTS 확장 추정치는 청크당 `to_tsvector` 비용이 일정하다는 가정 위에 있다. 텍스트 길이가 다르면 달라진다.
- 데이터가 늘었을 때 PostgreSQL 실행 계획이 지금의 반복 스캔을 유지할지 여부. 통계가 바뀌면 계획이 바뀔 수 있고, 그러면 반복 배수는 줄지만 전체 스캔은 남는다.
- 첫 요청 9.5초의 발생 위치.
- 부하 상황에서 실제로 동시에 열리는 커넥션 수. 이론적 상한 246과 실제 값의 차이는 측정해야 안다.

## 다음 행동

1. FTS용 GIN 인덱스를 추가하고 같은 쿼리로 실행 계획을 다시 잰다. 이 항목만으로 검색 성립 여부가 갈린다.
2. 비용이 작고 실측과 무관한 두 가지를 결정한다: startup warm-up `encode()` 적용 여부, DB 풀 크기와 `pool_timeout` 명시 설정.
3. #104 측정에서 병목별 판정 지표를 기록한다: 동시 검색 수준별 임베딩 queue wait·거부율, FTS·KNN p95(청크 1천·1만·5만), DB connection acquire 시간과 `pg_stat_activity` 동시 연결 수, PostgreSQL VM CPU·IO.
4. 부하테스트 시나리오별로 함께 떠 있는 서비스를 정하고, 그 조합의 커넥션 상한이 97 안에 들어오는지 확인한다.
5. 실측 결과가 나오면 이 문서의 순위와 계산값을 실측값으로 교체한다.

## 측정 근거

- Cloud Run 설정: `gcloud run services describe {frontend,search-service,core-api,pipeline-worker} --region=asia-northeast3`
- PostgreSQL 설정: `pg_settings`에서 `max_connections`, `shared_buffers`, `work_mem`, `effective_cache_size` 조회
- 인덱스 현황: `pg_indexes`에서 `chunk`, `vector_index_entry` 조회
- FTS 실행 계획: 프로젝트 `f1ec715a-e795-4b3d-bfed-ea526442b9e6`(READY 영상 11개, 청크 58개) 대상 `EXPLAIN (ANALYZE, BUFFERS)`
- 벡터 검색 실행 계획: 같은 프로젝트, `index_name = vector-bge-m3-base`
- 임베딩 VM 환경변수: `gcloud compute instances describe biblio-perf-ed2d3cb0-embedding-search`의 startup-script 메타데이터
- 코드 근거: `services/search-service/src/infra/db/search_repository.py:265`(FTS), `:322`(벡터), `services/search-service/src/infra/db/session.py:5`(풀 설정), `services/managed-embedding-endpoint/src/core/admission_control.py:184`(슬롯 판정)
