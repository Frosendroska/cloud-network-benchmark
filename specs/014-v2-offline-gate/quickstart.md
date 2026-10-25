# F10 validation guide

## Planning status

The commands below define the implementation acceptance procedure. `scripts/validate_offline.py` and final validation docs do not exist at planning time; no F10 implementation test or full gate has run. Existing command/module/test paths were inspected. This feature stops at credential-free proof.

## Prepared local prerequisites

Use `/private/tmp/cnb-f10` on `codex/f10-v2-offline-gate` and the exact merged base in research.md. The coordinator prepared Python `/private/tmp/cnb-offline-venv-20261025/bin/python`, Terraform `/private/tmp/cnb-tools/terraform` and PowerShell `/private/tmp/cnb-tools/powershell-7.5.11/pwsh`. It also prepared `/private/tmp/cnb-f10-provider-mirror` with exact locked darwin_arm64 providers and initialized all six roots with backend disabled, input disabled and lockfile readonly; no registry/cloud calls were made. These are prerequisite reports, not F10 results. Prep logs: `/private/tmp/cnb-f10-terraform-prep-{aws,azure,gcp}.log`. A different host must prepare matching local tools/providers separately; the gate never installs missing prerequisites.

Set `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`, `PYTHONPATH=src`, `MPLBACKEND=Agg` and a private writable `MPLCONFIGDIR`. Supply PowerShell on PATH for existing harness tests. The script supplies an isolated filesystem-only Terraform CLI config; no registry fallback/checkpoints. Never supply cloud credentials to the gate.

## Focused implementation checks

After the script/test changes exist:

```sh
PYTHONPATH=src /private/tmp/cnb-offline-venv-20261025/bin/python -m pytest tests/unit/test_offline_gate.py -q
```

Run the existing default-factory mode suite with the same full-gate environment to prove the new saved-bundle F12 bridge; keep its fake commands/time and test-only source inputs. Any behavioral test/code change requires a fresh final full run after committing.

## One final clean-checkout command

After the implementation/code/tests are committed and `git status --short` is empty:

```sh
THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config \
/private/tmp/cnb-offline-venv-20261025/bin/python scripts/validate_offline.py \
  --evidence-dir /private/tmp/cnb-f10-final-evidence \
  --terraform /private/tmp/cnb-tools/terraform \
  --pwsh /private/tmp/cnb-tools/powershell-7.5.11/pwsh \
  --provider-mirror /private/tmp/cnb-f10-provider-mirror
```

Use a new evidence directory for each invocation. Expected exit 0 only when required offline suites/schema/six-root static/mock checks and integrated report replay pass at unchanged clean HEAD. `summary.json` and logs must retain actual counts, argv/exits, environment/revisions, log hashes, required case coverage and the two exact not-run Darwin gaps if applicable. Failed/incomplete/missing-tool/unknown-skip runs are nonzero; do not report them passed. The script does not run Terraform init, cloud CLIs, real plans/applies, SSH or native traffic.

Coordinator copies/retains final raw logs under its durable ignored evidence path and records their references in `docs/validation/f10-offline-gate.md`. Rerun after any final integration affecting behavior. A later documentation-only commit must record exact tested SHA and unchanged executable-content comparison; it cannot make new code count as tested. Do not hardcode historical F09 totals as F10 results.

## Separate future paid handoff

Deliver `docs/paid-acceptance.md` with registered V-S1 (ten measured seconds each phase, N=4 before calibration), V-A-FAIL/V-Z-FAIL/V-G-FAIL, later V-S3 and separately admitted V-S2/V-S4. Require refreshed identified cost inputs/quotas, complete-lifetime reservation, independent expiry before create, real source-built/native Linux checks, strict SSH trust, receiver streaming, close/copy/verify and full billable cleanup. Physical controller loss must prove provider fallback; stop/deallocation alone is not cleanup.

Then CAL-S1 uses 1/4/2/8, 8/2/4/1, 2/8/1/4 orders, three 60-second trials/count with ten-second pauses on one retained S1 pair/cloud. Select common N by the approved ≥95% rule; preserve no-common-plateau evidence. Measure actual log growth against 32-GiB disks and 20-GiB/cloud export allowance before a separately admitted DAY. DAY uses another retained pair/cloud, one continuous 86,400-second TCP interval and ≤300-second local probes; full TCP/≥99% RTT coverage and diagnostic analysis precede exact research-window selection. No repeat or paid next step is automatic. Real N/windows/prices and provider behavior remain unproved by F10.
