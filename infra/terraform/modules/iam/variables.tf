variable "project_id" {
  type = string
}

variable "bucket_names" {
  type = object({
    video       = string
    feedback_log = string
    ml_artifact  = string
  })
}

variable "secret_ids" {
  type = object({
    database_url   = string
    jwt_secret_key = string
    db_password    = string
  })
}
