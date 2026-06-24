output "secret_ids" {
  value = {
    database_url   = google_secret_manager_secret.database_url.secret_id
    jwt_secret_key = google_secret_manager_secret.jwt_secret_key.secret_id
    db_password    = google_secret_manager_secret.db_password.secret_id
  }
}

output "secret_names" {
  value = {
    database_url   = google_secret_manager_secret.database_url.name
    jwt_secret_key = google_secret_manager_secret.jwt_secret_key.name
    db_password    = google_secret_manager_secret.db_password.name
  }
}
