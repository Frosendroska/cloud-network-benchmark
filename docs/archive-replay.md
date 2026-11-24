# Closed archive replay

`lifecycle replay-archives` retains downloaded role archives and restored native
scratch by default. To prune only those two verified scratch trees after valid
native replay and F12 export, pass `--prune-verified-scratch` with a fresh,
separately hash-bound user-quota receipt:

```sh
cloud-network-benchmark lifecycle replay-archives \
  --capsule <capsule.json> --capsule-sha256 <capsule-sha256> \
  --runtime-config <private-runtime.json> --restore-path <restore-directory> \
  --prune-verified-scratch --quota-evidence <fresh-quota.json> \
  --quota-evidence-sha256 <fresh-quota-sha256>
```

The quota JSON must contain integer `measured_free_bytes`, timezone-aware
`measured_at`, positive integer `metadata_headroom_bytes`, positive integer
`projected_other_growth_bytes`, and positive integer `reserve_bytes`. Its
separate SHA-256 must match the file, and the measurement must be no more than
15 minutes old. `projected_other_growth_bytes` must include planned growth from
other retained replay trees, CAL, private Terraform/state, reports, and any
configured backup. The command does not infer these values from filesystem
free space. A stale, incomplete, mismatched, or insufficient receipt is
rejected before the archive backend is initialized or materialization starts.

Admission uses the capsule's immutable inventories to calculate:

```text
compressed role archive bytes
+ 3 * closed native raw bytes
+ metadata_headroom_bytes
+ projected_other_growth_bytes
+ reserve_bytes
```

The three raw copies at peak are restored native scratch, canonical
`results/raw` files, and the verified F12 export. Pruning begins only after F08
validity and export verification succeed. Before the first unlink, every
provider's exact generated archive and native scratch inventory is checked for
expected hashes, sizes, regular files, and absence of symlinks or external hard
links. A failed preflight removes nothing. Canonical native results, exports,
manifests, replay receipts, runtime context, and verified remote archive objects
remain in place.

The replay export is written to
`<stage-results-root>/closed/<registered-entry-id>/export` only for this opt-in
path. The CLI derives the entry id and results root from the private runtime
and confirms it against the prepared stage, campaign journal, parent manifest,
run ids, and exact replay capsule. The fresh quota reserve must meet the frozen
runtime controller minimum. Reconciliation then verifies and reuses that export
directory instead of making another raw copy. The default path is the
existing `results/analysis/archive-replays/<campaign-id>` location and keeps all
scratch data.
