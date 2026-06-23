variable "project_id" {
  type = string
}

variable "database_url_secret_id" {
  type = string
}

variable "jwt_secret_key_secret_id" {
  type = string
}

variable "db_password_secret_id" {
  type = string
}

variable "jwt_secret_key" {
  type        = string
  sensitive   = true
  description = "JWT signing secret. Stored in Secret Manager and Terraform state."
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "Postgres password. Stored in Secret Manager and Terraform state."
}
