output "network_self_link" {
  value = google_compute_network.vpc.self_link
}

output "network_id" {
  value = google_compute_network.vpc.id
}

output "cloudrun_subnet_self_link" {
  value = google_compute_subnetwork.cloudrun.self_link
}

output "cloudrun_subnet_id" {
  value = google_compute_subnetwork.cloudrun.id
}

output "cloudrun_subnet_cidr" {
  value = google_compute_subnetwork.cloudrun.ip_cidr_range
}

output "postgres_subnet_self_link" {
  value = google_compute_subnetwork.postgres.self_link
}

output "postgres_subnet_cidr" {
  value = google_compute_subnetwork.postgres.ip_cidr_range
}

output "embedding_subnet_self_link" {
  value = google_compute_subnetwork.embedding.self_link
}

output "embedding_subnet_cidr" {
  value = google_compute_subnetwork.embedding.ip_cidr_range
}

output "postgres_network_tag" {
  value = local.postgres_network_tag
}

output "embedding_network_tag" {
  value = local.embedding_network_tag
}

output "load_test_network_tag" {
  value = local.load_test_network_tag
}
