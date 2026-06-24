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
