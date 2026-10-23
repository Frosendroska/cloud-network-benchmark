mock_provider "google" {}

variables {
  project_id         = "cnb-test-12345"
  namespace          = "gcp-campaign-run-abcdef12"
  scenario           = "same_zone"
  expiry_time        = "2026-10-23T18:00:00Z"
  image_a            = "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001"
  image_b            = "projects/ubuntu-os-cloud/global/images/ubuntu-2404-lts-amd64-v20261001"
  zone_a             = "europe-west3-a"
  zone_b             = "europe-west3-a"
  controller_cidr    = "192.0.2.4/32"
  public_key         = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA test"
  labels             = { cnb-run = "11111111111111111111", cnb-deploy = "22222222222222222222" }
  bootstrap_template = "bootstrap/cloud-init.yaml"
  bootstrap_inputs   = { mtu = 1460 }
  network_intent     = { tcp_congestion_control = "cubic", mtu = 1460, dscp = 0, ecn = false, default_offloads = true }
}

run "same_zone" {
  command = plan
  assert {
    condition     = length(google_compute_instance_template.role) == 2
    error_message = "protection must create exactly two role-specific templates"
  }
  assert {
    condition     = local.ips == ["10.80.1.10", "10.80.1.11"] && local.subnet_a == "10.80.1.0/24"
    error_message = "same-zone roles must share the approved Frankfurt subnet and addresses"
  }
  assert {
    condition     = google_compute_instance_template.role["vm_a"].machine_type == "n2-standard-4" && google_compute_instance_template.role["vm_b"].machine_type == "n2-standard-4"
    error_message = "both role templates use the selected shape"
  }
  assert {
    condition     = google_compute_instance_template.role["vm_a"].scheduling[0].instance_termination_action == "STOP" && google_compute_instance_template.role["vm_a"].scheduling[0].termination_time == var.expiry_time
    error_message = "absolute STOP is registered before either VM is created"
  }
  assert {
    condition     = google_compute_instance_template.role["vm_a"].reservation_affinity[0].type == "NO_RESERVATION"
    error_message = "template scheduling cannot consume an unrelated reservation"
  }
  assert {
    condition     = google_compute_instance_template.role["vm_a"].metadata["enable-guest-attributes"] == "TRUE" && google_compute_instance_template.role["vm_a"].metadata["block-project-ssh-keys"] == "TRUE"
    error_message = "template carries the selected guest host-key and project-key controls"
  }
}

run "cross_zone" {
  command = plan
  variables {
    scenario = "cross_zone"
    zone_b   = "europe-west3-b"
  }
  assert {
    condition     = local.ips == ["10.80.1.10", "10.80.1.11"] && local.subnet_b == "10.81.1.0/24"
    error_message = "S2 changes zones only and keeps both roles in the single Frankfurt subnet"
  }
  assert {
    condition     = length(google_compute_subnetwork.b) == 0
    error_message = "S2 must not create a second subnet"
  }
}

run "placement_optimization" {
  command = plan
  variables {
    scenario = "placement_optimization"
  }
  assert {
    condition     = length(google_compute_resource_policy.compact) == 1 && local.ips == ["10.80.1.10", "10.80.1.11"]
    error_message = "S3 adds only its compact placement policy to the same-zone subnet pair"
  }
}

run "inter_region" {
  command = plan
  variables {
    scenario = "inter_region"
    zone_b   = "europe-west2-a"
  }
  assert {
    condition     = length(google_compute_subnetwork.b) == 1 && local.subnet_a == "10.80.1.0/24" && local.subnet_b == "10.81.1.0/24"
    error_message = "S4 uses distinct regional subnets"
  }
  assert {
    condition     = local.ips == ["10.80.1.10", "10.81.1.11"]
    error_message = "S4 assigns the selected Frankfurt and London role addresses"
  }
}
