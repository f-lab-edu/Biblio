terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.30"
    }
  }
}

locals {
  network_name          = "${var.name_prefix}-vpc"
  cloudrun_subnet_name  = "${var.name_prefix}-cloudrun-subnet"
  postgres_subnet_name  = "${var.name_prefix}-postgres-subnet"
  embedding_subnet_name = "${var.name_prefix}-embedding-subnet"
  router_name           = "${var.name_prefix}-router"
  nat_name              = "${var.name_prefix}-nat"
  postgres_network_tag  = "${var.name_prefix}-postgres"
  embedding_network_tag = "${var.name_prefix}-embedding-vm"

  postgres_firewall_name  = "${var.name_prefix}-postgres-allow-cloudrun"
  embedding_firewall_name = "${var.name_prefix}-embedding-allow-cloudrun"
  embedding_iap_ssh_name  = "${var.name_prefix}-embedding-allow-iap-ssh"
}

resource "google_compute_network" "vpc" {
  project                 = var.project_id
  name                    = local.network_name
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "cloudrun" {
  project       = var.project_id
  region        = var.region
  name          = local.cloudrun_subnet_name
  network       = google_compute_network.vpc.id
  ip_cidr_range = var.cloudrun_subnet_cidr
}

resource "google_compute_subnetwork" "postgres" {
  project                  = var.project_id
  region                   = var.region
  name                     = local.postgres_subnet_name
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = var.postgres_subnet_cidr
  private_ip_google_access = true
}

resource "google_compute_subnetwork" "embedding" {
  project                  = var.project_id
  region                   = var.region
  name                     = local.embedding_subnet_name
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = var.embedding_subnet_cidr
  private_ip_google_access = true
}

resource "google_compute_router" "router" {
  project = var.project_id
  region  = var.region
  name    = local.router_name
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  project = var.project_id
  region  = var.region
  name    = local.nat_name
  router  = google_compute_router.router.name

  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.postgres.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  subnetwork {
    name                    = google_compute_subnetwork.embedding.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
}

resource "google_compute_firewall" "postgres_from_cloudrun" {
  project = var.project_id
  name    = local.postgres_firewall_name
  network = google_compute_network.vpc.id

  source_ranges = [
    var.cloudrun_subnet_cidr,
    var.embedding_subnet_cidr,
  ]
  target_tags = [local.postgres_network_tag]

  allow {
    protocol = "tcp"
    ports    = ["5432"]
  }
}

resource "google_compute_firewall" "postgres_ssh_from_iap" {
  project = var.project_id
  name    = "${var.name_prefix}-postgres-allow-iap-ssh"
  network = google_compute_network.vpc.id

  source_ranges = ["35.235.240.0/20"]
  target_tags   = [local.postgres_network_tag]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "embedding_from_cloudrun" {
  project = var.project_id
  name    = local.embedding_firewall_name
  network = google_compute_network.vpc.id

  source_ranges = [var.cloudrun_subnet_cidr]
  target_tags   = [local.embedding_network_tag]

  allow {
    protocol = "tcp"
    ports    = ["8000"]
  }
}

resource "google_compute_firewall" "embedding_ssh_from_iap" {
  project = var.project_id
  name    = local.embedding_iap_ssh_name
  network = google_compute_network.vpc.id

  source_ranges = ["35.235.240.0/20"]
  target_tags   = [local.embedding_network_tag]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
