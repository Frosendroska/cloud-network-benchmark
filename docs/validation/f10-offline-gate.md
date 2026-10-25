# F10 offline readiness gate

## Scope and prerequisites

This gate is the credential-free, local proof at one clean committed revision. It does not prove Linux guest process behavior, provider permissions, cloud networking, expiry, deletion or billing. It performs no install, Terraform init, registry access, live plan/apply/destroy, cloud CLI, SSH/SCP or benchmark traffic. Prepared tools and locked local providers are prerequisites, never installed by the gate.

The assigned clone is `/private/tmp/cnb-f10`, branch `codex/f10-v2-offline-gate`, based on merged F09/F12 `eb0be0b40d2b38607212cad3761f454295122f9b`. The coordinator prepared Python `/private/tmp/cnb-offline-venv-20261025/bin/python`, Terraform `/private/tmp/cnb-tools/terraform1.16.6`, PowerShell `/private/tmp/cnb-tools/powershell-7.5.11/pwsh7.5.11`, and the local mirror `/private/tmp/cnb-f10-provider-mirror`. Six Terraform roots were initialized beforehand with backend disabled and lockfiles read only. Preparation is not F10 validation evidence. The gate checks these prerequisites and uses only a filesystem provider mirror.

The script verifies clean HEAD/tree before and after execution, refuses reused or source-overlapping evidence paths, removes inherited cloud/SSH credential variables from subprocesses, and records only allowlisted versions and command evidence. Outputs live in a new local evidence directory outside the source checkout. JUnit must contain every named identity and all four factory mode IDs. Only the two exact unavailable-`/proc` tests on Darwin may be recorded as not run; any other required skip or missing case fails the gate.

## Required coverage inventory

The exact named test identities and evidence boundaries are maintained in [coverage.md](../../specs/014-v2-offline-gate/contracts/coverage.md). The complete suite is run once, and the gate checks its JUnit report for those identities, four all-provider modes, and controller interruption cases. This reuses existing server/controller failure, deadline, actual-overlap, chunk-gap, receiver-streaming, retrieval, budget/expiry, residual-resource, provenance, source-patch and F12 replay suites. AWS has no Terraform mock-plan suite; the gate runs only the existing four Azure/GCP mock-provider plan suites. No mock suite or coverage count is invented.

The integrated default-factory test saves the F09 validator inputs and results for AWS/Azure/GCP, then feeds those exact files and native result roots through F12. It compares full output sets/bytes across reordered selections and verifies saved source hashes remain unchanged. The fixtures are synthetic offline evidence, not research results. Missing cost snapshots remain unknown; calibration remains comparison-ineligible; diagnostic-day context is separate from idle baselines.

## Source and command review

Read-only source review checked the current Thesis `advisor/docs/design_document.md`, `DECISIONS.md`, `experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md`, `experiments/EXPERIMENTS.md`, `tracking/IMPLEMENTATION_PROMPTS.md`, `tracking/M04_implementation.md`, `tracking/framework_implementation_roadmap.html` and `tracking/M05_experiments.md`. The runbook keeps acceptance at ten measured seconds per phase and N=4 before calibration; uses the approved CAL-S1 1/4/2/8, 8/2/4/1, 2/8/1/4 trial orders and ≥95% common-N rule; checks measured log growth against 32-GiB disks and 20-GiB/cloud export; requires a separate 86,400-second continuous TCP DAY with ≤300-second local chunks and ≥99% RTT coverage; and leaves study windows, refreshed prices, quotas and real N pending. This follows M05's execution order and does not treat its provisional dates or dated cost forecast as current approval.

Commands used for this read-only check were `sed -n '1,240p' /Users/katya/Desktop/University/Masters/Thesis/tracking/M05_experiments.md`, `rg -n 'acceptance|calibration|diagnostic day|86,400|95%|20 GiB|expiry|cleanup' /Users/katya/Desktop/University/Masters/Thesis/{advisor/docs/design_document.md,DECISIONS.md,experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md,experiments/EXPERIMENTS.md,tracking/M05_experiments.md}`, and `PYTHONPATH=src /private/tmp/cnb-offline-venv-20261025/bin/python -m cloud_network_benchmark --help` (exit 0). CLI help exposes `validate`, `resolve`, `dry-run`, `init`, `analyze` and `lifecycle`; it contains no paid-acceptance command. No such command is presented as executable below.

## Command and result

The executable interface is:

```sh
THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config \
/private/tmp/cnb-offline-venv-20261025/bin/python scripts/validate_offline.py \
  --evidence-dir /private/tmp/cnb-f10-final-evidence \
  --terraform /private/tmp/cnb-tools/terraform1.16.6 \
  --pwsh /private/tmp/cnb-tools/powershell-7.5.11/pwsh7.5.11 \
  --provider-mirror /private/tmp/cnb-f10-provider-mirror
```

A zero exit requires every full-suite, schema, diff, six-root format/validate and four mocked-plan check to pass, complete named JUnit coverage, and unchanged clean source state. The evidence directory contains each argv/exit/duration/log/checksum, JUnit, safe tool/environment metadata, coverage/skips and a `summary.json`; failed or omitted checks are never counted passed. Full command results, tested SHA, log hashes and any two Darwin platform gaps will be added here only after the clean committed run completes. Until then, the full gate status is **not run**.

Focused implementation validation has run separately from the full gate:

- `PYTHONPATH=src /private/tmp/cnb-offline-venv-20261025/bin/python -m pytest tests/unit/test_offline_gate.py -q` → **5 passed**.
- `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config PYTHONPATH=src PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH /private/tmp/cnb-offline-venv-20261025/bin/python -m pytest tests/integration/test_f09_multi_provider_factory.py -q -k 'all-provider-acceptance or all-provider-calibration or all-provider-short-study or all-provider-diagnostic-day'` → **4 passed, 7 deselected** in 137.75 seconds; output `/private/tmp/cnb-f10-factory.log`.
- `/private/tmp/cnb-offline-venv-20261025/bin/python scripts/validate_offline.py --help` → **exit 0**; syntax/import compilation with `-m py_compile scripts/validate_offline.py tests/unit/test_offline_gate.py` → **exit 0**.

These focused checks do not substitute for the committed-code full gate below. The full gate remains **not run** until the implementation commit is clean.

## Paid stage boundary

See [paid acceptance handoff](../paid-acceptance.md) for the separate source-backed sequence. Acceptance, calibration, the diagnostic day, window selection and research execution require refreshed registered inputs and separate authorization. No launch or paid action is performed by F10.
