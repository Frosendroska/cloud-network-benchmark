variable "region" {
  type = string

  validation {
    condition     = contains(["eu-central-1", "eu-west-2"], var.region)
    error_message = "AWS safety protection is limited to the selected Frankfurt and London regions."
  }
}

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
    condition     = length(var.resource_namespace) >= 1 && length(var.resource_namespace) <= 80 && can(regex("^[A-Za-z0-9._-]+$", var.resource_namespace))
    error_message = "resource_namespace must be a non-empty attempt-safe identifier of at most 80 characters."
  }
}

variable "run_id" {
  type = string
}

variable "deployment_id" {
  type = string
}
