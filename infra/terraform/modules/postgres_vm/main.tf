terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.30"
    }
  }
}

locals {
  startup_script = templatefile("${path.module}/startup-postgres.sh.tftpl", {
    postgres_version        = var.postgres_version
    pgmq_version            = var.pgmq_version
    database_name           = var.database_name
    database_user           = var.database_user
    db_password_secret_name = var.db_password_secret_name
    allowed_cidr_blocks     = var.allowed_cidr_blocks
  })
}

resource "google_compute_instance" "postgres" {
  project      = var.project_id
  zone         = var.zone
  name         = var.instance_name
  machine_type = var.machine_type
  tags         = var.network_tags

  boot_disk {
    initialize_params {
      image = var.boot_image
      size  = var.boot_disk_size_gb
      type  = var.boot_disk_type
    }
  }

  network_interface {
    network    = var.network
    subnetwork = var.subnetwork
  }

  metadata_startup_script = local.startup_script

  service_account {
    email  = var.service_account_email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  allow_stopping_for_update = true
}
