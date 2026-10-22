locals {
  tags            = { managed_by = "cloud-network-benchmark", deployment_id = var.deployment_id, config_sha256 = var.config_sha256, scenario = var.scenario }
  london          = var.region_b == "uksouth"
  image_parts     = split(":", var.selected_image)
  image_reference = length(local.image_parts) == 4 ? local.image_parts : ["", "", "", ""]
  vnets = local.london ? {
    a = { location = var.region_a, prefix = "10.70.0.0/16", subnet = "10.70.0.0/24" }
    b = { location = var.region_b, prefix = "10.71.0.0/16", subnet = "10.71.0.0/24" }
    } : {
    a = { location = var.region_a, prefix = "10.70.0.0/16", subnet = "10.70.0.0/24" }
  }
  network = {
    a = { location = var.region_a, ip = var.private_a, zone = var.zone_a, vnet_key = "a" }
    b = { location = var.region_b, ip = var.private_b, zone = var.zone_b, vnet_key = local.london ? "b" : "a" }
  }
}

data "azurerm_resource_group" "pair" {
  name = var.resource_group_name
}

resource "azurerm_proximity_placement_group" "pair" {
  count               = var.create_ppg ? 1 : 0
  name                = var.placement_group_name
  location            = var.location
  resource_group_name = data.azurerm_resource_group.pair.name
  allowed_vm_sizes    = ["Standard_D4s_v5"]
  tags                = local.tags
}

resource "azurerm_virtual_network" "pair" {
  for_each            = local.vnets
  name                = "${var.resource_group_name}-${each.key}-vnet"
  location            = each.value.location
  resource_group_name = data.azurerm_resource_group.pair.name
  address_space       = [each.value.prefix]
  tags                = local.tags
}

resource "azurerm_subnet" "pair" {
  for_each             = local.vnets
  name                 = "benchmark"
  resource_group_name  = data.azurerm_resource_group.pair.name
  virtual_network_name = azurerm_virtual_network.pair[each.key].name
  address_prefixes     = [each.value.subnet]
}

resource "azurerm_public_ip" "pair" {
  for_each            = local.network
  name                = "${var.resource_group_name}-${each.key}-pip"
  location            = each.value.location
  resource_group_name = data.azurerm_resource_group.pair.name
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = [each.value.zone]
  tags                = local.tags
}

resource "azurerm_network_security_group" "pair" {
  for_each            = local.network
  name                = "${var.resource_group_name}-${each.key}-nsg"
  location            = each.value.location
  resource_group_name = data.azurerm_resource_group.pair.name
  security_rule {
    name                       = "allow-controller-ssh"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = var.controller_cidr
    destination_address_prefix = "*"
  }
  security_rule {
    name                       = "deny-other-ssh"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
  dynamic "security_rule" {
    for_each = each.key == "b" ? {
      sockperf = { protocol = "Udp", port = "11111" }
      iperf3   = { protocol = "Tcp", port = "5201" }
      icmp     = { protocol = "Icmp", port = "*" }
    } : {}
    content {
      name                       = "allow-a-${security_rule.key}"
      priority                   = 120 + index(["sockperf", "iperf3", "icmp"], security_rule.key) * 10
      direction                  = "Inbound"
      access                     = "Allow"
      protocol                   = security_rule.value.protocol
      source_port_range          = "*"
      destination_port_range     = security_rule.value.port
      source_address_prefix      = local.network.a.ip
      destination_address_prefix = "*"
    }
  }
  dynamic "security_rule" {
    for_each = each.key == "b" ? {
      sockperf = { protocol = "Udp", port = "11111" }
      iperf3   = { protocol = "Tcp", port = "5201" }
      icmp     = { protocol = "Icmp", port = "*" }
    } : {}
    content {
      name                       = "deny-other-${security_rule.key}"
      priority                   = 160 + index(["sockperf", "iperf3", "icmp"], security_rule.key) * 10
      direction                  = "Inbound"
      access                     = "Deny"
      protocol                   = security_rule.value.protocol
      source_port_range          = "*"
      destination_port_range     = security_rule.value.port
      source_address_prefix      = "*"
      destination_address_prefix = "*"
    }
  }
  security_rule {
    name                       = "deny-default-vnet-source"
    priority                   = 4000
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "VirtualNetwork"
    destination_address_prefix = "*"
  }
  tags = local.tags
}

resource "azurerm_network_interface" "pair" {
  for_each                       = local.network
  name                           = "${var.resource_group_name}-${each.key}-nic"
  location                       = each.value.location
  resource_group_name            = data.azurerm_resource_group.pair.name
  accelerated_networking_enabled = true
  ip_configuration {
    name                          = "private"
    subnet_id                     = azurerm_subnet.pair[each.value.vnet_key].id
    private_ip_address_allocation = "Static"
    private_ip_address            = each.value.ip
    public_ip_address_id          = azurerm_public_ip.pair[each.key].id
  }
  tags = local.tags
}

resource "azurerm_network_interface_security_group_association" "pair" {
  for_each                  = local.network
  network_interface_id      = azurerm_network_interface.pair[each.key].id
  network_security_group_id = azurerm_network_security_group.pair[each.key].id
}

resource "azurerm_linux_virtual_machine" "pair" {
  for_each                        = local.network
  name                            = "${var.resource_group_name}-${each.key}"
  computer_name                   = each.key
  resource_group_name             = data.azurerm_resource_group.pair.name
  location                        = each.value.location
  size                            = "Standard_D4s_v5"
  zone                            = each.value.zone
  priority                        = "Regular"
  admin_username                  = "ubuntu"
  network_interface_ids           = [azurerm_network_interface.pair[each.key].id]
  disable_password_authentication = true
  admin_ssh_key {
    username   = "ubuntu"
    public_key = var.ssh_public_key
  }
  source_image_id = startswith(var.selected_image, "/") ? var.selected_image : null
  dynamic "source_image_reference" {
    for_each = startswith(var.selected_image, "/") ? [] : [1]
    content {
      publisher = local.image_reference[0]
      offer     = local.image_reference[1]
      sku       = local.image_reference[2]
      version   = local.image_reference[3]
    }
  }
  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 32
  }
  proximity_placement_group_id = var.create_ppg ? azurerm_proximity_placement_group.pair[0].id : null
  custom_data = base64encode(<<-CLOUD
    #cloud-config
    package_update: true
    write_files:
      - path: /etc/sysctl.d/90-cnb.conf
        content: |
          net.core.default_qdisc=fq
          net.ipv4.tcp_congestion_control=cubic
          net.ipv4.tcp_ecn=0
    runcmd:
      - [sh, -c, "ip link set dev $(ip route show default | awk '{print $5}') mtu 1460"]
  CLOUD
  )
  tags       = local.tags
  depends_on = [azurerm_network_interface_security_group_association.pair]
}

resource "azurerm_virtual_network_peering" "a_to_b" {
  count                        = var.create_peering ? 1 : 0
  name                         = "${var.resource_group_name}-a-to-b"
  resource_group_name          = data.azurerm_resource_group.pair.name
  virtual_network_name         = azurerm_virtual_network.pair["a"].name
  remote_virtual_network_id    = azurerm_virtual_network.pair["b"].id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = false
  allow_gateway_transit        = false
  use_remote_gateways          = false
}
resource "azurerm_virtual_network_peering" "b_to_a" {
  count                        = var.create_peering ? 1 : 0
  name                         = "${var.resource_group_name}-b-to-a"
  resource_group_name          = data.azurerm_resource_group.pair.name
  virtual_network_name         = azurerm_virtual_network.pair["b"].name
  remote_virtual_network_id    = azurerm_virtual_network.pair["a"].id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = false
  allow_gateway_transit        = false
  use_remote_gateways          = false
}
