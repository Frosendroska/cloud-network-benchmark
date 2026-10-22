variable "subscription_id" { type = string }
variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "deployment_id" { type = string }
variable "config_sha256" { type = string }
variable "ssh_public_key" {
  type      = string
  sensitive = true
}

variable "selected_image" {
  type = string
  validation {
    condition     = startswith(var.selected_image, "/") || (length(split(":", var.selected_image)) == 4 && !can(regex("(?i)latest", var.selected_image)))
    error_message = "Select a concrete gallery image ID or four-part publisher:offer:sku:version; latest aliases are not reproducible."
  }
}

variable "placement_group_name" {
  type    = string
  default = ""
}

variable "scenario" {
  type = string
  validation {
    condition     = contains(["same_zone", "cross_zone", "placement_optimization", "inter_region"], var.scenario)
    error_message = "Unsupported scenario."
  }
  validation {
    condition = (
      (var.scenario == "same_zone" && var.region_a == var.region_b && var.zone_a == var.zone_b && !var.create_ppg && !var.create_peering && var.placement_group_name == "") ||
      (var.scenario == "cross_zone" && var.region_a == var.region_b && var.zone_a != var.zone_b && !var.create_ppg && !var.create_peering && var.placement_group_name == "") ||
      (var.scenario == "placement_optimization" && var.region_a == var.region_b && var.zone_a == var.zone_b && var.create_ppg && !var.create_peering && var.placement_group_name != "") ||
      (var.scenario == "inter_region" && var.region_a != var.region_b && var.zone_a == var.zone_b && !var.create_ppg && var.create_peering && var.placement_group_name == "")
    )
    error_message = "Scenario, locality, placement group, and peering flags must describe exactly one supported F06 Azure topology."
  }
}

variable "region_a" {
  type    = string
  default = "germanywestcentral"
}

variable "region_b" { type = string }

variable "zone_a" {
  type    = string
  default = "1"
}

variable "zone_b" { type = string }
variable "create_peering" { type = bool }
variable "create_ppg" { type = bool }

variable "controller_cidr" {
  type = string
  validation {
    condition     = can(cidrnetmask(var.controller_cidr)) && can(regex("/32$", var.controller_cidr))
    error_message = "SSH controller source must be one IPv4 /32."
  }
}

variable "private_a" {
  type = string
  validation {
    condition     = contains(["10.70.0.4"], var.private_a)
    error_message = "VM A must use a deterministic private address in the declared RFC1918 VNet."
  }
}

variable "private_b" {
  type = string
  validation {
    condition     = contains(["10.70.0.5", "10.71.0.4"], var.private_b) && var.private_a != var.private_b
    error_message = "VM B must use a distinct deterministic private address."
  }
}

variable "active_priority" {
  type    = string
  default = "Regular"
  validation {
    condition     = var.active_priority == "Regular"
    error_message = "Only Regular purchase is supported."
  }
}

variable "network_limit_mbps" {
  type    = number
  default = 12500
}
