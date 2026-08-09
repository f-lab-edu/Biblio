locals {
  repository_id = "biblio"

  image_registry = "${var.region}-docker.pkg.dev/${var.project_id}/${local.repository_id}"

  service_images = {
    "core-api"                    = "${local.image_registry}/core-api:${var.image_tags.core_api}"
    "search-service"              = "${local.image_registry}/search-service:${var.image_tags.search_service}"
    "frontend"                    = "${local.image_registry}/frontend:${var.image_tags.frontend}"
    "managed-embedding-endpoint"  = "${local.image_registry}/managed-embedding-endpoint:${var.image_tags.managed_embedding_endpoint}"
    "pipeline-worker"             = "${local.image_registry}/pipeline-worker:${var.image_tags.pipeline_worker}"
    "feedback-ingestion-pipeline" = "${local.image_registry}/feedback-ingestion-pipeline:${var.image_tags.feedback_ingestion_pipeline}"
    "feedback-loop-pipeline"      = "${local.image_registry}/feedback-loop-pipeline:${var.image_tags.feedback_loop_pipeline}"
  }

  bucket_names = {
    video        = "${var.name_prefix}-video"
    feedback_log = "${var.name_prefix}-feedback-log"
    ml_artifact  = "${var.name_prefix}-ml-artifact"
  }

  database_url = "postgresql+asyncpg://${urlencode(var.database_user)}:${urlencode(var.db_password)}@${module.postgres_vm.private_ip}:5432/${urlencode(var.database_name)}"

  batch_embedding_vm_url  = "http://${module.embedding_vm.private_ip}:8000"
  search_embedding_vm_url = "http://${module.embedding_search_vm.private_ip}:8000"
  search_embedding_routing_url = (
    var.search_embedding_cutover_enabled
    ? local.search_embedding_vm_url
    : local.batch_embedding_vm_url
  )

  feedback_loop_worker_roles = {
    scheduler = {
      service_name = "feedback-loop-scheduler"
    }
    dataset-worker = {
      service_name = "feedback-loop-dataset-worker"
    }
    train-release-worker = {
      service_name = "feedback-loop-train-release-worker"
    }
    rollback-worker = {
      service_name = "feedback-loop-rollback-worker"
    }
    legacy-reindex-worker = {
      service_name = "feedback-loop-legacy-reindex-worker"
    }
    reembedding-worker = {
      service_name = "feedback-loop-reembedding-worker"
    }
  }

  feedback_loop_common_env = {
    BROKER_TYPE                   = "pgmq"
    ARTIFACT_STORE_BACKEND        = "gcs"
    LOCAL_ARTIFACT_ROOT           = "/tmp/feedback-loop-artifacts"
    GCS_FEEDBACK_LOG_BUCKET_NAME  = module.object_storage.bucket_names.feedback_log
    GCS_ML_ARTIFACT_BUCKET_NAME   = module.object_storage.bucket_names.ml_artifact
    RAW_FEEDBACK_LOG_PREFIX       = var.raw_feedback_log_prefix
    DATASET_ARTIFACT_PREFIX       = var.dataset_artifact_prefix
    MODEL_ARTIFACT_PREFIX         = var.model_artifact_prefix
    MODEL_VERSION_PREFIX          = var.model_version_prefix
    SERVING_MODEL_ARTIFACT_PREFIX = var.serving_model_artifact_prefix
    EVALUATION_ARTIFACT_PREFIX    = var.evaluation_artifact_prefix
    BATCH_EMBEDDING_ENDPOINT_URL  = local.batch_embedding_vm_url
    SEARCH_EMBEDDING_ENDPOINT_URL = local.search_embedding_routing_url
    SEARCH_SERVICE_URL            = module.search_service.url
    LOCAL_TRAINING_MODEL_NAME     = var.local_training_model_name
    EMBEDDING_DIMENSION           = tostring(var.embedding_dimension)
    TRAINING_CONFIG_PATH          = var.training_config_path
    EVALUATION_DATASET_REF        = var.evaluation_dataset_ref
  }

  cloud_run_vpc_network_interfaces = [
    {
      network    = module.network.network_id
      subnetwork = module.network.cloudrun_subnet_id
    }
  ]

  cloud_run_job_vpc_network_interfaces = [
    {
      network    = module.network.network_id
      subnetwork = module.network.cloudrun_subnet_id
    }
  ]
}

module "artifact_registry" {
  source = "../../modules/artifact_registry"

  project_id    = var.project_id
  region        = var.region
  repository_id = local.repository_id
}

module "object_storage" {
  source = "../../modules/object_storage"

  project_id                = var.project_id
  location                  = var.region
  video_bucket_name         = local.bucket_names.video
  video_bucket_cors_origins = [var.frontend_origin]
  feedback_log_bucket_name  = local.bucket_names.feedback_log
  ml_artifact_bucket_name   = local.bucket_names.ml_artifact
}

module "secrets" {
  source = "../../modules/secrets"

  project_id               = var.project_id
  database_url_secret_id   = "${var.name_prefix}-database-url"
  jwt_secret_key_secret_id = "${var.name_prefix}-jwt-secret-key"
  db_password_secret_id    = "${var.name_prefix}-db-password"
  jwt_secret_key           = var.jwt_secret_key
  db_password              = var.db_password
}

module "iam" {
  source = "../../modules/iam"

  project_id   = var.project_id
  bucket_names = module.object_storage.bucket_names
  secret_ids   = module.secrets.secret_ids
}

module "network" {
  source = "../../modules/network"

  project_id            = var.project_id
  region                = var.region
  name_prefix           = var.name_prefix
  cloudrun_subnet_cidr  = var.cloudrun_subnet_cidr
  postgres_subnet_cidr  = var.postgres_subnet_cidr
  embedding_subnet_cidr = var.embedding_subnet_cidr
}

module "load_test_vm" {
  source = "../../modules/load_test_vm"

  project_id            = var.project_id
  zone                  = var.zone
  instance_name         = "${var.name_prefix}-k6-runner"
  machine_type          = var.load_test_vm_machine_type
  boot_disk_size_gb     = var.load_test_vm_disk_size_gb
  network               = module.network.network_self_link
  subnetwork            = module.network.cloudrun_subnet_self_link
  network_tags          = [module.network.load_test_network_tag]
  service_account_email = module.iam.service_account_emails["load-test"]
  k6_version            = var.load_test_k6_version
  auto_shutdown_hours   = var.load_test_auto_shutdown_hours

  depends_on = [module.network, module.iam]
}

module "postgres_vm" {
  source = "../../modules/postgres_vm"

  project_id              = var.project_id
  zone                    = var.zone
  instance_name           = "${var.name_prefix}-postgres"
  network                 = module.network.network_self_link
  subnetwork              = module.network.postgres_subnet_self_link
  network_tags            = [module.network.postgres_network_tag]
  service_account_email   = module.iam.service_account_emails["postgres-vm"]
  db_password_secret_name = module.secrets.secret_names.db_password
  database_name           = var.database_name
  database_user           = var.database_user
  boot_disk_size_gb       = 20
  boot_disk_type          = "pd-balanced"
  allowed_cidr_blocks = [
    module.network.cloudrun_subnet_cidr,
    var.embedding_subnet_cidr,
  ]

  depends_on = [module.network, module.iam, module.secrets]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = module.secrets.secret_names.database_url
  secret_data = local.database_url
}

module "database_migration_job" {
  source = "../../modules/cloud_run_job"

  project_id            = var.project_id
  region                = var.region
  job_name              = "${var.name_prefix}-database-migration"
  image_url             = local.service_images["core-api"]
  service_account_email = module.iam.service_account_emails["core-api"]
  command               = ["alembic"]
  args                  = ["upgrade", "head"]
  working_dir           = "/app"

  secret_env_vars = {
    DATABASE_URL = {
      secret_name = module.secrets.secret_names.database_url
    }
  }

  vpc_network_interfaces = local.cloud_run_job_vpc_network_interfaces

  depends_on = [
    google_secret_manager_secret_version.database_url,
    module.postgres_vm,
  ]
}

module "model_release_seed_job" {
  source = "../../modules/cloud_run_job"

  project_id            = var.project_id
  region                = var.region
  job_name              = "${var.name_prefix}-model-release-seed"
  image_url             = local.service_images["managed-embedding-endpoint"]
  service_account_email = module.iam.service_account_emails["managed-embedding-endpoint"]
  command               = ["python"]
  args                  = ["-m", "src.core.model_release_seed"]
  working_dir           = "/app"

  env_vars = {
    MODEL_ARTIFACT_PATH = var.model_artifact_path
    EMBEDDING_DIMENSION = tostring(var.embedding_dimension)
  }

  secret_env_vars = {
    DATABASE_URL = {
      secret_name = module.secrets.secret_names.database_url
    }
  }

  vpc_network_interfaces = local.cloud_run_job_vpc_network_interfaces

  depends_on = [
    module.database_migration_job,
  ]
}

module "managed_embedding_endpoint" {
  count  = var.enable_managed_embedding_cloud_run ? 1 : 0
  source = "../../modules/cloud_run_service"

  project_id             = var.project_id
  region                 = var.region
  service_name           = "managed-embedding-endpoint"
  image_url              = local.service_images["managed-embedding-endpoint"]
  service_account_email  = module.iam.service_account_emails["managed-embedding-endpoint"]
  port                   = 8000
  startup_probe_path     = "/health"
  max_instance_count     = var.cloud_run_max_instance_count
  vpc_network_interfaces = local.cloud_run_vpc_network_interfaces

  env_vars = {
    MODEL_ARTIFACT_PATH = var.model_artifact_path
    MODEL_ARTIFACT_ROOT = var.model_artifact_root
  }

  secret_env_vars = {
    DATABASE_URL = {
      secret_name = module.secrets.secret_names.database_url
    }
  }

  depends_on = [google_secret_manager_secret_version.database_url]
}

module "embedding_vm" {
  source = "../../modules/embedding_vm"

  project_id                        = var.project_id
  zone                              = var.zone
  instance_name                     = "${var.name_prefix}-embedding"
  machine_type                      = var.embedding_vm_machine_type
  network                           = module.network.network_self_link
  subnetwork                        = module.network.embedding_subnet_self_link
  network_tags                      = [module.network.embedding_network_tag]
  internal_ip                       = "10.20.3.14"
  service_account_email             = module.iam.service_account_emails["managed-embedding-endpoint"]
  image_url                         = local.service_images["managed-embedding-endpoint"]
  database_url_secret_name          = module.secrets.secret_names.database_url
  gcs_ml_artifact_bucket_name       = module.object_storage.bucket_names.ml_artifact
  model_artifact_path               = var.model_artifact_path
  model_artifact_prefix             = var.embedding_model_artifact_prefix
  local_model_cache_root            = var.local_model_cache_root
  model_disk_size_gb                = var.embedding_vm_model_disk_size_gb
  search_request_limit              = var.embedding_search_request_limit
  video_preprocess_request_limit    = var.embedding_video_preprocess_request_limit
  search_wait_timeout_sec           = var.embedding_search_wait_timeout_sec
  video_preprocess_wait_timeout_sec = var.embedding_video_preprocess_wait_timeout_sec

  depends_on = [google_secret_manager_secret_version.database_url, module.iam]
}

module "embedding_search_vm" {
  source = "../../modules/embedding_vm"

  project_id                        = var.project_id
  zone                              = var.zone
  instance_name                     = "${var.name_prefix}-embedding-search"
  machine_type                      = var.embedding_vm_machine_type
  network                           = module.network.network_self_link
  subnetwork                        = module.network.embedding_subnet_self_link
  network_tags                      = [module.network.embedding_network_tag]
  internal_ip                       = "10.20.3.15"
  service_account_email             = module.iam.service_account_emails["managed-embedding-endpoint"]
  image_url                         = local.service_images["managed-embedding-endpoint"]
  database_url_secret_name          = module.secrets.secret_names.database_url
  gcs_ml_artifact_bucket_name       = module.object_storage.bucket_names.ml_artifact
  model_artifact_path               = var.model_artifact_path
  model_artifact_prefix             = var.embedding_model_artifact_prefix
  local_model_cache_root            = var.local_model_cache_root
  model_disk_size_gb                = var.embedding_vm_model_disk_size_gb
  max_concurrency                   = var.embedding_search_max_concurrency
  inference_threads                 = var.embedding_search_inference_threads
  search_request_limit              = var.embedding_search_request_limit
  video_preprocess_request_limit    = var.embedding_video_preprocess_request_limit
  search_wait_timeout_sec           = var.embedding_search_wait_timeout_sec
  video_preprocess_wait_timeout_sec = var.embedding_video_preprocess_wait_timeout_sec
  enable_warp_proxy                 = false

  depends_on = [google_secret_manager_secret_version.database_url, module.iam]
}

module "search_service" {
  source = "../../modules/cloud_run_service"

  project_id             = var.project_id
  region                 = var.region
  service_name           = "search-service"
  image_url              = local.service_images["search-service"]
  service_account_email  = module.iam.service_account_emails["search-service"]
  port                   = 8082
  max_instance_count     = var.cloud_run_max_instance_count
  vpc_network_interfaces = local.cloud_run_vpc_network_interfaces

  env_vars = {
    EMBEDDING_API_URL     = local.search_embedding_routing_url
    EMBEDDING_TIMEOUT_SEC = tostring(var.search_embedding_timeout_sec)
    GCP_LOCATION          = "us-central1"
    GCP_PROJECT_ID        = var.project_id
    GEMINI_MODEL_NAME     = "gemini-2.5-flash"
    LLM_TIMEOUT_SEC       = "60"
    LLM_MAX_OUTPUT_TOKENS = "2048"
  }

  secret_env_vars = {
    DATABASE_URL = {
      secret_name = module.secrets.secret_names.database_url
    }
    JWT_SECRET_KEY = {
      secret_name = module.secrets.secret_names.jwt_secret_key
    }
  }

  depends_on = [google_secret_manager_secret_version.database_url, module.secrets]
}

module "feedback_ingestion_pipeline" {
  source = "../../modules/cloud_run_service"

  project_id             = var.project_id
  region                 = var.region
  service_name           = "feedback-ingestion-pipeline"
  image_url              = local.service_images["feedback-ingestion-pipeline"]
  service_account_email  = module.iam.service_account_emails["feedback-ingestion-pipeline"]
  port                   = 8080
  max_instance_count     = var.cloud_run_max_instance_count
  vpc_network_interfaces = local.cloud_run_vpc_network_interfaces

  # FIP(vector)는 202 응답 후 GCS flush를 백그라운드 배치로 처리한다.
  # 풀 가동(운영·전체 인스턴스 테스트) 시에는 flush 지연·누락을 막기 위해
  # min=1 + cpu_idle=false가 정답이다.
  # 지금은 전체 인스턴스를 띄우는 단계가 아니라 비용 절감을 위해 min=0으로 둔다.
  # 풀 가동으로 검증할 때는 min=1 + cpu_idle=false로 되돌린다.
  min_instance_count = 0

  env_vars = {
    GCS_FEEDBACK_LOG_BUCKET_NAME = module.object_storage.bucket_names.feedback_log
  }
}

module "core_api" {
  source = "../../modules/cloud_run_service"

  project_id             = var.project_id
  region                 = var.region
  service_name           = "core-api"
  image_url              = local.service_images["core-api"]
  service_account_email  = module.iam.service_account_emails["core-api"]
  port                   = 8080
  max_instance_count     = var.cloud_run_max_instance_count
  vpc_network_interfaces = local.cloud_run_vpc_network_interfaces

  env_vars = {
    GCS_VIDEO_BUCKET_NAME     = module.object_storage.bucket_names.video
    GCP_PROJECT_ID            = var.project_id
    BROKER_TYPE               = "pgmq"
    FIP_FEEDBACK_DELIVERY_URL = "${module.feedback_ingestion_pipeline.url}/feedback/events"
    # FIP도 인증을 요구하는 Cloud Run 서비스라, 배포 환경에서는 ID 토큰을 붙여 호출한다.
    FIP_DELIVERY_USE_IAM_AUTH = "true"
    AUTH_COOKIE_SECURE        = "true"
  }

  secret_env_vars = {
    DATABASE_URL = {
      secret_name = module.secrets.secret_names.database_url
    }
    JWT_SECRET_KEY = {
      secret_name = module.secrets.secret_names.jwt_secret_key
    }
  }

  depends_on = [google_secret_manager_secret_version.database_url, module.secrets]
}

module "frontend" {
  source = "../../modules/cloud_run_service"

  project_id            = var.project_id
  region                = var.region
  service_name          = "frontend"
  image_url             = local.service_images["frontend"]
  service_account_email = module.iam.service_account_emails["frontend"]
  port                  = 8080
  max_instance_count    = var.cloud_run_max_instance_count

  # frontend는 백엔드를 공개 URL + ID 토큰으로 호출하므로 VPC 연결이 필요 없다.
  env_vars = {
    CORE_API_URL       = module.core_api.url
    SEARCH_SERVICE_URL = module.search_service.url
    PROXY_USE_IAM_AUTH = "true"
  }
}

# 브라우저는 로그인 전에도 프론트에 접근해야 하므로 frontend만 공개 invoker를 부여한다.
resource "google_cloud_run_v2_service_iam_member" "frontend_public_invoker" {
  project  = var.project_id
  location = var.region
  name     = module.frontend.service_name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# core-api가 FIP(인증 필요 Cloud Run)를 호출할 수 있도록 invoker 권한을 부여한다.
resource "google_cloud_run_v2_service_iam_member" "core_api_invokes_fip" {
  project  = var.project_id
  location = var.region
  name     = module.feedback_ingestion_pipeline.service_name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${module.iam.service_account_emails["core-api"]}"
}

# feedback-loop-pipeline이 search-service(인증 필요 Cloud Run)의 internal reload API를 호출할 수 있도록 한다.
resource "google_cloud_run_v2_service_iam_member" "feedback_loop_invokes_search_service" {
  project  = var.project_id
  location = var.region
  name     = module.search_service.service_name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${module.iam.service_account_emails["feedback-loop-pipeline"]}"
}

# frontend 프록시 서버가 인증 필요 Cloud Run 백엔드를 호출할 수 있도록 한다.
resource "google_cloud_run_v2_service_iam_member" "frontend_invokes_core_api" {
  project  = var.project_id
  location = var.region
  name     = module.core_api.service_name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${module.iam.service_account_emails["frontend"]}"
}

# frontend 프록시 서버가 인증 필요 search-service를 호출할 수 있도록 한다.
resource "google_cloud_run_v2_service_iam_member" "frontend_invokes_search_service" {
  project  = var.project_id
  location = var.region
  name     = module.search_service.service_name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${module.iam.service_account_emails["frontend"]}"
}

resource "google_cloud_run_v2_service_iam_member" "load_test_invokes_core_api" {
  project  = var.project_id
  location = var.region
  name     = module.core_api.service_name
  role     = "roles/run.invoker"
  member   = module.iam.service_account_members["load-test"]
}

resource "google_cloud_run_v2_service_iam_member" "load_test_invokes_search_service" {
  project  = var.project_id
  location = var.region
  name     = module.search_service.service_name
  role     = "roles/run.invoker"
  member   = module.iam.service_account_members["load-test"]
}

module "pipeline_worker" {
  source = "../../modules/cloud_run_worker"

  project_id             = var.project_id
  region                 = var.region
  service_name           = "pipeline-worker"
  image_url              = local.service_images["pipeline-worker"]
  service_account_email  = module.iam.service_account_emails["pipeline-worker"]
  min_instance_count     = 0
  max_instance_count     = var.worker_max_instance_count
  memory                 = "4Gi"
  vpc_network_interfaces = local.cloud_run_vpc_network_interfaces

  env_vars = {
    BROKER_TYPE              = "pgmq"
    GCP_PROJECT_ID           = var.project_id
    STT_LOCATION             = "us"
    STT_MODEL_VERSION        = "chirp_3"
    VISION_LOCATION          = "global"
    VISION_MODEL             = "gemini-3.1-flash-lite"
    VISION_MAX_OUTPUT_TOKENS = "2048"
    WORKER_CONCURRENCY       = "4"
    # 임베딩 VM의 wireproxy(WARP) SOCKS5. YouTube 트래픽만 이 프록시로 우회한다.
    YOUTUBE_PROXY_URL     = "socks5://${module.embedding_vm.private_ip}:1080"
    GCS_VIDEO_BUCKET_NAME = module.object_storage.bucket_names.video
    EMBEDDING_API_URL     = local.batch_embedding_vm_url
    EMBEDDING_TIMEOUT_SEC = tostring(var.pipeline_embedding_timeout_sec)
    EMBEDDING_BATCH_SIZE  = tostring(var.pipeline_embedding_batch_size)
    CHUNK_MAX_TOKENS      = tostring(var.pipeline_chunk_max_tokens)
  }

  secret_env_vars = {
    DATABASE_URL = {
      secret_name = module.secrets.secret_names.database_url
    }
  }

  depends_on = [google_secret_manager_secret_version.database_url]
}

module "feedback_loop_workers" {
  for_each = local.feedback_loop_worker_roles

  source = "../../modules/cloud_run_worker"

  project_id            = var.project_id
  region                = var.region
  service_name          = each.value.service_name
  image_url             = local.service_images["feedback-loop-pipeline"]
  service_account_email = module.iam.service_account_emails["feedback-loop-pipeline"]
  # 풀 가동 시에는 min=1이 정답이다. 지금은 전체 인스턴스를 띄우는 단계가
  # 아니라 비용 절감을 위해 min=0으로 둔다.
  min_instance_count     = 0
  max_instance_count     = var.worker_max_instance_count
  vpc_network_interfaces = local.cloud_run_vpc_network_interfaces

  env_vars = merge(
    local.feedback_loop_common_env,
    {
      APP_ROLE = each.key
    },
  )

  secret_env_vars = {
    DATABASE_URL = {
      secret_name = module.secrets.secret_names.database_url
    }
  }

  depends_on = [google_secret_manager_secret_version.database_url]
}
