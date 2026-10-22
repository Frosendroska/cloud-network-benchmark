# AWS provider interface

Frozen imports: `contracts.deployment.ProviderDeploymentInput`, `ProviderDeploymentOutput`, `ProviderVmOutput`, `RemoteConnectionData`; `contracts.admission.ExpiryRequest`, `ExpiryEvidence`, `ReservationEvidence`; `contracts.common.CleanupState`, `FailureEvidence`, `Evidence`, `StructuredEvidence`; `contracts.execution.CommandRunner`, `CommandRequest`, `CommandResult`. No shared CleanupEvidence exists. F05-owned AwsCleanupResult contains cleanup_state, cleanup_failure, inventory and expiry as defined in data-model.md; F09 later maps those to existing manifest cleanup fields. No new shared contract types are permitted.

## Python boundary

`AwsProviderDriver(runner: CommandRunner, settings: AwsRuntimeSettings, clock=...)` lives in `providers/aws.py`. Private settings/clock helpers stay provider-local.

- `register_expiry(deployment: ProviderDeploymentInput, request: ExpiryRequest) -> ExpiryEvidence`: validates reservation/cap/identity/cost input, safety-root resources, regional Automation starts and authenticated readbacks. Failure returns failed evidence (or a provider-local exception preserving equivalent evidence) and never runs the pair root. Only fully observed all-region protection can be registered.
- `provision(deployment: ProviderDeploymentInput) -> ProviderDeploymentOutput`: requires matching registered expiry and its private journal; validates remaining workflow fits trigger and fresh registration state immediately before pair command. Writes isolated vars/state, invokes fakeable Terraform pair apply/output then provider-authenticated image/type/zone/console evidence. Failure preserves partial output and attempts cleanup; no imports from F02R/F04/F11.
- `cleanup(deployment: ProviderDeploymentInput) -> AwsCleanupResult`: destroy pair with original state, inventory owned resources, preserve protection until compute absent, cancel/read back execution(s), destroy safety root, verify all absent. Primary/secondary failures remain separate. Calling cleanup again reuses journal/state safely.

Caller supplies current reservation, admitted priority/gate decision and exact AWS runtime settings. P2/P3 requires the provider-local sourced review decision described in data-model.md, including admitted S4 acceptance; unavailable evidence fails closed. The provider checks the supplied remaining complete-workflow estimate against its earlier trigger and never fabricates an allowance. F09 composes these entry points later. F05 makes no new CLI command. API implementation may use ordinary private helpers, with no generic provider framework.

## Terraform boundary

Safety root inputs: attempt namespace/run/deployment IDs, AWS account/regions, expiry document body and tag-scoped policy. Outputs: document name/version/hash and Automation role ARN by region. It contains zero aws_instance resources or remote-exec/local-exec provisioners.

Pair root inputs: exact two roles, scenario, selected AMI IDs, zones, CIDRs/private addresses, controller /32, attempt public authentication key, supplied cloud-init/preparation content and attempt tags. Fixed settings cannot be overridden to unsupported values. A guard confirms all registered regions before driver invokes root; Terraform never registers protection itself after instances.

Pair outputs contain role->instance ID/private/public IPv4, AMI/shape/region/zone/zone ID, placement IDs, boot volume IDs, network/security/peering resource IDs and requested preparation settings. Secret-bearing Terraform output/state never becomes the public artifact directly. Driver validates/normalizes observed values into ProviderDeploymentOutput and writes only `terraform-outputs.redacted.json`.

S4 uses both provider aliases and both expiry executions first. Peering includes the requester resource, London accepter and routes in each VPC; the accepter alone cannot delete peering. Selected subnet/addresses are in research.md. One auto-assigned public IPv4 per VM; no EIP/NAT/bastion.

## AWS control-plane and trust boundary

All local AWS CLI, Terraform, ssh-keyscan and ssh-keygen invocations are shell-free CommandRequests with explicit cwd/action_id/deadline/redactions. Credential profile/reference is supplied outside exported environment values. Console GetConsoleOutput is authenticated and checked against account/region/instance/current boot. Candidate keyscan content is never trusted until its SHA256 fingerprint matches the console fingerprint; after match, host_key_reference points to the private strict known_hosts file. Public authentication-key fingerprint is not host identity.

SSM calls use documented Start/Get/StopAutomationExecution APIs. Registration readback predicate and two-step sleep/script are in research.md. Fakes use real AWS response keys and status strings. Automation API acceptance alone is not proof that the role can later stop resources. Offline tests validate policy/argv/response handling; paid acceptance tests actual IAM and abrupt controller loss.

Cleanup inventories exact owned IDs plus tags, paginated in every region: instances (terminated is absent compute), boot EBS volumes, ENIs/public addresses (auto-assigned address disappearance), placement groups, peering, security groups/subnets/routes/IGWs/VPCs, keypairs and safety document/role/policies. State-only absence, one empty page, successful destroy, or stopped compute is insufficient. Inventory denial/timeouts are unknown and block closure. Never delete another attempt's resource or broad account inventory.
