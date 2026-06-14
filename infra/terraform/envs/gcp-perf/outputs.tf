output "core_api_url" {
  value = module.core_api.url
}

output "search_service_url" {
  value = module.search_service.url
}

output "embedding_endpoint_url" {
  value = module.managed_embedding_endpoint.url
}

output "fip_url" {
  value = module.feedback_ingestion_pipeline.url
}

output "postgres_private_ip" {
  value = module.postgres_vm.private_ip
}

output "bucket_names" {
  value = module.object_storage.bucket_names
}
