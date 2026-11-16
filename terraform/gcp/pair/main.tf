resource "terraform_data" "scope_guard" {
  input = var.project_id
  lifecycle {
    precondition {
      condition = contains([
        "europe-west3-a|europe-west3-a",
        "europe-west3-a|europe-west3-b",
        "europe-west3-a|europe-west2-a",
        "europe-north1-a|europe-north1-a",
        "europe-north1-b|europe-north1-b",
        "europe-north1-c|europe-north1-c",
      ], "${var.zone_a}|${var.zone_b}")
      error_message = "GCP pair zones must match a selected baseline topology or Finland same-zone pair."
    }
  }
}
locals {
  namespace = trimsuffix(var.name_a, "-a")
  region_a  = join("-", slice(split("-", var.zone_a), 0, length(split("-", var.zone_a)) - 1))
  region_b  = join("-", slice(split("-", var.zone_b), 0, length(split("-", var.zone_b)) - 1))
  ips       = var.scenario == "inter_region" ? ["10.80.1.10", "10.81.1.11"] : ["10.80.1.10", "10.80.1.11"]
  subnet_b  = var.scenario == "inter_region" ? "b" : "a"
}
resource "google_compute_instance_from_template" "vm_a" {
  depends_on               = [terraform_data.scope_guard]
  name                     = var.name_a
  project                  = var.project_id
  zone                     = var.zone_a
  source_instance_template = var.template_a
  network_interface {
    network    = "projects/${var.project_id}/global/networks/${local.namespace}-net"
    subnetwork = "projects/${var.project_id}/regions/${local.region_a}/subnetworks/${local.namespace}-a"
    network_ip = local.ips[0]
    nic_type   = "GVNIC"
    access_config {}
  }
}
resource "google_compute_instance_from_template" "vm_b" {
  depends_on               = [terraform_data.scope_guard]
  name                     = var.name_b
  project                  = var.project_id
  zone                     = var.zone_b
  source_instance_template = var.template_b
  network_interface {
    network    = "projects/${var.project_id}/global/networks/${local.namespace}-net"
    subnetwork = "projects/${var.project_id}/regions/${local.region_b}/subnetworks/${local.namespace}-${local.subnet_b}"
    network_ip = local.ips[1]
    nic_type   = "GVNIC"
    access_config {}
  }
}
