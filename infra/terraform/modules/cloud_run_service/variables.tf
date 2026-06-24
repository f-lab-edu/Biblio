variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "service_name" {
  type = string
}

variable "image_url" {
  type = string
}

variable "service_account_email" {
  type = string
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

variable "port" {
  type    = number
  default = 8080
}

variable "startup_probe_path" {
  type    = string
  default = null
}

variable "min_instance_count" {
  type    = number
  default = 0
}

variable "cpu_idle" {
  type    = bool
  default = true
}

variable "max_instance_count" {
  type    = number
  default = 100
}

variable "ingress" {
  type    = string
  default = "INGRESS_TRAFFIC_ALL"
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
  default = 300
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
