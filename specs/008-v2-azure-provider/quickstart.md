# F06 Azure offline quickstart

This feature can be tested without Azure credentials or Azure API access. Run from the assigned worktree and keep Terraform state in isolated temporary attempt paths. The commands below do not execute `terraform plan`, `apply`, or `destroy`; the Terraform tests use `mock_provider` and `command = plan` only.

```sh
test "$PWD" = /private/tmp/cnb-f06 || exit 1
PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_azure_provider_contract.py tests/unit/test_azure_provider.py tests/integration/test_azure_provider_flow.py
PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_schema_integrity.py tests/contract/test_v2_schema_export.py
/private/tmp/cnb-tools/terraform fmt -check -recursive terraform/azure
/private/tmp/cnb-tools/terraform -chdir=terraform/azure/pair init -backend=false -input=false
/private/tmp/cnb-tools/terraform -chdir=terraform/azure/pair validate
/private/tmp/cnb-tools/terraform -chdir=terraform/azure/pair test
/private/tmp/cnb-tools/terraform -chdir=terraform/azure/expiry init -backend=false -input=false
/private/tmp/cnb-tools/terraform -chdir=terraform/azure/expiry validate
/private/tmp/cnb-tools/terraform -chdir=terraform/azure/expiry test
git diff --check
```

The focused/full pytest runs prepend the temporary official PowerShell runtime so the fixture executes the checked-in runbook with fake cmdlets. If that runtime is unavailable, these two runtime-dependent tests skip and the missing PowerShell execution must be reported; static text checks are not a substitute. Terraform initialization is local-state/backend-disabled and may download only the pinned AzureRM provider. The HCL tests do not contact Azure.

The evidence guide at [`docs/validation/f06-azure-provider.md`](../../docs/validation/f06-azure-provider.md) records the tested versions, exact results and runtime limits. Real Automation dispatch/deallocation, effective cloud permissions, image/SKU/zone/quota availability, SSH connectivity, full resource deletion and all paid acceptance remain separately authorized work.
