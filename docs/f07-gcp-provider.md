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

The full suite and Terraform validation are offline implementation evidence, not cloud acceptance or F10 readiness.

Final implementation source commit: `4336ce8c5fe3a7f5dbe542fec365f0310ea7eeb6`. Subsequent commits only record and clarify this evidence checkpoint. `specs/009-v2-gcp-provider/tasks.md` has 13/26 tasks checked and 13 still open for convergence assessment.

## Follow-up implementation checkpoint (2026-10-23)

The follow-up implementation extends the earlier committed checkpoint above; its recorded counts remain historical. Provider execution now uses thread-local attempt deadlines, validates immutable template numeric and unique-link identities, validates template creation timestamps against reservation activation and registration, rejects relaunch after partial creation/cleanup, records observed disk identities, and verifies VM roles, zones, addresses, disk/image/architecture, GVNIC, and absolute STOP readbacks. Cleanup scans all project zones, both selected regions, and global support/address inventories, preserves foreign resources, leaves protection installed while compute remains or inventory fails, retries from original private state, and reports due STOP outcomes as unverified. An interrupted provider operation now attempts cleanup, retains the original interruption as primary failure evidence, then propagates it.

Focused current checks: `tests/unit/test_gcp_provider.py`, `tests/unit/test_gcp_expiry.py`, `tests/contract/test_gcp_provider_contract.py`, and `tests/integration/test_gcp_provider_sequences.py` pass 50 tests. The full suite passes 1,109 tests with no skips using the official PowerShell path. The local Terraform format checks pass for both roots. Sandbox plugin startup failed during validate/schema inspection; after normal tool permission review, both local `validate -no-color` checks passed and provider schema inspection confirmed template scheduling fields `provisioning_model`, `instance_termination_action`, `termination_time`, and pair `source_instance_template`. No GCP API or resource operation was invoked.

After the F07 checkpoint commit, the branch normally merged coordinator-verified origin/main PR18 at `027c9cc483f87da89f853e94a68a4c4d9280a503`; the integrated head is `34dc70bbb98f6dd7a2601b1cae40e2b4b0ce5922`. The full offline suite at that integrated head passed 1,127 tests with zero skips. Both Terraform roots passed fmt, validate, and provider-schema checks at the integrated head.

Task evidence is now 25/26 complete. T026 remains pending the separate Sol convergence review; no F07 convergence or cloud-readiness claim is made here. The inventory boundary uses gcloud list commands, which perform pagination internally; command failures and unexpected response shapes remain open cleanup failures. The gcloud wrapper does not expose continuation tokens for direct later-page injection, so this is not represented as independent page-token test coverage. Paid acceptance remains necessary for actual IAM, image availability, policy inheritance, host trust, placement, networking, stop delay, billing, and deletion.

## Phase 7 implementation evidence (2026-10-23)

The 13 appended convergence implementation tasks T027–T039 are implemented in the F07-owned provider, Terraform roots, tests, and this evidence record. They restore the shared Frankfurt subnet and exact per-scenario topology; strictly bind role templates, project/unique IDs, selected settings, costs and reservation cap; recheck workflow fit after template readbacks; inventory canonical orphan VM/disk candidates and fail closed on incomplete gcloud list warnings; preserve launch blockers and independent healthy registered attempts; validate guest-agent SSH host-key blobs and public management addresses; and preserve primary failure evidence through cleanup. S3 placement is optional-review-exempt while S2/S4 retain their sourced review gate.

The focused command `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest tests/unit/test_gcp_provider.py tests/integration/test_gcp_provider_sequences.py -q --tb=short` passed 65 tests. The complete credential-free suite with official PowerShell on PATH passed 1,150 tests with zero skips. Canonical schema `--check` passed. Terraform `fmt -check -recursive terraform/gcp` passed; the protection root mock test passed all four scenario runs and the pair root mock test passed its inherited-pair run; both roots passed `validate -no-color` with the locked local provider. No cloud operation or Terraform plan was run.

Task state after this implementation slice is 38/39 complete: T001–T025 and T027–T039 are checked; T026 remains the separate Spec Kit convergence task for the Sol stage. The gcloud CLI performs internal pagination; F07 rejects scope warnings, command errors and unexpected response shapes, but it does not expose continuation tokens for independently injected later-page failures. No new support-cost claim was added for compute, disks, IPs, transfer, taxes or uncertainty. No paid acceptance or F10 readiness is claimed.

After checkpoint `ff207376f4ad6977f14460da87b7822f586e2f84`, the worktree normally merged coordinator-verified `origin/main` PR19 at `4a80f4af86feddc86e4ab930d254a19e29b35591`; the integrated head is `c695a1a5e135f3b795cdabac21cafce15f02f75c`. At that integrated head the full offline suite passed 1,236 tests with zero skips, schema check and Terraform formatting passed, both GCP Terraform roots passed validation, and the protection/pair mock suites passed 4/4 and 1/1 runs respectively. This includes the F08 sources present on main and is the final integrated F07 implementation checkpoint. T026 remains pending Spec Kit convergence; the remaining cloud and pagination-token evidence limits above are unchanged.

## Phase 8 convergence repairs (2026-10-23)

T040–T043 close the four appended provider findings. Instance-template description fixtures now use the Compute Engine REST `id`, `selfLink`, string-valued `diskSizeGb`, and the actual properties-level scheduling/network fields; Terraform 7.46.1's template output fixture uses `self_link_unique`, which that pinned provider derives from REST `SelfLink` and `Id`. Runtime checks normalize either provider link form and compare the exact project, resource path, numeric ID, and uniqueId. They reject malformed or boolean integer values. Sources: [Compute Engine instanceTemplates.get](https://docs.cloud.google.com/compute/docs/reference/rest/v1/instanceTemplates/get) and [Google provider 7.46.1 template read](https://raw.githubusercontent.com/hashicorp/terraform-provider-google/v7.46.1/google/services/compute/resource_compute_instance_template.go).

Template and instance readback now check network performance, fixed CPU, and node affinity at their documented field locations; bind the complete canonical project/network/subnetwork paths; and require GVNIC in the selected image's `guestOsFeatures`. The journal save path merges immutable IDs and ownership/cap data, disk and VM observations, host bindings, event history and separate failure records so provision's later saves cannot erase earlier checkpoints. The session gate permits a healthy registered sibling from the same live controller while blocking unresolved registration or prior-session state; verified cleanup resolves that blocker. Restart coverage confirms that a saved host address cannot silently change for the same VM ID.

Focused current provider unit and sequence tests pass 77/77. The full offline suite and final integrated static/schema gates have not been rerun in this stage; they remain for the serialized post-convergence gate. No cloud API, credentials, real Terraform plan/apply/destroy, or paid run was used. T001–T025 and T027–T043 are implementation-checked; T026 remains the separate Sol convergence gate. Offline proof still does not establish actual image availability, IAM, guest-agent behavior, native STOP timing/billing, placement acceptance, or deletion behavior.
