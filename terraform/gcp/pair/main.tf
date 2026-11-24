resource "terraform_data" "scope_guard" {
  input = var.project_id
  lifecycle {
    precondition {
      condition = contains([
        "europe-west3-a|europe-west3-a",
        "europe-west3-a|europe-west3-b",
        "europe-west3-a|europe-west2-a",
        "europe-north1-c|europe-north1-c",
      ], "${var.zone_a}|${var.zone_b}")
      error_message = "GCP pair zones must match a selected baseline topology or Finland same-zone pair in zone c."
    }
    precondition {
      condition = var.cleanup_mode || (
        var.region_a == replace(var.zone_a, "/-[a-z]$/", "") &&
        var.region_b == replace(var.zone_b, "/-[a-z]$/", "") &&
        var.network_a == "projects/${var.project_id}/global/networks/${trimsuffix(var.name_a, "-a")}-net" &&
        var.network_b == var.network_a &&
        var.subnetwork_a == "projects/${var.project_id}/regions/${var.region_a}/subnetworks/${trimsuffix(var.name_a, "-a")}-a" &&
        var.subnetwork_b == "projects/${var.project_id}/regions/${var.region_b}/subnetworks/${trimsuffix(var.name_a, "-a")}-${var.scenario == "inter_region" ? "b" : "a"}" &&
        var.network_ip_a == "10.80.1.10" &&
        var.network_ip_b == (var.scenario == "inter_region" ? "10.81.1.11" : "10.80.1.11") &&
        var.nic_type_a == "GVNIC" && var.nic_type_b == "GVNIC" &&
        var.public_management_a && var.public_management_b
      )
      error_message = "GCP pair NICs must retain the selected private topology, fixed role addresses, gVNIC, and public management IPv4."
    }
  }
}
resource "google_compute_instance_from_template" "vm_a" {
  depends_on               = [terraform_data.scope_guard]
  name                     = var.name_a
  project                  = var.project_id
  zone                     = var.zone_a
  source_instance_template = var.template_a
  dynamic "network_interface" {
    for_each = var.cleanup_mode ? [] : [true]
    content {
      network    = var.network_a
      subnetwork = var.subnetwork_a
      network_ip = var.network_ip_a
      nic_type   = var.nic_type_a
      dynamic "access_config" {
        for_each = var.public_management_a ? [true] : []
        content {}
      }
    }
  }
}
resource "google_compute_instance_from_template" "vm_b" {
  depends_on               = [terraform_data.scope_guard]
  name                     = var.name_b
  project                  = var.project_id
  zone                     = var.zone_b
  source_instance_template = var.template_b
  dynamic "network_interface" {
    for_each = var.cleanup_mode ? [] : [true]
    content {
      network    = var.network_b
      subnetwork = var.subnetwork_b
      network_ip = var.network_ip_b
      nic_type   = var.nic_type_b
      dynamic "access_config" {
        for_each = var.public_management_b ? [true] : []
        content {}
      }
    }
  }
}
