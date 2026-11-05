# Run on a remote VM; analyse on your laptop

Use Ubuntu 24.04 x86-64 with Python 3.12, stable outbound public IPv4 and persistent storage. The provisional controller is 4 vCPUs / 8 GB RAM / 200 GB disk. Confirm storage and transfer capacity from measured file growth before DAY. This guide does not authorize cloud spending; real acceptance remains supervised.

## Install once

Install [Terraform](https://developer.hashicorp.com/terraform/install), [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-linux?pivots=apt) and [Google Cloud CLI](https://docs.cloud.google.com/sdk/docs/install-sdk) using their official Linux instructions. Use the repository's Terraform lock files. Then, as your normal runner user:

```sh
sudo apt-get update
sudo apt-get install -y python3.12-venv git rsync openssh-client ca-certificates
sudo install -d -o "$USER" -g "$(id -gn)" /srv/cnb
git clone https://github.com/Frosendroska/cloud-network-benchmark.git /srv/cnb/repository
python3.12 -m venv /srv/cnb/venv
/srv/cnb/venv/bin/python -m pip install -e /srv/cnb/repository
mkdir -p /srv/cnb/private /srv/cnb/results /srv/cnb/evidence /srv/cnb/credentials
chmod 700 /srv/cnb/private /srv/cnb/credentials
sudo install -d -o "$USER" -g "$(id -gn)" /etc/cloud-network-benchmark
install -m 600 /srv/cnb/repository/plans/operator-settings.example.yaml /etc/cloud-network-benchmark/operator.yaml
mkdir -p ~/.config/systemd/user
install -m 644 /srv/cnb/repository/deploy/cloud-network-benchmark-campaign.service ~/.config/systemd/user/
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable cloud-network-benchmark-campaign.service
export PATH="/srv/cnb/venv/bin:$PATH"
cd /srv/cnb/repository
cloud-network-benchmark campaign --help
```

Edit the **one operator file** at `/etc/cloud-network-benchmark/operator.yaml`, with Codex's help if useful. Nulls deliberately block launch. Supply actual accounts, concrete Ubuntu images, runner `/32`, SSH paths, interface names, reviewed prices/funding, approval sources and stage allowances. The driver generates manifests and internal runtime/approval/cost JSON; do not assemble those by hand. See [input evidence](campaign-developer.md) for what each review records.

Keep the private SSH key, cloud credential stores, certificate keys and recovery state outside Git/results. `authentication_reference` is the absolute SSH private-key path. A separate optional `/etc/cloud-network-benchmark/controller.env` supplies credential **references** to the service, such as `AWS_PROFILE`, `AZURE_CONFIG_DIR`, `CLOUDSDK_CONFIG` and `GOOGLE_APPLICATION_CREDENTIALS`; protect it with mode 600. The service uses this environment, not your SSH terminal's exported variables. `campaign check` uses the invoking terminal environment; repeat identity/renewal verification in the actual service environment before relying on unattended access.

For the full study, configure provider-supported unattended renewal: for example [AWS Roles Anywhere credential_process](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/credential-helper.html), [Azure service-principal certificate authentication](https://learn.microsoft.com/en-us/cli/azure/authenticate-azure-cli-service-principal), or [GCP Workload Identity Federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation). These require your account's permitted identity setup; no identity is created by the driver. Prove renewal with the service identity beyond the initial token lifetime, through the final slot and cleanup, and record that evidence. Interactive logins alone do not establish week-long access. Verify both CLI and Terraform authentication and quota for 4 VMs / 16 vCPUs per cloud; the runner is separate.

## Run one stage, review, then proceed

After each new SSH login, restore the command path and repository directory:

```sh
export PATH="/srv/cnb/venv/bin:$PATH"
cd /srv/cnb/repository
```

```sh
cloud-network-benchmark campaign check --plan plans/acceptance.yaml
cloud-network-benchmark campaign start --plan plans/acceptance.yaml --execute
cloud-network-benchmark campaign status
cloud-network-benchmark campaign logs --follow
```

`check` creates no resources and runs no benchmarks. It checks local evidence, tools/storage and read-only identity access; real IAM, placement, expiry and deletion still need acceptance. Starting without `--execute` only checks. `start --execute` hands work to one systemd service and returns; leave SSH normally. The service resumes the journal after restart and never blindly repeats a paid measurement.

Acceptance runs registered V-S1 and V-S3 sequentially, at 10 measured seconds/phase with N=4, then waits for the registered failure checks. Confirm actual AWS Cluster Placement Group, Azure **Standard** PPG and GCP **COLLOCATED** Compact Placement Policy membership from native provider readback, plus valid raw output and full deletion. The provider controls differ; their names alone do not prove membership. Follow the [supervised failure instructions](campaign-developer.md#supervised-registered-failure-checks) before continuing one registered attempt:

```sh
cloud-network-benchmark campaign continue --stage-id ACTUAL_STAGE_ID --entry-id V-A-FAIL --confirm-supervision
```

Repeat explicitly for V-Z-FAIL and V-G-FAIL after reviewing/recovering the previous attempt. This extra command is required to prepare physical controller-loss supervision; a successful normal benchmark never counts as a passed failure check. Preserve and review failures rather than re-running automatically.

After completed acceptance and reviewed failure/cleanup evidence:

```sh
cloud-network-benchmark campaign start --plan plans/calibration.yaml --execute
```

Copy the completed stage to your laptop. Calibration is one separate retained pair/cloud, 1/2/4/8 streams × 3 loaded 60-second trials in the fixed order. On the **laptop**, combine the three saved `validation/result.json` objects into one array and run:

```sh
cloud-network-benchmark lifecycle recommend-common-n --validation-results calibration-results.json --format json > common-n.json
```

Copy only that recommendation/review back and set `common_n_recommendation` to its path. The runner checks the frozen recommendation; it does not select N or make plots. Record measured log growth, free disk and a checksum-verified export before DAY; fill its size/export review and future `start_utc`.

```sh
cloud-network-benchmark campaign start --plan plans/day.yaml --execute
```

DAY retains separate pairs and runs one continuous 86,400-second aggregate TCP interval, local RTT chunks ≤300 seconds and one-second diagnostics. Copy/analyse locally. Review coverage and select the four exact UTC windows; incomplete DAY never triggers an automatic repeat. Then fill `first_day_utc`, the four `windows_utc`, measured complete-workflow allowances and corresponding reviews:

```sh
cloud-network-benchmark campaign start --plan plans/week.yaml --execute
```

WEEK dispatches 28 P0/S1 + 28 P1/S3 independent all-cloud calls, paired in the same UTC slots for 7 days. Resources are fresh per call and deleted afterwards. Missed slots stay missed. A failed measurement with verified cleanup is recorded and later slots continue; unresolved cleanup or launch uncertainty blocks new work. There are no automatic paid replacements or P2/P3 launches.

## Inspect, copy and recover

`campaign status` reports expected/completed/failed/missed counts, current/next slot, paths, free disk, cleanup liabilities, export and protection status. Per-entry/provider evidence and the original private journals are retained; only controller logs rotate. An optional mounted `backup_destination` copies **closed verified bundles**, and an optional executable `notification_hook` receives a summary JSON argument. Unset means inactive. Confirm that a backup mount is actually off-machine and test the hook before relying on either.

On your laptop (substitute the actual SSH host and stage ID):

```sh
rsync -av YOUR_USER@YOUR_RUNNER:/srv/cnb/results/ACTUAL_STAGE_ID/ ./results/ACTUAL_STAGE_ID/
cloud-network-benchmark analyze --selection results/ACTUAL_STAGE_ID/export/selection.json --output results/processed/NEW_REPORT
```

Install `.[analysis]` locally when plots are needed. Keep the runner copy until the laptop copy is verified. Copying a stage's results does not copy credentials, Terraform state or private recovery journals.

For a cleanup liability, inspect `campaign status`, then use the exact saved private path and session shown in the prepared stage:

```sh
cloud-network-benchmark lifecycle cleanup --recovery /srv/cnb/private/ACTUAL_STAGE_ID/ACTUAL_ENTRY_ID/lifecycle-recovery.json --controller-session ACTUAL_SAVED_SESSION --format json
```

This is a live cleanup command; it never resumes measurement and never targets the runner. Use original private state, never the public export, to recover. Check deletion of VMs/disks/addresses/support resources. Provider compute expiry is an independent fallback and does not prove full cleanup. Resolve liabilities before another stage; do not delete or reset the shared ledger to get past a block. Changed frozen pricing requires engineering maintenance that preserves the original source and ledger.

Before unattended DAY/WEEK, test the service on Ubuntu by disconnecting/reconnecting the invoking SSH session and verifying the same stage/session/journal persists, then test driver restart during supervised acceptance. Offline fakes and this macOS development environment do not prove systemd or real cloud behavior. A benchmark-VM/disk loss before final retrieval can lose native data; runner-disk loss can lose local-only closed bundles and private recovery state. Persistent disk and an optional verified second copy reduce those risks without changing the local-measurement protocol.
