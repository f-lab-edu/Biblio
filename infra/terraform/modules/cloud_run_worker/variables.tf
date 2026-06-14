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

variable "command" {
  type    = list(string)
  default = null
}

variable "args" {
  type    = list(string)
  default = null
}

variable "min_instance_count" {
  type    = number
  default = 1
}

variable "max_instance_count" {
  type    = number
  default = 1
}

variable "cpu_idle" {
  type    = bool
  default = false
}

variable "ingress" {
  type    = string
  default = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
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
  default = 3600
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
