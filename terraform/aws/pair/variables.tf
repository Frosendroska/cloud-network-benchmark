variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit AWS account ID."
  }
}

variable "resource_namespace" {
  type = string
  validation {
    condition     = length(var.resource_namespace) <= 80 && can(regex("^[A-Za-z0-9._-]+$", var.resource_namespace))
    error_message = "resource_namespace must be an attempt-safe identifier."
  }
}

variable "run_id" { type = string }
variable "deployment_id" { type = string }

variable "scenario" {
  type = string
  validation {
    condition     = contains(["same_zone", "cross_zone", "placement_optimization", "inter_region"], var.scenario)
    error_message = "scenario must be one of the four frozen AWS scenarios."
  }
}

variable "controller_cidr" {
  type = string
  validation {
    condition     = can(cidrhost(var.controller_cidr, 0)) && endswith(var.controller_cidr, "/32")
    error_message = "controller_cidr must be one IPv4 /32."
  }
}

variable "image_id_frankfurt" { type = string }
variable "image_id_london" { type = string }
variable "host_key_bootstrap" {
  type      = string
  sensitive = true
}
variable "placement_group_name" { type = string }
