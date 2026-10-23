output "resource_group_id" { value = azurerm_resource_group.attempt.id }
output "automation_account_id" { value = azurerm_automation_account.expiry.id }
output "principal_id" { value = azurerm_automation_account.expiry.identity[0].principal_id }
output "role_assignment_id" { value = azurerm_role_assignment.deallocate.id }
output "role_definition_id" { value = azurerm_role_definition.deallocate.role_definition_resource_id }
output "role_actions" { value = azurerm_role_definition.deallocate.permissions[0].actions }
