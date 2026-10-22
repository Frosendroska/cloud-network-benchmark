data "aws_caller_identity" "current" {}
data "aws_availability_zones" "frankfurt" {
  state = "available"
}
data "aws_availability_zones" "london" {
  count    = local.inter_region ? 1 : 0
  provider = aws.london
  state    = "available"
}

locals {
  tags = {
    cnb_run_id        = var.run_id
    cnb_deployment_id = var.deployment_id
    cnb_namespace     = var.resource_namespace
  }
  inter_region            = var.scenario == "inter_region"
  cross_zone              = var.scenario == "cross_zone"
  placement               = var.scenario == "placement_optimization"
  subnet_b_frankfurt_cidr = local.cross_zone ? "10.60.2.0/24" : "10.60.1.0/24"
  subnet_b_frankfurt_ip   = local.cross_zone ? "10.60.2.10" : "10.60.1.11"
  zone_a                  = "eu-central-1a"
  zone_b_frankfurt        = local.cross_zone ? "eu-central-1b" : "eu-central-1a"
  zone_b_london           = "eu-west-2a"
}

resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.account_id
      error_message = "AWS credentials resolve to a different account than the admitted attempt."
    }
  }
}

resource "aws_vpc" "frankfurt" {
  depends_on           = [terraform_data.account_guard]
  cidr_block           = "10.60.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${var.resource_namespace}-frankfurt" })
}

resource "aws_internet_gateway" "frankfurt" {
  vpc_id = aws_vpc.frankfurt.id
  tags   = merge(local.tags, { Name = "${var.resource_namespace}-frankfurt-igw" })
}

resource "aws_route_table" "frankfurt" {
  vpc_id = aws_vpc.frankfurt.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.frankfurt.id
  }
  tags = merge(local.tags, { Name = "${var.resource_namespace}-frankfurt-public" })
}

resource "aws_subnet" "a" {
  vpc_id                  = aws_vpc.frankfurt.id
  cidr_block              = "10.60.1.0/24"
  availability_zone       = local.zone_a
  map_public_ip_on_launch = true
  tags                    = merge(local.tags, { Name = "${var.resource_namespace}-vm-a" })
}

resource "aws_subnet" "b_frankfurt" {
  count                   = local.inter_region ? 0 : 1
  vpc_id                  = aws_vpc.frankfurt.id
  cidr_block              = local.subnet_b_frankfurt_cidr
  availability_zone       = local.zone_b_frankfurt
  map_public_ip_on_launch = true
  tags                    = merge(local.tags, { Name = "${var.resource_namespace}-vm-b" })
}

resource "aws_route_table_association" "a" {
  subnet_id      = aws_subnet.a.id
  route_table_id = aws_route_table.frankfurt.id
}

resource "aws_route_table_association" "b_frankfurt" {
  count          = local.inter_region ? 0 : 1
  subnet_id      = aws_subnet.b_frankfurt[0].id
  route_table_id = aws_route_table.frankfurt.id
}

resource "aws_security_group" "vm_a" {
  name        = "${var.resource_namespace}-vm-a"
  description = "Attempt-scoped SSH and private ICMP for AWS VM A"
  vpc_id      = aws_vpc.frankfurt.id
  ingress {
    description = "Controller SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.controller_cidr]
  }
  ingress {
    description = "Private ICMP from VM B"
    from_port   = 8
    to_port     = 0
    protocol    = "icmp"
    cidr_blocks = [local.inter_region ? "10.61.1.10/32" : local.subnet_b_frankfurt_ip == "10.60.2.10" ? "10.60.2.10/32" : "10.60.1.11/32"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.tags, { Name = "${var.resource_namespace}-vm-a" })
}

resource "aws_security_group" "vm_b_frankfurt" {
  count       = local.inter_region ? 0 : 1
  name        = "${var.resource_namespace}-vm-b"
  description = "Attempt-scoped AWS benchmark receiver"
  vpc_id      = aws_vpc.frankfurt.id
  ingress {
    description = "Controller SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.controller_cidr]
  }
  ingress {
    description = "Private ICMP from VM A"
    from_port   = 8
    to_port     = 0
    protocol    = "icmp"
    cidr_blocks = ["10.60.1.10/32"]
  }
  ingress {
    description = "Sockperf UDP from VM A"
    from_port   = 11111
    to_port     = 11111
    protocol    = "udp"
    cidr_blocks = ["10.60.1.10/32"]
  }
  ingress {
    description = "iperf3 TCP from VM A"
    from_port   = 5201
    to_port     = 5201
    protocol    = "tcp"
    cidr_blocks = ["10.60.1.10/32"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.tags, { Name = "${var.resource_namespace}-vm-b" })
}

resource "aws_security_group" "vm_b_london" {
  provider    = aws.london
  count       = local.inter_region ? 1 : 0
  name        = "${var.resource_namespace}-vm-b"
  description = "Attempt-scoped AWS inter-region benchmark receiver"
  vpc_id      = aws_vpc.london[0].id
  ingress {
    description = "Controller SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.controller_cidr]
  }
  ingress {
    description = "Private ICMP from VM A"
    from_port   = 8
    to_port     = 0
    protocol    = "icmp"
    cidr_blocks = ["10.60.1.10/32"]
  }
  ingress {
    description = "Sockperf UDP from VM A"
    from_port   = 11111
    to_port     = 11111
    protocol    = "udp"
    cidr_blocks = ["10.60.1.10/32"]
  }
  ingress {
    description = "iperf3 TCP from VM A"
    from_port   = 5201
    to_port     = 5201
    protocol    = "tcp"
    cidr_blocks = ["10.60.1.10/32"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.tags, { Name = "${var.resource_namespace}-vm-b" })
}

resource "aws_vpc" "london" {
  provider             = aws.london
  count                = local.inter_region ? 1 : 0
  cidr_block           = "10.61.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${var.resource_namespace}-london" })
}

resource "aws_internet_gateway" "london" {
  provider = aws.london
  count    = local.inter_region ? 1 : 0
  vpc_id   = aws_vpc.london[0].id
  tags     = merge(local.tags, { Name = "${var.resource_namespace}-london-igw" })
}

resource "aws_route_table" "london" {
  provider = aws.london
  count    = local.inter_region ? 1 : 0
  vpc_id   = aws_vpc.london[0].id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.london[0].id
  }
  tags = merge(local.tags, { Name = "${var.resource_namespace}-london-public" })
}

resource "aws_subnet" "london" {
  provider                = aws.london
  count                   = local.inter_region ? 1 : 0
  vpc_id                  = aws_vpc.london[0].id
  cidr_block              = "10.61.1.0/24"
  availability_zone       = local.zone_b_london
  map_public_ip_on_launch = true
  tags                    = merge(local.tags, { Name = "${var.resource_namespace}-vm-b" })
}

resource "aws_route_table_association" "london" {
  provider       = aws.london
  count          = local.inter_region ? 1 : 0
  subnet_id      = aws_subnet.london[0].id
  route_table_id = aws_route_table.london[0].id
}

resource "aws_placement_group" "cluster" {
  count    = local.placement ? 1 : 0
  name     = var.placement_group_name
  strategy = "cluster"
  tags     = merge(local.tags, { Name = var.placement_group_name })
}

resource "aws_vpc_peering_connection" "london" {
  count       = local.inter_region ? 1 : 0
  vpc_id      = aws_vpc.frankfurt.id
  peer_vpc_id = aws_vpc.london[0].id
  peer_region = "eu-west-2"
  auto_accept = false
  tags        = merge(local.tags, { Name = "${var.resource_namespace}-frankfurt-london" })
}

resource "aws_vpc_peering_connection_accepter" "london" {
  provider                  = aws.london
  count                     = local.inter_region ? 1 : 0
  vpc_peering_connection_id = aws_vpc_peering_connection.london[0].id
  auto_accept               = true
  tags                      = merge(local.tags, { Name = "${var.resource_namespace}-frankfurt-london" })
}

resource "aws_route" "frankfurt_to_london" {
  count                     = local.inter_region ? 1 : 0
  route_table_id            = aws_route_table.frankfurt.id
  destination_cidr_block    = "10.61.0.0/16"
  vpc_peering_connection_id = aws_vpc_peering_connection_accepter.london[0].id
}

resource "aws_route" "london_to_frankfurt" {
  provider                  = aws.london
  count                     = local.inter_region ? 1 : 0
  route_table_id            = aws_route_table.london[0].id
  destination_cidr_block    = "10.60.0.0/16"
  vpc_peering_connection_id = aws_vpc_peering_connection_accepter.london[0].id
}

resource "aws_instance" "vm_a" {
  ami                         = var.image_id_frankfurt
  instance_type               = "m7i.xlarge"
  subnet_id                   = aws_subnet.a.id
  private_ip                  = "10.60.1.10"
  vpc_security_group_ids      = [aws_security_group.vm_a.id]
  associate_public_ip_address = true
  user_data                   = var.host_key_bootstrap
  user_data_replace_on_change = true
  placement_group             = local.placement ? aws_placement_group.cluster[0].name : null
  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }
  root_block_device {
    volume_size           = 32
    volume_type           = "gp3"
    delete_on_termination = true
  }
  tags = merge(local.tags, { Name = "${var.resource_namespace}-vm-a", cnb_role = "vm_a" })
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.account_id
      error_message = "Attempt AWS account does not match the authenticated provider account."
    }
  }
}

resource "aws_instance" "vm_b_frankfurt" {
  count                       = local.inter_region ? 0 : 1
  ami                         = var.image_id_frankfurt
  instance_type               = "m7i.xlarge"
  subnet_id                   = aws_subnet.b_frankfurt[0].id
  private_ip                  = local.subnet_b_frankfurt_ip
  vpc_security_group_ids      = [aws_security_group.vm_b_frankfurt[0].id]
  associate_public_ip_address = true
  user_data                   = var.host_key_bootstrap
  user_data_replace_on_change = true
  placement_group             = local.placement ? aws_placement_group.cluster[0].name : null
  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }
  root_block_device {
    volume_size           = 32
    volume_type           = "gp3"
    delete_on_termination = true
  }
  tags = merge(local.tags, { Name = "${var.resource_namespace}-vm-b", cnb_role = "vm_b" })
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.account_id
      error_message = "Attempt AWS account does not match the authenticated provider account."
    }
  }
}

resource "aws_instance" "vm_b_london" {
  provider                    = aws.london
  count                       = local.inter_region ? 1 : 0
  ami                         = var.image_id_london
  instance_type               = "m7i.xlarge"
  subnet_id                   = aws_subnet.london[0].id
  private_ip                  = "10.61.1.10"
  vpc_security_group_ids      = [aws_security_group.vm_b_london[0].id]
  associate_public_ip_address = true
  user_data                   = var.host_key_bootstrap
  user_data_replace_on_change = true
  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }
  root_block_device {
    volume_size           = 32
    volume_type           = "gp3"
    delete_on_termination = true
  }
  tags = merge(local.tags, { Name = "${var.resource_namespace}-vm-b", cnb_role = "vm_b" })
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.account_id
      error_message = "Attempt AWS account does not match the authenticated provider account."
    }
  }
}
