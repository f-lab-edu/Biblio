output "instance_name" {
  value = google_compute_instance.postgres.name
}

output "private_ip" {
  value = google_compute_instance.postgres.network_interface[0].network_ip
}

output "database_host_reference" {
  value = google_compute_instance.postgres.network_interface[0].network_ip
}
