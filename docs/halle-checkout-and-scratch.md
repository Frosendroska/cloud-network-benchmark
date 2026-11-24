# Halle checkout and scratch layout

This runbook describes the clean code checkout and file placement for the
reduced pre-DAY workflow. It is an operational layout guide only. It does not
authorize a campaign, provider operation, GCS object operation, CAL, DAY, or
WEEK. Follow the current Thesis pre-DAY prompt and exact reviewed inputs for
any separate authorized action.

## Clean, pinned framework checkout

Keep one current framework checkout in the Halle home directory. Do not
replace or clean a frozen checkout, active evidence directory, or private
runtime tree. After the final integration revision is published, create the
current checkout from that exact revision:

```bash
set -euo pipefail
CNB_REPOSITORY="$HOME/cnb/repository"
CNB_REVISION='REPLACE_WITH_PUBLISHED_FULL_COMMIT_SHA'
git clone https://github.com/Frosendroska/cloud-network-benchmark.git "$CNB_REPOSITORY"
git -C "$CNB_REPOSITORY" fetch origin "$CNB_REVISION"
git -C "$CNB_REPOSITORY" checkout --detach "$CNB_REVISION"
test "$(git -C "$CNB_REPOSITORY" rev-parse HEAD)" = "$CNB_REVISION"
test -z "$(git -C "$CNB_REPOSITORY" status --porcelain)"
git -C "$CNB_REPOSITORY" rev-parse HEAD
git -C "$CNB_REPOSITORY" rev-parse 'HEAD^{tree}'
```

Replace the placeholder only with the full SHA of the reviewed, published
integration commit. A candidate source SHA is not a substitute for that final
revision. Keep this checkout detached and clean for operation; perform later
software updates in a separate clean clone and switch only after review.

Use a home-directory virtual environment tied to this checkout. Keep private
settings and evidence references outside the repository, for example:

```bash
CNB_VENV="$HOME/cnb/venv"
CNB_PRIVATE="$HOME/cnb/private"
CNB_SETTINGS="$CNB_PRIVATE/operator.yaml"
CNB_RUNTIME="$CNB_PRIVATE/ACTUAL_STAGE/ACTUAL_ENTRY/runtime-config.json"
CNB_SCRATCH="/var/tmp/cnb-$(id -un)"
```

The paths above are names only; use the actual private paths in the supported
settings and saved runtime. Do not put credential contents in shell history,
the repository, results, or this runbook. Provider credential references such
as `AWS_PROFILE`, Azure CLI's selected account, and
`GOOGLE_APPLICATION_CREDENTIALS` must point to the operator's separately
managed private credential stores. Offline analysis does not need them.

## Scratch and durable output

For the reviewed reduced pre-DAY layout, use the exact `/var/tmp` paths already
bound in the original supported settings/runtime for bounded transfer parts,
compressed guest copies, restored native files, and replay/analysis
intermediates. Read back those paths from the original private inputs; do not
guess replacements or override them with ad hoc campaign flags. Keep private
campaign state, journals, barriers, archive metadata, and small receipts in
their configured durable home-directory roots. Confirm the user's quota and
actual headroom from current evidence before any operation that grows those
roots; filesystem-wide free space is not a user-quota measurement.

The production archive path persists and verifies bounded parts and the final
archive inventory before any explicitly configured scratch pruning. Preserve
the guest originals through verified local copy. Replay verifies archive and
native inventories before writing the analysis export. Scratch pruning is
withheld unless the replay, export, and exact per-role scratch inventories
pass their source-bound checks. A failed or incomplete transfer/replay keeps
its evidence for review; do not delete it manually to recover space.

Replay's `analysis-report.json` remains inside its export under the configured
results root. For a separate compact durable report, run the existing analysis
CLI after selecting the verified local export. Put only this generated
analysis report directory on a durable home or laptop path; keep selection
inputs, native data, compressed archives, and replay scratch under their
configured data roots:

```bash
CNB_REPORT="$HOME/cnb/reports/ACTUAL_STAGE-ACTUAL_ENTRY-$(date -u +%Y%m%dT%H%M%SZ)"
cloud-network-benchmark analyze \
  --selection "/var/tmp/ACTUAL_VERIFIED_EXPORT/selection.json" \
  --output "$CNB_REPORT"
```

`analyze` revalidates its selected evidence and writes a new directory with
JSON/CSV summaries, figures, and an offline HTML report. The report records
source identities and validation status; it is not a replacement for raw
native evidence. Verify the command completed successfully and inspect the
report status before copying the report directory to a laptop. Do not copy an
in-progress output directory. From the laptop, copy a completed report with a
command such as
`rsync -a --partial USER@HALLE:/home/USER/cnb/reports/ACTUAL_REPORT/ "$HOME/cnb-reports/ACTUAL_STAGE-ACTUAL_ENTRY/"`;
use the actual home path, host/account, and an existing local report
destination.

## Handoff record

For a later software handoff, record the published full commit and tree SHA,
clean checkout status, virtual-environment/package versions, and the private
settings/runtime *paths* used. Keep credentials, settings contents, journals,
state, Terraform plans/state, raw measurement bundles, and large archives out
of GitHub and out of the compact report copy. A clean checkout and successful
offline analysis do not establish provider acceptance, storage quota
sufficiency, campaign readiness, or authority to start work.
