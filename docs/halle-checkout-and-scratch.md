# Halle checkout and scratch layout

This runbook describes the clean code checkout and file placement for the
reduced pre-DAY workflow. It is an operational layout guide only. It does not
authorize a campaign, provider operation, GCS object operation, CAL, DAY, or
WEEK. Follow the current Thesis pre-DAY prompt and exact reviewed inputs for
any separate authorized action.

## Clean, pinned framework checkout

Keep the existing canonical framework checkout in the Halle home directory.
Do not replace or clean a frozen checkout, active evidence directory, private
runtime tree, ignored setup files, or generated artifacts. After the final
integration revision is published, synchronize the canonical checkout only
if its tracked and untracked status is clean:

```bash
set -euo pipefail
CNB_REPOSITORY="$HOME/cnb/repository"
CNB_REVISION='REPLACE_WITH_PUBLISHED_FULL_COMMIT_SHA'
test -d "$CNB_REPOSITORY/.git"
test "$(git -C "$CNB_REPOSITORY" remote get-url origin)" = "https://github.com/Frosendroska/cloud-network-benchmark.git"
test -z "$(git -C "$CNB_REPOSITORY" status --porcelain --untracked-files=all)"
git -C "$CNB_REPOSITORY" fetch origin "$CNB_REVISION"
git -C "$CNB_REPOSITORY" checkout --detach FETCH_HEAD
test "$(git -C "$CNB_REPOSITORY" rev-parse HEAD)" = "$CNB_REVISION"
test -z "$(git -C "$CNB_REPOSITORY" status --porcelain)"
git -C "$CNB_REPOSITORY" rev-parse HEAD
git -C "$CNB_REPOSITORY" rev-parse 'HEAD^{tree}'
```

Replace the placeholder only with the full SHA of the reviewed, published
integration commit. A candidate source SHA is not a substitute for that final
revision. Keep this checkout detached and clean for operation; perform later
software updates in a separate clean clone and switch only after review.

Use a separate pinned worktree under `/var/tmp` for archive replay and analysis
commands whose repository root follows the current working directory. This
keeps generated files off the home volume while leaving the canonical checkout
available for durable code and the home-directory virtual environment:

```bash
CNB_SCRATCH_SOURCE="/var/tmp/brek-cnb-work/source"
test ! -e "$CNB_SCRATCH_SOURCE"
git -C "$CNB_REPOSITORY" worktree add --detach "$CNB_SCRATCH_SOURCE" "$CNB_REVISION"
test "$(git -C "$CNB_SCRATCH_SOURCE" rev-parse HEAD)" = "$CNB_REVISION"
test -z "$(git -C "$CNB_SCRATCH_SOURCE" status --porcelain --untracked-files=all)"
```

Run replay and analysis with this worktree as the working directory. The
source-bound runtime must place its `results_root` inside this worktree (for
example, `$CNB_SCRATCH_SOURCE/results`) and keep durable journals, barriers,
state, and receipts under their configured home paths. A runtime that points
`results_root` beside or outside the worktree is not ready for this layout;
prepare and review a supported runtime instead of editing consumed inputs in
place. Preserve the temporary worktree until the evidence and report handoff
are closed, then follow the operator's reviewed cleanup procedure.

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
archive inventory before any explicitly configured scratch pruning. Retain
guest originals until remote generations, object sizes and hashes, and the
completed archive manifest/readback are verified. Replay verifies archive
and native inventories before writing the analysis export. Scratch pruning is
withheld unless replay, export, and exact per-role scratch inventories pass
their source-bound checks. A failed or incomplete transfer/replay keeps its
evidence for review; do not delete it manually to recover space.

Replay's `analysis-report.json` and the full F12 report remain under the
configured scratch results root. For a compact durable report, run the
existing analysis CLI into `/var/tmp`, then copy only summary tables, plots,
and a provenance digest to the durable report directory. Keep per-bin data,
the full JSON/HTML report, selection inputs, native data, compressed archives,
and replay scratch in their configured data roots:

```bash
CNB_ANALYSIS="/var/tmp/brek-cnb-work/analysis/ACTUAL_STAGE-ACTUAL_ENTRY"
CNB_REPORT="$HOME/cnb/reports/ACTUAL_STAGE-ACTUAL_ENTRY-$(date -u +%Y%m%dT%H%M%SZ)"
cd "$CNB_SCRATCH_SOURCE"
cloud-network-benchmark analyze \
  --selection "/var/tmp/ACTUAL_VERIFIED_EXPORT/selection.json" \
  --output "$CNB_ANALYSIS"
mkdir -m 700 "$CNB_REPORT"
cp "$CNB_ANALYSIS/runs.csv" "$CNB_ANALYSIS/deployments.csv" \
  "$CNB_ANALYSIS/comparisons.csv" "$CNB_ANALYSIS/reliability.csv" \
  "$CNB_ANALYSIS/costs.csv" "$CNB_REPORT/"
mkdir -m 700 "$CNB_REPORT/figures"
cp "$CNB_ANALYSIS"/figures/*.png "$CNB_ANALYSIS"/figures/*.svg "$CNB_REPORT/figures/"
python - "$CNB_ANALYSIS/report.json" \
  "/var/tmp/ACTUAL_VERIFIED_EXPORT/selection.json" "$CNB_REPORT/provenance.json" <<'PY'
import hashlib, json, sys
from pathlib import Path
report_path, selection_path, output_path = map(Path, sys.argv[1:])
report = json.loads(report_path.read_text(encoding="utf-8"))
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
keys = ("format_version", "selection_fingerprint", "analysis_git", "renderer", "numeric_runtime", "utc_window", "reliability_summary")
result = {key: report.get(key) for key in keys}
result["report_json_sha256"] = digest(report_path)
result["selection_sha256"] = digest(selection_path)
result["source_artifacts"] = [{key: item.get(key) for key in ("run_id", "artifact_id", "kind", "sha256", "unit_id", "producer")} for item in report.get("sources", [])]
output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
(cd "$CNB_REPORT" && find . -type f ! -name checksums.sha256 -print0 | sort -z | xargs -0 sha256sum > checksums.sha256)
```

`analyze` revalidates its selected evidence and writes a new full report
directory. The compact copy contains `runs.csv`, `deployments.csv`,
`comparisons.csv`, `reliability.csv`, `costs.csv`, PNG/SVG figures,
`provenance.json`, and `checksums.sha256`; it omits per-bin tables and the full
JSON/HTML report. The provenance file records the analysis revision,
selection fingerprint and digest, source artifact digests, and the full
report's digest without source paths. Inspect the full report status in scratch
before accepting the compact files. This report does not replace native
evidence. Do not copy an in-progress output directory. From the laptop, copy a
completed report with a command such as
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
