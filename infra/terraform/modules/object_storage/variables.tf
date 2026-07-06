variable "project_id" {
  type = string
}

variable "location" {
  type = string
}

variable "video_bucket_name" {
  type = string
}

variable "video_bucket_cors_origins" {
  type    = list(string)
  default = []
}

variable "feedback_log_bucket_name" {
  type = string
}

variable "ml_artifact_bucket_name" {
  type = string
}
