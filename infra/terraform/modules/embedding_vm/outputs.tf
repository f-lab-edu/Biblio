output "private_ip" {
  value = google_compute_instance.embedding.network_interface[0].network_ip
}

output "instance_name" {
  value = google_compute_instance.embedding.name
}
