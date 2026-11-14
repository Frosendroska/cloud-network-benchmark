# AWS pre-VM failure and lifecycle reporting repair

14 November 2026. Baseline ccb8a7e, with executable trees matching 0b70033.
[Bounded repair artifact](../../specs/013-v2-lifecycle/repairs/pre-vm-failure.md).

The execution chat reported a 90-character namespace from normal initialization
IDs rejected by the unchanged <=80 AWS Terraform inputs. The first safety plan
failed before apply; rollback destroyed only empty state. The provider failure
then reached finalization as initialized and CLI returned invalid input with zero
calls. Independent execution-chat API evidence later closed cleanup through the
existing ledger API while retaining its full high-water liability. This repair
never opens or modifies that live source, inputs, state, authority, journal or
ledger and performs no cloud retry.

## Changes

One AWS function now supplies a deterministic <=80 namespace to provider inputs,
F11 admission/financial bindings and recovery locations. Short names retain their
original bytes. Long names retain a readable prefix and a 24-hex SHA-256 suffix;
run/deployment IDs and ownership tags are unchanged. Terraform limits remain.

Existing old long journals are resolved in place. Cleanup validates the original
saved request namespace, retains original vars/state paths and rejects missing or
ambiguous journals. It does not regenerate original namespaces after this code
change. There is no live-state migration.

Early protection/claim failures and aborted peers now have failed execution state.
Finalization retains the typed protection failure, keeps unavailable guest
measurements unavailable, and persists strict failed-data validation. The
initialized-manifest invariant remains unchanged. CLI counts actual dispatched
commands/copies, including failing calls and cleanup, and retains provider
failures when finalization itself fails. The private saved-plan directory retains
`plan-result.json` with redacted command-result stdout/stderr, including a rejected
plan before it can produce a binary or apply. Callback-only offline cleanup reports
zero external commands, rather than counting provider callbacks as cloud calls.

Cleanup authenticates the expected account and independently inventories all
12 tagged regional categories even before a VM apply. It destroys any partial
safety root with original inputs and verifies the exact role/document via typed
absence errors. Prospective targets are lookup identities, not invented created
resources. Wrong account, denied/truncated inventory, present role, denied document,
modified original inputs or uncertain execution identity keeps cleanup blocked.
Lost execution identity preserves protection; it never starts a new execution.
The source-bound F11 absence receipt includes independently checked expected ARNs,
closes cleanup only and retains the original conservative financial liability.

## Focused validation

Local Python 3.13.7: **266 distinct passing cases**, zero failures/errors/skips:
129 failure/provider cases + 76 reporting/manifest/init/factory cases, then 61
shared saved-plan cases plus 17 new-case reruns after adding private plan logs.
The new 17-case regression module uses normal initialize_campaign IDs, source-shaped
commands, real orchestration/finalization/F11 cleanup reconciliation, old namespace
recovery, original input/state retention, failure/unknown inventories and truthful
CLI diagnostics. Existing fake stage runners gained explicit identity/absence
responses; production checks were not bypassed. New failure-path tests exercise
the actual saved-plan boundary; only external commands are injected. Synthetic
financial fixtures retain their explicitly scoped test isolation and grant no
financial-readiness evidence.

Native validation is pending at this source checkpoint. It will use the affected
Python delta and two AWS Terraform mock roots with bounded-positive/old-long-negative
inputs; unchanged broad baselines and paid execution are excluded.

## Handoff boundary

No code path has been exercised live by this repair. Execution chat owns original
cleanup and independent billing reconciliation. Changed executable/source bindings
need fresh review and exact authority before any separately authorized attempt;
the prior three-cloud card/window does not authorize this source or a retry.
Remaining live acceptance includes real plan review/apply, expiry, VM/guest/SSH,
measurement/retrieval, controller/server failure and full cleanup. Unknown eventual
charges remain conservative liabilities until independent billing evidence arrives.
