# F01R offline validation

## Environment and baseline

- Worktree: `/private/tmp/cnb-f01r`
- Branch: `codex/f01r-v2-contracts`
- Reviewed implementation start: `17ae3714cfd043df62721a1761bb8d0547a58eb7`
- Merged dependency base: `e7fc15ff1a7d6a4aa43dfebe5209592689a87fa7`
- Python: `/private/tmp/cnb-offline-venv/bin/python`
- Host: Darwin arm64
- Test import path: `PYTHONPATH=src`; Thesis source root: `/Users/katya/Desktop/University/Masters/Thesis`

Baseline command:

```bash
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
```

Exit 1: **203 passed, 3 failed in 3.06s**. The failures match the planned migration cases:

- `tests/contract/test_config_traceability.py::test_every_executable_config_has_a_concrete_thesis_anchor` assumes legacy `### EXP-001` headings rather than the current Thesis scenario/config table.
- `tests/contract/test_deployment_contract.py::test_deployment_output_links_connection_roles_and_canonical_path` expects the legacy optional-scenario behavior; V2 requires scenario identity even for partial output.
- `tests/contract/test_remote_access_contract.py::test_deployment_handoff_requires_scenario_metadata` uses malformed `run_id="r"`, which fails provider-suffix validation before reaching the intended scenario assertion.

No cloud, credential, Terraform, or benchmark operation was run. Historical `specs/001-core-offline-contracts/` and `specs/002-vm-access-bootstrap/` must remain byte-identical.

## Implementation validation

Foundation checkpoint (source HEAD is the implementation worktree state after T004):

```bash
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_v2_common.py tests/unit/test_v2_admission_artifacts.py
```

Exit 0: **14 passed**. This checks common identity/status/evidence rules, schedule/admission shapes,
provider expiry semantics, safe artifact paths, symlink containment and verification evidence.
The full suite is not yet run after implementation changes. Schema export/check, quickstart CLI
routes, `git diff --check` for the final delivery head, and required CI status remain pending.
