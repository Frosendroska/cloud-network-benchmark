# AWS pre-VM failure and lifecycle reporting repair

14 November 2026. Tested implementation **eb91626507d0419003a739ecc314dbbf9549fc14**
on `codex/aws-pre-vm-failure-repair`. Baseline ccb8a7e had executable trees matching
0b70033.
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

Native Linux Python 3.12.3 at exact **eb91626**: **266/266 cases passed**,
zero failures/errors/skips. Terraform 1.16.6: both AWS roots init with readonly
locks and validate; **four mock test cases** passed (two bounded positives,
two original-long expected rejections). Actual native resource-change scope
checks passed: safety three managed addresses, pair fourteen. The driver passed
**15/15 checks**, with clean source before/after and all seven executable hashes
matching the Mac. The Terraform roots, constraints and locks are unchanged.
Native input proof comes from normal initialize_campaign output: old namespace
90 characters, bounded namespace 80, full run/deployment IDs retained.

Mock plan output omits ordinary show's format/variables envelope; the adapter
adds only those from the native input snapshot and preserves native
resource_changes. These are mock-provider plans, never provider apply/acceptance.
The first transfer identity check stopped before tests because fast-export
rewrote a signed merge commit; matching trees were verified and the original
commit objects were restored before the passing exact-source run. That initial
check is retained in the archive.

[Machine-readable source/log/JUnit evidence](pre-vm-failure-repair-evidence.json).
Downloaded archive `/private/tmp/cnb-pre-vm-eb91626-native-evidence.zip`,
2,689,066 bytes; CRC and **34 file hashes** independently verified on the Mac.
Archive SHA-256:
`b4fb3e8aaf485686f9f52cb1a4256554e6e5232ba990e94fef660d5450d6a537`.
Native summary SHA-256:
`3e3c46fa09711eef7e609b3df085ec4bfb434471c40d7f6cb836f63a4d5b0a5e`.
Native JUnit SHA-256:
`c9a45be061c736884e6dc17438badc08e7ef69eb26152cd0bf72a56ac9bab6a3`.
Native pytest log SHA-256:
`53ca3cc092a5e2d5dbcf0caf6de32517d45643c2c55c74473663650949af78f0`.
Remote source/evidence remains separate under
`/u/halle/brek/home_at/cnb/saved-plan-native-wsJSS7/pre-vm-repair-*`.
No unchanged broad suite, cloud credential/environment, provider apply, real
financial mutation, active input or original execution-state change was used.

## Handoff boundary

No code path has been exercised live by this repair. Execution chat owns original
cleanup and independent billing reconciliation. Changed executable/source bindings
need fresh review and exact authority before any separately authorized attempt;
the prior three-cloud card/window does not authorize this source or a retry.
Remaining live acceptance includes real plan review/apply, expiry, VM/guest/SSH,
measurement/retrieval, controller/server failure and full cleanup. Unknown eventual
charges remain conservative liabilities until independent billing evidence arrives.
