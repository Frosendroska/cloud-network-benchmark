mock_provider "google" {}

variables {
  project_id = "cnb-test-12345"
  name_a     = "gcp-campaign-run-abcdef12-a"
  name_b     = "gcp-campaign-run-abcdef12-b"
  zone_a     = "europe-west3-a"
  zone_b     = "europe-west3-a"
  template_a = "projects/cnb-test-12345/global/instanceTemplates/template-a?uniqueId=111"
  template_b = "projects/cnb-test-12345/global/instanceTemplates/template-b?uniqueId=222"
  scenario   = "same_zone"
  region_a   = "europe-west3"
  region_b   = "europe-west3"
  network_a  = "projects/cnb-test-12345/global/networks/gcp-campaign-run-abcdef12-net"
  network_b  = "projects/cnb-test-12345/global/networks/gcp-campaign-run-abcdef12-net"
  subnetwork_a = "projects/cnb-test-12345/regions/europe-west3/subnetworks/gcp-campaign-run-abcdef12-a"
  subnetwork_b = "projects/cnb-test-12345/regions/europe-west3/subnetworks/gcp-campaign-run-abcdef12-a"
  network_ip_a = "10.80.1.10"
  network_ip_b = "10.80.1.11"
  nic_type_a = "GVNIC"
  nic_type_b = "GVNIC"
  public_management_a = true
  public_management_b = true
}

run "exactly_two_template_roles_with_selected_nics" {
  command = plan
  assert {
    condition     = google_compute_instance_from_template.vm_a.source_instance_template == var.template_a && google_compute_instance_from_template.vm_b.source_instance_template == var.template_b
    error_message = "each pair role uses its registered immutable template link"
  }
  assert {
    condition     = google_compute_instance_from_template.vm_a.zone == var.zone_a && google_compute_instance_from_template.vm_b.zone == var.zone_b
    error_message = "the instances retain their selected zones"
  }
  assert {
    condition     = google_compute_instance_from_template.vm_a.name != google_compute_instance_from_template.vm_b.name
    error_message = "pair role names are distinct and provider-valid"
  }
  assert {
    condition     = length([google_compute_instance_from_template.vm_a.id, google_compute_instance_from_template.vm_b.id]) == 2
    error_message = "the pair root creates exactly two VM resources"
  }
  assert {
    condition = (
      google_compute_instance_from_template.vm_a.network_interface[0].network_ip == "10.80.1.10" &&
      google_compute_instance_from_template.vm_b.network_interface[0].network_ip == "10.80.1.11" &&
      google_compute_instance_from_template.vm_a.network_interface[0].nic_type == "GVNIC" &&
      google_compute_instance_from_template.vm_b.network_interface[0].nic_type == "GVNIC" &&
      length(google_compute_instance_from_template.vm_a.network_interface[0].access_config) == 1 &&
      length(google_compute_instance_from_template.vm_b.network_interface[0].access_config) == 1
    )
    error_message = "role IPs, gVNIC and public management access are explicitly applied"
  }
}

run "finland_same_zone_retains_template_binding_and_explicit_nics" {
  command = plan
  variables {
    zone_a        = "europe-north1-c"
    zone_b        = "europe-north1-c"
    region_a      = "europe-north1"
    region_b      = "europe-north1"
    subnetwork_a  = "projects/cnb-test-12345/regions/europe-north1/subnetworks/gcp-campaign-run-abcdef12-a"
    subnetwork_b  = "projects/cnb-test-12345/regions/europe-north1/subnetworks/gcp-campaign-run-abcdef12-a"
  }
  assert {
    condition = (google_compute_instance_from_template.vm_a.project == var.project_id &&
      google_compute_instance_from_template.vm_b.project == var.project_id &&
      google_compute_instance_from_template.vm_a.zone == "europe-north1-c" &&
      google_compute_instance_from_template.vm_b.zone == "europe-north1-c" &&
      google_compute_instance_from_template.vm_a.source_instance_template == var.template_a &&
      google_compute_instance_from_template.vm_b.source_instance_template == var.template_b &&
      google_compute_instance_from_template.vm_a.network_interface[0].network_ip == "10.80.1.10" &&
      google_compute_instance_from_template.vm_b.network_interface[0].network_ip == "10.80.1.11")
    error_message = "Finland pair must retain exact templates, zones and role addresses"
  }
}

run "legacy_cleanup_omits_new_override_blocks" {
  command = plan
  variables { cleanup_mode = true }
  assert {
    condition = (length(google_compute_instance_from_template.vm_a.network_interface) == 0 &&
      length(google_compute_instance_from_template.vm_b.network_interface) == 0)
    error_message = "legacy cleanup may destroy from registered templates without rewriting old inputs"
  }
}

run "reject_unreviewed_finland_zone" {
  command = plan
  variables {
    zone_a = "europe-north1-a"
    zone_b = "europe-north1-b"
  }
  expect_failures = [terraform_data.scope_guard]
}

run "reject_wrong_selected_ip" {
  command = plan
  variables { network_ip_a = "10.80.1.2" }
  expect_failures = [terraform_data.scope_guard]
}

run "reject_wrong_selected_subnetwork" {
  command = plan
  variables { subnetwork_a = "projects/cnb-test-12345/regions/europe-west3/subnetworks/foreign" }
  expect_failures = [terraform_data.scope_guard]
}

run "reject_wrong_selected_gvnic" {
  command = plan
  variables { nic_type_a = "VIRTIO_NET" }
  expect_failures = [terraform_data.scope_guard]
}

run "reject_missing_public_management_ipv4" {
  command = plan
  variables { public_management_a = false }
  expect_failures = [terraform_data.scope_guard]
}
