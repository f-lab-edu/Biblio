terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.30"
    }
  }
}

resource "google_compute_disk" "model_cache" {
  project = var.project_id
  zone    = var.zone
  name    = "${var.instance_name}-models"
  type    = "pd-balanced"
  size    = var.model_disk_size_gb
}

resource "google_compute_instance" "embedding" {
  project      = var.project_id
  zone         = var.zone
  name         = var.instance_name
  machine_type = var.machine_type
  tags         = var.network_tags

  boot_disk {
    initialize_params {
      image = var.boot_disk_image
      size  = var.boot_disk_size_gb
    }
  }

  attached_disk {
    source      = google_compute_disk.model_cache.id
    device_name = "model-cache"
  }

  network_interface {
    network    = var.network
    subnetwork = var.subnetwork
    network_ip = var.internal_ip
  }

  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
  }

  service_account {
    email = var.service_account_email
    scopes = [
      "https://www.googleapis.com/auth/cloud-platform",
    ]
  }

  metadata_startup_script = templatefile("${path.module}/startup-embedding.sh.tftpl", {
    database_url_secret_name    = var.database_url_secret_name
    gcs_ml_artifact_bucket_name = var.gcs_ml_artifact_bucket_name
    image_url                   = var.image_url
    local_model_cache_root      = var.local_model_cache_root
    model_artifact_path         = var.model_artifact_path
    model_artifact_prefix       = var.model_artifact_prefix
  })
}
