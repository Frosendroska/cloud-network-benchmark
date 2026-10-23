locals {
  subnet_a = var.scenario == "inter_region" ? "10.80.1.0/24" : "10.80.1.0/24"
  subnet_b = var.scenario == "inter_region" ? "10.81.1.0/24" : var.scenario == "cross_zone" ? "10.80.2.0/24" : "10.80.1.0/24"
  region_a = join("-", slice(split("-", var.zone_a), 0, length(split("-", var.zone_a)) - 1))
  region_b = join("-", slice(split("-", var.zone_b), 0, length(split("-", var.zone_b)) - 1))
  ips      = var.scenario == "cross_zone" ? ["10.80.1.10", "10.80.2.10"] : var.scenario == "inter_region" ? ["10.80.1.10", "10.81.1.11"] : ["10.80.1.10", "10.80.1.11"]
}
resource "google_compute_network" "attempt" {
  name                    = "${var.namespace}-net"
  auto_create_subnetworks = false
  mtu                     = 1460
}
resource "google_compute_subnetwork" "a" {
  name          = "${var.namespace}-a"
  region        = local.region_a
  network       = google_compute_network.attempt.id
  ip_cidr_range = local.subnet_a
}
resource "google_compute_subnetwork" "b" {
  count         = var.scenario == "inter_region" || var.scenario == "cross_zone" ? 1 : 0
  name          = "${var.namespace}-b"
  region        = local.region_b
  network       = google_compute_network.attempt.id
  ip_cidr_range = local.subnet_b
}
resource "google_compute_firewall" "ssh" {
  name          = "${var.namespace}-ssh"
  network       = google_compute_network.attempt.name
  direction     = "INGRESS"
  source_ranges = [var.controller_cidr]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
resource "google_compute_firewall" "listeners" {
  name          = "${var.namespace}-listeners"
  network       = google_compute_network.attempt.name
  direction     = "INGRESS"
  source_ranges = [local.ips[0] == "10.80.1.10" ? "10.80.1.10/32" : "10.80.1.10/32"]
  target_tags   = ["${var.namespace}-vm-b"]
  allow {
    protocol = "tcp"
    ports    = ["5201"]
  }
  allow {
    protocol = "udp"
    ports    = ["11111"]
  }
}
resource "google_compute_firewall" "pair_icmp" {
  name          = "${var.namespace}-icmp"
  network       = google_compute_network.attempt.name
  direction     = "INGRESS"
  source_ranges = ["10.80.1.10/32", "10.80.2.10/32", "10.81.1.10/32"]
  allow {
    protocol = "icmp"
  }
}
resource "google_compute_resource_policy" "compact" {
  count  = var.scenario == "placement_optimization" ? 1 : 0
  name   = "${var.namespace}-compact"
  region = local.region_a
  group_placement_policy {
    collocation = "COLLOCATED"
  }
}
resource "google_compute_instance_template" "role" {
  for_each       = { vm_a = { image = var.image_a, ip = local.ips[0], tag = "${var.namespace}-vm-a" }, vm_b = { image = var.image_b, ip = local.ips[1], tag = "${var.namespace}-vm-b" } }
  name_prefix    = "${var.namespace}-${each.key}-"
  machine_type   = "n2-standard-4"
  can_ip_forward = false
  tags           = [each.value.tag]
  labels         = var.labels
  network_interface {
    subnetwork = each.key == "vm_b" && length(google_compute_subnetwork.b) == 1 ? google_compute_subnetwork.b[0].id : google_compute_subnetwork.a.id
    network_ip = each.value.ip
    nic_type   = "GVNIC"
    access_config {}
  }
  disk {
    boot         = true
    auto_delete  = true
    source_image = each.value.image
    disk_type    = "pd-balanced"
    disk_size_gb = 32
  }
  scheduling {
    provisioning_model          = "STANDARD"
    automatic_restart           = false
    instance_termination_action = "STOP"
    termination_time            = var.expiry_time
  }
  resource_policies = length(google_compute_resource_policy.compact) == 1 ? [google_compute_resource_policy.compact[0].id] : null
  metadata          = { ssh-keys = "benchmark:${var.public_key}" }
}
