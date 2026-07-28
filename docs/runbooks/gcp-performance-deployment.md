# GCP 성능 테스트 환경 배포 런북

이 문서는 `infra/terraform/envs/gcp-perf` 환경의 최초 배포, 재배포, 검증, 비용 절감용 중지, 재시작, 완전 삭제 절차를 설명한다.

현재 환경은 운영용 고가용성 구성이 아니다. 성능 테스트와 배포 흐름 검증을 위한 환경이다.

## 1. 현재 배포 구조

Terraform은 다음 자원을 배포한다.

| 구분 | 자원 |
|---|---|
| Cloud Run 서비스 | `core-api`, `search-service`, `feedback-ingestion-pipeline` |
| Cloud Run worker | `pipeline-worker`, feedback-loop worker 6종 |
| Cloud Run Job | DB migration, ModelRelease seed |
| GCE VM | PostgreSQL, 임베딩 엔드포인트 |
| 저장소 | 영상, 피드백 로그, ML 아티팩트 GCS bucket |
| 기타 | Artifact Registry, Secret Manager, 서비스 계정과 IAM |

feedback-loop worker 6종은 같은 image를 사용하고 `APP_ROLE`로 역할을 구분한다.

- `scheduler`
- `dataset-worker`
- `train-release-worker`
- `rollback-worker`
- `legacy-reindex-worker`
- `reembedding-worker`

네트워크는 다음과 같이 구성한다.

- Biblio 전용 VPC
- Cloud Run, PostgreSQL VM, 임베딩 VM 전용 subnet
- Cloud Run의 Direct VPC egress
- PostgreSQL VM private IP의 `5432` 포트
- 임베딩 VM private IP의 `8000` 포트
- PostgreSQL VM과 임베딩 VM outbound용 Cloud NAT
- 외부 IP 없는 VM에 접속하기 위한 IAP SSH

현재 기본 실행 상태는 비용 절감을 우선한다.

- `pipeline-worker`: 최소 인스턴스 0
- feedback-loop worker 6종: 최소 인스턴스 0
- `feedback-ingestion-pipeline`: 최소 인스턴스 0
- PostgreSQL과 임베딩 엔드포인트는 GCE VM이므로 실행 중이면 계속 과금됨

## 2. 사전 조건

### 2.1 필요한 도구

- Terraform 1.6 이상
- Google Cloud CLI
- Docker
- Python 3

설치와 인증 상태를 확인한다.

```bash
terraform version
gcloud version
docker version
python3 --version

gcloud auth login
gcloud auth application-default login
```

배포할 프로젝트를 기본값으로 설정한다.

```bash
export GCP_PROJECT_ID=<GCP_PROJECT_ID>
export GCP_REGION=asia-northeast3
export GCP_ZONE=asia-northeast3-a
export NAME_PREFIX=<TERRAFORM_NAME_PREFIX>

gcloud config set project "$GCP_PROJECT_ID"
gcloud config set run/region "$GCP_REGION"
gcloud auth application-default set-quota-project "$GCP_PROJECT_ID"
```

`NAME_PREFIX`는 `terraform.tfvars`의 `name_prefix`와 같아야 한다.

### 2.2 필요한 GCP API

새 GCP 프로젝트에 처음 배포할 때 다음 API를 활성화한다.

```bash
gcloud services enable \
  compute.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com \
  speech.googleapis.com \
  aiplatform.googleapis.com \
  --project="$GCP_PROJECT_ID"
```

주요 용도는 다음과 같다.

| API | 용도 |
|---|---|
| Compute Engine | VM, VPC, subnet, Cloud NAT, 방화벽 |
| Cloud Run | API, worker, Job |
| Artifact Registry | Docker image 저장 |
| Secret Manager | DB 비밀번호, JWT secret, `DATABASE_URL` |
| Cloud Storage | 영상, 피드백 로그, ML 아티팩트, Terraform state |
| IAM Credentials | `core-api`의 GCS 서명 URL 생성 |
| Speech-to-Text | pipeline-worker의 STT |
| Vertex AI | Vision 처리와 검색 답변 생성 |

### 2.3 Terraform state bucket

Terraform은 state를 저장할 bucket을 스스로 만들 수 없다. 최초 한 번 수동 생성한다.

```bash
export TF_STATE_BUCKET=<TERRAFORM_STATE_BUCKET>

gcloud storage buckets create "gs://$TF_STATE_BUCKET" \
  --project="$GCP_PROJECT_ID" \
  --location="$GCP_REGION" \
  --uniform-bucket-level-access
```

state에는 비밀번호와 secret 값이 들어갈 수 있다. state bucket 접근 권한은 배포 담당자에게만 부여한다.

### 2.4 Terraform 변수 파일

예제 파일을 복사한다.

```bash
cp infra/terraform/envs/gcp-perf/terraform.tfvars.example \
  infra/terraform/envs/gcp-perf/terraform.tfvars
```

다음 값을 실제 환경에 맞게 입력한다.

- `project_id`
- `region`
- `zone`
- `name_prefix`
- `image_tags`
- `db_password`
- `jwt_secret_key`
- subnet CIDR
- 임베딩 VM machine type과 disk 크기
- `model_artifact_path`

각 image는 독립적인 tag를 사용한다. 처음 배포할 때는 모두 같은 tag로 시작해도 된다.

```hcl
image_tags = {
  core_api                    = "<GIT_SHA_OR_RELEASE_TAG>"
  search_service              = "<GIT_SHA_OR_RELEASE_TAG>"
  frontend                    = "<GIT_SHA_OR_RELEASE_TAG>"
  managed_embedding_endpoint  = "<GIT_SHA_OR_RELEASE_TAG>"
  pipeline_worker             = "<GIT_SHA_OR_RELEASE_TAG>"
  feedback_ingestion_pipeline = "<GIT_SHA_OR_RELEASE_TAG>"
  feedback_loop_pipeline      = "<GIT_SHA_OR_RELEASE_TAG>"
}
```

`terraform.tfvars`는 secret을 포함하므로 Git에 커밋하지 않는다.

## 3. 최초 배포

최초 배포는 아래 순서로 진행한다.

### 3.1 Terraform 초기화와 검증

```bash
terraform -chdir=infra/terraform/envs/gcp-perf init \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="prefix=biblio/gcp-perf"

terraform -chdir=infra/terraform/envs/gcp-perf validate
```

### 3.2 Artifact Registry 생성

Docker image를 push하기 전에 저장소를 먼저 만든다.

```bash
terraform -chdir=infra/terraform/envs/gcp-perf apply \
  -target=module.artifact_registry
```

### 3.3 전체 image 빌드와 push

인자를 생략하면 다음 image 7개를 같은 tag로 모두 빌드한다.

- `core-api`
- `search-service`
- `frontend`
- `managed-embedding-endpoint`
- `pipeline-worker`
- `feedback-ingestion-pipeline`
- `feedback-loop-pipeline`

```bash
export IMAGE_TAG=$(git rev-parse --short HEAD)

PROJECT_ID="$GCP_PROJECT_ID" \
REGION="$GCP_REGION" \
IMAGE_TAG="$IMAGE_TAG" \
bash scripts/deploy/build_and_push_images.sh
```

빌드한 `IMAGE_TAG`를 `terraform.tfvars`의 `image_tags` 항목에도 입력한다.

### 3.4 DB와 migration Job 생성

전체 Cloud Run 서비스를 올리기 전에 PostgreSQL과 migration Job에 필요한 자원을 먼저 생성한다.

```bash
terraform -chdir=infra/terraform/envs/gcp-perf apply \
  -target=module.database_migration_job
```

PostgreSQL VM의 startup script는 다음 작업을 수행한다.

- PostgreSQL 설치
- `pgvector` 설치
- PGMQ SQL 설치
- DB와 사용자 생성
- Cloud Run 및 임베딩 VM subnet의 DB 접속 허용

VM 최초 부팅은 패키지 설치와 image pull 때문에 시간이 걸릴 수 있다.

### 3.5 DB migration 실행

Cloud Run Job이 생성됐는지 확인하고 migration을 실행한다.

```bash
MIGRATION_JOB_NAME=$(
  terraform -chdir=infra/terraform/envs/gcp-perf \
    output -raw database_migration_job_name
)

gcloud run jobs execute "$MIGRATION_JOB_NAME" \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --wait
```

처음 배포할 때와 Alembic migration이 추가됐을 때 실행한다.

PostgreSQL VM을 단순히 중지했다가 다시 시작한 경우에는 재실행할 필요가 없다.

### 3.6 ModelRelease seed Job 생성과 실행

seed Job과 필요한 의존 자원을 생성한다.

```bash
terraform -chdir=infra/terraform/envs/gcp-perf apply \
  -target=module.model_release_seed_job
```

```bash
MODEL_RELEASE_SEED_JOB_NAME=$(
  terraform -chdir=infra/terraform/envs/gcp-perf \
    output -raw model_release_seed_job_name
)

gcloud run jobs execute "$MODEL_RELEASE_SEED_JOB_NAME" \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --wait
```

이 Job은 최초 모델 release 정보를 생성한다. 이미 같은 초기 데이터가 있으면 중복 생성하지 않는다.

### 3.7 전체 인프라 생성

먼저 plan을 확인한다.

```bash
terraform -chdir=infra/terraform/envs/gcp-perf plan \
  -out=/tmp/biblio-gcp-perf.tfplan
```

특히 다음 항목을 확인한다.

- 예상하지 않은 자원 삭제가 없는지
- 서비스 image tag가 방금 빌드한 값인지
- PostgreSQL과 임베딩 VM이 새로 만들어지는지 또는 교체되는지
- Secret Manager secret version이 불필요하게 계속 추가되지 않는지

확인 후 적용한다.

```bash
terraform -chdir=infra/terraform/envs/gcp-perf apply \
  /tmp/biblio-gcp-perf.tfplan
```

임베딩 VM의 startup script는 다음 작업을 수행한다.

- 별도 persistent disk를 `/models`에 mount
- Docker 설치
- Artifact Registry에서 image pull
- GCS 기반 모델 설정과 `DATABASE_URL` 구성
- 임베딩 컨테이너를 systemd로 자동 실행

임베딩 VM 최초 부팅은 패키지 설치와 image pull 때문에 시간이 걸릴 수 있다.

## 4. 배포 상태 검증

### 4.1 Terraform output 확인

```bash
terraform -chdir=infra/terraform/envs/gcp-perf output
```

주요 output은 다음과 같다.

- `core_api_url`
- `search_service_url`
- `fip_url`
- `batch_embedding_endpoint_url`
- `search_embedding_endpoint_url`
- `postgres_private_ip`
- `embedding_vm_private_ip`
- `bucket_names`

두 embedding endpoint output은 Cloud Run URL이 아니다. 각 임베딩 VM private IP의 `http://<PRIVATE_IP>:8000` 주소다.

### 4.2 VM 상태 확인

```bash
gcloud compute instances describe "${NAME_PREFIX}-postgres" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE" \
  --format='value(status,networkInterfaces[0].networkIP)'

gcloud compute instances describe "${NAME_PREFIX}-embedding" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE" \
  --format='value(status,networkInterfaces[0].networkIP)'

gcloud compute instances describe "${NAME_PREFIX}-embedding-search" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE" \
  --format='value(status,networkInterfaces[0].networkIP)'
```

기대 결과는 PostgreSQL과 두 임베딩 VM이 모두 `RUNNING`이고 private IP가 출력되는 것이다.

### 4.3 PostgreSQL 검증

IAP SSH로 PostgreSQL VM에 접속한다.

```bash
gcloud compute ssh "${NAME_PREFIX}-postgres" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE" \
  --tunnel-through-iap
```

VM 안에서 확인한다.

```bash
sudo systemctl status postgresql --no-pager
sudo -u postgres psql -d app -c '\dx'
sudo -u postgres psql -d app -c '\dn pgmq'
```

기대 결과:

- PostgreSQL service가 `active`
- extension 목록에 `vector`가 존재
- PGMQ가 생성한 schema와 함수가 존재

DB 이름을 기본값 `app`에서 변경했다면 실제 `database_name`을 사용한다.

### 4.4 임베딩 엔드포인트 검증

임베딩 VM은 external IP가 없으므로 IAP SSH를 통해 VM 내부에서 확인한다.

```bash
gcloud compute ssh "${NAME_PREFIX}-embedding" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE" \
  --tunnel-through-iap \
  --command='curl -fsS http://127.0.0.1:8000/health'

gcloud compute ssh "${NAME_PREFIX}-embedding-search" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE" \
  --tunnel-through-iap \
  --command='curl -fsS http://127.0.0.1:8000/health'
```

실제 벡터 생성까지 확인한다.

```bash
gcloud compute ssh "${NAME_PREFIX}-embedding" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE" \
  --tunnel-through-iap \
  --command="curl -fsS http://127.0.0.1:8000/embed \
    -H 'Content-Type: application/json' \
    -d '{\"texts\":[\"smoke text\"],\"model_version\":null}'"
```

기대 결과는 2xx 응답과 embedding 벡터 배열이다.

`/health`가 성공해도 `/embed`가 실패하면 모델 준비가 끝난 상태가 아니다.

### 4.5 Cloud Run 배포 상태 확인

```bash
gcloud run services list \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION"
```

다음 서비스가 Ready 상태인지 확인한다.

- `core-api`
- `search-service`
- `feedback-ingestion-pipeline`
- `pipeline-worker`
- `feedback-loop-scheduler`
- `feedback-loop-dataset-worker`
- `feedback-loop-train-release-worker`
- `feedback-loop-rollback-worker`
- `feedback-loop-legacy-reindex-worker`
- `feedback-loop-reembedding-worker`

배포 image와 환경 변수는 다음 명령으로 확인한다.

```bash
gcloud run services describe core-api \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --format='yaml(spec.template.spec.containers,status.latestReadyRevisionName)'
```

서비스 이름을 바꿔 다른 Cloud Run 서비스도 같은 방식으로 확인한다.

### 4.6 FIP 원격 검증

`feedback-ingestion-pipeline`은 인증이 필요한 Cloud Run 서비스다. 호출 계정에는 해당 서비스의 `roles/run.invoker` 권한이 있어야 한다.

```bash
export FIP_URL=$(
  terraform -chdir=infra/terraform/envs/gcp-perf output -raw fip_url
)

export FIP_ID_TOKEN=$(gcloud auth print-identity-token)
```

테스트 이벤트를 전송한다. `created_at`은 명령 실행 시점의 UTC 시각으로 자동 설정한다.

```bash
export FIP_SMOKE_EVENT_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
export FIP_SMOKE_REQ_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')

FIP_STATUS=$(
  curl -sS -o /tmp/fip-smoke-response.json -w '%{http_code}' \
  -X POST "$FIP_URL/feedback/events" \
  -H "Authorization: Bearer $FIP_ID_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{
    \"schema_version\": 1,
    \"event_id\": \"$FIP_SMOKE_EVENT_ID\",
    \"user_id\": \"22222222-2222-4222-8222-222222222222\",
    \"project_id\": \"33333333-3333-4333-8333-333333333333\",
    \"req_id\": \"$FIP_SMOKE_REQ_ID\",
    \"query_text\": \"remote smoke query\",
    \"rating\": \"LIKE\",
    \"topk_ids\": [\"55555555-5555-4555-8555-555555555555\"],
    \"used_ids\": [\"55555555-5555-4555-8555-555555555555\"],
    \"active_model_version\": \"embedding-smoke-v1\",
    \"active_index_name\": \"project-smoke-active\",
    \"response_snapshot_ref\": \"search_response_snapshot:$FIP_SMOKE_REQ_ID\",
    \"created_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
    \"trace_id\": \"66666666-6666-4666-8666-666666666666\",
    \"served_vector_paths\": []
  }"
)

test "$FIP_STATUS" = "202" \
  && echo "FIP accepted: HTTP 202" \
  || {
    echo "FIP request failed: HTTP $FIP_STATUS"
    cat /tmp/fip-smoke-response.json
    exit 1
  }
```

기대 결과는 HTTP `202`다.

FIP는 응답 후 GCS에 batch flush한다. 현재 최소 인스턴스가 0이고 CPU가 요청 처리 중에만 할당되므로, 테스트 직후 instance가 내려가면 flush가 늦어질 수 있다.

```bash
export GCS_FEEDBACK_LOG_BUCKET_NAME=$(
  terraform -chdir=infra/terraform/envs/gcp-perf output -json bucket_names \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['feedback_log'])"
)

sleep 20

gcloud storage ls -r \
  "gs://$GCS_FEEDBACK_LOG_BUCKET_NAME/feedback/raw_logs/schema_version=1/" \
  --project="$GCP_PROJECT_ID"
```

가장 최근 object에서 `req_id`를 확인한다.

```bash
latest=$(
  gcloud storage ls -r \
    "gs://$GCS_FEEDBACK_LOG_BUCKET_NAME/feedback/raw_logs/schema_version=1/" \
    --project="$GCP_PROJECT_ID" \
    | grep -v '/$' \
    | sort \
    | tail -1
)

gcloud storage cat "$latest" --project="$GCP_PROJECT_ID" \
  | grep -q "$FIP_SMOKE_REQ_ID" \
  && echo "FIP smoke OK" \
  || echo "FIP smoke failed"
```

검증용 object를 지우려면 다음 명령을 사용한다.

```bash
gcloud storage rm "$latest" --project="$GCP_PROJECT_ID"
```

### 4.7 전체 기능 흐름 검증

현재 배포 환경에서 검증된 기능 흐름은 다음과 같다.

```text
영상 업로드
→ GCS 저장
→ pipeline-worker 색인
→ 임베딩 VM 호출
→ PostgreSQL 벡터 저장
→ search-service 검색
→ core-api 피드백 제출
→ FIP 수집
→ GCS raw feedback 저장
→ dataset-worker 데이터셋 생성
```

전체 기능 검증은 사용자 JWT, 테스트 사용자와 프로젝트, 실제 영상 파일이 필요하다. 공통 로컬 E2E 도구는 다음 문서를 참고한다.

- `docs/e2e-backend-runbook.md`
- `scripts/e2e_backend_smoke.py`
- `scripts/e2e_backend_smoke_transcript_fixture.py`

피드백 루프의 학습, 배포, 롤백 전 구간은 아직 배포 환경 E2E가 완료되지 않았다. 현재 train-release 산출물은 임베딩 엔드포인트가 바로 로드할 수 있는 모델 형태가 아니므로, 이 단계를 성공한 절차로 기록하지 않는다.

## 5. 코드 변경 후 재배포

### 5.1 배포 원칙

각 image tag는 `terraform.tfvars`의 `image_tags`에서 따로 관리한다.

코드가 변경된 image만 빌드하고 해당 tag만 바꾼다. 그 뒤에도 `-target`이 아닌 전체 plan과 apply를 실행한다. Terraform은 image 주소가 바뀐 서비스만 갱신한다.

재배포 전에 반드시 다음을 확인한다.

```bash
git rev-parse --short HEAD
terraform -chdir=infra/terraform/envs/gcp-perf console \
  <<< 'var.image_tags'
```

### 5.2 일부 image 재배포

빌드할 image 이름을 스크립트 인자로 넘긴다. 여러 개를 한 번에 지정할 수도 있다.

```bash
export IMAGE_TAG=$(git rev-parse --short HEAD)

PROJECT_ID="$GCP_PROJECT_ID" \
REGION="$GCP_REGION" \
IMAGE_TAG="$IMAGE_TAG" \
bash scripts/deploy/build_and_push_images.sh frontend
```

두 image를 함께 빌드하는 예시는 다음과 같다.

```bash
PROJECT_ID="$GCP_PROJECT_ID" \
REGION="$GCP_REGION" \
IMAGE_TAG="$IMAGE_TAG" \
bash scripts/deploy/build_and_push_images.sh frontend core-api
```

빌드한 image에 해당하는 `image_tags` 값만 `IMAGE_TAG`와 같은 값으로 바꾼다. 예를 들어 frontend만 빌드했다면 기존 `image_tags` 블록에서 다음 한 줄의 값만 바꾼다. 나머지 항목은 기존 값을 유지한다.

```hcl
frontend = "<NEW_IMAGE_TAG>"
```

전체 plan에서 의도하지 않은 서비스 변경이나 VM 교체가 없는지 확인한 뒤 apply한다.

```bash
terraform -chdir=infra/terraform/envs/gcp-perf plan \
  -out=/tmp/biblio-gcp-perf.tfplan

terraform -chdir=infra/terraform/envs/gcp-perf apply \
  /tmp/biblio-gcp-perf.tfplan
```

frontend만 변경했다면 plan에 embedding VM의 `must be replaced`가 나타나면 안 된다.

### 5.3 전체 image 재배포

```bash
export IMAGE_TAG=$(git rev-parse --short HEAD)

PROJECT_ID="$GCP_PROJECT_ID" \
REGION="$GCP_REGION" \
IMAGE_TAG="$IMAGE_TAG" \
bash scripts/deploy/build_and_push_images.sh
```

`terraform.tfvars`의 모든 `image_tags`를 같은 값으로 변경한 뒤 plan과 apply를 실행한다.

```bash
terraform -chdir=infra/terraform/envs/gcp-perf plan \
  -out=/tmp/biblio-gcp-perf.tfplan

terraform -chdir=infra/terraform/envs/gcp-perf apply \
  /tmp/biblio-gcp-perf.tfplan
```

DB schema가 변경됐다면 migration Job을 다시 실행한다.

### 5.4 검색·배치 임베딩 VM 최초 분리

기존 단일 임베딩 VM을 두 대로 나누는 최초 전환은 두 변경으로 나눠 적용한다.

1. `search_embedding_cutover_enabled = false`로 적용한다. 검색 VM은 생성되지만 search-service와 feedback-loop 검색 주소는 기존 배치 VM을 유지한다.
2. 검색 VM `/internal/reload-models`를 호출하고, DB의 현재 `active_model_version`이 `/health`의 `ready_model_versions`에 포함되는지 확인한다.
3. 준비 확인 후 `search_embedding_cutover_enabled = true`로 바꾸고 두 번째 Terraform plan과 apply를 실행한다.
4. 검색 1건과 영상 업로드 1건으로 요청이 각각 검색 VM과 배치 VM에 들어가는지 확인한다.

검색 VM 생성과 서비스 주소 전환을 같은 Terraform 적용에 넣지 않는다. 전환 뒤 문제가 생기면 서비스 주소만 기존 배치 VM으로 되돌리고 검색 VM은 원인 확인을 위해 유지한다.

## 6. worker와 FIP 풀 가동

현재 다음 자원은 비용 절감을 위해 최소 인스턴스 0이다.

- `pipeline-worker`
- feedback-loop worker 6종
- `feedback-ingestion-pipeline`

PGMQ를 계속 polling하는 worker는 최소 인스턴스 0이면 queue message만으로 자동 기동되지 않는다. 작업을 처리하려면 테스트 동안 최소 인스턴스를 1로 올려야 한다.

```bash
gcloud run services update pipeline-worker \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --min-instances=1

gcloud run services update feedback-loop-dataset-worker \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --min-instances=1
```

FIP는 응답 후 GCS flush를 수행한다. 지속 검증 중에는 최소 인스턴스 1과 CPU 상시 할당이 안전하다.

```bash
gcloud run services update feedback-ingestion-pipeline \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --min-instances=1 \
  --no-cpu-throttling
```

수동 변경은 다음 `terraform apply`에서 Terraform 설정으로 돌아간다. 장기 적용이 필요하면 `main.tf`를 수정하고 plan을 검토한다.

검증 종료 후 다시 0으로 내린다.

```bash
gcloud run services update pipeline-worker \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --min-instances=0

gcloud run services update feedback-loop-dataset-worker \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --min-instances=0

gcloud run services update feedback-ingestion-pipeline \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --min-instances=0 \
  --cpu-throttling
```

## 7. 비용 절감용 중지와 재시작

환경을 삭제하지 않고 잠시 보존하려면 Cloud Run 최소 인스턴스를 0으로 두고 VM 2개를 중지한다.

### 7.1 VM 중지

```bash
gcloud compute instances stop \
  "${NAME_PREFIX}-postgres" \
  "${NAME_PREFIX}-embedding" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE"
```

VM 중지 후 CPU와 RAM 과금은 멈춘다. 다음 비용은 계속 발생한다.

- PostgreSQL boot disk
- 임베딩 VM boot disk
- 임베딩 모델 persistent disk
- GCS
- Artifact Registry
- Secret Manager
- Cloud NAT 등 일부 네트워크 자원

### 7.2 VM 재시작

PostgreSQL을 먼저 시작한다.

```bash
gcloud compute instances start "${NAME_PREFIX}-postgres" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE"

gcloud compute instances start "${NAME_PREFIX}-embedding" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE"
```

재시작 후 PostgreSQL과 임베딩 `/health`, `/embed`를 다시 검증한다.

PostgreSQL startup script는 최초 설치 완료 표시 파일이 있으면 재설치를 건너뛰고 service만 재시작한다. 임베딩 모델 disk도 유지된다.

## 8. 완전 삭제

삭제 전에 보존할 데이터를 확인한다.

- 영상 bucket
- 피드백 raw log
- 학습 데이터셋과 모델 artifact
- PostgreSQL 데이터
- 임베딩 모델 disk

현재 GCS bucket에는 `force_destroy`가 설정되어 있지 않다. object가 남아 있으면 Terraform이 bucket을 삭제하지 못한다.

필요한 데이터를 다른 위치에 백업한 뒤 Terraform 관리 bucket 이름을 확인한다.

```bash
terraform -chdir=infra/terraform/envs/gcp-perf output -json bucket_names \
  | python3 -c 'import json,sys; [print(v) for v in json.load(sys.stdin).values()]'
```

출력된 각 bucket의 object를 삭제한다. 다음 명령은 bucket 자체가 아니라 bucket 안의 object를 대상으로 한다.

```bash
gcloud storage rm --recursive "gs://<BUCKET_NAME>/**"
```

세 bucket이 비어 있는 것을 확인한 뒤 destroy plan을 검토하고 적용한다.

```bash
terraform -chdir=infra/terraform/envs/gcp-perf plan -destroy \
  -out=/tmp/biblio-gcp-perf-destroy.tfplan

terraform -chdir=infra/terraform/envs/gcp-perf apply \
  /tmp/biblio-gcp-perf-destroy.tfplan
```

Terraform state bucket은 이 환경의 관리 대상이 아니므로 자동 삭제되지 않는다.

state까지 완전히 제거할 때만 별도로 삭제한다.

```bash
gcloud storage rm -r "gs://$TF_STATE_BUCKET"
```

## 9. 장애 확인

### 9.1 Terraform apply가 Cloud Run 생성에서 멈춤

확인 항목:

- container가 지정된 port를 열고 있는지
- worker image에 health server가 실행되는지
- startup log에 import 또는 설정 오류가 없는지
- image tag가 실제 Artifact Registry에 존재하는지

```bash
gcloud run services logs read <SERVICE_NAME> \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --limit=100
```

### 9.2 Cloud Run에서 PostgreSQL에 연결하지 못함

확인 항목:

- PostgreSQL VM이 `RUNNING`인지
- VM private IP가 `DATABASE_URL`과 일치하는지
- Cloud Run에 Direct VPC egress가 적용됐는지
- PostgreSQL `5432` 방화벽이 Cloud Run subnet을 허용하는지
- PostgreSQL의 `pg_hba.conf`가 같은 subnet을 허용하는지

### 9.3 Cloud Run에서 임베딩 엔드포인트에 연결하지 못함

확인 항목:

- 임베딩 VM이 `RUNNING`인지
- 컨테이너가 `8000` 포트에서 실행 중인지
- Cloud Run `EMBEDDING_API_URL`이 VM private IP를 가리키는지
- 임베딩 `8000` 방화벽이 Cloud Run subnet을 허용하는지

VM 내부 상태:

```bash
gcloud compute ssh "${NAME_PREFIX}-embedding" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE" \
  --tunnel-through-iap \
  --command='sudo systemctl status biblio-managed-embedding-endpoint --no-pager; sudo docker ps'
```

### 9.4 VM startup 실패

serial log를 확인한다.

```bash
gcloud compute instances get-serial-port-output "${NAME_PREFIX}-postgres" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE" \
  --port=1

gcloud compute instances get-serial-port-output "${NAME_PREFIX}-embedding" \
  --project="$GCP_PROJECT_ID" \
  --zone="$GCP_ZONE" \
  --port=1
```

주요 원인:

- Cloud NAT 누락
- Secret Manager 접근 권한 누락
- Artifact Registry image tag 없음
- GCS model artifact 없음
- PostgreSQL 또는 Docker package 저장소 접근 실패

### 9.5 FIP가 202를 반환했지만 GCS object가 없음

확인 항목:

- FIP 최소 인스턴스와 CPU 할당
- GCS bucket 환경 변수
- FIP 서비스 계정의 `storage.objectCreator` 권한
- Cloud Run log의 GCS sink 오류

```bash
gcloud run services logs read feedback-ingestion-pipeline \
  --project="$GCP_PROJECT_ID" \
  --region="$GCP_REGION" \
  --limit=200
```

### 9.6 PGMQ 메시지가 중복 처리됨

현재 PGMQ 가시성 시간보다 영상 처리 시간이 길어질 수 있다. `pipeline-worker`는 이를 줄이기 위해 동시 실행 1개와 최대 인스턴스 1개로 운영 중이다.

이 설정은 완전한 해결이 아니다. 가시성 연장, 처리 중 갱신, 영상별 잠금이 구현되기 전까지 worker 동시성을 임의로 높이지 않는다.
