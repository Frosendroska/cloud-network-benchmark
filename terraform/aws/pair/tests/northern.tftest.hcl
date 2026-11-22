mock_provider "aws" {}
mock_provider "aws" {
  alias = "london"
}

override_data {
  target = data.aws_caller_identity.current
  values = { account_id = "123456789012" }
}

override_data {
  target = data.aws_availability_zones.primary
  values = {
    names    = ["eu-north-1a"]
    zone_ids = ["eun1-az1"]
  }
}

variables {
  account_id           = "123456789012"
  resource_namespace   = "cnb-northern-offline"
  run_id               = "offline-north-aws"
  deployment_id        = "offline-north-aws-deployment"
  scenario             = "same_zone"
  controller_cidr      = "192.0.2.4/32"
  primary_region       = "eu-north-1"
  primary_zone         = "eu-north-1a"
  image_id_primary     = "ami-0123456789abcdef0"
  image_id_london      = "ami-0abcdef0123456789"
  host_key_bootstrap   = "#cloud-config\n"
  placement_group_name = "cnb-northern-offline-cluster"
}

run "stockholm_same_zone_pair" {
  command = plan
  assert {
    condition = (aws_subnet.a.availability_zone == "eu-north-1a" &&
      aws_instance.vm_a.ami == var.image_id_primary &&
      aws_instance.vm_b_frankfurt[0].ami == var.image_id_primary &&
      aws_instance.vm_a.instance_type == "m7i.xlarge" &&
    aws_instance.vm_b_frankfurt[0].instance_type == "m7i.xlarge")
    error_message = "Stockholm pair must use the pinned regional AMI, selected zone and exact shape"
  }
  assert {
    condition = (length(aws_instance.vm_b_frankfurt) == 1 &&
      length(aws_instance.vm_b_london) == 0 &&
    length(aws_subnet.b_frankfurt) == 0)
    error_message = "Stockholm acceptance creates exactly the local same-zone pair"
  }
}

run "reject_stockholm_cross_zone" {
  command = plan
  variables { scenario = "cross_zone" }
  expect_failures = [terraform_data.account_guard]
}

run "stockholm_s3_cluster_pair" {
  command = plan
  variables { scenario = "placement_optimization" }
  assert {
    condition = (length(aws_placement_group.cluster) == 1 &&
      aws_instance.vm_a.placement_group == var.placement_group_name &&
      aws_instance.vm_b_frankfurt[0].placement_group == var.placement_group_name)
    error_message = "Stockholm S3 must use the selected cluster placement group on both roles"
  }
}

run "stockholm_s3_cluster_pair" {
  command = plan
  variables { scenario = "placement_optimization" }
  assert {
    condition = (length(aws_placement_group.cluster) == 1 &&
      aws_instance.vm_a.placement_group == var.placement_group_name &&
      aws_instance.vm_b_frankfurt[0].placement_group == var.placement_group_name)
    error_message = "Stockholm S3 must use the selected cluster placement group on both roles"
  }
}

run "reject_stockholm_zone_mismatch" {
  command = plan
  variables { primary_zone = "eu-central-1a" }
  expect_failures = [terraform_data.account_guard]
}
