variable "subscription_id" { type = string }

variable "tenant_id" {
  type    = string
  default = ""
}

variable "resource_group_name" { type = string }

variable "location" {
  type    = string
  default = "germanywestcentral"
}

variable "deployment_id" {
  type    = string
  default = "reserved"
}
