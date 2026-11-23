# Registered failure operator recovery

This helper supports one already-dispatched `V-A-FAIL`, `V-Z-FAIL`, or
`V-G-FAIL` entry at a time. It does not start or resume a campaign. It reads
the prepared stage, campaign journal, runtime/config/source snapshots, original
lifecycle recovery, saved cleanup settings, provider journal, and ledger before
arming. It requires exactly one provider in the entry and recovery, and no other
entry in `starting` or `running` state. Other registered entries may remain
scheduled.

## Arm before the guest server fault

Run on the Linux runner from the deployed source environment, using the exact
prepared stage path:

```sh
setsid "$CNB_PY" -m cloud_network_benchmark.operator_recovery supervise \
  --prepared-stage /PRIVATE/STATE/STAGE_ID/prepared-stage.json \
  --entry-id V-Z-FAIL </dev/null \
  >>/PRIVATE/STATE/STAGE_ID/operator-recovery-V-Z-FAIL.log 2>&1 &
```

Use the registered entry matching the single provider: `V-A-FAIL` for AWS,
`V-Z-FAIL` for Azure, or `V-G-FAIL` for GCP. The helper derives the campaign
supervisor PID only from the original `campaign-supervisor.pid`, then verifies
its UID, boot ID, start ticks, working directory, module command line, and state
root. It retains that verified pidfd until the first ancestry capture confirms
the same process identity, closing the PID-reuse gap before any stop signal. It
captures descendants by ancestry, including children in separate sessions,
and freezes child-first to a stable process-tree fixed point. It does not use a
shared process group.
After freezing, it revalidates the immutable prepared entry, parent/child IDs,
source configs, runtime, saved cleanup settings, recovery liabilities, provider
expiry, and original cap before accepting a guest fault receipt. Observer
receipt/binding progress is allowed during the live campaign. Changes to
immutable authority before freeze abort and thaw only helper-stopped owners;
source drift after retirement is recorded as changed or unreadable and does not
block the original cleanup-only attempt. Ledger progress is not used as an
immutable whole-file hash; cleanup still validates the original saved
reservation and recovery record.

Before stopping the guest server, verify the private checkpoint exists at
`STATE_ROOT/STAGE_ID/operator-recovery/ENTRY_ID/checkpoint.json` with
`phase=controllers_frozen`. The helper waits only until the original
reservation expiry minus the saved cleanup allowance. It never resets either
deadline. If the exact server-fault proof is absent or invalid at that cutoff,
the result is inconclusive; it retires the captured tree and runs cleanup-only.

## Capture the actual server fault

While the registered TCP measurement is active, use the already-reviewed SSH
route to stop the exact deployment's VM B listener:

```sh
ssh -i ACTUAL_KEY -o UserKnownHostsFile=ACTUAL_KNOWN_HOSTS \
  -o StrictHostKeyChecking=yes ACTUAL_USER@ACTUAL_VM_B \
  'sudo systemctl stop cnb-ACTUAL_DEPLOYMENT_ID-iperf3.service'
```

Capture the SSH transcript privately, then capture the read-only service state:

```sh
ssh -i ACTUAL_KEY -o UserKnownHostsFile=ACTUAL_KNOWN_HOSTS \
  -o StrictHostKeyChecking=yes ACTUAL_USER@ACTUAL_VM_B \
  'systemctl show --property=ActiveState --property=SubState --value cnb-ACTUAL_DEPLOYMENT_ID-iperf3.service'
```

After verifying the transcript shows the listener had active TCP traffic before
the stop and the readback is exactly `inactive` / `dead`, write the private
`server-fault-proof.json` in the checkpoint directory with these fields:

```json
{
  "entry_id": "V-Z-FAIL",
  "provider": "azure",
  "target_unit": "cnb-ACTUAL_DEPLOYMENT_ID-iperf3.service",
  "command": "sudo systemctl stop cnb-ACTUAL_DEPLOYMENT_ID-iperf3.service",
  "readback_command": "systemctl show --property=ActiveState --property=SubState --value cnb-ACTUAL_DEPLOYMENT_ID-iperf3.service",
  "observed_state": "inactive",
  "readback_stdout": "inactive\ndead",
  "server_was_active_tcp": true,
  "read_only": true,
  "captured_at": "ACTUAL_UTC_TIMESTAMP",
  "guest_receipt_path": "PRIVATE_TRANSCRIPT_PATH",
  "guest_receipt_sha256": "SHA256_OF_TRANSCRIPT",
  "readback_path": "PRIVATE_READBACK_PATH",
  "readback_sha256": "SHA256_OF_READBACK"
}
```

The helper accepts only the unit derived from the source-bound deployment ID,
the matching commands and entry/provider, private receipt files with matching
hashes, and the exact stopped state. It then sends `SIGKILL` through the held
pidfds to those captured owners only and waits for them to be gone. It never
signals a process group. Physical Halle loss remains waived; this is ordinary
process recovery.

## Original provider expiry and cleanup

After old owners are gone, place a private `expiry-proof.json` alongside the
checkpoint only after the registered provider's original trigger and a
read-only provider readback. It must bind the derived `binding_sha256`, exact
provider, registration ID, full target selector, expected state (`stopped`,
`deallocated`, or `terminated`), observation UTC time, read-only command,
`readback_path`, and the readback file's SHA-256. The helper checks the readback
file and rejects a future timestamp, wrong registration/target/state, or an
observation before the original trigger.

The original trigger comes from the provider-native journal: AWS
`expiry_evidence`, GCP `expiry`, or Azure internal `expiry.state` plus
`expiry.metadata`. Azure uses the actual minute-floored `metadata.trigger`,
`metadata.target_ids`, `metadata.job_schedule_id`, `metadata.automation_account_id`,
and `metadata.api_version`; the requested trigger is not substituted. The
original cap and cleanup allowance come from the typed saved cleanup settings.
The latest safe observation time is the original cap less that saved cleanup
allowance. If matching provider proof is absent then, the helper records expiry
as unproved/inconclusive and immediately prioritizes the single cleanup-only
call within the saved finite cleanup timeout.

The only command the helper constructs is the existing
`lifecycle cleanup --recovery ORIGINAL --controller-session ORIGINAL --format
json` route. It derives Python from the running helper, the repository root from
the prepared stage, and all other arguments from the original recovery record.
An exclusive create-only claim prevents a second cleanup call. The output and
checkpoint retain the exit code and actual provider cleanup report. No
measurement, `campaign run`, continuation, registration, or relaunch follows.
