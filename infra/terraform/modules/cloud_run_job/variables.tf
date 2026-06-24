variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "job_name" {
  type = string
}

variable "image_url" {
  type = string
}

variable "service_account_email" {
  type = string
}

variable "command" {
  type = list(string)
}

variable "args" {
  type    = list(string)
  default = []
}

variable "working_dir" {
  type    = string
  default = null
}

variable "env_vars" {
  type    = map(string)
  default = {}
}

variable "secret_env_vars" {
  type = map(object({
    secret_name = string
    version     = optional(string, "latest")
  }))
  default = {}
}

variable "cpu" {
  type    = string
  default = "1"
}

variable "memory" {
  type    = string
  default = "512Mi"
}

variable "timeout_seconds" {
  type    = number
  default = 900
}

variable "max_retries" {
  type    = number
  default = 0
}

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "vpc_network_interfaces" {
  type = list(object({
    network    = string
    subnetwork = string
    tags       = optional(list(string), [])
  }))
  default = []
}

variable "vpc_egress" {
  type    = string
  default = "PRIVATE_RANGES_ONLY"
}
