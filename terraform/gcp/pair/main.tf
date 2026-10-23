resource "google_compute_instance_from_template" "vm_a" {
  name                     = var.name_a
  project                  = var.project_id
  zone                     = var.zone_a
  source_instance_template = var.template_a
}
resource "google_compute_instance_from_template" "vm_b" {
  name                     = var.name_b
  project                  = var.project_id
  zone                     = var.zone_b
  source_instance_template = var.template_b
}
