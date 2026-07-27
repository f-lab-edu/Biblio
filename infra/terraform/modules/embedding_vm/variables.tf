variable "project_id" {
  type = string
}

variable "zone" {
  type = string
}

variable "instance_name" {
  type = string
}

variable "machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "network" {
  type = string
}

variable "subnetwork" {
  type = string
}

variable "network_tags" {
  type    = list(string)
  default = []
}

variable "internal_ip" {
  type        = string
  default     = null
  description = "고정 내부 IP. null이면 GCP가 자동 할당(교체 시 바뀔 수 있음). 값을 지정하면 VM 교체에도 IP가 유지돼 참조 서비스 재배포가 불필요하다."
}

variable "service_account_email" {
  type = string
}

variable "image_url" {
  type = string
}

variable "database_url_secret_name" {
  type = string
}

variable "gcs_ml_artifact_bucket_name" {
  type = string
}

variable "model_artifact_path" {
  type = string
}

variable "model_artifact_prefix" {
  type    = string
  default = "models"
}

variable "local_model_cache_root" {
  type    = string
  default = "/models"
}

variable "enable_warp_proxy" {
  type    = bool
  default = true
}

variable "max_concurrency" {
  type    = number
  default = 1

  validation {
    condition = (
      var.max_concurrency > 0 &&
      floor(var.max_concurrency) == var.max_concurrency
    )
    error_message = "max_concurrency must be a positive integer."
  }
}

variable "search_request_limit" {
  type = number
}

variable "video_preprocess_request_limit" {
  type = number
}

variable "search_wait_timeout_sec" {
  type = number
}

variable "video_preprocess_wait_timeout_sec" {
  type = number
}

variable "boot_disk_image" {
  type    = string
  default = "debian-cloud/debian-12"
}

variable "boot_disk_size_gb" {
  type    = number
  default = 30
}

variable "model_disk_size_gb" {
  type    = number
  default = 100
}
