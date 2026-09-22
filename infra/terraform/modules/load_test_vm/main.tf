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
  startup_script = templatefile("${path.module}/startup-k6.sh.tftpl", {
    auto_shutdown_hours = var.auto_shutdown_hours
    k6_version          = var.k6_version
  })
}

resource "google_compute_instance" "load_test" {
  project        = var.project_id
  zone           = var.zone
  name           = var.instance_name
  machine_type   = var.machine_type
  desired_status = "TERMINATED"
  tags           = var.network_tags

  boot_disk {
    auto_delete = true

    initialize_params {
      image = var.boot_image
      size  = var.boot_disk_size_gb
      type  = "pd-standard"
    }
  }

  network_interface {
    network    = var.network
    subnetwork = var.subnetwork
  }

  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
  }

  metadata_startup_script = local.startup_script

  service_account {
    email  = var.service_account_email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  scheduling {
    automatic_restart  = false
    preemptible        = false
    provisioning_model = "STANDARD"
  }

  allow_stopping_for_update = true
  deletion_protection       = false
}
