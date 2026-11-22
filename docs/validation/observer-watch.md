# Independent campaign observer watcher

The campaign supervisor writes `observer-status.json` while it is alive. The
separate `cloud_network_benchmark.observer_watch` process reads that file and
detects an unhealthy or stale snapshot after the supervisor exits. It never
dispatches campaign work. Run it on the same durable controller that owns the
state directory (for example, in a separate SSH session on Halle); closing the
Codex chat does not stop that process. A host shutdown still stops both
processes.

Start it manually, after the campaign state directory exists:

```sh
PYTHONPATH=src nohup .venv/bin/python -m cloud_network_benchmark.observer_watch \
  --state-root /absolute/path/to/campaign-state \
  --interval 30 --stale-after 90 \
  > /absolute/path/to/campaign-state/observer-watch.log 2>&1 \
  < /dev/null &
```

The command is an operator example, not an installed service or an automatic
start. By default it only reports events to its log. Real notification delivery
requires an operator-configured executable and the explicit `--deliver` flag;
the hook receives a single JSON event on stdin. Events are deduplicated while
the same fault remains. A failed hook is reported and retried on the next poll.
Healthy unchanged snapshots stay quiet.

## Scope and qualification boundary

The supervisor's `output_freshness` is controller-local evidence only. During
measurement, native outputs remain on the guests; a closed or remote archive
may not be represented by local raw files. `observer_mode` therefore states
that guest health is unqualified unless the live binding probe succeeds. The
independent watcher detects snapshot staleness, missed schedule entries,
supervisor faults, and an observer that has stopped updating. It is not a
second dispatcher.

## Guest binding publisher contract

When any journal entry is `starting` or `running`, the lifecycle owner must
publish `observer_bindings_path` and `observer_bindings_sha256` in the durable
prepared stage/journal before treating guest health as observable. The digest
is SHA-256 of the exact JSON bytes. The JSON top level contains `stage_id`,
`campaign_id`, and only the exact active `entries`. Each entry contains
`entry_id`, `provider`, `run_id`, `deployment_id` (the admitted deployment ID),
and `roles` with exactly `vm_a` and `vm_b`. Each active
`(entry_id, provider, run_id)` must appear exactly once. Results use that
composite identity so one provider cannot overwrite another. The consumer checks
the complete expected participant set, stage/campaign, entry/provider/run,
deployment and active-entry set against the prepared stage; it checks each
workspace against that entry's runtime bootstrap workspace.

The prepared stage also binds `observer_launch_receipt_path` and
`observer_launch_receipt_sha256` to the actual launch receipt. Its `stage_id`
must match and its `participants` map must contain exactly all active composite
identities. Each participant maps both roles to the provider-owned `vm_id` and
the exact connection tuple (`host`, `user`, `port`, `known_hosts`,
`identity_file`) from provider and `ConnectionConfig` receipts. The guest binding
repeats that tuple and VM ID; any mismatch leaves health unknown/faulted. SSH
target is constructed from the validated receipt fields, never accepted as an
arbitrary shell target. SSH explicitly supplies its identity file, port,
known-hosts file, `IdentitiesOnly=yes`, and strict host-key checking.

Each role contains exact `workspace`, `processes`, and `files`. VM A must publish
`producer` (actual measurement producer) and
`diagnostics` process records; VM B must publish `receiver` (actual iperf3
receiver) and `diagnostics`. Every process record has `kind`, `pid`, `boot_id`,
and `/proc/<pid>/stat` `start_ticks` captured when that exact process starts.
VM A `files` must include the current Sockperf chunk and any completed chunks,
plus the local diagnostics path. VM B must include receiver JSONL and local
diagnostics. File records have workspace-relative `path` and `kind`; Sockperf
chunks additionally have `state: current|completed`. Producer/receiver/diagnostic
files must be nonempty and fresh; completed chunks must be present and
nonempty, and the current chunk must be fresh. Unsafe or escaping paths are
rejected. Unknown, stale, unreachable, malformed, reused-PID, incomplete or
identity-mismatched probes cannot yield healthy.

The consumer makes bounded `ssh` calls using batch mode, strict known-host
checking, 5-second connect and at most 12-second per-probe limits. All probes
in one observer cycle share a 20-second total deadline; later probes are marked
budget-exhausted after that deadline. Its remote Python
snippet only reads `/proc`, the boot ID, workspace existence, and explicitly
bound file metadata. It never copies data, runs a provider CLI, or changes guest
state. All subprocess and clock boundaries are injectable for no-call tests.

**Lifecycle-owner handoff:** at the measurement launch boundary, persist the
actual acquisition process handle and its boot ID/start ticks; persist the
provider-owned VM ID and exact SSH connection tuple in the hash-pinned launch
receipt; for each
Sockperf rotation, atomically update the current chunk path and mark prior
chunks completed; publish the iperf3 receiver process and streaming JSONL path
on VM B; and publish each diagnostics collector identity plus JSONL path on
both guests. Bind those exact records to provider/run/deployment/role/workspace
and the owned VM receipt, then atomically write the binding bytes and digest
before setting the entry running. Refresh the binding after each chunk rotation
and on terminal state. If a process handle/path cannot be durably published,
leave guest health `unknown`. This consumer does not infer or reconstruct these
records from names or logs.

No native guest or notification qualification is implied by fakes. Still
required: prove the exact `/proc` identity and file freshness checks on native
Ubuntu guests; verify SSH host-key and user mapping against the launch receipt;
exercise hook delivery and failure on the selected destination; and show the
watcher remains running across chat closure on the controller. Controller reboot
recovery remains separate; physical Halle loss/reboot remains waived, not
passed. `--provider-preflight` on the campaign supervisor explicitly enables its
existing 300-second read-only identity/expiry check. It can run AWS STS and GCP
ADC token refresh. The default observer does not call it; local credential
horizon comparison alone is not proof that credentials work.
