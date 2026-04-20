# Biblio Managed Embedding Endpoint

`BAAI/bge-m3`를 로드해 텍스트를 dense embedding 벡터로 반환하는 FastAPI 서비스입니다.

현재 구현 기준으로는 `/health`, `/embed` 두 개의 HTTP 엔드포인트를 제공하며, 모델이 로드되지 않으면 `503 Service Unavailable`을 반환합니다.

## 개요

- 입력: 텍스트 목록
- 출력: 텍스트별 dense embedding 벡터 목록
- 모델: `BAAI/bge-m3`
- 기본 포트: `8000`

## 사전 준비

- Python 3.12+
- [Poetry](https://python-poetry.org/)
- 인터넷 연결 또는 로컬에 내려받은 `bge-m3` 모델 디렉터리

## 로컬 실행

### 1. 의존성 설치

```bash
poetry install
cp .env.example .env
```

### 2. 환경변수 설정

현재 구현은 `MODEL_ARTIFACT_PATH`에 아래 둘 중 하나가 들어오기를 기대합니다.

- **실제로 존재하는 로컬 모델 디렉터리 경로**
- **Hugging Face 모델 ID** 예: `BAAI/bge-m3`

로컬 경로가 없고 모델 ID가 들어오면, 첫 실행 시 모델을 내려받아 `MODEL_CACHE_DIR`에 캐시합니다.

예시:

```bash
export MODEL_ARTIFACT_PATH=BAAI/bge-m3
export MODEL_CACHE_DIR=/home/artyom9/.cache/huggingface
export PORT=8000
```

또는 `.env`에 직접 넣어도 됩니다.

이미 로컬 모델 폴더가 있다면 아래처럼 절대 경로를 그대로 써도 됩니다.

```bash
export MODEL_ARTIFACT_PATH=/home/artyom9/models/bge-m3
```

### 3. 서버 실행

```bash
poetry run uvicorn src.main:create_app --factory --host 0.0.0.0 --port 8000
```

## 환경변수

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `MODEL_ARTIFACT_PATH` | 예 | 없음 | 로컬 `bge-m3` 모델 디렉터리 경로 또는 Hugging Face 모델 ID |
| `PORT` | 아니오 | `8000` | HTTP 포트 |
| `MODEL_CACHE_DIR` | 아니오 | `""` | 모델 캐시 디렉터리 |
| `MAX_TEXTS_PER_REQUEST` | 아니오 | `32` | 한 번의 `/embed` 요청에서 허용하는 최대 텍스트 수 |
| `MAX_TEXT_LENGTH_CHARS` | 아니오 | `4096` | 텍스트 1개당 최대 길이 |
| `MAX_PAYLOAD_BYTES` | 아니오 | `262144` | 요청 body 최대 크기 |
| `MAX_CONCURRENCY` | 아니오 | `1` | 동시에 처리할 최대 추론 요청 수 |

## API

### `GET /health`

모델이 준비된 경우:

```json
{"status":"ok","model_version":"/home/artyom9/models/bge-m3"}
```

모델 로드 실패 또는 미준비 상태에서는 `503`을 반환합니다.

### `POST /embed`

요청 예시:

```json
{"texts":["first sentence","second sentence"]}
```

응답 예시:

```json
{"embeddings":[[0.012,-0.034,...],[0.056,0.078,...]]}
```

`bge-m3` 기준으로 embedding 차원은 `1024`입니다.

## 스모크 테스트

서버가 실행 중일 때 아래 순서로 확인하면 됩니다.

### health 확인

```bash
curl http://localhost:8000/health
```

### 단일 텍스트 embedding 확인

```bash
curl -X POST http://localhost:8000/embed \
  -H 'Content-Type: application/json' \
  -d '{"texts":["smoke test"]}'
```

### 배치 요청 확인

```bash
curl -s -X POST http://localhost:8000/embed \
  -H 'Content-Type: application/json' \
  -d '{"texts":["first text","second text","third text"]}' \
  | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data['embeddings']), len(data['embeddings'][0]))"
```

정상이면 `3 1024`가 출력됩니다.

### Trace 헤더 확인

`X-Trace-Id`는 UUID 형식일 때 그대로 echo 됩니다.

```bash
curl -i -X POST http://localhost:8000/embed \
  -H 'Content-Type: application/json' \
  -H 'X-Trace-Id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee' \
  -d '{"texts":["smoke test"]}'
```

### Guardrail 확인

기본 설정에서는 텍스트 33개를 보내면 `400 INVALID_ARGUMENT`가 반환됩니다.

```bash
curl -X POST http://localhost:8000/embed \
  -H 'Content-Type: application/json' \
  -d '{"texts":["a","b","c","d","e","f","g","h","i","j","k","l","m","n","o","p","q","r","s","t","u","v","w","x","y","z","1","2","3","4","5","6","7"]}'
```

## 테스트

테스트는 모두 stub 기반으로 동작하므로 실제 모델 다운로드 없이 실행할 수 있습니다.

```bash
poetry run pytest
poetry run poe cov
```

## Docker

### 이미지 빌드

```bash
docker build -t biblio-embedding .
```

### 컨테이너 실행

컨테이너에서도 `MODEL_ARTIFACT_PATH`는 로컬 모델 디렉터리 또는 모델 ID를 가리킬 수 있습니다.

```bash
docker run --rm -p 8000:8000 \
  -v bge-model-cache:/models/cache \
  -e MODEL_ARTIFACT_PATH=BAAI/bge-m3 \
  -e MODEL_CACHE_DIR=/home/appuser/.cache/huggingface \
  -e PORT=8000 \
  biblio-embedding
```

이미 로컬 모델 폴더가 있으면 기존처럼 mount해서 쓸 수 있습니다.

```bash
docker run --rm -p 8000:8000 \
  -v /path/to/bge-m3:/models/bge-m3:ro \
  -e MODEL_ARTIFACT_PATH=/models/bge-m3 \
  -e PORT=8000 \
  biblio-embedding
```

## 주의사항

- WSL에서 `/mnt/c/...` 경로의 대용량 모델을 직접 읽으면 기동이 느릴 수 있습니다.
- 가능하면 WSL 내부 경로 예: `~/models/bge-m3`를 사용하는 편이 낫습니다.
- 현재 환경에서 CUDA가 비활성화되어 있으면 모델 로드와 추론이 CPU 기준으로 동작할 수 있습니다.
