# Operator runbook: accounts → acceptance → DAY → WEEK

Updated 6 November 2026. Run collection on one remote Ubuntu VM; analyse on your laptop. Driver PR #25 is merged and [offline-tested](validation/campaign-driver.md). Real Ubuntu/systemd and cloud acceptance remain pending. Commands with `--execute` create billable resources: run them only after the named review/authorization. Replace `ACTUAL_*` and `YOUR_*` with real values. No AI session is needed during measurement.

## 1. Create the three accounts and obtain the controller

1. Create or select your [AWS account](https://aws.amazon.com/), [Azure account/subscription](https://azure.microsoft.com/en-us/pricing/purchase-options/azure-account) and [GCP project with billing](https://docs.cloud.google.com/resource-manager/docs/creating-managing-projects). Enable MFA on human accounts. Record AWS account ID, Azure subscription/tenant IDs and GCP project ID. Check actual student/trial/university credit eligibility and service restrictions; do not assume credits cover these VMs. Keep the $250 working / $50 protected / $300 total budget; refresh gross workflow quotes, tax, export and expiry-support costs before launch.
2. Ask TUM for an always-on **Ubuntu 24.04 x86-64, 4 vCPU, 8 GB RAM, 200 GB persistent disk**, normal user with sudo, SSH and stable outbound public IPv4. Ask for its host name, user, disk mount, network policy and backup/recovery arrangements. A paid controller is a separately reviewed fallback, not another benchmark VM. There is no TUM provisioning API assumed here: the next command starts after the VM exists.
3. Check quota/capacity for **4 benchmark VMs / 16 vCPUs per cloud**, disks, public IPs, placement and independent expiry permissions in the configured regions. The controller is separate. Selected benchmark VMs remain m7i.xlarge / Standard_D4s_v5 / n2-standard-4; controller sizing does not change them.

On your laptop:

```sh
ssh YOUR_USER@YOUR_RUNNER
```

**Ask Codex now:** review the real host/accounts, intended identity method, IAM/expiry permissions, quotas and funding before configuring paid execution. Share IDs and redacted settings, never keys/tokens. [Provider and failure details](campaign-developer.md) remain the reference for acceptance.

## 2. Install the controller and run all offline/native checks

On the runner, as the normal user, install [Terraform](https://developer.hashicorp.com/terraform/install), [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-linux?pivots=apt), [Google Cloud CLI](https://docs.cloud.google.com/sdk/docs/install-sdk) and [PowerShell 7](https://learn.microsoft.com/en-us/powershell/scripting/install/install-ubuntu) using their official Linux instructions. PowerShell is needed for Azure offline checks. Preserve committed Terraform lock files; do not run `init -upgrade`.

```sh
sudo apt-get update
sudo apt-get install -y python3.12-venv git rsync openssh-client ca-certificates
sudo install -d -o "$USER" -g "$(id -gn)" /srv/cnb
git clone https://github.com/Frosendroska/cloud-network-benchmark.git /srv/cnb/repository
cd /srv/cnb/repository
git rev-parse HEAD
python3.12 -m venv /srv/cnb/venv
. /srv/cnb/venv/bin/activate
python -m pip install -e '.[test,analysis]'
mkdir -p /srv/cnb/private /srv/cnb/results /srv/cnb/evidence /srv/cnb/credentials
chmod 700 /srv/cnb/private /srv/cnb/credentials /srv/cnb/evidence
```

Run before putting cloud credentials on this fresh VM. These commands download dependencies/providers but do not apply Terraform, create resources or send benchmark traffic. Use Bash, stop on any failure, and retain the evidence directory printed by `date`:

```bash
bash
set -euo pipefail
cd /srv/cnb/repository
. /srv/cnb/venv/bin/activate
CNB_EVIDENCE="/srv/cnb/evidence/native-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$CNB_EVIDENCE"
git rev-parse HEAD > "$CNB_EVIDENCE/revision.txt"
python -m pip freeze > "$CNB_EVIDENCE/python-packages.txt"
terraform version > "$CNB_EVIDENCE/terraform-version.txt"
pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()' > "$CNB_EVIDENCE/powershell-version.txt"
python -m pytest -q --junitxml="$CNB_EVIDENCE/pytest.xml" 2>&1 | tee "$CNB_EVIDENCE/pytest.log"
python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
for root in aws/pair aws/safety azure/pair azure/expiry gcp/pair gcp/protection; do
  terraform -chdir="terraform/$root" init -backend=false -input=false -lockfile=readonly
  terraform -chdir="terraform/$root" fmt -check -recursive
  terraform -chdir="terraform/$root" validate -no-color
done 2>&1 | tee "$CNB_EVIDENCE/terraform-static.log"
for root in azure/pair azure/expiry gcp/pair gcp/protection; do
  terraform -chdir="terraform/$root" test -no-color
done 2>&1 | tee "$CNB_EVIDENCE/terraform-mocks.log"
git diff --check
git status --short
```

Inspect `pytest.xml`/log: on native Ubuntu the two `/proc` cases must run, not remain skipped. Investigate failures and unexpected skips; do not weaken tests. Native guest builds and real SSH/providers still need acceptance. The saved macOS gate was 1,572 passed / two declared Linux skips / 20 checks; [its full required-coverage/mirror procedure](validation/campaign-driver.md) is a developer audit, not a portable Ubuntu setup script (its mirror check currently expects `darwin_arm64`). Record actual Ubuntu results; do not copy the macOS count as proof. Exit this Bash shell when finished.

## 3. Store credentials, settings and the service

Use provider-supported identities with renewal through the last WEEK slot **and cleanup**: [AWS Roles Anywhere/profile credential process](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/credential-helper.html), [Azure service-principal certificate](https://learn.microsoft.com/en-us/cli/azure/authenticate-azure-cli-service-principal), [GCP Workload Identity Federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation). Have Codex adapt setup to your actual permitted identity provider; there is no universal account-creation/IAM command safe for every institution. Verify both CLI and Terraform credentials, not just a terminal login. Interactive SSO alone is insufficient for WEEK.

After approved identity setup, keep its stores/certificate keys outside Git/results. For example, AWS profile `cnb` uses its reviewed credential process; Azure's reviewed certificate contains its private key and certificate; the GCP file is the actual federation configuration:

```sh
umask 077
ssh-keygen -t ed25519 -f /srv/cnb/credentials/id_ed25519
# Use a dedicated key available non-interactively to the service; protect its directory.
export AWS_PROFILE=cnb
az login --service-principal --username ACTUAL_APP_ID --certificate /srv/cnb/credentials/azure.pem --tenant ACTUAL_TENANT_ID
az account set --subscription ACTUAL_SUBSCRIPTION_ID
gcloud auth login --cred-file=/srv/cnb/credentials/gcp-wif.json
gcloud config set project ACTUAL_PROJECT_ID
export GOOGLE_APPLICATION_CREDENTIALS=/srv/cnb/credentials/gcp-wif.json
aws sts get-caller-identity
az account show --query '{subscription:id,tenant:tenantId}' --output json
gcloud projects describe ACTUAL_PROJECT_ID --format='value(projectId)'
```

`authentication_reference` in settings is the absolute benchmark **SSH private-key path**, not a cloud token. Do not overwrite an existing key/settings file when resuming this runbook. Do not paste private keys into Codex.

```sh
sudo install -d -o "$USER" -g "$(id -gn)" /etc/cloud-network-benchmark
install -m 600 plans/operator-settings.example.yaml /etc/cloud-network-benchmark/operator.yaml
install -m 600 /dev/null /etc/cloud-network-benchmark/controller.env
editor /etc/cloud-network-benchmark/operator.yaml
editor /etc/cloud-network-benchmark/controller.env
mkdir -p ~/.config/systemd/user
install -m 644 deploy/cloud-network-benchmark-campaign.service ~/.config/systemd/user/
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable cloud-network-benchmark-campaign.service
```

Fill **one operator YAML** with actual account/image IDs, controller `/32`, SSH paths, measured interface names, quoted complete-workflow prices, funding, allowances and review-source paths. Nulls/false confirmations deliberately block launch. Use the [field/evidence table](campaign-developer.md#one-settings-file-real-evidence). Confirm an approval only after its named review exists. The driver generates internal JSON/configs; do not handcraft runtime manifests.

`controller.env` uses systemd `NAME=value` lines (no `export`), with actual absolute credential references, for example `AWS_PROFILE=cnb`, `GOOGLE_APPLICATION_CREDENTIALS=/srv/cnb/credentials/gcp-wif.json`, and `AZURE_CONFIG_DIR`/`CLOUDSDK_CONFIG` if custom. Terraform may need additional identity-specific variables; review those with Codex. The service does **not** inherit SSH-shell exports. Prove renewal beyond initial token lifetime in the service environment and retain evidence before setting `credential_refresh.confirmed`.

For persistent host logs, ask the controller administrator to enable bounded persistent journald storage (e.g. `Storage=persistent`, `SystemMaxUse=512M` in a journald drop-in; see [systemd settings](https://www.freedesktop.org/software/systemd/man/latest/journald.conf.html)). Preserve private journals and the shared ledger in an access-controlled off-machine backup, separately from public results; these contain recovery-sensitive paths/state. Never publish that backup. A results export is not a recovery-state backup.

## 4. Run acceptance, calibration and DAY with reviews

After every new SSH login:

```sh
export PATH="/srv/cnb/venv/bin:$PATH"
cd /srv/cnb/repository
cloud-network-benchmark campaign check --plan plans/acceptance.yaml
```

`check` creates no resources; it checks inputs, tools/storage and bounded read-only identities. It does not prove IAM/quota/expiry or readiness. **Ask Codex to review the check, native test logs and real authorization/price evidence before this first paid command:**

```sh
cloud-network-benchmark campaign start --plan plans/acceptance.yaml --execute
cloud-network-benchmark campaign status
cloud-network-benchmark campaign logs --follow
```

Acceptance runs V-S1/V-S3 at 10 seconds/phase, N=4, then waits for supervised failure entries. Verify actual AWS Cluster Placement Group, Azure **Standard** PPG and GCP **COLLOCATED** policy membership; inspect native data and full deletion. Follow the [physical failure/controller-loss procedure](campaign-developer.md#supervised-registered-failure-checks), including original expiry deadlines and independent absence evidence, before continuing each attempt:

```sh
cloud-network-benchmark campaign continue --stage-id ACTUAL_STAGE_ID --entry-id V-A-FAIL --confirm-supervision
# Repeat explicitly for V-Z-FAIL and V-G-FAIL after the previous attempt is reviewed/recovered.
```

During supervised acceptance, disconnect/reconnect SSH and verify the same session/journal continues; exercise supervisor restart under the physical-loss procedure. Do not improvise restart/power-loss tests during research. Normal success alone does not pass a failure check.

**Review acceptance/native builds/renewal/cleanup with Codex**, then prepare the future start and measured allowance in the operator file:

```sh
cloud-network-benchmark campaign check --plan plans/calibration.yaml
cloud-network-benchmark campaign start --plan plans/calibration.yaml --execute
```

Calibration uses one separate retained pair/cloud, 1/2/4/8 streams × three loaded 60-second trials in fixed order. Download the closed stage (step 6). On the **laptop**, use the downloaded calibration stage's final selection (`CNB_LOCAL` is its local directory):

```sh
python - "$CNB_LOCAL/export/selection.json" > calibration-results.json <<'PY'
import json, sys
from pathlib import Path
selection = Path(sys.argv[1])
rows = json.loads(selection.read_text())['runs']
if len(rows) != 3:
    raise SystemExit('Expected three cloud calibration results; review missing/extra runs')
results = [json.loads((selection.parent / row['validation_result']).read_text()) for row in rows]
print(json.dumps(results))
PY
cloud-network-benchmark lifecycle recommend-common-n --validation-results calibration-results.json --format json > common-n.json
```

**Ask Codex to review/freeze common N and storage/export evidence**; copy the reviewed recommendation back and set `common_n_recommendation`. Fill future DAY start/reviews and check before launch:

```sh
cloud-network-benchmark campaign check --plan plans/day.yaml
cloud-network-benchmark campaign start --plan plans/day.yaml --execute
```

DAY uses separate retained pairs, uninterrupted 86,400-second aggregate TCP, local RTT chunks ≤300 seconds and one-second diagnostics. Its raw files are retrieved **after measurement**. A live controller log does not prove complete guest data. Before launch, measure both guests' file growth/free space from acceptance/calibration, conservatively project all output to 24 hours plus overhead, verify 32 GiB guest disks and the 20 GiB/cloud export allowance, and demonstrate checksum-verified transfer within the cap. Controller free space must cover original data **and closed exports** plus headroom (at least twice projected raw bytes, and retained weekly data/software). 200 GB is provisional; enlarge before admission if needed. No automatic deletion or disk rescue is provided.

## 5. Start WEEK and observe it

**Review DAY with Codex:** full TCP duration, ≥99% RTT time coverage, missing data, sustained throughput, costs, exact four UTC windows and feasible seven-day dates. Incomplete DAY requires review; no automatic repeat. Set `first_day_utc`, four `windows_utc`, reviewed allowances/authorization; keep common N fixed.

```sh
cloud-network-benchmark campaign check --plan plans/week.yaml
cloud-network-benchmark campaign start --plan plans/week.yaml --execute
```

WEEK makes 28 P0/S1 + 28 P1/S3 independent all-cloud calls, concurrent in the same slots, with fresh resources and cleanup after each observation (168 child runs total). `start` hands off to systemd and returns. Leave SSH normally; the controller must stay available. Missed slots stay missed; uncertain launches/cleanup liabilities block new work. No automatic paid replacements or P2/P3.

Run these after the first slot and at least daily; inspect count changes after each expected slot:

```sh
cloud-network-benchmark campaign status
cloud-network-benchmark campaign logs --follow
systemctl --user status cloud-network-benchmark-campaign.service --no-pager
journalctl --user -u cloud-network-benchmark-campaign.service --since today --no-pager
# Add -f to journalctl for live service output; Ctrl-C only stops the viewer.
date -u
timedatectl status
df -h /srv/cnb/results /srv/cnb/private
du -sh /srv/cnb/results/*
```

| Evidence | Location / meaning |
| --- | --- |
| Controller events | `/srv/cnb/private/campaign.log`; rotates at 5 MiB, three backups |
| Service stdout/restarts | `journalctl --user -u cloud-network-benchmark-campaign.service`; retention depends on host journald |
| Detailed entry output | `/srv/cnb/private/STAGE_ID/ENTRY_ID.log` |
| Durable launch/cleanup state | `/srv/cnb/private/STAGE_ID/campaign-journal.json`, per-entry `lifecycle-recovery.json`; keep private |
| Original native results | `/srv/cnb/results/STAGE_ID/raw/RUN_ID/`; closed copies include native files, diagnostics, manifests, config/schedule, validation and checksums |
| Ready midweek | `/srv/cnb/results/STAGE_ID/closed/ENTRY_ID/export/selection.json` after entry `export_status: verified` |
| Final whole-stage selection | `/srv/cnb/results/STAGE_ID/export/selection.json`, only after stage settles |

Status reports expected/completed/failed/missed counts, current/next slot, disk space, cleanup liabilities and per-entry export/backup outcomes. Check missing/failed observations and actual UTC times, not only whether the service is running. Native files never rotate. Optional `backup_destination` copies closed exports; verify the mount is physically off-machine and test checksum/readback. `notification_hook` is opt-in, at-most-once and can miss delivery on a crash; test it and continue checking status. Neither option protects in-progress guest files. No CloudWatch or new telemetry service is required.

## 6. Download and analyse while WEEK continues

On the **laptop**, install the same framework revision in a Python 3.12 venv and `python -m pip install -e '.[analysis]'`. Work outside Git-managed datasets (the checkout's `results/processed/` is ignored). Get actual stage/entry IDs from status. For **one closed entry with verified export**:

```sh
CNB_STAGE=ACTUAL_STAGE_ID
CNB_ENTRY=ACTUAL_ENTRY_ID
CNB_LOCAL="$HOME/cnb-data/$CNB_STAGE"
mkdir -p "$CNB_LOCAL/closed/$CNB_ENTRY"
rsync -av --partial YOUR_USER@YOUR_RUNNER:/srv/cnb/results/$CNB_STAGE/closed/$CNB_ENTRY/export/ "$CNB_LOCAL/closed/$CNB_ENTRY/export/"
python - "$CNB_LOCAL/closed/$CNB_ENTRY/export" <<'PY'
import sys
from pathlib import Path
from cloud_network_benchmark.campaign_export import verify_export
if not verify_export(Path(sys.argv[1])):
    raise SystemExit('Copy integrity failed: recopy and investigate before analysis')
print('Export and native checksums verified')
PY
CNB_REPORT="results/processed/$CNB_STAGE-$CNB_ENTRY-$(date -u +%Y%m%dT%H%M%SZ)"
cloud-network-benchmark analyze --selection "$CNB_LOCAL/closed/$CNB_ENTRY/export/selection.json" --output "$CNB_REPORT"
open "$CNB_REPORT/index.html" # macOS; Linux: xdg-open
```

Repeat for other closed entries whenever wanted. Copying/checking these closed files does not touch active guests or stop WEEK. Do not copy active `raw/` as a validated snapshot. If an export contains only partial failure evidence, it may have no analysable selection: preserve it and review the failure rather than manufacturing one. Final stage copying can use `rsync -av --partial ...:/srv/cnb/results/$CNB_STAGE/ "$CNB_LOCAL/"`, then verify the final `export` and analyse its `selection.json`; keep `closed/` alongside it because the final selection references those bundles.

For a combined **interim** view after copying several closed entries, create a new local selection; this verifies every copied export and preserves source files:

```sh
CNB_SELECTION="$CNB_LOCAL/interim-$(date -u +%Y%m%dT%H%M%SZ).json"
python - "$CNB_LOCAL" "$CNB_SELECTION" <<'PY'
import json, os, sys
from pathlib import Path
from cloud_network_benchmark.campaign_export import verify_export
root, target = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
runs = []
derived = target.parent / (target.stem + '-inputs')
derived.mkdir()
for selection in sorted((root / 'closed').glob('*/export/selection.json')):
    if not verify_export(selection.parent):
        raise SystemExit(f'Incomplete/corrupt copy: {selection.parent}')
    for row in json.loads(selection.read_text())['runs']:
        source = selection.parent / row['validator_input']
        descriptor = json.loads(source.read_text())
        for key in ('campaign_manifest_path', 'child_manifest_path', 'child_result_root'):
            value = descriptor.get(key)
            if isinstance(value, str) and value and not Path(value).is_absolute():
                descriptor[key] = str((selection.parent / value).resolve())
        rebased = derived / (selection.parent.parent.name + '-' + source.name)
        rebased.write_text(json.dumps(descriptor, indent=2))
        runs.append({'validator_input': os.path.relpath(rebased, target.parent),
                     'validation_result': str((selection.parent / row['validation_result']).resolve())})
if not runs:
    raise SystemExit('No verified closed observations selected')
with target.open('x') as stream:
    json.dump({'format_version': 1, 'runs': runs}, stream, indent=2)
print(f'{len(runs)} child runs selected; keep cutoff and failed/missed counts with report')
PY
cloud-network-benchmark analyze --selection "$CNB_SELECTION" --output "results/processed/$CNB_STAGE-interim-$(date -u +%Y%m%dT%H%M%SZ)"
```

Reports contain `report.json`, seven normalized CSV tables, PNG/SVG plots and offline `index.html`; see [analysis semantics](native-analysis.md). Retain the cutoff, selected IDs, expected/completed/failed/missed counts, framework/analysis revision and validation findings. Interim selections above contain no cost-ledger snapshot: unknown costs stay unavailable; use the private ledger/provider billing for operational budget review. Start provisional figures and writing, check data quality early, and reconcile the entire expected sample before final conclusions. Do not adapt N/windows or stop early because of interim performance. Keep originals until verified laptop and second copies exist; never sync private credentials/state into public results or commit data.

## 7. If something fails; finish safely

Bring Codex the status, redacted relevant logs, entry IDs, source revision and local report; never credentials. Stop before further spending when a decision is needed: failed acceptance/renewal, inadequate storage, incomplete DAY, changed price evidence, cleanup liability, replacement request, or infeasible dates. Preserve the original ledger, journal and frozen settings; do not bypass admission or reset reservations.

For cleanup use the **actual saved** recovery path/session reported by private prepared state, following the [recovery procedure](campaign-developer.md):

```sh
cloud-network-benchmark lifecycle cleanup --recovery /srv/cnb/private/ACTUAL_STAGE_ID/ACTUAL_ENTRY_ID/lifecycle-recovery.json --controller-session ACTUAL_SAVED_SESSION --format json
```

This is live cleanup, not measurement. Verify all experiment VMs/disks/addresses/support resources are absent; compute expiry is only a fallback. Never target the controller or delete recovery state to unblock a stage. Finalize verified downloads, failure/missing-slot and cost reconciliation, full resource absence and final reports with Codex. Then update Thesis run references and canonical findings. P2/P3 and paid replacements require separate decisions. Controller and benchmark-disk loss before copying remain risks; local logs and offline tests cannot eliminate them.
