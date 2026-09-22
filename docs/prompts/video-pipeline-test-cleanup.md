# Video Pipeline 테스트 정리 프롬프트

## [사용 시기]

Video Pipeline 부하 테스트나 S1~S4 시나리오가 완료·실패·중단된 뒤 테스트 데이터와 작업 상태를 정리할 때 이 문서를 태그한다.

예시:

```text
@docs/prompts/video-pipeline-test-cleanup.md
방금 중단한 S3 테스트를 다시 실행할 수 있게 정리해줘.
```

## [목적]

다음 실행이 이전 테스트의 DB 상태, Queue 메시지, GCS 객체에 영향을 받지 않게 한다. 동시에 완료된 테스트 결과나 다른 사용자의 데이터는 삭제하지 않는다.

## [에이전트 실행 원칙]

1. 사용자가 정리를 요청하면 진단만 길게 하지 말고 아래 절차를 끝까지 수행한다.
2. 삭제 전에 대상 `video_id` 목록을 확정하고 개수를 보고한다.
3. Signed URL에서 첫 UUID는 project 또는 owner ID일 수 있다. 경로 `videos/<project_id>/<video_id>/...`의 두 번째 UUID를 영상 ID로 사용한다.
4. UUID 일부, 생성 시각만으로 삭제하지 않는다. 실행 폴더·runner 출력·API·DB 중 두 곳 이상에서 소유 관계를 확인한다.
5. 다른 실행의 데이터와 구분되지 않으면 삭제하지 말고 부족한 정보를 보고한다.
6. 전체 테이블 삭제, Queue 전체 purge, Bucket 전체 삭제는 금지한다.
7. DB를 직접 수정해야 한다면 트랜잭션을 사용하고 삭제 건수를 출력한다.
8. `pg_stat_user_tables.n_live_tup`은 추정치다. Queue 최종 판정에는 실제 `pgmq.q_*` 행을 조회한다.
9. 삭제 후에는 요청 접수로 끝내지 않고 DB·Queue·API·GCS를 모두 검증한다.

## [정리 대상 목록]

실행마다 아래 항목을 확인한다.

| 구분 | 정리 대상 | 완료 기준 |
| --- | --- | --- |
| 실행 프로세스 | 로컬 runner, 원격 sampler | 해당 실행 프로세스가 없음 |
| API 데이터 | `video` | 대상 영상 조회가 404이거나 DB 0건 |
| Pipeline 상태 | `pipeline_run`, `pipeline_audio_part`, `pipeline_chunk_work` | 대상 영상 기준 모두 0건 |
| Embedding 상태 | `pipeline_embedding_batch` | 대상 연결 배치와 활성 고아 배치 0건 |
| Queue | preprocess, normalize, transcribe, enrich, embed, delete | 대상 메시지 0건, 재실행 전 전체 실제 행 0건 권장 |
| GCS | 원본, audio part, frame, 중간 산출물 | 대상 영상 prefix의 객체 0개 |
| 로컬 artifact | 실패·중단 실행 폴더 | 사용자 보존 정책에 따라 보존 또는 삭제 |

Pipeline Queue 이름:

```text
pgmq.q_preprocess_request
pgmq.q_normalize_video
pgmq.q_transcribe_part
pgmq.q_enrich_chunk
pgmq.q_embed_batch
pgmq.q_delete_request
pgmq.q_project_delete_request
```

## [표준 절차]

### 1. 실행 상태와 대상 범위 확정

- runner가 살아 있으면 먼저 정상 종료한다. 이미 사용자가 `Ctrl+C`로 종료했다면 중복 종료하지 않는다.
- 실행 폴더와 터미널 출력에서 다음 정보를 수집한다.
  - scenario: S1, S2, S3, S4
  - run directory
  - project ID
  - 생성된 `video_id` 전체
  - 계획 수, 생성 수, 완료 수
- 실행 폴더가 비어 있거나 incomplete라면 Cloud Logging의 생성·complete 이벤트와 DB 생성 시각을 함께 확인한다.
- 예상 영상 수와 확인된 ID 수가 다르면 누락 여부부터 확인한다.

### 2. 보존할 증거 결정

- 성공 실행: artifact를 삭제하지 않는다.
- 실패·중단 실행:
  - 사용자가 결과 보존을 요청했으면 기존 실행 폴더 안에 partial summary와 필요한 로그를 남긴다.
  - 사용자가 깨끗한 재실행만 요청했고 분석 가치가 없으면 불완전한 실행 폴더를 삭제한다.
  - 별도 경로로 복사하지 말라는 요청이 있으면 반드시 기존 실행 폴더 안에서만 복구·보존한다.
- 판단이 필요한 경우 DB/GCS 삭제는 계속 진행하되 로컬 artifact는 보존한다.

### 3. 삭제 전 상태 기록

대상 영상에 한정해 아래를 조회한다.

```sql
SELECT status, count(*)
FROM pipeline_run
WHERE video_id IN (<target_video_ids>)
GROUP BY status;

SELECT w.enrichment_status, w.embedding_status, count(*)
FROM pipeline_chunk_work w
JOIN pipeline_run r ON r.id = w.pipeline_run_id
WHERE r.video_id IN (<target_video_ids>)
GROUP BY w.enrichment_status, w.embedding_status;
```

Embedding batch는 FK 열만 보지 말고 JSONB 목록도 확인한다.

```sql
SELECT b.batch_id, b.status, b.created_at,
       count(w.chunk_work_id) AS existing_chunk_count
FROM pipeline_embedding_batch b
LEFT JOIN LATERAL jsonb_array_elements_text(b.chunk_work_ids) item(chunk_work_id)
  ON true
LEFT JOIN pipeline_chunk_work w
  ON w.chunk_work_id = item.chunk_work_id::uuid
WHERE b.status IN ('READY', 'DISPATCHED', 'RUNNING')
GROUP BY b.batch_id, b.status, b.created_at
ORDER BY b.created_at;
```

`existing_chunk_count = 0`인 활성 배치는 이전 정리에서 남은 고아 배치일 수 있다. 생성 시각과 대상 실행을 확인한 뒤 정리 대상에 포함한다.

### 4. 공식 삭제 요청

Core API의 공식 경로를 먼저 사용한다.

```http
POST /api/v1/videos:batch-delete
Content-Type: application/json

{"video_ids": ["<video-id-1>", "<video-id-2>"]}
```

- 응답의 `delete_requested=true`와 반환된 ID 목록을 확인한다.
- `DELETE_REQUEST`가 처리될 시간을 주고 API·DB·Queue를 다시 확인한다.
- 공식 삭제가 정상 완료되면 직접 DB 삭제를 하지 않는다.

### 5. 삭제가 `DeletionDeferred`로 멈춘 경우

`Pipeline work is still running`으로 반복 지연되면 대상 영상의 실행 상태만 정리한다.

1. 대상 chunk에 연결된 `embedding_batch_id`를 임시 테이블에 먼저 저장한다.
2. 대상 영상의 `pipeline_chunk_work`를 삭제한다.
3. 저장한 batch와 실제 chunk 참조가 0개인 활성 고아 batch만 삭제한다.
4. `DELETE_REQUEST` 중 대상 `video_ids`가 일치하는 메시지만 즉시 재노출한다.
5. Worker가 공식 삭제 로직을 다시 수행하게 한다.

안전한 기본 형태:

```sql
BEGIN;

CREATE TEMP TABLE cleanup_target_video(video_id uuid PRIMARY KEY) ON COMMIT DROP;
-- 확정한 video_id만 INSERT한다.

CREATE TEMP TABLE cleanup_target_batch(batch_id uuid PRIMARY KEY) ON COMMIT DROP;

INSERT INTO cleanup_target_batch(batch_id)
SELECT DISTINCT w.embedding_batch_id
FROM pipeline_chunk_work w
JOIN pipeline_run r ON r.id = w.pipeline_run_id
JOIN cleanup_target_video t ON t.video_id = r.video_id
WHERE w.embedding_batch_id IS NOT NULL
UNION
SELECT DISTINCT b.batch_id
FROM pipeline_embedding_batch b
JOIN LATERAL jsonb_array_elements_text(b.chunk_work_ids) item(chunk_work_id)
  ON true
JOIN pipeline_chunk_work w
  ON w.chunk_work_id = item.chunk_work_id::uuid
JOIN pipeline_run r ON r.id = w.pipeline_run_id
JOIN cleanup_target_video t ON t.video_id = r.video_id;

DELETE FROM pipeline_chunk_work w
USING pipeline_run r, cleanup_target_video t
WHERE w.pipeline_run_id = r.id
  AND r.video_id = t.video_id;

DELETE FROM pipeline_embedding_batch b
USING cleanup_target_batch t
WHERE b.batch_id = t.batch_id;

COMMIT;
```

재노출 전에는 메시지 본문에서 대상 ID가 모두 일치하는지 확인한다.

```sql
SELECT msg_id, read_ct, vt, message
FROM pgmq.q_delete_request
ORDER BY enqueued_at;
```

확인된 `msg_id` 한 건의 `vt`만 현재 시각으로 변경한다. `q_delete_request` 전체의 `vt`를 변경하지 않는다.

추가 고아 batch 삭제 조건:

```sql
b.status IN ('READY', 'DISPATCHED', 'RUNNING')
AND NOT EXISTS (
  SELECT 1
  FROM jsonb_array_elements_text(b.chunk_work_ids) item(chunk_work_id)
  JOIN pipeline_chunk_work w
    ON w.chunk_work_id = item.chunk_work_id::uuid
)
```

고아 조건만 맞는다는 이유로 즉시 삭제하지 않는다. 생성 시각과 직전 테스트 정리 이력을 함께 확인한다.

### 6. GCS와 로컬 artifact 정리

- 공식 삭제 완료 뒤 다음 prefix를 확인한다.

```text
gs://<video-bucket>/videos/<project_id>/<video_id>/**
```

- 객체가 남았으면 정확한 영상 prefix만 삭제한다.
- Bucket, `videos/<project_id>/`, wildcard project 전체 삭제는 금지한다.
- 로컬 artifact는 2단계에서 정한 보존 정책대로 처리한다.

### 7. 최종 검증

다음 조건을 모두 확인해야 정리 완료로 판정한다.

```text
[ ] 대상 video = 0
[ ] 대상 pipeline_run = 0
[ ] 대상 pipeline_audio_part = 0
[ ] 대상 pipeline_chunk_work = 0
[ ] 대상 연결 embedding batch = 0
[ ] READY/DISPATCHED/RUNNING 고아 embedding batch = 0
[ ] 대상 video_id를 포함한 Pipeline PGMQ 실제 메시지 = 0
[ ] 전용 perf 환경이면 Pipeline PGMQ 전체 실제 메시지 = 0
[ ] 대상 GCS 객체 = 0
[ ] runner와 sampler 프로세스 = 0
[ ] 로컬 artifact 보존/삭제 정책 반영 완료
```

Queue는 각 실제 테이블을 확인한다. 통계가 1건인데 실제 조회가 0건이면 실제 조회 결과를 기준으로 한다.

## [완료 보고 형식]

추측 없이 실제 수치로 짧게 보고한다.

```text
정리 완료
- 대상 영상: 8개 삭제, 잔여 0
- pipeline chunk work: 64건 삭제, 잔여 0
- embedding batch: 대상 64건 + 고아 11건 삭제, 활성 고아 0
- Pipeline Queue: 실제 메시지 0
- GCS: 대상 8개 prefix 객체 0
- 로컬 artifact: 삭제 또는 보존 경로
- 다음 테스트 실행 가능 여부: 가능/불가와 이유
```

## [금지 사항]

- 확인되지 않은 UUID 삭제
- 다른 실행의 성공 artifact 삭제
- `TRUNCATE`, Queue 전체 purge, Bucket 전체 삭제
- `pipeline_chunk_work`만 삭제하고 `pipeline_embedding_batch`를 남기는 정리
- 삭제 요청만 발행하고 완료 검증 없이 종료
- `n_live_tup` 추정치만 보고 Queue가 비었다고 판정
- 증거 보존 요청이 있는데 새 경로로 임의 이동 또는 복사
