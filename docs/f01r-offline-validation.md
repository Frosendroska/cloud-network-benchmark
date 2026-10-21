# F01R offline validation

## Environment and baseline

- Worktree: `/private/tmp/cnb-f01r`
- Branch: `codex/f01r-v2-contracts`
- Reviewed implementation start: `17ae3714cfd043df62721a1761bb8d0547a58eb7`
- Merged dependency base: `e7fc15ff1a7d6a4aa43dfebe5209592689a87fa7`
- Python: `/private/tmp/cnb-offline-venv/bin/python`
- Host: Darwin arm64
- Test import path: `PYTHONPATH=src`; Thesis source root: `/Users/katya/Desktop/University/Masters/Thesis`
- Source-code test commit: `7d3f6ca5a54d68cb465bd6579e5cd80b599bc917`. Schema export/check ran against that worktree; this metadata/artifact follow-up adds the generated schema files and this exact reference.

Baseline command:

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
```

Exit 1: **203 passed, 3 failed in 3.06s**. The failures matched the planned migration cases:

- `tests/contract/test_config_traceability.py::test_every_executable_config_has_a_concrete_thesis_anchor` assumed legacy `### EXP-001` headings rather than the current Thesis scenario/config table.
- `tests/contract/test_deployment_contract.py::test_deployment_output_links_connection_roles_and_canonical_path` expected legacy optional-scenario behavior; V2 requires scenario identity even for partial output.
- `tests/contract/test_remote_access_contract.py::test_deployment_handoff_requires_scenario_metadata` used malformed `run_id="r"`, which failed provider-suffix validation before reaching the intended scenario assertion.

No cloud, credential, Terraform, or benchmark operation was run. Historical `specs/001-core-offline-contracts/` and `specs/002-vm-access-bootstrap/` remain byte-identical to the merged dependency base.

## Final implementation validation

The exact quickstart full-suite command passed:

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q --tb=short
```

Exit 0: **210 passed in 5.70s**. This includes the baseline migration cases, local Git provenance scenarios, V2 manifests and failure preservation, deployment/access handoffs, validation/artifact/timing contracts, schema consistency, and pending/frozen acceptance stream resolution.

Schema export and drift check both exited 0:

```bash
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
```

All four quickstart CLI routes exited 0 without launching external actions:

```bash
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark validate --config configs/tests/exp-903-three-provider-smoke.yaml --format json
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark dry-run --config configs/experiments/exp-001-multi-provider.yaml --format json
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark dry-run --config configs/experiments/exp-001-calibration.yaml --format json
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark dry-run --config configs/experiments/exp-001-diagnostic-day.yaml --format human
```

The acceptance preview preserves pending common-N state while resolving multi-flow to four streams; a focused test also verifies a frozen N is used after calibration. Research pending N remains null and blocking. The three-provider research preview remains pending admission/calibration. Calibration renders twelve ordered units and the day mode renders one continuous unit with its probe chunk template.

Final hygiene checks:

```bash
git diff --check
git diff e7fc15ff1a7d6a4aa43dfebe5209592689a87fa7 -- specs/001-core-offline-contracts specs/002-vm-access-bootstrap
```

Both exited 0; the historical-spec diff was empty. Local CI configuration was not run or inspected as a remote CI result. Terraform static checks are not applicable/not run because F01R does not change Terraform. No cloud, SSH/SCP, benchmark, F10, or real expiry/readiness operation was run; this work is credential-free contract and CLI validation only.

## Delivery boundary

Schema V2, configuration/native intent, provenance snapshots, manifests, handoff DTOs, deterministic schema export, validation/timing/artifact contracts, and human/machine dry-run previews are implemented in the assigned F01R scope. Source prices/cost snapshot hash remain unavailable because no exact workbook snapshot was supplied; no value was inferred or fabricated. F01R completion does not establish cloud readiness or authorize paid work. Next stage is Spec Kit convergence against the delivered feature artifacts, then coordinator review/merge before dependent features consume these contracts.
