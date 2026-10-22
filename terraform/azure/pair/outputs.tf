output "vm_a" {
  value = { id = azurerm_linux_virtual_machine.pair["a"].id, private_ipv4 = azurerm_network_interface.pair["a"].private_ip_address, public_ipv4 = azurerm_public_ip.pair["a"].ip_address, image = var.selected_image, size = azurerm_linux_virtual_machine.pair["a"].size, region = azurerm_linux_virtual_machine.pair["a"].location, zone = azurerm_linux_virtual_machine.pair["a"].zone, ppg = var.create_ppg ? azurerm_proximity_placement_group.pair[0].name : null, accelerated_networking = azurerm_network_interface.pair["a"].accelerated_networking_enabled }
}
output "vm_b" {
  value = { id = azurerm_linux_virtual_machine.pair["b"].id, private_ipv4 = azurerm_network_interface.pair["b"].private_ip_address, public_ipv4 = azurerm_public_ip.pair["b"].ip_address, image = var.selected_image, size = azurerm_linux_virtual_machine.pair["b"].size, region = azurerm_linux_virtual_machine.pair["b"].location, zone = azurerm_linux_virtual_machine.pair["b"].zone, ppg = var.create_ppg ? azurerm_proximity_placement_group.pair[0].name : null, accelerated_networking = azurerm_network_interface.pair["b"].accelerated_networking_enabled }
}
output "network_limit_mbps" { value = var.network_limit_mbps }
