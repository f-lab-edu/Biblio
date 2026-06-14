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

variable "boot_image" {
  type    = string
  default = "projects/debian-cloud/global/images/family/debian-12"
}

variable "boot_disk_size_gb" {
  type    = number
  default = 50
}

variable "boot_disk_type" {
  type    = string
  default = "pd-ssd"
}

variable "network" {
  type    = string
  default = "default"
}

variable "subnetwork" {
  type    = string
  default = null
}

variable "network_tags" {
  type    = list(string)
  default = []
}

variable "service_account_email" {
  type = string
}

variable "db_password_secret_name" {
  type = string
}

variable "database_name" {
  type    = string
  default = "app"
}

variable "database_user" {
  type    = string
  default = "postgres"
}

variable "postgres_version" {
  type    = string
  default = "16"
}

variable "pgmq_version" {
  type    = string
  default = "v1.10.0"
}

variable "allowed_cidr_blocks" {
  type    = list(string)
  default = ["10.0.0.0/8"]
}
