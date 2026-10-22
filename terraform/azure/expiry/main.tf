resource "azurerm_resource_group" "attempt" {
  name     = var.resource_group_name
  location = var.location
  tags     = { managed_by = "cloud-network-benchmark", deployment_id = var.deployment_id, purpose = "independent-expiry" }
}
resource "azurerm_automation_account" "expiry" {
  name                = "${var.resource_group_name}-auto"
  location            = var.location
  resource_group_name = azurerm_resource_group.attempt.name
  sku_name            = "Basic"
  identity { type = "SystemAssigned" }
  tags = { managed_by = "cloud-network-benchmark", deployment_id = var.deployment_id }
}
resource "azurerm_role_definition" "deallocate" {
  name        = "${var.resource_group_name}-deallocate-only"
  scope       = azurerm_resource_group.attempt.id
  description = "F06 deallocate-only expiry access"
  permissions {
    actions = [
      "Microsoft.Compute/virtualMachines/read",
      "Microsoft.Compute/virtualMachines/instanceView/read",
      "Microsoft.Compute/virtualMachines/deallocate/action",
    ]
    not_actions = []
  }
  assignable_scopes = [azurerm_resource_group.attempt.id]
}
resource "azurerm_role_assignment" "deallocate" {
  scope              = azurerm_resource_group.attempt.id
  role_definition_id = azurerm_role_definition.deallocate.role_definition_resource_id
  principal_id       = azurerm_automation_account.expiry.identity[0].principal_id
  principal_type     = "ServicePrincipal"
}
