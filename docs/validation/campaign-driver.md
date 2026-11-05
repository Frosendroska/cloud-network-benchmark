# Remote campaign driver offline validation

The clean-checkout offline gate **passed all 20 checks** on 5 November 2026: **1,572 tests passed, 2 declared Linux `/proc` checks skipped**, and all 112 required coverage identities accounted for with no unexpected skips. No cloud experiments, provider resource creation or purchased resources were involved.

## Tested revision and environment

- Commit: `7608495a6a619a792df48182b1094c3579d8be1a`, branch `codex/campaign-driver`, based on `3413d94`.
- Tree: `686d9f362d9502f7a8ff13b63fa307ffa4130879`; checkout clean at start and end, unchanged throughout the gate. Later validation-document/task updates do not change implementation or tests.
- Host: macOS ARM64, Python 3.12.14; pytest 8.4.2, Pydantic 2.14.3, PyYAML 6.0.3, matplotlib 3.11.2 and numpy 2.5.4. Package/deployment minimum is Python 3.12; Python 3.9 is no longer advertised after its reported import failure.
- Tools: Terraform 1.16.6, PowerShell 7.5.11; AWS provider 6.70.0, AzureRM 4.81.0 and Google 7.46.1. Official archives were SHA256-verified; provider archives matched committed lock checksums.
- Thesis provenance read-only at `e33f67152b02d190d8a9bd14a2db3e0317a57819`. No Thesis files were edited.

The gate includes the full Python suite, required-coverage inventory, schema and diff checks, `fmt -check -recursive` and `validate -no-color` in all six Terraform roots, and explicit mocked `terraform test -no-color` in Azure pair/expiry and GCP pair/protection. Mock suites contain plan commands only. All six roots were first initialized with `-backend=false -input=false -lockfile=readonly` against a local filesystem mirror without registry fallback. Local provider sockets required execution outside the macOS sandbox; provider credential variables were stripped.

Campaign regressions cover the full 56-observation schedule and 168 child identities, simultaneous P0/P1 dispatch and actual shared reservations, configuration/schedule/runtime isolation, missing inputs, uncertain and completed-native recovery, supervised input immutability, cleanup blocking/reconciliation, missed/late slots, low disk, transfer/checksum/backup failure, portable native analysis replay after relocation, log rotation and public status. The final focused native regression passed 84 tests before the full gate, including 41 driver cases and the two convergence repairs. One existing tar-extraction deprecation warning remains; it is not a test failure.

Final SpecKit convergence found zero remaining gaps across 16 requirements, 5 success criteria, 10 acceptance scenarios, 26 tasks and 5 constitution principles. The two appended remediation tasks (sampled disk high-water and truthful backup-failure status) are implemented and covered by the passing gate.

## Resource evidence and retained logs

The final gate took **571.054 seconds** overall; the Python suite took 557.08 seconds. Its wrapper recorded **956,825,600 bytes** child/descendant high-water RSS and **821,151,909 bytes** peak new pytest-fixture logical size sampled each second. These are local test-process and fixture-file measurements, not total simultaneous VM RAM, whole-disk usage, guest storage or future DAY volume. Operational acceptance separately records sampled controller RSS and shared-results-filesystem free-space low-water/usage delta; concurrent or unrelated writes may contribute.

The full summary, JUnit report, command logs and resource JSON are retained privately under `/Users/katya/.codex/campaign-driver-recovery/20261105/final-gate/`. Their recorded identities are:

| Evidence | SHA256 |
| --- | --- |
| Gate summary | `c7e9bead7c5d37152b58fdff7b4cbb14e4079019fcaee295e487a527987e4835` |
| JUnit report | `2e6c5c0b547c2ea4fcd082b0faa810cb27942abef0e6e57762ab2e8be2b38670` |
| Full pytest log | `96d87769fd43d3d5522f44c6b6010e234b64e30efea854d5932990b65e640635` |

## Reproduce the gate

Prepare the Python environment, verified tool binaries, initialized locked provider mirror and a clean feature checkout. Adapt these absolute paths and use a new evidence directory:

```sh
PATH=/private/tmp/cnb-campaign-native-venv/bin:/private/tmp/cnb-campaign-tools/powershell-7.5.11:$PATH \
THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
/private/tmp/cnb-campaign-native-venv/bin/python scripts/validate_offline.py \
  --evidence-dir /private/tmp/cnb-campaign-final-gate \
  --terraform /private/tmp/cnb-campaign-tools/terraform \
  --pwsh /private/tmp/cnb-campaign-tools/powershell-7.5.11/pwsh \
  --provider-mirror /private/tmp/cnb-campaign-tools/provider-mirror
```

The script strips provider credential environment variables and verifies the source stayed clean and unchanged. It does not install tools, initialize providers, perform Terraform apply, connect SSH or launch benchmark traffic.

## Remaining supervised acceptance

Offline fakes/static checks do not prove native Ubuntu systemd/disconnect behavior, the two skipped Linux process-identity cases, real IAM/quota/placement, cloud-supported credential renewal, VM-to-VM traffic, independent expiry, full billable-resource deletion or actual DAY volume/export capacity. Follow [the operator guide](../campaign-runner.md) and [physical-loss procedure](../campaign-developer.md#supervised-registered-failure-checks) only after separate authorization.

Record actual placement membership, terminal disconnect and supervisor restart without duplicate measurement, each registered physical controller-loss/failure check, original deadlines and absence evidence. Actual N, study windows, account/image IDs, credentials and refreshed reviewed prices remain pending inputs. No empirical research result or unattended-readiness claim follows from offline implementation.
