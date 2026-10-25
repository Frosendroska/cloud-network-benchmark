# F10 offline readiness gate

## Outcome

The credential-free offline gate **passed** at executable commit `3cfb059cfa716875dca1e422c6690491922a4ce8` (Git tree `3271b04230b9b4333792c7282e79f10526cd9075`). It began and ended clean at that revision. The run did not verify cloud behavior or native Linux process identity and did not launch any cloud, paid or benchmark activity.

The full pytest suite reported **1,450 passed, 2 skipped, 0 failed**, with one existing Python `extractall` deprecation warning. The two skips are the exact unavailable-Linux-`/proc` tests below. The full gate evaluated 29 named identities, including all four all-provider lifecycle modes and both controller-interruption variants; no required identity was missing and there were no unexpected skips. The schema check, diff check, all six Terraform fmt and validate commands, and all four existing mocked Azure/GCP plan suites passed (19 total checks, each exit 0). AWS has no checked-in Terraform mock-plan suite; the gate does not invent one.

The default AWS/Azure/GCP factories saved actual F09 `validation/input.json` and validation results then fed those files and native result roots into F12. Reports from reversed selections had identical complete file sets and bytes, and input/native source hashes were unchanged. The report kept calibration comparison-ineligible, represented diagnostic-day idle baseline as unavailable, and left costs unavailable without a verified cost snapshot. These are fake/offline evidence, not research results.

## Exact full command and evidence

The definitive command used a new evidence directory and the corrected prepared tool paths:

```sh
THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config \
/private/tmp/cnb-offline-venv-20261025/bin/python scripts/validate_offline.py \
  --evidence-dir /private/tmp/cnb-f10-final-evidence-5 \
  --terraform /private/tmp/cnb-tools/terraform \
  --pwsh /private/tmp/cnb-tools/powershell-7.5.11/pwsh \
  --provider-mirror /private/tmp/cnb-f10-provider-mirror
```

Exit: **0**. The same committed command was first attempted in the default sandbox and reached Terraform provider startup, where the sandbox denied its local Unix-socket bind. The definitive run used the normal reviewed local execution permission for those provider sockets; it did not access a cloud or network service. Earlier path/lock preflight failures and the sandbox-denied run remain preserved in `/private/tmp/cnb-f10-final-evidence-{1,2,3,4}`; they are failed/incomplete attempts, not the successful gate evidence.

The definitive raw evidence is under `/private/tmp/cnb-f10-final-evidence-5/`. `summary.json` SHA-256 is `01730b8da0a711d87d4fa3f1e4b8ae4fa0e4ce032a084f0951844b8304fe57ba`; `pytest.xml` SHA-256 is `a070fac39bc6c84452c00836105713e6de9320fab57e5a7707e83866d19c77dc`; the full pytest log `01-full-pytest.log` SHA-256 is `95746ae028ea2ca6f4a8136f1a2b967382648f43026fec197ee6c9106ee04e7f`. `summary.json` records exact argv, status, exit, duration and log hash for every check. All 19 checks passed. The isolated Terraform CLI config SHA-256 is `1260c0a338af19a6900aace387e66b50a8e9900610cd13f8dcc322fcb5ff6a28`.

The checked environment was Darwin arm64, Python 3.12.14, pytest 8.4.2, Pydantic 2.14.1, PyYAML 6.0.3, Matplotlib 3.11.2, NumPy 2.5.4, Terraform 1.16.6 and PowerShell 7.5.11. The read-only Thesis source revision was `ce89a8549bafe95b7fe989784f3b37e8f65b9bbf`.

## Named native-platform gaps

Only these two tests were not run because Darwin does not expose Linux `/proc` process identity:

- `tests/unit/test_f09_guest.py::test_f03_scoped_stop_reaps_owned_session_when_original_leader_has_exited`
- `tests/unit/test_f09_guest.py::test_f03_scoped_stop_refuses_reused_pid_start_ticks`

Their actual skip reason is “the local test host has no Linux /proc process identity interface.” Both must run on the separately authorized native Linux acceptance host. Native Linux guest `/proc`, instrumented source-built Sockperf/iperf3 execution, real SSH trust, provider permissions/networking, expiry behavior and full billable-resource removal remain unproved.

## Source review and paid boundary

The read-only Thesis review covered `advisor/docs/design_document.md`, `DECISIONS.md`, `experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md`, `experiments/EXPERIMENTS.md`, `tracking/IMPLEMENTATION_PROMPTS.md`, `tracking/M04_implementation.md`, `tracking/framework_implementation_roadmap.html` and `tracking/M05_experiments.md`. The separate [paid acceptance handoff](../paid-acceptance.md) follows the registered ten-second/N=4 acceptance, CAL-S1 orders and ≥95% common-N rule, measured disk/export gate, separate 86,400-second DAY, ≤300-second probe chunks and ≥99% RTT coverage. Exact research N, UTC windows, refreshed prices, quotas and realized costs remain pending. M05 dates and dated cost forecasts remain provisional inputs, not current approval.

Commands used for the source and CLI review were `sed -n '1,240p' /Users/katya/Desktop/University/Masters/Thesis/tracking/M05_experiments.md`, `rg -n 'acceptance|calibration|diagnostic day|86,400|95%|20 GiB|expiry|cleanup' /Users/katya/Desktop/University/Masters/Thesis/{advisor/docs/design_document.md,DECISIONS.md,experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md,experiments/EXPERIMENTS.md,tracking/M05_experiments.md}`, and `PYTHONPATH=src /private/tmp/cnb-offline-venv-20261025/bin/python -m cloud_network_benchmark --help` (exit 0). CLI help contains no paid-acceptance command, so the runbook does not claim one exists.
