variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "cloudrun_subnet_cidr" {
  type    = string
  default = "10.20.1.0/24"
}

variable "postgres_subnet_cidr" {
  type    = string
  default = "10.20.2.0/24"
}
