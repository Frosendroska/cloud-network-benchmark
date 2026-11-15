mock_provider "google" {}

variables {
  project_id = "cnb-test-12345"
  name_a     = "gcp-campaign-run-abcdef12-a"
  name_b     = "gcp-campaign-run-abcdef12-b"
  zone_a     = "europe-west3-a"
  zone_b     = "europe-west3-b"
  template_a = "projects/cnb-test-12345/global/instanceTemplates/template-a?uniqueId=111"
  template_b = "projects/cnb-test-12345/global/instanceTemplates/template-b?uniqueId=222"
  scenario   = "same_zone"
}

run "exactly_two_inherited_roles" {
  command = plan
  assert {
    condition     = google_compute_instance_from_template.vm_a.source_instance_template == var.template_a && google_compute_instance_from_template.vm_b.source_instance_template == var.template_b
    error_message = "each pair role uses its registered immutable template link"
  }
  assert {
    condition     = google_compute_instance_from_template.vm_a.zone == var.zone_a && google_compute_instance_from_template.vm_b.zone == var.zone_b
    error_message = "the inherited instances retain their selected zones"
  }
  assert {
    condition     = google_compute_instance_from_template.vm_a.name != google_compute_instance_from_template.vm_b.name
    error_message = "pair role names are distinct and provider-valid"
  }
  assert {
    condition     = length([google_compute_instance_from_template.vm_a.id, google_compute_instance_from_template.vm_b.id]) == 2
    error_message = "the pair root creates exactly two inherited VM resources"
  }
  assert {
    condition     = google_compute_instance_from_template.vm_a.network_interface[0].network_ip == "10.80.1.10" && google_compute_instance_from_template.vm_b.network_interface[0].network_ip == "10.80.1.11"
    error_message = "pair NIC overrides retain both role addresses from the registered templates"
  }
}

run "inter_region_pair_uses_role_b_subnet_and_address" {
  command = plan
  variables {
    scenario = "inter_region"
    zone_a   = "europe-west3-a"
    zone_b   = "europe-west2-a"
  }
  assert {
    condition     = google_compute_instance_from_template.vm_b.network_interface[0].network_ip == "10.81.1.11" && google_compute_instance_from_template.vm_b.network_interface[0].subnetwork == "projects/cnb-test-12345/regions/europe-west2/subnetworks/gcp-campaign-run-abcdef12-b"
    error_message = "inter-region VM B retains its dedicated role subnet and address"
  }
}

run "finland_same_zone_inherits_original_templates" {
  command = plan
  variables {
    zone_a = "europe-north1-a"
    zone_b = "europe-north1-a"
  }
  assert {
    condition = (google_compute_instance_from_template.vm_a.project == var.project_id &&
      google_compute_instance_from_template.vm_b.project == var.project_id &&
      google_compute_instance_from_template.vm_a.zone == "europe-north1-a" &&
      google_compute_instance_from_template.vm_b.zone == "europe-north1-a" &&
      google_compute_instance_from_template.vm_a.source_instance_template == var.template_a &&
    google_compute_instance_from_template.vm_b.source_instance_template == var.template_b)
    error_message = "Finland pair must inherit both registered templates in the original project and zone"
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
