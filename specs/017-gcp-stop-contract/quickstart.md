# Offline validation guide

Run implementation checks only after coordinator records the merged acquisition prerequisite and releases this shared-contract writer. Use `/private/tmp/cnb-gcp-stop` on `codex/f01r-gcp-stop`. No credentials, cloud/SSH commands, Terraform apply or benchmark traffic.

## 1. Context

```bash
test "$PWD" = /private/tmp/cnb-gcp-stop
git branch --show-current
git rev-parse HEAD
git status --short
```

Record exact prerequisite/base/source/head in feature `validation.md`; rescan `instance_termination_action` in `src/`, `configs/`, `tests/` and active reference text. Preserve other writers' work and specs 001/002. Current probes in [research.md](research.md) show unimplemented debt.

## 2. Focused runtime/schema corpus

After implementing the [acceptance matrix](data-model.md):

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_gcp_stop_contract.py tests/contract/test_campaign_config_contract.py tests/contract/test_deployment_contract.py tests/contract/test_v2_schema_export.py tests/integration/test_dry_run_cli.py
```

The new `test_gcp_stop_contract.py` is planned, absent at planning handoff. Expect exit 0 after implementation: independent boundary/schema parity, all active config/mode/scenario coverage, exact handoff/preview options, AWS/Azure and sanitized error controls.

## 3. Canonical export

```bash
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
```

Only export on the released merged base. Expect exit 0 and byte-identical repeat export; preserve merged acquisition content. Never hand-edit JSON or copy another worktree's exports.

## 4. Existing CLI previews

```bash
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark validate --config configs/tests/exp-903-three-provider-smoke.yaml --format json
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark dry-run --config configs/experiments/exp-001-multi-provider.yaml --format json
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark dry-run --config configs/experiments/exp-001-calibration.yaml --format json
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark dry-run --config configs/experiments/exp-001-diagnostic-day.yaml --format human
```

Expect exit 0 and STANDARD/STOP in GCP child options, preserving pending admission/calibration/schedule evidence. Automated tests cover all eight GCP examples, including S2/S3/S4. Previews describe actions without executing them and never authorize P2/P3 launches.

## 5. Full offline gate/history

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q --tb=short
git diff --check
git diff --exit-code e03a07eaa5ed9ea372c695db19fd6fbcffdad024 -- specs/001-core-offline-contracts specs/002-vm-access-bootstrap
```

Expect exit 0; record actual counts/results, not earlier green evidence. Inspect diff against recorded integrated base: only owned paths change and AWS/Azure YAML blocks remain equal. Mark not-run checks explicitly. Hand off to Spec Kit convergence, then coordinator integration/PR/merge. F07 implementation waits for verified merge. Actual GCP permissions, expiry, billing and cleanup remain unproved.
