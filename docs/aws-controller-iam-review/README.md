# AWS controller IAM review packet

Offline review artifacts only. These JSON files have not been created in IAM and grant no permissions by themselves. The controller draft binds the current reviewed identity/account `238360820461`, the measured region `eu-north-1`, and the expiry role name prefix `cnb-expiry-*`. It is limited to the current Stockholm same-zone and placement-optimization controller path. The source can issue the enumerated lifecycle calls; this document does not prove the current console profile permits them.

## Console preparation, after code review

1. In IAM, create a customer-managed policy from `expiry-role-boundary.json`, named `CNBExpiryRoleBoundary` at the policy path `/`. Its ARN must be `arn:aws:iam::238360820461:policy/CNBExpiryRoleBoundary`, or the controller draft and runtime option must be updated to the exact reviewed path and ARN together.
2. Review the boundary statement conditions and role tags. When the boundary option is configured, expiry-role Terraform tags that role with its `cnb_run_id` and `cnb_deployment_id`; the boundary uses those principal tags to limit `StopInstances` to matching tagged experiment instances in Stockholm. With the option omitted, role tags remain omitted to preserve legacy behavior.
3. Review `controller-policy.json`, replace neither account nor region without a separate source review, then create a customer-managed controller policy from it. Attach it to the dedicated controller identity only after review. Do not attach administrator, power-user, or full-access policies.
4. Configure the AWS runtime `expiry_role_permissions_boundary_arn` with the exact policy ARN. Omit this option to preserve legacy behavior. A configured value is rejected unless it is an IAM policy ARN in account `238360820461`; the provider checks `iam get-role` readback before continuing expiry registration.
5. Treat IAM setup as preparation only. It does not authorize paid launches, provider acceptance, or live tests.

## Scope and limits

The controller policy lists source-observed EC2, SSM, IAM, and STS actions. Read-only EC2 discovery and SSM calls are region-conditioned; `RunInstances`, tag changes, and multiple network create/delete APIs retain `Resource: "*"` because Terraform creates several resource types and IDs are not known before creation. IAM writes are restricted to `cnb-expiry-*`; role creation requires the configured boundary, `PassRole` is limited to those roles and `ssm.amazonaws.com`, and the policy explicitly denies changing or removing a boundary. The role tag action is key-limited but can change existing run/deployment principal tags; the dynamic tag relationship has not been proven tamper-resistant by an AWS policy simulation. SSM uses `Resource: "*"` pending action-by-action AWS service-authorization verification, with a Stockholm condition. This is a **regional controller capability**, broader than the fixed-batch approval scope; the framework/card must enforce separate run-count and launch authorization. It is not a claim of perfect least privilege.

Source action inventory for the bounded Stockholm controller path:

- EC2: `Describe*`, `GetConsoleOutput`, `RunInstances`, `TerminateInstances`, `StopInstances`, VPC/IGW/route-table/route/subnet/security-group/placement-group lifecycle, and `CreateTags`/`DeleteTags`.
- SSM: document create/read/version/permission/delete, tag list/add/remove, and automation start/read/list/stop.
- IAM: list roles for manual prefix preflight; create/read/delete expiry roles and inline policies; list attached/inline policies and instance profiles for a role; tag/read role tags; and `PassRole` to Systems Manager.
- STS: `GetCallerIdentity`.

No `StartInstances`, VPC peering, Elastic IP, key-pair creation, `ssm:SendCommand`, Parameter Store, quota APIs, `ec2:DeleteVolume`, or `ec2:DeleteNetworkInterface` permission is included. Volume and network-interface residual recovery remains a separate cleanup permission review; the controller draft does not claim physical billable-resource closure for those residuals. AWS policy simulation and live acceptance remain pending.

Before attaching the controller policy, an administrator must inspect all existing `cnb-expiry-*` roles and confirm they are absent or already have the exact boundary. Stop for review if any existing role is unbounded; this draft intentionally does not grant boundary changes or automatic deletion to retrofit one. The controller includes `iam:ListRoles` for prefix preflight, but IAM does not enforce that prefix filter in the permission itself. Do not allow another principal to create unbounded roles in the reserved prefix.

## Code integration and validation status

`src/cloud_network_benchmark/terraform_plan.py` now accepts Terraform's explicit null default for the new nullable AWS safety boundary when a legacy reviewed tfvars file omits that variable. The remaining plan variables still use exact equality. `_aws_scope` binds a configured boundary to the reviewed AWS account and exact role attribute, and requires the expiry role's run/deployment ownership tags. Focused source-shaped plan tests cover the legacy null default, configured boundary and tags, and wrong-account, wrong-boundary, and tag mutations.

These checks use synthetic Terraform plan documents. No Terraform CLI was available, so native plan JSON generation and provider-native apply remain unproved. AWS policy simulation and live acceptance are also pending.
