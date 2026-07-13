output "bucket_names" {
  value = {
    video        = google_storage_bucket.video.name
    feedback_log = google_storage_bucket.feedback_log.name
    ml_artifact  = google_storage_bucket.ml_artifact.name
  }
}
