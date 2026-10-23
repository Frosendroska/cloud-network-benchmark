mock_provider "azurerm" {}

run "expiry_prerequisites_only" {
  command = plan
  variables {
    subscription_id     = "00000000-0000-0000-0000-000000000001"
    tenant_id           = "00000000-0000-0000-0000-000000000002"
    resource_group_name = "f06-attempt"
    deployment_id       = "campaign-azure-deployment"
  }
  assert {
    condition     = azurerm_automation_account.expiry.sku_name == "Basic"
    error_message = "expiry uses the minimal Basic Automation account"
  }
  assert {
    condition     = toset(azurerm_role_definition.deallocate.permissions[0].actions) == toset(["Microsoft.Compute/virtualMachines/read", "Microsoft.Compute/virtualMachines/instanceView/read", "Microsoft.Compute/virtualMachines/deallocate/action"])
    error_message = "expiry permissions must be deallocation-only"
  }
}
