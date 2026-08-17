output "instance_name" {
  value = google_compute_instance.load_test.name
}

output "zone" {
  value = google_compute_instance.load_test.zone
}

output "private_ip" {
  value = google_compute_instance.load_test.network_interface[0].network_ip
}
