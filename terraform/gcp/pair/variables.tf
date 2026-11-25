variable "project_id" { type = string }
variable "name_a" { type = string }
variable "name_b" { type = string }
variable "zone_a" { type = string }
variable "zone_b" { type = string }
variable "template_a" { type = string }
variable "template_b" { type = string }
variable "scenario" {
  type    = string
  default = ""
}
variable "region_a" {
  type    = string
  default = ""
}
variable "region_b" {
  type    = string
  default = ""
}
variable "network_a" {
  type    = string
  default = ""
}
variable "network_b" {
  type    = string
  default = ""
}
variable "subnetwork_a" {
  type    = string
  default = ""
}
variable "subnetwork_b" {
  type    = string
  default = ""
}
variable "network_ip_a" {
  type    = string
  default = ""
}
variable "network_ip_b" {
  type    = string
  default = ""
}
variable "nic_type_a" {
  type    = string
  default = ""
}
variable "nic_type_b" {
  type    = string
  default = ""
}
variable "public_management_a" {
  type    = bool
  default = false
}
variable "public_management_b" {
  type    = bool
  default = false
}
variable "cleanup_mode" {
  type    = bool
  default = false
}
