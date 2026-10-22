mock_provider "azurerm" {
  mock_data "azurerm_resource_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/f06", name = "f06", location = "germanywestcentral" }
  }
}

variables {
  subscription_id     = "00000000-0000-0000-0000-000000000001"
  resource_group_name = "f06"
  location            = "germanywestcentral"
  deployment_id       = "campaign-azure-deployment"
  config_sha256       = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  controller_cidr     = "192.0.2.1/32"
  ssh_public_key      = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc test"
  selected_image      = "Canonical:ubuntu-24_04-lts:server:24.04.20261001"
  private_a           = "10.70.0.4"
  private_b           = "10.70.0.5"
  create_peering      = false
  create_ppg          = false
}

run "same_zone" {
  command = plan
  variables {
    scenario = "same_zone"
    region_b = "germanywestcentral"
    zone_a   = "1"
    zone_b   = "1"
  }
  assert {
    condition     = length(azurerm_linux_virtual_machine.pair) == 2
    error_message = "exactly two VMs are required"
  }
  assert {
    condition     = azurerm_linux_virtual_machine.pair["a"].priority == "Regular"
    error_message = "must use Regular purchase"
  }
  assert {
    condition     = azurerm_linux_virtual_machine.pair["b"].os_disk[0].disk_size_gb == 32
    error_message = "must use the selected 32 GiB disk"
  }
  assert {
    condition     = alltrue([for vm in values(azurerm_linux_virtual_machine.pair) : vm.size == "Standard_D4s_v5" && vm.priority == "Regular" && vm.admin_username == "ubuntu"])
    error_message = "both VMs use the comparable D4s_v5 Regular Ubuntu configuration"
  }
  assert {
    condition     = alltrue([for vm in values(azurerm_linux_virtual_machine.pair) : vm.os_disk[0].storage_account_type == "StandardSSD_LRS" && vm.os_disk[0].disk_size_gb == 32])
    error_message = "both boot disks must be 32 GiB StandardSSD_LRS"
  }
  assert {
    condition     = alltrue([for nic in values(azurerm_network_interface.pair) : nic.accelerated_networking_enabled])
    error_message = "both NICs must enable Accelerated Networking"
  }
  assert {
    condition     = alltrue([for pip in values(azurerm_public_ip.pair) : pip.sku == "Standard" && pip.allocation_method == "Static" && length(pip.zones) == 1])
    error_message = "each VM needs a zone-matched Standard static public IPv4"
  }
  assert {
    condition     = one([for rule in azurerm_network_security_group.pair["a"].security_rule : rule.priority if rule.name == "allow-controller-ssh"]) == 100 && one([for rule in azurerm_network_security_group.pair["a"].security_rule : rule.priority if rule.name == "deny-other-ssh"]) == 110
    error_message = "controller SSH allow must precede the deny-other-SSH rule"
  }
  assert {
    condition     = length(azurerm_network_security_group.pair["a"].security_rule) == 3 && length(azurerm_network_security_group.pair["b"].security_rule) == 9
    error_message = "A exposes controller SSH only; B adds A-source benchmark and ICMP rules before explicit default-VNet deny"
  }
}

run "cross_zone" {
  command = plan
  variables {
    scenario  = "cross_zone"
    region_b  = "germanywestcentral"
    zone_a    = "1"
    zone_b    = "2"
    private_b = "10.70.0.5"
  }
  assert {
    condition     = azurerm_linux_virtual_machine.pair["a"].zone == "1" && azurerm_linux_virtual_machine.pair["b"].zone == "2"
    error_message = "cross_zone must preserve Azure logical zones"
  }
}

run "placement_optimization" {
  command = plan
  variables {
    scenario             = "placement_optimization"
    region_b             = "germanywestcentral"
    zone_a               = "1"
    zone_b               = "1"
    create_ppg           = true
    placement_group_name = "f06-ppg"
  }
  assert {
    condition     = length(azurerm_proximity_placement_group.pair) == 1
    error_message = "placement_optimization requires one Standard PPG"
  }
}

run "inter_region" {
  command = plan
  variables {
    scenario       = "inter_region"
    region_b       = "uksouth"
    zone_a         = "1"
    zone_b         = "1"
    private_b      = "10.71.0.4"
    create_peering = true
  }
  assert {
    condition     = length(azurerm_virtual_network_peering.a_to_b) == 1 && length(azurerm_virtual_network_peering.b_to_a) == 1
    error_message = "Global VNet peering must be bidirectional"
  }
}

run "reject_cross_zone_same_zone" {
  command = plan
  variables {
    scenario = "cross_zone"
    region_b = "germanywestcentral"
    zone_a   = "1"
    zone_b   = "1"
  }
  expect_failures = [var.scenario]
}
