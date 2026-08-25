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

variable "search_embedding_timeout_sec" {
  type        = number
  default     = 15
  description = "Timeout in seconds for each search embedding HTTP request."

  validation {
    condition = (
      var.search_embedding_timeout_sec > 0 &&
      floor(var.search_embedding_timeout_sec) == var.search_embedding_timeout_sec
    )
    error_message = "search_embedding_timeout_sec must be a positive integer."
  }
}

variable "embedding_search_request_limit" {
  type        = number
  default     = 32
  description = "Maximum admitted search embedding requests."

  validation {
    condition = (
      var.embedding_search_request_limit > 0 &&
      floor(var.embedding_search_request_limit) == var.embedding_search_request_limit
    )
    error_message = "embedding_search_request_limit must be a positive integer."
  }
}

variable "embedding_video_preprocess_request_limit" {
  type        = number
  default     = 4
  description = "Maximum admitted video preprocessing embedding requests."

  validation {
    condition = (
      var.embedding_video_preprocess_request_limit > 0 &&
      floor(var.embedding_video_preprocess_request_limit) == var.embedding_video_preprocess_request_limit
    )
    error_message = "embedding_video_preprocess_request_limit must be a positive integer."
  }
}

variable "embedding_search_wait_timeout_sec" {
  type        = number
  default     = 5
  description = "Maximum server-side slot wait for search embedding requests."

  validation {
    condition     = var.embedding_search_wait_timeout_sec > 0
    error_message = "embedding_search_wait_timeout_sec must be positive."
  }
}

variable "embedding_video_preprocess_wait_timeout_sec" {
  type        = number
  default     = 300
  description = "Maximum server-side slot wait for video preprocessing requests."

  validation {
    condition     = var.embedding_video_preprocess_wait_timeout_sec > 0
    error_message = "embedding_video_preprocess_wait_timeout_sec must be positive."
  }
}

variable "pipeline_embedding_timeout_sec" {
  type        = number
  default     = 450
  description = "Timeout in seconds for each pipeline-worker embedding HTTP request."

  validation {
    condition = (
      var.pipeline_embedding_timeout_sec > 0 &&
      floor(var.pipeline_embedding_timeout_sec) == var.pipeline_embedding_timeout_sec
    )
    error_message = "pipeline_embedding_timeout_sec must be a positive integer."
  }
}

variable "pipeline_embedding_queue_visibility_timeout_sec" {
  type        = number
  default     = 1800
  description = "Queue visibility timeout in seconds for each embedding stage delivery."

  validation {
    condition = (
      var.pipeline_embedding_queue_visibility_timeout_sec > var.pipeline_embedding_timeout_sec &&
      floor(var.pipeline_embedding_queue_visibility_timeout_sec) == var.pipeline_embedding_queue_visibility_timeout_sec
    )
    error_message = "pipeline_embedding_queue_visibility_timeout_sec must be an integer greater than pipeline_embedding_timeout_sec."
  }
}

variable "pipeline_embedding_batch_size" {
  type        = number
  default     = 4
  description = "Maximum enriched texts sent in one pipeline-worker embedding request."

  validation {
    condition = (
      var.pipeline_embedding_batch_size > 0 &&
      floor(var.pipeline_embedding_batch_size) == var.pipeline_embedding_batch_size
    )
    error_message = "pipeline_embedding_batch_size must be a positive integer."
  }
}

variable "pipeline_chunk_max_tokens" {
  type        = number
  default     = 300
  description = "Target maximum whitespace-delimited words per pipeline chunk; sentence boundaries may exceed it."

  validation {
    condition = (
      var.pipeline_chunk_max_tokens > 0 &&
      floor(var.pipeline_chunk_max_tokens) == var.pipeline_chunk_max_tokens
    )
    error_message = "pipeline_chunk_max_tokens must be a positive integer."
  }
}

variable "pipeline_frame_extraction_concurrency" {
  type        = number
  default     = 2
  description = "Maximum concurrent FFmpeg frame seeks within one normalization job."

  validation {
    condition = (
      var.pipeline_frame_extraction_concurrency > 0 &&
      floor(var.pipeline_frame_extraction_concurrency) == var.pipeline_frame_extraction_concurrency
    )
    error_message = "pipeline_frame_extraction_concurrency must be a positive integer."
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

variable "embedding_batch_max_concurrency" {
  type    = number
  default = 2

  validation {
    condition = (
      var.embedding_batch_max_concurrency > 0 &&
      floor(var.embedding_batch_max_concurrency) == var.embedding_batch_max_concurrency
    )
    error_message = "embedding_batch_max_concurrency must be a positive integer."
  }
}

variable "embedding_batch_inference_threads" {
  type    = number
  default = 1

  validation {
    condition = (
      var.embedding_batch_inference_threads > 0 &&
      floor(var.embedding_batch_inference_threads) == var.embedding_batch_inference_threads
    )
    error_message = "embedding_batch_inference_threads must be a positive integer."
  }
}

variable "embedding_batch_max_length" {
  type        = number
  default     = 1024
  description = "Maximum tokenizer sequence length for batch embedding; sized to preserve the measured enriched chunk distribution."

  validation {
    condition = (
      var.embedding_batch_max_length > 0 &&
      floor(var.embedding_batch_max_length) == var.embedding_batch_max_length
    )
    error_message = "embedding_batch_max_length must be a positive integer."
  }
}

variable "embedding_search_max_concurrency" {
  type    = number
  default = 2

  validation {
    condition = (
      var.embedding_search_max_concurrency > 0 &&
      floor(var.embedding_search_max_concurrency) == var.embedding_search_max_concurrency
    )
    error_message = "embedding_search_max_concurrency must be a positive integer."
  }
}

variable "embedding_search_inference_threads" {
  type    = number
  default = 1

  validation {
    condition = (
      var.embedding_search_inference_threads > 0 &&
      floor(var.embedding_search_inference_threads) == var.embedding_search_inference_threads
    )
    error_message = "embedding_search_inference_threads must be a positive integer."
  }
}

variable "search_embedding_cutover_enabled" {
  type        = bool
  default     = false
  description = "검색 VM 준비 확인 후 true로 바꿔 search-service와 feedback-loop 검색 경로를 전환한다."
}

variable "embedding_model_artifact_prefix" {
  type    = string
  default = "models"
}

variable "local_model_cache_root" {
  type    = string
  default = "/models"
}

variable "load_test_vm_machine_type" {
  type        = string
  default     = "e2-medium"
  description = "Machine type for the dedicated k6 load generator."
}

variable "load_test_vm_disk_size_gb" {
  type        = number
  default     = 10
  description = "Size of the k6 runner pd-standard boot disk."

  validation {
    condition     = var.load_test_vm_disk_size_gb >= 10 && floor(var.load_test_vm_disk_size_gb) == var.load_test_vm_disk_size_gb
    error_message = "load_test_vm_disk_size_gb must be an integer of at least 10."
  }
}

variable "load_test_k6_version" {
  type        = string
  default     = "2.0.0"
  description = "Pinned k6 version without a v prefix."

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.load_test_k6_version))
    error_message = "load_test_k6_version must use a numeric semantic version without a v prefix."
  }
}

variable "load_test_auto_shutdown_hours" {
  type        = number
  default     = 4
  description = "Hours after boot before the k6 runner powers itself off."

  validation {
    condition     = var.load_test_auto_shutdown_hours > 0 && floor(var.load_test_auto_shutdown_hours) == var.load_test_auto_shutdown_hours
    error_message = "load_test_auto_shutdown_hours must be a positive integer."
  }
}
