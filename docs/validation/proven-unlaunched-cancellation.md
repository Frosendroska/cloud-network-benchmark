# Proven-unlaunched reservation cancellation

This is a narrow F11 accounting terminal for the four acceptance reservations
listed in `admission.py`. It restores each record's original funding buckets
only when the pinned protection rejection, parent terminal journal, complete
provider recovery summary, ledger state, and all hashes agree. It does not
record a bill, declare an invoice final, or establish that invoices are zero.

The reviewed baseline is commit `70267ec9dd58b0149f281fb66a8d2520b19cfd92`
with tree `cfdd9dda76ee4dbb3f42c08d5dcf364d962545ac`. The accepted failures
are all-provider protection rejections before any creation claim. The two retry
S3 records additionally require the paired Azure anchor/settings snapshot to
show the exact pre-provider timing-anchor rejection. Missing or changed source
artifacts, a nonzero/final billing record, a transfer debit, an expiry or
creation claim, active cleanup, an accepted review, or a different reservation
identity blocks cancellation.

## Review then apply

Create a private JSON proof file for one reservation. It must contain every
field from `UnlaunchedCancellationEvidence`; use the current ledger SHA-256 and
the SHA-256 of each exact evidence file. The controller-quiescence artifact
must bind the saved attempt and controller-session IDs, terminal state, a fresh
complete UID-scoped `/proc` inventory, and both campaign lock files' absolute
paths, device numbers, inode numbers, and exclusive-held observations. Process
rows include PID, UID, start ticks, cwd, cmdline SHA-256, ancestry PIDs, and the
attempt-scope match result. Preview/apply derives the state-root
`.campaign-driver.lock` and stage-root `campaign-journal.lock` from all three
saved provider journal references, acquires them in the campaign runner's lock
order, checks device/inode identity, and repeats a live `/proc` quiescence scan
while holding both. The scan fails closed if `/proc` is unavailable,
incomplete, or finds an attempt process. It does not fabricate held pidfds for
processes that are no longer running. If these proofs are unavailable, retain
the full hold. The exact user accounting authorization receipt is also
hash-bound. For the retry S3 reservation, include
both `azure_anchor_path`/`azure_anchor_sha256` and
`azure_settings_path`/`azure_settings_sha256`. The source commit/tree fields
must match the reviewed baseline above. Keep proof files private; the Azure
settings snapshot includes sensitive environment metadata.

Preview is read-only:

```sh
PYTHONPATH=src:. python -m cloud_network_benchmark.admission cancel-unused \
  --ledger /path/to/budget-ledger.json \
  --scope-id thesis-primary \
  --controller-session-id cancellation-review \
  --proof /path/to/private-cancellation-proof.json
```

The explicit `--apply` switch performs one local, lock-protected ledger
transaction. It is intended only after independent review of the preview and
the current financial authority. Apply requires Linux `/proc` evidence.
Refresh `expected_ledger_sha256` after any
ledger revision; a stale snapshot fails closed.

```sh
PYTHONPATH=src:. python -m cloud_network_benchmark.admission cancel-unused \
  --ledger /path/to/budget-ledger.json \
  --scope-id thesis-primary \
  --controller-session-id cancellation-review \
  --proof /path/to/private-cancellation-proof.json \
  --apply
```

The transaction preserves reservation, plan, cost-source, financial snapshots,
claims, source history, and prior evidence. It appends a separate hash-bound
terminal event, restores original buckets once, and permanently blocks that
reservation identity from resuming lifecycle work. A later billing or resource
contradiction reactivates the full conservative exposure and blocks admission.
Ordinary final-billing reconciliation requirements are unchanged. Remaining
funding, other reservations, and external liabilities continue through the
existing shared-finance checks.

This feature does not authorize provider calls, ledger mutation by this review
task, new acceptance retries, DAY/WEEK work, or a claim about current provider
invoices. Current console usage and alerts must remain separate sourced inputs
in any later financial review.
