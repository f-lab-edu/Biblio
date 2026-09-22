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
  default = "e2-medium"
}

variable "boot_image" {
  type    = string
  default = "projects/debian-cloud/global/images/family/debian-12"
}

variable "boot_disk_size_gb" {
  type    = number
  default = 10

  validation {
    condition     = var.boot_disk_size_gb >= 10 && floor(var.boot_disk_size_gb) == var.boot_disk_size_gb
    error_message = "boot_disk_size_gb must be an integer of at least 10."
  }
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

variable "k6_version" {
  type    = string
  default = "2.0.0"

  validation {
    condition     = can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+$", var.k6_version))
    error_message = "k6_version must use a numeric semantic version without a v prefix."
  }
}

variable "auto_shutdown_hours" {
  type    = number
  default = 4

  validation {
    condition     = var.auto_shutdown_hours > 0 && floor(var.auto_shutdown_hours) == var.auto_shutdown_hours
    error_message = "auto_shutdown_hours must be a positive integer."
  }
}
