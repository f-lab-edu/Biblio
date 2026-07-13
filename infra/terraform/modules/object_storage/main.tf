terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.30"
    }
  }
}

resource "google_storage_bucket" "video" {
  project  = var.project_id
  name     = var.video_bucket_name
  location = var.location

  uniform_bucket_level_access = true

  dynamic "cors" {
    for_each = length(var.video_bucket_cors_origins) == 0 ? [] : [var.video_bucket_cors_origins]

    content {
      origin          = cors.value
      method          = ["PUT", "GET", "HEAD", "OPTIONS"]
      response_header = ["Content-Type", "x-goog-content-length-range"]
      max_age_seconds = 3600
    }
  }
}

resource "google_storage_bucket" "feedback_log" {
  project  = var.project_id
  name     = var.feedback_log_bucket_name
  location = var.location

  uniform_bucket_level_access = true
}

resource "google_storage_bucket" "ml_artifact" {
  project  = var.project_id
  name     = var.ml_artifact_bucket_name
  location = var.location

  uniform_bucket_level_access = true
}
