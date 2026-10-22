# F06 offline validation guide

Run from `/private/tmp/cnb-f06`, branch `codex/f06-v2-azure`. Explicit feature context is `GIT_BRANCH_NAME=codex/f06-v2-azure SPECIFY_FEATURE_DIRECTORY=specs/008-v2-azure-provider`. The local scripts report the spec-directory basename as BRANCH; actual Git branch remains the assigned branch. Do not create a second branch from that script label.

## Available at planning time

Python `/private/tmp/cnb-offline-venv/bin/python` (3.11.13) and `/private/tmp/cnb-tools/terraform` (1.16.6) are installed. Existing shared-schema regression files exist. F06 source/tests/HCL are implementation targets and are not present yet. No F06 implementation test or Terraform validation has passed at planning time.

```sh
test "$PWD" = /private/tmp/cnb-f06 || exit 1
GIT_BRANCH_NAME=codex/f06-v2-azure SPECIFY_FEATURE_DIRECTORY=specs/008-v2-azure-provider .specify/scripts/bash/check-prerequisites.sh --json --require-spec --require-tasks --include-tasks
git diff --check
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_schema_integrity.py tests/contract/test_v2_schema_export.py
```

The schema check is a regression of existing shared contracts, not proof of an Azure provider implementation.

## Required after implementation

These are planned validation commands, not already executed F06 evidence. Confirm each target exists first; use no Azure credentials/configuration and inject all Terraform/Azure external boundaries in pytest.

```sh
test "$PWD" = /private/tmp/cnb-f06 || exit 1
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_azure_provider_contract.py tests/unit/test_azure_provider.py tests/integration/test_azure_provider_flow.py
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_schema_integrity.py tests/contract/test_v2_schema_export.py
/private/tmp/cnb-tools/terraform fmt -check -recursive terraform/azure
/private/tmp/cnb-tools/terraform -chdir=terraform/azure/pair init -backend=false -input=false
/private/tmp/cnb-tools/terraform -chdir=terraform/azure/pair validate
/private/tmp/cnb-tools/terraform -chdir=terraform/azure/pair test
/private/tmp/cnb-tools/terraform -chdir=terraform/azure/expiry init -backend=false -input=false
/private/tmp/cnb-tools/terraform -chdir=terraform/azure/expiry validate
/private/tmp/cnb-tools/terraform -chdir=terraform/azure/expiry test
git diff --check
```

`terraform test` is permitted only with checked-in mock_provider definitions and `command=plan` for every run; inspect that condition before invoking. No provider data source may escape the mocks. `init` may download the AzureRM plugin from the registry; it does not call Azure and must not configure a remote backend. Record unavailable registry access as not run, never replace schema validation with a claimed pass. Do not run real plan/apply or direct az commands during this workflow. F06 driver tests inspect those proposed argv/payloads through fakes.

## Expected evidence

- Four static scenario plans and invalid-input cases match selected zones/shape/image/disk/acceleration/NSG/peering. No cloud network behavior is implied.
- Actual request/payload ordering proves reservation and independent published action/binding readback before pair apply; expired, unpriced or mismatched inputs have zero VM-creating requests.
- An independent fake-provider clock attempts both targets after the fake controller stops. Async acceptance, initially absent/creating targets, late appearance and failed deallocation stay distinguishable from observed deallocated status and full deletion.
- Partial apply/output/identity/cleanup failures preserve materialized private state/journal/known_hosts where appropriate and redacted durable evidence. Residual/unknown disks, IPs, VM or expiry resources block cleanup success/new launch. Parallel attempts cannot overwrite/delete each other.
- Record exact head/base, commands/exits/counts, environment, assumptions and unrun checks in `docs/validation/f06-azure-provider.md`. The convergence gate reviews code, real HCL and script content, not only task boxes.

## Separate paid limits

After integrated F10 and separate authorization, refresh dated price/register inputs, subscription quota/region/image/SKU/zone availability and Automation runtime/modules. Prove effective custom-role permissions, authenticated Run Command host identity, registration before VMs, actual one-time dispatch, both Azure deallocations after abrupt controller loss, then full deletion of disks/addresses/account/roles. Record actual duration, dispatch lag, failures and costs. Those are unrun checks here. Static runbook checks/fake REST do not establish PowerShell execution compatibility or a hard billing cutoff; no real resources, traffic, calibration, diagnostic day or research execution is authorized by this guide.
