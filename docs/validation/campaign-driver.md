# Remote campaign driver offline validation

Implementation verification is in progress on 5 November 2026. Do not treat this page as a passing final gate until the final results below are recorded. No cloud experiments, provider resource creation or purchased resources are part of these checks.

## Environment and boundaries

Working implementation: `/private/tmp/cnb-campaign-work`, branch `codex/campaign-driver`, based on `3413d94`. The prior temporary filesystem disappeared after restart; source edits were reconstructed from successful local tool-history file writes, then checked again. A persistent recovery archive and operation audit are held outside temporary storage. Current tests, rather than historical totals, determine completion.

Initial full-run Python environment: Python 3.12.11, pytest 8.4.2, Pydantic 2.14.3, PyYAML 6.0.3, matplotlib 3.11.2 and numpy 2.5.4 on macOS. Deployment/package metadata now requires Python >=3.12, matching the requested deployment; older interpreters are no longer advertised. The earlier Python 3.9 import problem prompted that compatibility correction.

Static tools: Terraform 1.16.6, PowerShell 7.5.11, AWS provider 6.70.0, AzureRM 4.81.0 and Google 7.46.1. Official tool archives were SHA256-verified, and provider archives matched the committed Terraform lock checksums. All six roots were initialized with `-backend=false -input=false -lockfile=readonly` against a local filesystem mirror, without a registry fallback.

## Checks completed so far

- Final native ARM64 focused regression: **84 passed in 17.37 seconds** under Python 3.12.14 with the same dependency versions. Includes GCP unit, private Terraform initialization/deadline, 41 driver cases, export and service tests. It covers supervised input immutability, sealed-native recovery, cleanup-only reconciliation, rotation/status, truthful backup failure and deterministic operational disk/RSS sampling.
- Terraform: all 16 static commands passed: `fmt -check -recursive` and `validate -no-color` in all six roots; explicit mocked `terraform test -no-color` in Azure pair/expiry and GCP pair/protection. Mock suites contain plan commands only. macOS sandbox plugin-socket restrictions required running the same credential-free commands with local sockets permitted.
- `python3.12 -m compileall -q src tests` and `git diff --check` passed after reconstruction.

The first current full run completed on the x86_64 macOS Python 3.12.11 environment under the sandbox: **1,570 passed, 2 skipped, 1 failed**, with one existing tar-extraction deprecation warning. The failure was an old GCP unit assertion requiring exactly 5 seconds after a command-deadline recheck correctly reduced it to 4.999971792 seconds. The assertion now enforces the actual positive timeout cap; expired-deadline and redaction checks remain. This run is not a passing final gate.

The full-run wrapper measured 1,293.687 seconds elapsed, 969,375,744 bytes child high-water RSS and 811,343,824 bytes peak test-fixture logical size sampled every 0.5 seconds. Source/test/packaging hashes stayed unchanged during that run. These values describe the sandboxed x86 test process and fixture files, not the entire VM, total concurrent process memory, guest disks or future DAY volume.

Final native ARM64 Python 3.12.14 clean-checkout F10 validation and follow-up SpecKit convergence remain required. The native environment uses the same dependency versions and points to the recovered working source.

## Reproduce the final gate

Prepare the local Python environment, verified tool binaries, initialized locked provider mirror and a clean feature checkout. Then use the existing gate, with absolute paths adapted to the local installation:

```sh
PATH=/private/tmp/cnb-campaign-native-venv/bin:/private/tmp/cnb-campaign-tools/powershell-7.5.11:$PATH \
THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
/private/tmp/cnb-campaign-native-venv/bin/python scripts/validate_offline.py \
  --evidence-dir /private/tmp/cnb-campaign-final-gate \
  --terraform /private/tmp/cnb-campaign-tools/terraform \
  --pwsh /private/tmp/cnb-campaign-tools/powershell-7.5.11/pwsh \
  --provider-mirror /private/tmp/cnb-campaign-tools/provider-mirror
```

The script strips provider credential environment variables and runs the complete Python suite, required coverage inventory, schema/diff checks, six formatting/validation pairs and four mocked Terraform suites. It requires a new evidence directory and verifies the source stayed clean/unchanged. It does not install tools, initialize providers, perform Terraform apply, connect SSH or launch benchmark traffic.

## Remaining supervised acceptance

macOS fakes/static tests do not prove native Ubuntu systemd behavior, Linux `/proc` process identity, real IAM/quota/placement, cloud-supported credential renewal, VM-to-VM traffic, independent expiry, full billable-resource deletion or actual DAY volume/export capacity. Follow [the operator guide](../campaign-runner.md) and [physical-loss procedure](../campaign-developer.md#supervised-registered-failure-checks) only after separate authorization.

Acceptance must record actual placement membership, terminal disconnect and supervisor restart without duplicate measurement, each registered physical controller-loss/failure check, original deadlines and absence evidence. Actual N, study windows, account/image IDs, credentials and refreshed reviewed prices remain pending inputs. No empirical research result or unattended-readiness claim follows from offline implementation.
