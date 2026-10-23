# F06 Azure provider offline validation

## Scope and environment

This repair completes F06 convergence tasks T028–T035 in /private/tmp/cnb-f06 on codex/f06-v2-azure, based on merged main e03a07eaa5ed9ea372c695db19fd6fbcffdad024. Python is 3.11.13, Terraform 1.16.6, the locked AzureRM provider is 4.81.0, and the local PowerShell runtime is the official 7.5.11 macOS ARM64 release (archive SHA-256 93c5d5b71a3937e3dbb68934886d318689f57ecda466443764a9d7101f196e6f). The temporary PowerShell files are under /private/tmp/cnb-tools, outside the repository.

No Azure credentials, Azure API requests, real Terraform plans/applies/destroys, SSH connections, or benchmark traffic were used. Terraform checks used backend-disabled initialization and checked-in mock_provider plan tests. PowerShell 7.5.11 parsing/fakes do not establish compatibility with hosted PowerShell 5.1 or Az.Accounts.

## Executed checks

| Check | Result |
| --- | --- |
| Focused F06 contract/unit/integration tests | PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_azure_provider_contract.py tests/unit/test_azure_provider.py tests/integration/test_azure_provider_flow.py — exit 0, 65 passed. |
| Full Python suite | PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q — exit 0, 835 passed. |
| Shared schema regression | PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_schema_integrity.py tests/contract/test_v2_schema_export.py — exit 0, 7 passed. |
| Terraform formatting | terraform fmt -check -recursive terraform/azure — exit 0. |
| Pair Terraform root | terraform -chdir=terraform/azure/pair init -backend=false -input=false, validate, and test — exit 0; 5 mocked plan tests passed for four scenarios and one invalid topology. |
| Expiry Terraform root | terraform -chdir=terraform/azure/expiry init -backend=false -input=false, validate, and test — exit 0; 1 mocked plan test passed. |
| PowerShell parser and fake process | PowerShell 7.5.11 AST parse — exit 0, no syntax errors, 745 tokens. The focused suite ran the three fake runbook modes successfully. |
| Whitespace | git diff --check — exit 0. |

The Terraform provider binary was already present in the isolated root, but the first sandboxed provider handshake failed. The authorized local, backend-disabled Terraform checks passed when run outside that sandbox. No Azure or other cloud service was contacted.

## Repair behavior

The driver privately saves immutable pair and expiry Terraform variable inputs, initializes each isolated Terraform root during cleanup, and passes those original inputs to destroy. The recovery journal records deterministic expiry child IDs before mutations and observed role/account identities as they become available. If prerequisite creation completed but its output read was interrupted, cleanup retrieves the outputs from the retained state. Partial registration cleanup is tested across prerequisite, runbook, content, publication, schedule and job-binding failures. Child deletion uses exact identity reads, recognizes only explicit not-found responses as absence, and verifies absence after successful delete. A repeated cleanup can resume after an interrupted child deletion.

Cleanup consumes every page of the Azure Resource Manager resource list, rejects blank or malformed responses and foreign continuation links, independently verifies exact VM/account absence, the resource group, role assignments and the custom role definition, and leaves launch blocked on unknown or residual resources. Cleanup actions and returned evidence redact private material and cap command output. The exact reserved trigger and original UTC/monotonic cap are rechecked after expiry reads and pair initialization immediately before pair apply; workflow duration is bounded by admitted mode and reservation lifetime.

Expiry registration and the pre-create recheck observe the Automation account system-assigned identity, exact role definition assignment, PowerShell runbook type/runtime and publication, Az.Accounts module readiness, UTC one-time schedule, exact runbook/schedule binding and cloud runOn. poll_seconds is an integer from 1 to 300. These checks establish declared Azure configuration only; effective RBAC is not proven.

Image preflight reads a concrete Canonical Ubuntu 24.04 marketplace image or shared-gallery version and requires x64 metadata. Terraform outputs label the configured image as requested_image; after creation, the driver separately reads the VM image reference and records its observed exact identity. Authenticated Run Command uses the selected VM resource ID in the request and a unique per-invocation nonce in stdout; only one successful stdout status and one valid host key after that nonce can produce a private known_hosts entry. The response fixture follows the documented value[] shape for [Azure Compute Run Command](https://learn.microsoft.com/en-us/rest/api/compute/virtual-machines/run-command?view=rest-compute-2025-04-01).

## Limits

The fake tests prove local request/response validation, state transitions, redaction and cleanup gating. Terraform mock plans prove intended HCL configuration only. Neither proves Azure image availability, quota, networking, physical placement, effective permissions, Automation publication or dispatch timing, host-key retrieval, deletion, or cost.

Azure deallocation is not full billable-resource cleanup. The driver retains the independent Automation fallback until exact VM absence and scoped inventory are proven. A 202 response is asynchronous acceptance, not completed deallocation; retries are bounded by the registered retry end but an in-flight REST call cannot be preempted. There is no hard dispatch or billing guarantee. Dated expiry prices and job-minute estimates are fixture inputs that must be refreshed before any separately authorized paid run. The documented 12,500 Mbps D4s_v5 aggregate maximum is not measured or promised throughput.
