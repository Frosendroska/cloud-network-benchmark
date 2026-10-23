# F07 offline validation guide

Planning only. New GCP test paths and Terraform roots below are **not implemented and not run** at this handoff. Implementation first requires verified merged F01R_GCP_EXPIRY STOP migration; current base e03a07e rejects STOP. No actual GCP credentials, API calls, SSH, Terraform cloud plan/apply/destroy or traffic are needed.

Use `/private/tmp/cnb-f07` as cwd and `login:false`, guarded by `test "$PWD" = /private/tmp/cnb-f07`. Set `PYTHONPATH=src` and `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis` for checks. Actual tool availability was checked: `/private/tmp/cnb-offline-venv/bin/python` exists; Terraform `/private/tmp/cnb-tools/terraform` is v1.16.6 darwin_arm64.

After implementation, run:

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_gcp_provider.py tests/unit/test_gcp_expiry.py tests/contract/test_gcp_provider_contract.py tests/integration/test_gcp_provider_sequences.py
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_v2_handoffs.py tests/contract/test_deployment_contract.py tests/unit/test_v2_admission_artifacts.py
/private/tmp/cnb-tools/terraform -chdir=terraform/gcp/protection fmt -check
/private/tmp/cnb-tools/terraform -chdir=terraform/gcp/pair fmt -check
/private/tmp/cnb-tools/terraform -chdir=terraform/gcp/protection init -backend=false -input=false
/private/tmp/cnb-tools/terraform -chdir=terraform/gcp/pair init -backend=false -input=false
/private/tmp/cnb-tools/terraform -chdir=terraform/gcp/protection validate -no-color
/private/tmp/cnb-tools/terraform -chdir=terraform/gcp/pair validate -no-color
git diff --check
```

Backend-disabled init may download provider binaries from the Terraform registry with normal tool permission; it must not contact GCP. Use credential-empty environments with metadata credential lookup disabled/unreachable. Record actual provider version/lock and inspect `terraform providers schema -json` for both template scheduling and source-template fields. No `plan`, `apply` or `destroy` against GCP is authorized. Tests must inject command runners and guard against subprocess/cloud fallthrough.

Expected evidence:

1. Native template expiry: two observed provider template registrations before pair creation, unique IDs/property hashes; exact source-instance-template requests with no overrides; observed instance absolute STOP. Local due time alone does not prove trigger. Failed/uncertain/missing/mutated protection produces zero pair commands.
2. Original cap activation, T=cap end−30 seconds, positive complete remaining workflow fit twice, delayed setup/time exhaustion/restart cases and no cap reset. Thirty seconds concerns initiation only; no hard stop or billing bound.
3. S1–S4 exact two-VM/network/security/image/placement structure, no max_distance/Tier_1/Spot/MIG/platform, and private S4 custom VPC with both subnets. Unsupported settings fail closed.
4. Documented GCP response shapes and authenticated hostkeys; project/zone/numeric ID or key mismatch, missing metadata, invalid images and partial outputs retain honest failures.
5. Cleanup after partial creation/output failure/timeout/interruption, fake controller loss leaving native policy, paginated inventory, foreign ownership, stopped-but-not-deleted compute, disk/address leftovers and cancellation ordering. Repeated cleanup never recreates resources or extends lifetime.
6. Dated zero-support-cost source/hash plus separately sourced reserved compute/disk/IP/transfer/tax/uncertainty; GiB conversion and no invented current quote.

Update `docs/f07-gcp-provider.md` with tested head/base, exact commands/exit codes/counts/skips, lock versions and limitations. A fake cannot prove template inheritance enforcement in GCP, actual IAM, quota/zone/image availability, host trust, placement, private routing, stop delay, billing or deletion. The separate authorized paid-acceptance stage after F10 must test these, especially abrupt controller loss. No paid runbook execution belongs here.
