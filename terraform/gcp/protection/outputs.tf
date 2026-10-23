output "templates" {
  value = { for role, template in google_compute_instance_template.role : role => { id = template.numeric_id, self_link_unique = template.self_link_unique, name = template.name } }
}
output "network_id" { value = google_compute_network.attempt.id }
output "subnetwork_ids" { value = [google_compute_subnetwork.a.id, try(google_compute_subnetwork.b[0].id, null)] }
output "compact_policy_id" { value = try(google_compute_resource_policy.compact[0].id, null) }
