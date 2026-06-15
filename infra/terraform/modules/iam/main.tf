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
  service_accounts = {
    "core-api" = {
      display_name = "core-api"
    }
    "search-service" = {
      display_name = "search-service"
    }
    "managed-embedding-endpoint" = {
      display_name = "managed-embedding-endpoint"
    }
    "pipeline-worker" = {
      display_name = "pipeline-worker"
    }
    "feedback-ingestion-pipeline" = {
      display_name = "feedback-ingestion-pipeline"
    }
    "feedback-loop-pipeline" = {
      display_name = "feedback-loop-pipeline"
    }
    "postgres-vm" = {
      display_name = "postgres-vm"
    }
  }

  secret_access_bindings = [
    {
      key       = "core-api-database-url"
      secret_id = var.secret_ids.database_url
      member    = google_service_account.service_accounts["core-api"].member
    },
    {
      key       = "core-api-jwt-secret-key"
      secret_id = var.secret_ids.jwt_secret_key
      member    = google_service_account.service_accounts["core-api"].member
    },
    {
      key       = "search-service-database-url"
      secret_id = var.secret_ids.database_url
      member    = google_service_account.service_accounts["search-service"].member
    },
    {
      key       = "search-service-jwt-secret-key"
      secret_id = var.secret_ids.jwt_secret_key
      member    = google_service_account.service_accounts["search-service"].member
    },
    {
      key       = "managed-embedding-endpoint-database-url"
      secret_id = var.secret_ids.database_url
      member    = google_service_account.service_accounts["managed-embedding-endpoint"].member
    },
    {
      key       = "pipeline-worker-database-url"
      secret_id = var.secret_ids.database_url
      member    = google_service_account.service_accounts["pipeline-worker"].member
    },
    {
      key       = "feedback-loop-pipeline-database-url"
      secret_id = var.secret_ids.database_url
      member    = google_service_account.service_accounts["feedback-loop-pipeline"].member
    },
    {
      key       = "postgres-vm-db-password"
      secret_id = var.secret_ids.db_password
      member    = google_service_account.service_accounts["postgres-vm"].member
    },
  ]

  bucket_access_bindings = [
    {
      key         = "core-api-video-bucket"
      bucket_name = var.bucket_names.video
      role        = "roles/storage.objectAdmin"
      member      = google_service_account.service_accounts["core-api"].member
    },
    {
      key         = "pipeline-worker-video-bucket"
      bucket_name = var.bucket_names.video
      role        = "roles/storage.objectAdmin"
      member      = google_service_account.service_accounts["pipeline-worker"].member
    },
    {
      key         = "feedback-ingestion-pipeline-feedback-log-bucket"
      bucket_name = var.bucket_names.feedback_log
      role        = "roles/storage.objectCreator"
      member      = google_service_account.service_accounts["feedback-ingestion-pipeline"].member
    },
    {
      key         = "feedback-loop-pipeline-feedback-log-viewer"
      bucket_name = var.bucket_names.feedback_log
      role        = "roles/storage.objectViewer"
      member      = google_service_account.service_accounts["feedback-loop-pipeline"].member
    },
    {
      key         = "feedback-loop-pipeline-ml-artifact-writer"
      bucket_name = var.bucket_names.ml_artifact
      role        = "roles/storage.objectCreator"
      member      = google_service_account.service_accounts["feedback-loop-pipeline"].member
    },
    {
      key         = "feedback-loop-pipeline-ml-artifact-viewer"
      bucket_name = var.bucket_names.ml_artifact
      role        = "roles/storage.objectViewer"
      member      = google_service_account.service_accounts["feedback-loop-pipeline"].member
    },
    {
      key         = "managed-embedding-endpoint-ml-artifact-viewer"
      bucket_name = var.bucket_names.ml_artifact
      role        = "roles/storage.objectViewer"
      member      = google_service_account.service_accounts["managed-embedding-endpoint"].member
    },
  ]
}

resource "google_service_account" "service_accounts" {
  for_each = local.service_accounts

  project      = var.project_id
  account_id   = each.key
  display_name = each.value.display_name
}

resource "google_secret_manager_secret_iam_member" "secret_access" {
  for_each = {
    for binding in local.secret_access_bindings : binding.key => binding
  }

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value.member
}

resource "google_storage_bucket_iam_member" "bucket_access" {
  for_each = {
    for binding in local.bucket_access_bindings : binding.key => binding
  }

  bucket = each.value.bucket_name
  role   = each.value.role
  member = each.value.member
}

resource "google_project_iam_member" "managed_embedding_endpoint_artifact_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = google_service_account.service_accounts["managed-embedding-endpoint"].member
}
