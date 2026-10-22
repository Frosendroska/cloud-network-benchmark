# F05 AWS provider implementation

F05 adds an AWS-specific provider adapter with an isolated regional SSM Automation safety root and a separately applied two-VM topology root. `AwsProviderDriver.register_expiry` requires the active reservation and dated cost identity, registers and reads back a running absolute-time sleep in every involved region, and only then permits pair apply. Its 360-second trigger lead comprises a 300-second discovery/stop window and 60 seconds of planned margin. The Automation script re-queries for the entire bounded horizon using both run and deployment tags, records stop requests separately from observed states, and never deletes resources. A successful stop is compute expiry evidence, not cleanup completion.

The pair root selects two On-Demand Ubuntu 24.04 amd64 `m7i.xlarge` VMs, 32 GiB gp3 root volumes, fixed scenario-specific Frankfurt/London subnets and private benchmark ingress. The adapter revalidates the account, image owner/architecture, instance type, physical zone ID, addresses, root volume and console-authenticated ED25519 host key before creating strict attempt-local `known_hosts`. State, tfvars, command records and host trust files are private and isolated by run/deployment. Cleanup attempts Terraform destroy, independently inventories tagged resources in all involved regions, and retains expiry support if inventory is incomplete or resources remain.

The scenario admission evidence and network values are supplied by the caller and must remain sourced; this adapter does not invent an S4 review or imply that a throughput ceiling is observed. The dated public SSM pricing fixture assumes no free tier and records $0.013 per regional two-step/300-second execution ($0.026 for two regions). This is a planning input, not a billing guarantee. Expiry automation has startup, stop latency, and late-creation limits; later paid acceptance must verify the registered safety role and real resource behavior.

## Ownership and source identity

- Feature: `specs/007-v2-aws-provider`; owned paths are `src/cloud_network_benchmark/providers/aws.py`, `terraform/aws/{pair,safety}`, `tests/fixtures/aws`, AWS unit/contract/integration tests, this document and the feature task markers.
- Frozen interfaces: F01R provider deployment, admission, evidence and command contracts. No shared model/schema edits.
- Research authority: canonical Thesis design, `DECISIONS.md`, `experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md`, `experiments/EXPERIMENTS.md`, exact F05 packet in `tracking/IMPLEMENTATION_PROMPTS.md`, and `tracking/M04_implementation.md`; read-only.
- Assigned branch: `codex/f05-v2-aws`. Original implementation base: `42b5e590f396cd39b1124820fec65a6d001c594c`. F02R and F03 have since merged into main; their merge is to be incorporated at a clean checkpoint before final verification.
- Tools: Python `/private/tmp/cnb-offline-venv/bin/python` 3.11.13; Terraform `/private/tmp/cnb-tools/terraform` v1.16.6 darwin_arm64.
- Expected lock/provider identity: not yet recorded. Terraform registry DNS was unavailable during provider initialization, so no provider lock file or schema validation is claimed. No lock hashes are guessed.

## Offline checks and limits

Passed so far: `tests/unit/test_aws_provider.py` and `tests/unit/test_aws_expiry.py` (18 tests), using credential-free fakes. Terraform fmt was run on both roots. `terraform init -backend=false -input=false` was attempted; it exited 1 because DNS resolution for `registry.terraform.io` failed. Consequently `terraform validate` and lock generation were not run. The unrelated cloud APIs, real credentials, SSH, plan/apply/destroy, and Thesis writes were not run.

Follow-up evidence belongs here after the clean main integration: exact head/base, expanded AWS/F01R test results, both root fmt/init/validate exit codes, provider lock versions if normal registry access is available, and truthful remaining task markers. A fake trace establishes adapter control flow only; it does not establish AWS IAM propagation, SSM durability, stop latency, deletion, billing or live networking.
