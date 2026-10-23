variable "project_id" { type = string }
variable "namespace" { type = string }
variable "scenario" { type = string }
variable "expiry_time" { type = string }
variable "image_a" { type = string }
variable "image_b" { type = string }
variable "zone_a" { type = string }
variable "zone_b" { type = string }
variable "controller_cidr" { type = string }
variable "public_key" {
  type      = string
  sensitive = true
}
variable "labels" { type = map(string) }
variable "bootstrap_template" { type = string }
variable "bootstrap_inputs" { type = any }
variable "network_intent" { type = any }
