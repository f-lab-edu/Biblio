output "service_account_emails" {
  value = {
    for name, account in google_service_account.service_accounts : name => account.email
  }
}

output "service_account_names" {
  value = {
    for name, account in google_service_account.service_accounts : name => account.name
  }
}

output "service_account_members" {
  value = {
    for name, account in google_service_account.service_accounts : name => account.member
  }
}
