# Backend E2E Runbook

`core-api`, `pipeline-worker`, `managed-embedding-endpoint`, `search-service`를 실제 외부 인프라와 함께 로컬에서 끝까지 검증하는 절차다.

기준 테스트 영상:
- `C:\Users\ASUS\Downloads\도커가 바꾼 개발바닥.mp4`
- Git Bash 경로: `/c/Users/ASUS/Downloads/도커가 바꾼 개발바닥.mp4`
- WSL 경로: `/mnt/c/Users/ASUS/Downloads/도커가 바꾼 개발바닥.mp4`

기준 테스트 사용자:
- `requester_user_id = 11111111-1111-1111-1111-111111111111`

주의:
- `search-service`는 같은 사용자 아래 `READY`가 아닌 video가 하나라도 남아 있으면 `409 SEARCH_NOT_READY`를 반환한다.
- 따라서 매 실행 전에는 이전 테스트 video를 먼저 정리하는 것을 기본 절차로 둔다.

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
