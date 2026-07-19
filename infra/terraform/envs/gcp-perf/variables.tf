variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "zone" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "image_tags" {
  type = object({
    core_api                    = string
    search_service              = string
    frontend                    = string
    managed_embedding_endpoint  = string
    pipeline_worker             = string
    feedback_ingestion_pipeline = string
    feedback_loop_pipeline      = string
  })

  description = "Image tag for each independently built application image."
}

variable "frontend_origin" {
  type        = string
  description = "Public frontend origin allowed to upload local video files to the video bucket via browser PUT."
}

variable "database_name" {
  type    = string
  default = "app"
}

variable "database_user" {
  type    = string
  default = "postgres"
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "Postgres password. Used for Secret Manager versions, so the value is also stored in Terraform state."
}

variable "jwt_secret_key" {
  type        = string
  sensitive   = true
  description = "JWT signing secret. Used for Secret Manager versions, so the value is also stored in Terraform state."
}

variable "raw_feedback_log_prefix" {
  type    = string
  default = "feedback/raw_logs"
}

variable "dataset_artifact_prefix" {
  type    = string
  default = "feedback/datasets"
}

variable "model_artifact_prefix" {
  type    = string
  default = "feedback/model-artifacts"
}

variable "model_version_prefix" {
  type    = string
  default = "bge-m3"
}

variable "serving_model_artifact_prefix" {
  type    = string
  default = "models"
}

variable "evaluation_artifact_prefix" {
  type    = string
  default = "feedback/evaluations"
}

variable "local_training_model_name" {
  type    = string
  default = "BAAI/bge-m3"
}

variable "embedding_dimension" {
  type    = number
  default = 1024
}

variable "training_config_path" {
  type    = string
  default = "configs/training/local-smoke.yaml"
}

variable "evaluation_dataset_ref" {
  type    = string
  default = "feedback/evaluation/evaluation_dataset.json"
}

variable "cloud_run_max_instance_count" {
  type    = number
  default = 3
}

variable "worker_max_instance_count" {
  type    = number
  default = 1
}

variable "pipeline_embedding_timeout_sec" {
  type        = number
  default     = 180
  description = "Timeout in seconds for each pipeline-worker embedding HTTP request."

  validation {
    condition = (
      var.pipeline_embedding_timeout_sec > 0 &&
      floor(var.pipeline_embedding_timeout_sec) == var.pipeline_embedding_timeout_sec
    )
    error_message = "pipeline_embedding_timeout_sec must be a positive integer."
  }
}

variable "pipeline_embedding_batch_size" {
  type        = number
  default     = 16
  description = "Maximum enriched texts sent in one pipeline-worker embedding request."

  validation {
    condition = (
      var.pipeline_embedding_batch_size > 0 &&
      floor(var.pipeline_embedding_batch_size) == var.pipeline_embedding_batch_size
    )
    error_message = "pipeline_embedding_batch_size must be a positive integer."
  }
}

variable "cloudrun_subnet_cidr" {
  type    = string
  default = "10.20.1.0/24"
}

variable "postgres_subnet_cidr" {
  type    = string
  default = "10.20.2.0/24"
}

variable "embedding_subnet_cidr" {
  type    = string
  default = "10.20.3.0/24"
}

variable "model_artifact_path" {
  type    = string
  default = "BAAI/bge-m3"
}

variable "model_artifact_root" {
  type    = string
  default = ""
}

variable "enable_managed_embedding_cloud_run" {
  type    = bool
  default = false
}

variable "embedding_vm_machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "embedding_vm_model_disk_size_gb" {
  type    = number
  default = 100
}

variable "embedding_model_artifact_prefix" {
  type    = string
  default = "models"
}

variable "local_model_cache_root" {
  type    = string
  default = "/models"
}
