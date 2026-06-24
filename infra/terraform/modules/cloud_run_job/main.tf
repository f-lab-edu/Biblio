terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.30"
    }
  }
}

resource "google_cloud_run_v2_job" "job" {
  project  = var.project_id
  location = var.region
  name     = var.job_name

  deletion_protection = var.deletion_protection

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = var.service_account_email
      timeout         = "${var.timeout_seconds}s"
      max_retries     = var.max_retries

      dynamic "vpc_access" {
        for_each = length(var.vpc_network_interfaces) == 0 ? [] : [true]

        content {
          egress = var.vpc_egress

          dynamic "network_interfaces" {
            for_each = var.vpc_network_interfaces

            content {
              network    = network_interfaces.value.network
              subnetwork = network_interfaces.value.subnetwork
              tags       = network_interfaces.value.tags
            }
          }
        }
      }

      containers {
        image       = var.image_url
        command     = var.command
        args        = var.args
        working_dir = var.working_dir

        resources {
          limits = {
            cpu    = var.cpu
            memory = var.memory
          }
        }

        dynamic "env" {
          for_each = var.env_vars

          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = var.secret_env_vars

          content {
            name = env.key

            value_source {
              secret_key_ref {
                secret  = env.value.secret_name
                version = env.value.version
              }
            }
          }
        }
      }
    }
  }
}
