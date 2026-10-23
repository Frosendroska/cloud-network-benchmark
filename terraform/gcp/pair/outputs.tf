output "instances" {
  value = {
    vm_a = { id = google_compute_instance_from_template.vm_a.instance_id, self_link = google_compute_instance_from_template.vm_a.self_link, name = google_compute_instance_from_template.vm_a.name, zone = google_compute_instance_from_template.vm_a.zone }
    vm_b = { id = google_compute_instance_from_template.vm_b.instance_id, self_link = google_compute_instance_from_template.vm_b.self_link, name = google_compute_instance_from_template.vm_b.name, zone = google_compute_instance_from_template.vm_b.zone }
  }
}
