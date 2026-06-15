locals {
  repository_id = "biblio"

  image_registry = "${var.region}-docker.pkg.dev/${var.project_id}/${local.repository_id}"

  service_images = {
    "core-api"                    = "${local.image_registry}/core-api:${var.image_tag}"
    "search-service"              = "${local.image_registry}/search-service:${var.image_tag}"
    "managed-embedding-endpoint"  = "${local.image_registry}/managed-embedding-endpoint:${var.image_tag}"
    "pipeline-worker"             = "${local.image_registry}/pipeline-worker:${var.image_tag}"
    "feedback-ingestion-pipeline" = "${local.image_registry}/feedback-ingestion-pipeline:${var.image_tag}"
    "feedback-loop-pipeline"      = "${local.image_registry}/feedback-loop-pipeline:${var.image_tag}"
  }

  bucket_names = {
    video        = "${var.name_prefix}-video"
    feedback_log = "${var.name_prefix}-feedback-log"
    ml_artifact  = "${var.name_prefix}-ml-artifact"
  }

  database_url = "postgresql+asyncpg://${urlencode(var.database_user)}:${urlencode(var.db_password)}@${module.postgres_vm.private_ip}:5432/${urlencode(var.database_name)}"

  embedding_vm_url = "http://${module.embedding_vm.private_ip}:8000"

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
    BROKER_TYPE                    = "pgmq"
    ARTIFACT_STORE_BACKEND         = "gcs"
    GCS_FEEDBACK_LOG_BUCKET_NAME   = module.object_storage.bucket_names.feedback_log
    GCS_ML_ARTIFACT_BUCKET_NAME    = module.object_storage.bucket_names.ml_artifact
    RAW_FEEDBACK_LOG_PREFIX        = var.raw_feedback_log_prefix
    DATASET_ARTIFACT_PREFIX        = var.dataset_artifact_prefix
    MODEL_ARTIFACT_PREFIX          = var.model_artifact_prefix
    EVALUATION_ARTIFACT_PREFIX     = var.evaluation_artifact_prefix
    MANAGED_EMBEDDING_ENDPOINT_URL = local.embedding_vm_url
    SEARCH_SERVICE_URL             = module.search_service.url
    LOCAL_TRAINING_MODEL_NAME      = var.local_training_model_name
    EMBEDDING_DIMENSION            = tostring(var.embedding_dimension)
    TRAINING_CONFIG_PATH           = var.training_config_path
    EVALUATION_DATASET_REF         = var.evaluation_dataset_ref
  }

  cloud_run_vpc_network_interfaces = [
    {
      network    = module.network.network_self_link
      subnetwork = module.network.cloudrun_subnet_self_link
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

  project_id               = var.project_id
  location                 = var.region
  video_bucket_name        = local.bucket_names.video
  feedback_log_bucket_name = local.bucket_names.feedback_log
  ml_artifact_bucket_name  = local.bucket_names.ml_artifact
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
  allowed_cidr_blocks     = [module.network.cloudrun_subnet_cidr]

  depends_on = [module.network, module.iam, module.secrets]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = module.secrets.secret_names.database_url
  secret_data = local.database_url
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

  project_id                  = var.project_id
  zone                        = var.zone
  instance_name               = "${var.name_prefix}-embedding"
  machine_type                = var.embedding_vm_machine_type
  network                     = module.network.network_self_link
  subnetwork                  = module.network.embedding_subnet_self_link
  network_tags                = [module.network.embedding_network_tag]
  service_account_email       = module.iam.service_account_emails["managed-embedding-endpoint"]
  image_url                   = local.service_images["managed-embedding-endpoint"]
  database_url_secret_name    = module.secrets.secret_names.database_url
  gcs_ml_artifact_bucket_name = module.object_storage.bucket_names.ml_artifact
  model_artifact_path         = var.model_artifact_path
  model_artifact_prefix       = var.embedding_model_artifact_prefix
  local_model_cache_root      = var.local_model_cache_root
  model_disk_size_gb          = var.embedding_vm_model_disk_size_gb

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
    EMBEDDING_API_URL = local.embedding_vm_url
    GCP_LOCATION      = "us-central1"
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
  # 인스턴스를 상시 1개 유지하고 CPU를 항상 할당해야 flush가 지연·누락되지 않는다.
  min_instance_count = 1
  cpu_idle           = false

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

module "pipeline_worker" {
  source = "../../modules/cloud_run_worker"

  project_id             = var.project_id
  region                 = var.region
  service_name           = "pipeline-worker"
  image_url              = local.service_images["pipeline-worker"]
  service_account_email  = module.iam.service_account_emails["pipeline-worker"]
  min_instance_count     = 1
  max_instance_count     = var.worker_max_instance_count
  vpc_network_interfaces = local.cloud_run_vpc_network_interfaces

  env_vars = {
    BROKER_TYPE           = "pgmq"
    GCP_PROJECT_ID        = var.project_id
    GCP_LOCATION          = "us-central1"
    GCS_VIDEO_BUCKET_NAME = module.object_storage.bucket_names.video
    EMBEDDING_API_URL     = local.embedding_vm_url
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

  project_id             = var.project_id
  region                 = var.region
  service_name           = each.value.service_name
  image_url              = local.service_images["feedback-loop-pipeline"]
  service_account_email  = module.iam.service_account_emails["feedback-loop-pipeline"]
  min_instance_count     = 1
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
