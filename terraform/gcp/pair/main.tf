resource "terraform_data" "scope_guard" {
  input = var.project_id
  lifecycle {
    precondition {
      condition = contains([
        "europe-west3-a|europe-west3-a",
        "europe-west3-a|europe-west3-b",
        "europe-west3-a|europe-west2-a",
        "europe-north1-a|europe-north1-a",
      ], "${var.zone_a}|${var.zone_b}")
      error_message = "GCP pair zones must match a selected baseline topology or Finland same-zone pair."
    }
  }
}
resource "google_compute_instance_from_template" "vm_a" {
  depends_on               = [terraform_data.scope_guard]
  name                     = var.name_a
  project                  = var.project_id
  zone                     = var.zone_a
  source_instance_template = var.template_a
}
resource "google_compute_instance_from_template" "vm_b" {
  depends_on               = [terraform_data.scope_guard]
  name                     = var.name_b
  project                  = var.project_id
  zone                     = var.zone_b
  source_instance_template = var.template_b
}
