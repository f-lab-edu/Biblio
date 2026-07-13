output "core_api_url" {
  value = module.core_api.url
}

output "search_service_url" {
  value = module.search_service.url
}

output "frontend_url" {
  value = module.frontend.url
}

output "embedding_endpoint_url" {
  value = local.embedding_vm_url
}

output "managed_embedding_cloud_run_url" {
  value = var.enable_managed_embedding_cloud_run ? module.managed_embedding_endpoint[0].url : null
}

output "embedding_vm_private_ip" {
  value = module.embedding_vm.private_ip
}

output "fip_url" {
  value = module.feedback_ingestion_pipeline.url
}

output "postgres_private_ip" {
  value = module.postgres_vm.private_ip
}

output "database_migration_job_name" {
  value = module.database_migration_job.name
}

output "model_release_seed_job_name" {
  value = module.model_release_seed_job.name
}

output "bucket_names" {
  value = module.object_storage.bucket_names
}
