# F06 Azure provider offline validation

## Scope and environment

Validation ran in `/private/tmp/cnb-f06` on `codex/f06-v2-azure`, based on merged F05/AWS `e03a07eaa5ed9ea372c695db19fd6fbcffdad024`. The feature commit before final evidence is `be65da8940180c16b47393e84c97315f4334c9da`; final code-and-evidence head is recorded in the commit history. Python was 3.11.13, Terraform 1.16.6 and the locked AzureRM provider 4.81.0. The AzureRM plugin was installed locally for backend-disabled validation. No Azure credentials, API requests, real plan/apply/destroy, SSH connection or benchmark traffic were used.

For required offline runbook parsing and fake-cmdlet execution, the temporary runtime was the official PowerShell 7.5.11 macOS ARM64 release from [PowerShell/PowerShell v7.5.11](https://github.com/PowerShell/PowerShell/releases/tag/v7.5.11). Its archive SHA-256 was `93c5d5b71a3937e3dbb68934886d318689f57ecda466443764a9d7101f196e6f`; it was extracted under `/private/tmp/cnb-tools` and is not part of the repository. This does not establish compatibility with the specified hosted PowerShell 5.1 Automation runtime or Az.Accounts module.

## Executed checks

| Check | Command / result |
| --- | --- |
| Focused Azure contract, unit and integration tests | `PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_azure_provider_contract.py tests/unit/test_azure_provider.py tests/integration/test_azure_provider_flow.py` — exit 0, 41 passed in 2.13s. |
| Full Python suite | `PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q` — exit 0, 811 passed in 40.57s. |
| Shared schema regression | `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_schema_integrity.py tests/contract/test_v2_schema_export.py` — 7 passed. |
| Terraform formatting | `/private/tmp/cnb-tools/terraform fmt -check -recursive terraform/azure` — exit 0. |
| Pair Terraform root | `/private/tmp/cnb-tools/terraform -chdir=terraform/azure/pair init -backend=false -input=false`, `/private/tmp/cnb-tools/terraform -chdir=terraform/azure/pair validate`, `/private/tmp/cnb-tools/terraform -chdir=terraform/azure/pair test` — all exit 0; 5 mocked plan tests passed across four scenarios and invalid topology. |
| Expiry Terraform root | `/private/tmp/cnb-tools/terraform -chdir=terraform/azure/expiry init -backend=false -input=false`, `/private/tmp/cnb-tools/terraform -chdir=terraform/azure/expiry validate`, `/private/tmp/cnb-tools/terraform -chdir=terraform/azure/expiry test` — all exit 0; 1 mocked plan test passed. |
| Runbook parser | `/private/tmp/cnb-tools/powershell-7.5.11/pwsh -NoLogo -NoProfile -Command '$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile("/private/tmp/cnb-f06/terraform/azure/expiry/deallocate.ps1", [ref]$tokens, [ref]$errors) | Out-Null; if ($errors.Count) { $errors; exit 1 }; "AST parse passed; tokens=$($tokens.Count)"'` — exit 0, no syntax errors, 745 tokens. |
| Fake runbook process | `tests/integration/test_azure_provider_flow.py -k runbook_fake_process` — 3 passed: late/initially absent target, transient A failure while B is attempted, and one denied A read followed by recovery. Fake cmdlets distinguish HTTP 202 from observed deallocated state. |
| Diff whitespace | `git diff --check` — passed. |

`terraform init` uses `-backend=false`; `terraform test` is restricted to checked-in `mock_provider` plan runs. The focused test suite exercises fake command/request/clock boundaries and private materialization. No cloud behavior is inferred from those passes.

## Implemented behavior and operational limits

The Azure-local driver creates isolated attempt workspaces and pair/expiry Terraform data directories, writes a private recovery journal before mutation, keeps command deadlines tied to the caller's original cap, requires matching reservation and dated expiry cost evidence, and registers/read-backs one UTC Automation schedule and binding before pair creation. It checks published runbook content SHA-256, job parameters and exact narrow role definition/assignment before apply. Independent deallocation attempts both deterministic VM IDs. The runbook reports late/absent/creating/denied/failed/async states without claiming a dispatch or completion guarantee. A returned 202 means accepted asynchronously; the driver does not treat it as completed deallocation.

The Automation identity's custom role is scoped to the attempt resource group and contains only VM read, instanceView read and deallocate actions. Effective authorization for the controller and managed identity is unproved. Controller permission to create/manage the attempt group, role, Automation resources, inventory and authenticated Run Command is an external paid-preflight requirement. Host keys are obtained by authenticated Run Command against the exact VM resource IDs and written to a mode-0600 private `known_hosts` file; no `ssh-keyscan` or private host-key retrieval is used.

Admission requires a dated hashed Automation job-minute input, an explicit job-minute bound covering the retry window, cost within the reservation, a minimum 300-second lead, and a complete-workflow estimate that fits before the explicitly selected trigger and reservation cap. These input values are estimates, not a live quote; refresh them before any paid run. Deallocation is not complete billable-resource cleanup. The driver retains the Automation fallback until VM absence and complete scoped inventory are proven, then cancels/deletes the binding, schedule, runbook, role/account and group; unknown or residual inventory blocks success and future launches.

The local test fixture uses the synthetic dated value `{date: 2026-10-22, source_id: automation-prices-2026-10-22, sha256: d…d, rate: $0.002/job-minute, job_minutes_bound: 20}`, with 900 seconds for the complete workflow and 600 seconds post-registration. These values exercise admission and fit checks only; the hash/rate are deliberately fixture values, not a captured Azure quote or approval for expenditure. The linked experiment register remains authoritative for campaign cost and lifetime caps.

`Invoke-AzRestMethod` has no per-request timeout parameter in the [Microsoft Learn command reference](https://learn.microsoft.com/en-us/powershell/module/az.accounts/invoke-azrestmethod?view=azps-16.3.0). The runbook clips its retry loop and sleeps to the absolute retry end, but an individual in-flight REST request cannot be preempted at that deadline. The design therefore has bounded retry intent, not a hard dispatch or billing cutoff. The offline PowerShell runtime is 7.5.11; no PowerShell 5.1 Automation execution or actual Az.Accounts compatibility was tested.

The Terraform fake-provider tests establish planned HCL intent only. They do not prove Azure image/SKU/zone/quota availability, physical co-location, logical-zone equivalence across subscriptions, public routing, NSG packet delivery, Global VNet Peering behavior, Automation publication/dispatch latency, actual role effectiveness, host-key retrieval, deletion, or cost. The `12,500 Mbps` value is the documented aggregate maximum input for Standard_D4s_v5, not observed or promised throughput. Complete resource cleanup before expiry cancellation and abrupt-controller-loss behavior need separate controlled acceptance with refreshed prices and authorization. No F06 step launches measurement or paid experiments.

## Commands

Reproduce from the assigned worktree using the offline commands in the [F06 quickstart](../../specs/008-v2-azure-provider/quickstart.md). The checked-in test fixtures synthesize official response shapes, but provider/network behavior remains unproved until the separate acceptance stage.
