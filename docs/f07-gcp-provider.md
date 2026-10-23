# F07 GCP provider: implementation and offline evidence

F07 binds the approved GCP `STANDARD`/`STOP` behavior to immutable, pre-created Compute Engine instance templates with an absolute RFC3339 `termination_time`. It creates exactly two standalone `google_compute_instance_from_template` resources from each template's `self_link_unique`. The cap is the reservation's original expiry minus a 30-second initiation lead. That lead is not a stop-completion or billing guarantee. The driver requires complete-workflow fit before registration and immediately before pair creation.

The coordinator verified F01R_GCP_EXPIRY PR #17 merged at main `2cd36e9ea7b12681545e8c6680ccda42388ffded`. F07 integrated that main normally; tested integration head/base was `d2bd3bc296c85fb95be7debdcdbd61cbc759369e`. STOP/DELETE regression tests passed 94/94 and canonical schema drift check passed before F07 code changes. Historical F01/F02 specifications were not changed.

## Provider identity and Terraform structure

Both roots pin `hashicorp/google` 7.46.1 (`~> 7.0`) in committed lock files. Terraform 1.16.6 was used. `terraform providers schema -json` exposed the template scheduling fields `provisioning_model`, `instance_termination_action`, and `termination_time`, template `self_link_unique`, and pair `source_instance_template`. Both configurations validated against that schema. The provider binaries were downloaded from the Terraform Registry with backend disabled; no GCP credential lookup or API request occurred during validation.

The original provider implementation commit was `c25552f`; the current implementation also includes the follow-up source/HCL/test changes recorded in the final F07 feature commit. Both lock files pin 7.46.1 with Terraform-generated package hashes; the platform hash is `h1:oaPjmiPJz4rtO1CCcQ53YsY+Xr/vUEvmYRz10SGieuk=`.

The protection root owns a custom MTU-1460 VPC, regional subnets, restricted ingress, optional single S3 compact placement policy, and two templates; it declares no standalone VM, disk, or reserved address. The pair root declares exactly two template-inherited VMs and no overrides. Both use `n2-standard-4`, Ubuntu 24.04 image references supplied as immutable image IDs, one gVNIC, 32-GiB `pd-balanced` boot disk, and ephemeral public management address. S4 places the pair in Frankfurt/London subnets on one VPC. S2 uses two Frankfurt zones. S3's only placement policy is `COLLOCATED`, without `max_distance`.

The complete-workflow cost input is required to be dated and source/hash identified. The separate support-cost fixture cites Google's statement that instance templates have no additional charge and records zero separate placement-policy support cost. It does not quote VM, disk, address, transfer, tax, or total prices. Those costs must come from the dated reservation evidence before admission. The fixture hash covers the stored source capture, not a signed or live web page.

The provider does not change the after-measurement result flow: measurement finishes and native files are copied and checksum-verified before full resource deletion. Native STOP is compute protection only; it never substitutes for that final copy or cleanup.

## Offline checks

On 2026-10-23, with `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`, and `/private/tmp/cnb-offline-venv/bin/python`:

| Check | Result |
| --- | --- |
| `pytest -q tests/contract/test_gcp_stop_contract.py tests/contract/test_campaign_config_contract.py tests/contract/test_deployment_contract.py tests/contract/test_v2_schema_export.py tests/integration/test_dry_run_cli.py` | 94 passed |
| Canonical schema `python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check` | exit 0 |
| GCP unit, contract and sequence tests: `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest tests/unit/test_gcp_provider.py tests/unit/test_gcp_expiry.py tests/contract/test_gcp_provider_contract.py tests/integration/test_gcp_provider_sequences.py -q` | 19 passed |
| Terraform init, protection and pair, `-backend=false -input=false` | exit 0; Google 7.46.1 locked |
| Terraform format, both roots, `fmt -check` | exit 0 |
| Terraform validate, both roots, `validate -no-color` | exit 0 |
| Terraform provider schema JSON, both roots | exit 0; expected scheduling and source-template fields present |
| `git diff --check` | exit 0 |
| Full offline suite: `PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q` | 1,078 passed, 0 skipped |

The initial registry lookup from the sandbox failed DNS. The same backend-disabled official provider download succeeded after normal tool permission review; subsequent local provider subprocess checks also required normal tool permission review. This did not cross the GCP/cloud boundary.

## Limits and remaining work

No GCP API, credentials, VM, SSH session, benchmark traffic, Terraform plan/apply/destroy, or paid run was used. The present implementation includes typed runtime checks, private attempt journaling, native-template registration/readback, pre-create lifetime fit, pair apply/readback, guest-attribute host-key acquisition, and cleanup sequencing. It remains an offline implementation and has not been through Sol convergence.

Tests use fake time and synthetic artifacts; they do not prove IAM permissions, template inheritance behavior, selected image availability, actual guest-agent hostkeys, placement acceptance, routes, firewall behavior, stop delay, billing, or deletion. Cleanup scans the selected zones/regions and global support inventories and filters by recorded numeric IDs, ownership labels, and the attempt namespace. `gcloud ... list --format=json` is relied upon for CLI pagination; the implementation fails closed on command errors or an unexpected response shape, but it does not independently expose page tokens. Dedicated injection coverage for denied later pages, reused numeric IDs, residual disk/address/template permutations, interrupted recovery and cancellation failure remains incomplete and is left for convergence. S2/S4 require a sourced review at runtime, and no review or paid admission is created here.

The full suite and Terraform validation are offline implementation evidence, not the complete F07 acceptance set. Several task rows remain unchecked where the requested failure corpus or per-scenario acceptance evidence is incomplete; no paid/cloud gate or F10 readiness is claimed.

Final implementation source commit: `4336ce8c5fe3a7f5dbe542fec365f0310ea7eeb6`. The branch's evidence-only HEAD is `bdcd8f0f20f70a80a0130e9ae694b8e4d7fe51c5` on `codex/f07-v2-gcp` (ahead seven commits of `origin/main`, with clean worktree). `specs/009-v2-gcp-provider/tasks.md` has 13/26 tasks checked and 13 still open for convergence assessment.
