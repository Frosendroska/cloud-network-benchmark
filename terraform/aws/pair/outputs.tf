locals {
  instance_b       = local.inter_region ? aws_instance.vm_b_london[0] : aws_instance.vm_b_frankfurt[0]
  zone_ids_primary = zipmap(data.aws_availability_zones.primary.names, data.aws_availability_zones.primary.zone_ids)
  zone_ids_london  = local.inter_region ? zipmap(data.aws_availability_zones.london[0].names, data.aws_availability_zones.london[0].zone_ids) : {}
}

output "deployment" {
  value = {
    vms = {
      vm_a = {
        instance_id              = aws_instance.vm_a.id
        network_interface_id     = aws_instance.vm_a.primary_network_interface_id
        private_ipv4             = aws_instance.vm_a.private_ip
        public_ipv4              = aws_instance.vm_a.public_ip
        region                   = var.primary_region
        zone                     = aws_instance.vm_a.availability_zone
        zone_id                  = local.zone_ids_primary[aws_instance.vm_a.availability_zone]
        image_id                 = aws_instance.vm_a.ami
        image_owner_id           = "099720109477"
        architecture             = "x86_64"
        shape                    = aws_instance.vm_a.instance_type
        placement_group          = aws_instance.vm_a.placement_group
        placement_group_strategy = local.placement ? "cluster" : null
        boot_volume_id           = aws_instance.vm_a.root_block_device[0].volume_id
      }
      vm_b = {
        instance_id              = local.instance_b.id
        network_interface_id     = local.instance_b.primary_network_interface_id
        private_ipv4             = local.instance_b.private_ip
        public_ipv4              = local.instance_b.public_ip
        region                   = local.inter_region ? "eu-west-2" : var.primary_region
        zone                     = local.instance_b.availability_zone
        zone_id                  = local.inter_region ? local.zone_ids_london[local.instance_b.availability_zone] : local.zone_ids_primary[local.instance_b.availability_zone]
        image_id                 = local.instance_b.ami
        image_owner_id           = "099720109477"
        architecture             = "x86_64"
        shape                    = local.instance_b.instance_type
        placement_group          = local.instance_b.placement_group
        placement_group_strategy = local.placement ? "cluster" : null
        boot_volume_id           = local.instance_b.root_block_device[0].volume_id
      }
    }
    network = {
      primary_vpc_id      = aws_vpc.frankfurt.id
      london_vpc_id       = local.inter_region ? aws_vpc.london[0].id : null
      peering_id          = local.inter_region ? aws_vpc_peering_connection.london[0].id : null
      security_group_a_id = aws_security_group.vm_a.id
      security_group_b_id = local.inter_region ? aws_security_group.vm_b_london[0].id : aws_security_group.vm_b_frankfurt[0].id
    }
    placement = {
      placement_group_id = local.placement ? aws_placement_group.cluster[0].id : null
      strategy           = local.placement ? "cluster" : null
    }
  }
}
