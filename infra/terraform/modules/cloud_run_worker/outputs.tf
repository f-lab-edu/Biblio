output "service_name" {
  value = google_cloud_run_v2_service.worker.name
}

output "service_uri" {
  value = google_cloud_run_v2_service.worker.uri
}

output "service_url" {
  value = google_cloud_run_v2_service.worker.uri
}
