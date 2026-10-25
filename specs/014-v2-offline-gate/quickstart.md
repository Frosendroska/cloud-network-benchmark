# F10 validation quickstart

## Verified implementation

The committed F10 gate passed at `3cfb059cfa716875dca1e422c6690491922a4ce8` with a clean source tree. The complete run produced 1,450 passed, two exact Darwin `/proc` skips, no failed tests, schema/diff checks, six Terraform format/validate pairs and four mocked Azure/GCP plan suites. The full gate summary and raw logs are preserved in `/private/tmp/cnb-f10-final-evidence-5/`; see [validation evidence](../../docs/validation/f10-offline-gate.md) for hashes and limitations.

## Run the local offline gate

Prepare the current Python dependencies, Terraform 1.16.6, PowerShell 7.5.11, the exact locked Darwin provider mirror and all six initialized provider roots before running the command. The gate itself does not install tools or initialize Terraform. From a clean checkout of this feature, run:

```sh
THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config \
/private/tmp/cnb-offline-venv-20261025/bin/python scripts/validate_offline.py \
  --evidence-dir /private/tmp/cnb-f10-next-evidence \
  --terraform /private/tmp/cnb-tools/terraform \
  --pwsh /private/tmp/cnb-tools/powershell-7.5.11/pwsh \
  --provider-mirror /private/tmp/cnb-f10-provider-mirror
```

Use a new evidence path for every run. A zero exit requires a clean unchanged revision, complete required JUnit coverage, the full suite, schema and diff checks, all six Terraform fmt/validate commands and all four existing mocked plan suites. The report marks checks that fail or are not run accurately. Only the two listed `/proc` tests may remain platform gaps on Darwin.

## Separate future handoff

F10 is offline-only. Native Linux acceptance, provider acceptance, calibration, diagnostic traffic, paid experiments and research interpretation require their separate registered inputs and authorization; no such action was run. See [paid acceptance](../../docs/paid-acceptance.md).
