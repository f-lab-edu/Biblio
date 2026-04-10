# Backend E2E Runbook

`core-api`, `pipeline-worker`, `managed-embedding-endpoint`, `search-service`를 실제 외부 인프라와 함께 로컬에서 끝까지 검증하는 절차다.

빠른 경로는 두 가지다.

- Docker Compose 기반 자동 smoke
  - 이미 패키징한 컨테이너를 그대로 쓰고, 스크립트가 JWT 발급, signed URL 업로드, complete 호출, READY polling, search까지 자동으로 수행한다.
- Docker Compose 기반 transcript-fixture smoke
  - STT 결과를 한 번 JSON fixture로 추출해두고, 이후 실행에서는 transcript를 DB에 seed한 뒤 `chunking -> vision -> enriched_text -> embedding -> search`만 검증한다.
- 기존 수동/로컬 프로세스 절차
  - DB와 각 서비스를 직접 띄우고, curl/python 명령으로 단계별 확인한다.

기준 테스트 영상:
- `C:\Users\ASUS\Downloads\도커가 바꾼 개발바닥.mp4`
- Git Bash 경로: `/c/Users/ASUS/Downloads/도커가 바꾼 개발바닥.mp4`
- WSL 경로: `/mnt/c/Users/ASUS/Downloads/도커가 바꾼 개발바닥.mp4`

기준 테스트 사용자:
- `requester_user_id = 11111111-1111-1111-1111-111111111111`

주의:
- `search-service`는 같은 사용자 아래 `READY`가 아닌 video가 하나라도 남아 있으면 `409 SEARCH_NOT_READY`를 반환한다.
- 따라서 매 실행 전에는 이전 테스트 video를 먼저 정리하는 것을 기본 절차로 둔다.

## A. Docker Compose 기반 자동 E2E

이 경로는 `docker compose up`으로 이미 떠 있는 스택에 붙어서 테스트한다.

### A.1 사전 준비

필수 파일:

- 루트 `.env`
  - `ADC_CREDENTIALS_PATH=...`
  - `POSTGRES_USER=postgres`
  - `POSTGRES_PASSWORD=postgres`
  - `POSTGRES_DB=app`
- 서비스별 `.env`
  - `services/core-api/.env`
  - `services/pipeline-worker/.env`
  - `services/managed-embedding-endpoint/.env`
  - `services/search-service/.env`

ADC 준비:

```bash
gcloud auth application-default login
```

현재 compose 구조는 앱 컨테이너가 `non-root`로 실행되므로, `pipeline-worker` 로그에 `/creds/adc.json Permission denied`가 보이면 아래처럼 로컬 ADC 파일 읽기 권한을 열어준다.

```bash
chmod 644 ~/.config/gcloud/application_default_credentials.json
```

### A.2 Docker 이미지 빌드

```bash
cd /mnt/c/Users/ASUS/project/Biblio
docker compose build
```

### A.3 Compose 기동

```bash
docker compose up
```

첫 실행에서는 `managed-embedding-endpoint`가 `BAAI/bge-m3`를 자동 다운로드하므로 오래 걸릴 수 있다. 정상 로그 예시는 아래와 같다.

- `Fetching 30 files`
- `model.load.success`
- `GET /health 200 OK`

기동 확인:

```bash
docker compose ps
```

기대 상태:

- `db` healthy
- `core-api` healthy
- `managed-embedding-endpoint` healthy
- `search-service` healthy
- `pipeline-worker` Up

### A.4 Docker attach-only smoke 실행

추천 방식은 시나리오 JSON을 고쳐서 실행하는 것입니다.

기본 예제:

- [scripts/e2e_scenarios/docker-basic.json](/mnt/c/Users/ASUS/project/Biblio/scripts/e2e_scenarios/docker-basic.json)

예시 구조:

```json
{
  "video_paths": [
    "/mnt/c/Users/ASUS/Downloads/도커가 바꾼 개발바닥.mp4"
  ],
  "queries": [
    "도커가 왜 필요한지 설명해줘",
    "이 영상 핵심 내용을 요약해줘"
  ],
  "user_id": "11111111-1111-1111-1111-111111111111",
  "ready_timeout_sec": 1800
}
```

실행:

```bash
services/core-api/.venv/bin/python scripts/e2e_backend_smoke_docker.py \
  --scenario scripts/e2e_scenarios/docker-basic.json
```

필요하면 CLI 인자로 일부만 덮어쓸 수도 있다.

```bash
services/core-api/.venv/bin/python scripts/e2e_backend_smoke_docker.py \
  --scenario scripts/e2e_scenarios/docker-basic.json \
  --video-path "/mnt/c/Users/ASUS/Downloads/다른영상.mp4" \
  --query "이 영상 핵심을 알려줘"
```

스크립트가 자동으로 하는 일:

1. Compose 서비스 health preflight
2. 테스트 사용자 stale video 정리
3. JWT 발급
4. video create
5. signed URL로 로컬 파일 업로드
6. `complete` 호출
7. `READY` polling
8. search 요청

성공 기준:

- 스크립트가 `Docker E2E smoke succeeded.`를 출력
- 각 video가 `READY`
- search 응답에 `answer`와 `chunks`가 존재

로그 아티팩트는 임시 디렉터리 `biblio-e2e-docker-logs-*` 아래에 저장된다.

### A.5 STT transcript fixture 추출

이 경로는 full pipeline smoke와 별개다. 가장 비싼 STT 호출을 한 번만 수행하고, 결과를 repo 안 JSON fixture로 저장해 이후 재사용한다.

기본 저장 위치:

- [scripts/e2e_fixtures/transcripts](/mnt/c/Users/ASUS/project/Biblio/scripts/e2e_fixtures/transcripts)

추출 스크립트:

- [scripts/extract_transcript_fixture.py](/mnt/c/Users/ASUS/project/Biblio/scripts/extract_transcript_fixture.py)

예시:

```bash
services/pipeline-worker/.venv/bin/python scripts/extract_transcript_fixture.py \
  --video-path "/mnt/c/Users/ASUS/Downloads/도커가 바꾼 개발바닥.mp4" \
  --output "/mnt/c/Users/ASUS/project/Biblio/scripts/e2e_fixtures/transcripts/docker-basic.json"
```

출력 JSON에는 아래가 들어간다.

- `source_video_path`
- `stt_model_version`
- `segments[]`
  - `segment_index`
  - `text`
  - `start_ms`
  - `end_ms`

주의:

- 이 스크립트는 실제 GCS/STT를 한 번 호출한다.
- 임시 audio object는 GCS에 올렸다가 STT 완료 후 삭제한다.
- 이후 동일 영상 회귀 테스트는 이 JSON fixture만 재사용하면 된다.

### A.6 Docker transcript-fixture smoke

이 경로는 기존 full-pipeline smoke와 겹치지 않는 별도 라인이다.

- full-pipeline smoke:
  - `upload -> STT -> vision -> enriched_text -> embedding -> search`
- transcript-fixture smoke:
  - `upload -> transcript seed -> vision -> enriched_text -> embedding -> search`

즉 GCS 업로드와 worker 파이프라인은 계속 검증하되, STT 비용만 제거하는 경로다.

기본 시나리오:

- [scripts/e2e_scenarios/transcript-fixture-basic.json](/mnt/c/Users/ASUS/project/Biblio/scripts/e2e_scenarios/transcript-fixture-basic.json)

예시 구조:

```json
{
  "video_path": "/mnt/c/Users/ASUS/Downloads/도커가 바꾼 개발바닥.mp4",
  "transcript_fixture_path": "/mnt/c/Users/ASUS/project/Biblio/scripts/e2e_fixtures/transcripts/docker-basic.json",
  "queries": [
    "도커가 왜 필요한지 설명해줘"
  ],
  "user_id": "11111111-1111-1111-1111-111111111111",
  "ready_timeout_sec": 1800
}
```

실행:

```bash
services/core-api/.venv/bin/python scripts/e2e_backend_smoke_transcript_fixture.py \
  --scenario scripts/e2e_scenarios/transcript-fixture-basic.json
```

스크립트가 자동으로 하는 일:

1. Compose 서비스 health preflight
2. 테스트 사용자 stale video 정리
3. JWT 발급
4. video create
5. signed URL로 로컬 파일 업로드
6. transcript fixture를 `transcript_segment`에 seed
7. `complete` 호출로 queue 발행
8. `READY` polling
9. search 요청

성공 기준:

- 스크립트가 `Transcript fixture Docker E2E smoke succeeded.`를 출력
- 각 video가 `READY`
- search 응답에 `answer`와 `chunks`가 존재

이 경로는 STT 비용을 아끼기 위한 다운스트림 회귀 테스트에 적합하고, GCS 업로드/STT까지 포함한 전체 end-to-end 보증은 여전히 A.4 full-pipeline smoke가 담당한다.

## B. 기존 수동/로컬 프로세스 절차

## 1. E2E DB 시작

이미지 빌드:

```bash
docker build -t biblio-e2e-db infra/e2e-db
```

기존 컨테이너 정리:

```bash
docker stop biblio-e2e-db 2>/dev/null || true
docker rm biblio-e2e-db 2>/dev/null || true
```

새 컨테이너 실행:

```bash
docker run -d --name biblio-e2e-db \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=app \
  -p 55433:5432 \
  biblio-e2e-db
```

기동 확인:

```bash
docker exec biblio-e2e-db pg_isready -U postgres -d app
```

## 2. Core API migration 적용

```bash
cd /mnt/c/Users/ASUS/project/Biblio/services/core-api
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55433/app .venv/bin/alembic upgrade head
```

## 3. 매 실행 전 stale test video 정리

먼저 현재 테스트 사용자 video 상태 확인:

```bash
docker exec -it biblio-e2e-db psql -U postgres -d app -c "
SELECT id, status, failed_stage, created_at, updated_at
FROM video
WHERE user_id = '11111111-1111-1111-1111-111111111111'
ORDER BY created_at;
"
```

불필요한 예전 테스트 video가 남아 있으면 해당 `VIDEO_ID`를 골라 아래 순서로 삭제한다.

```sql
DELETE FROM vector_index_entry WHERE video_id = 'VIDEO_ID';
DELETE FROM chunk WHERE video_id = 'VIDEO_ID';
DELETE FROM transcript_segment WHERE video_id = 'VIDEO_ID';
DELETE FROM asset WHERE video_id = 'VIDEO_ID';
DELETE FROM video WHERE id = 'VIDEO_ID';
```

다시 확인해서 이번에 사용할 테스트 데이터만 남기거나, 가능하면 아예 없는 상태에서 시작한다.

## 4. 서비스 `.env` 체크 포인트

### `services/core-api/.env`

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55433/app
BROKER_TYPE=pgmq
JWT_SECRET_KEY=...
GCP_PROJECT_ID=...
GCS_VIDEO_BUCKET_NAME=...
```

### `services/pipeline-worker/.env`

```env
BROKER_TYPE=pgmq
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55433/app
EMBEDDING_API_URL=http://localhost:8000
EMBEDDING_MODEL_VERSION=/home/artyom9/models/bge-m3
MAX_RETRIES=5
EMBEDDING_TIMEOUT_SEC=20
```

### `services/managed-embedding-endpoint/.env`

```env
MODEL_ARTIFACT_PATH=/home/artyom9/models/bge-m3
MODEL_CACHE_DIR=/home/artyom9/.cache/huggingface
PORT=8000
MAX_CONCURRENCY=4
```

### `services/search-service/.env`

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55433/app
JWT_SECRET_KEY=core-api와 동일한 값
EMBEDDING_API_URL=http://localhost:8000
LLM_PROVIDER=gemini
GEMINI_MODEL_NAME=gemini-2.5-flash
LLM_TIMEOUT_SEC=15
```

중요:
- `search-service`의 `EMBEDDING_API_URL`은 `/embed` 없는 base URL이어야 한다.
- `pipeline-worker`의 `EMBEDDING_MODEL_VERSION`은 실제 endpoint model version과 맞춰야 한다.

## 5. 서비스 기동 순서

### 5.1 Managed Embedding Endpoint

```bash
cd /mnt/c/Users/ASUS/project/Biblio/services/managed-embedding-endpoint
poetry run uvicorn src.main:create_app --factory --host 0.0.0.0 --port 8000
```

health 확인:

```bash
curl -i http://localhost:8000/health
```

### 5.2 Core API

```bash
cd /mnt/c/Users/ASUS/project/Biblio/services/core-api
poetry run uvicorn src.main:create_app --factory --host 0.0.0.0 --port 8080
```

health 확인:

```bash
curl -i http://localhost:8080/health
```

### 5.3 Pipeline Worker

```bash
cd /mnt/c/Users/ASUS/project/Biblio/services/pipeline-worker
poetry run python -m src.main
```

시작 로그 확인:
- `pipeline worker starting`
- `broker=pgmq ...`
- `pipeline worker ready ...`

### 5.4 Search Service

```bash
cd /mnt/c/Users/ASUS/project/Biblio/services/search-service
poetry run uvicorn src.main:create_app --factory --host 0.0.0.0 --port 8082
```

health 확인:

```bash
curl -i http://localhost:8082/health
```

## 6. JWT 발급

```bash
TOKEN=$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone
import jwt

secret = "core-api와 동일한 JWT_SECRET_KEY"
payload = {
    "requester_user_id": "11111111-1111-1111-1111-111111111111",
    "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=30),
}
print(jwt.encode(payload, secret, algorithm="HS256"))
PY
)
```

## 7. 새 video 생성

```bash
RESP=$(curl -s -X POST http://localhost:8080/api/v1/videos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "E2E Test Video",
    "category": "GENERAL",
    "input_type": "LOCAL_FILE",
    "extension": ".mp4"
  }')
```

ID와 signed URL 추출:

```bash
VIDEO_ID=$(python3 -c 'import sys,json; print(json.load(sys.stdin)["video_id"])' <<< "$RESP")
SIGNED_URL=$(python3 -c 'import sys,json; print(json.load(sys.stdin)["signed_url"])' <<< "$RESP")

echo "$VIDEO_ID"
echo "$SIGNED_URL"
```

## 8. 테스트 영상 업로드

Git Bash 기준:

```bash
curl -i -X PUT \
  -H "Content-Type: application/octet-stream" \
  -H "x-goog-content-length-range: 0,2147483648" \
  --upload-file "/c/Users/ASUS/Downloads/도커가 바꾼 개발바닥.mp4" \
  "$SIGNED_URL"
```

WSL 기준:

```bash
curl -i -X PUT \
  -H "Content-Type: application/octet-stream" \
  -H "x-goog-content-length-range: 0,2147483648" \
  --upload-file "/mnt/c/Users/ASUS/Downloads/도커가 바꾼 개발바닥.mp4" \
  "$SIGNED_URL"
```

## 9. 업로드 완료 통지

```bash
curl -i -X POST http://localhost:8080/api/v1/videos/$VIDEO_ID/complete \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## 10. 처리 상태 polling

```bash
while true; do
  curl -s http://localhost:8080/api/v1/videos/$VIDEO_ID \
    -H "Authorization: Bearer $TOKEN"
  echo
  sleep 5
done
```

기대 흐름:
- `UPLOADED`
- `PROCESSING`
- `READY`

실패 시:
- `FAILED`
- `failed_stage`

## 11. 실패 시 기본 점검

### 11.1 `failed_stage = EMBEDDING`

우선 값 확인:
- `services/pipeline-worker/.env`
  - `EMBEDDING_API_URL=http://localhost:8000`
  - `MAX_RETRIES=5`
  - `EMBEDDING_TIMEOUT_SEC=20`
- `services/managed-embedding-endpoint/.env`
  - `MAX_CONCURRENCY=4`

그리고 두 프로세스를 재시작한 뒤:

```bash
curl -i -X POST http://localhost:8080/api/v1/videos/$VIDEO_ID/retry \
  -H "Authorization: Bearer $TOKEN"
```

### 11.2 search에서 `SEARCH_NOT_READY`

같은 user 아래 `READY`가 아닌 video가 남아 있으면 발생한다.

확인:

```sql
SELECT id, status, failed_stage, created_at, updated_at
FROM video
WHERE user_id = '11111111-1111-1111-1111-111111111111'
ORDER BY created_at;
```

남아 있는 `PENDING/FAILED/DELETING/UPLOADED` test video를 먼저 정리한다.

### 11.3 search에서 Gemini timeout

`services/search-service/.env`의 `LLM_TIMEOUT_SEC`를 늘리고 search-service를 재시작한다.

## 12. 검색 요청

```bash
curl -s -X POST http://localhost:8082/api/v1/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"이 영상에서 어떤 내용이 나오나?"}'
```

성공 기준:
- `answer` 존재
- `chunks` 존재

## 13. 성공 판정

이 조건이 다 맞으면 backend E2E 성공으로 본다.

1. `video.status = READY`
2. `chunk`와 `vector_index_entry` row 존재
3. `search-service`가 `answer`와 `chunks`를 반환
4. `chunks[].used`와 citation이 answer와 논리적으로 맞는다
