variable "region" {
  type = string

  validation {
    condition     = contains(["eu-central-1", "eu-west-2", "eu-north-1"], var.region)
    error_message = "AWS safety protection is limited to the selected Frankfurt, London and Stockholm regions."
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

variable "expiry_role_permissions_boundary_arn" {
  type        = string
  description = "Optional existing customer-managed IAM policy ARN used as the expiry role permissions boundary."
  default     = null
  nullable    = true
  validation {
    condition = var.expiry_role_permissions_boundary_arn == null || can(regex(
      "^arn:aws:iam::[0-9]{12}:policy/(?:[A-Za-z0-9+=,.@_-]+/)*[A-Za-z0-9+=,.@_-]+$",
      var.expiry_role_permissions_boundary_arn
    ))
    error_message = "expiry_role_permissions_boundary_arn must be an IAM customer-managed policy ARN with a valid path."
  }
}
