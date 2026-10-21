# F02R research and engineering decisions

Date: 2026-10-21. Source checkout: Thesis `8927ff5a17a7113ac8080c15b412a4b8d8270744`; implementation base `cef838e42fc620f90b5943f5a8d1528ddc0b0724`.

## Sources and precedence

Read implementation AGENTS.md, constitution 2.0.0 and `docs/autonomous/WORKFLOW.md`; canonical Thesis `advisor/docs/design_document.md`, `DECISIONS.md`, `experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md`, `tracking/IMPLEMENTATION_PROMPTS.md` (F02R prompt and acceptance), `experiments/EXPERIMENTS.md`, `tracking/M04_implementation.md` and `tracking/framework_implementation_roadmap.html`. The 19 October concurrent P0/P1 supersession governs historical schedule text. These are source snapshots, not formal advisor approval or cloud evidence. No current source conflict found. No web lookup was necessary: this stage selects local engineering boundaries, not current release versions or provider mechanisms.

The actual skills for specify, clarify, plan, checklist, tasks and analyze were read from `.agents/skills/`. No `.specify/extensions.yml` exists. Per-worktree `feature.json` points only to this feature; explicit context accompanies scripts. Script JSON calls its feature-derived branch `004-v2-remote-access`; actual Git branch remains `codex/f02r-v2-remote-access`.

## R1 — Migrate, preserve the delivered baseline

**Decision**: Edit existing `access.py`, `bootstrap.py`, `readiness.py` and `retrieval.py`; update their existing tests and add feature-scoped fixtures/integration tests. Preserve historical specs 001/002 and frozen shared F01R contracts.

**Rationale**: Existing roles, command-runner replacement, transfer collision checks and failure evidence remain useful. Inspection found legacy FLENT/netperf setup and netserver health defaults; readiness currently creates a deadline per check; SCP lacks BatchMode/strict options; retrieval can manufacture a file from command stdout and optionally skips expected hash comparison. These are migration targets, not reasons to restart the framework.

**Alternatives considered**: New remote framework or duplicate V2 module tree would obscure historical behavior and add needless abstractions. Keeping obsolete FLENT defaults behind implicit compatibility would make successful V2 preparation ambiguous. Replace superseded tests while retaining non-obsolete safety coverage and historical specs unchanged.

## R2 — Authenticated transport and redaction

**Decision**: Reuse `RemoteConnectionData` and `CommandRunner`. Both SSH and SCP include `BatchMode=yes`, `IdentitiesOnly=yes`, `StrictHostKeyChecking=yes`, attempt key and required authenticated known-hosts file. Disable password/keyboard-interactive fallback. Provider drivers authenticate/populate known-hosts out of band; F02R rejects missing/empty local references and never trusts `ssh-keyscan` as authentication. Encode the remote command as one safely quoted argv string, because OpenSSH invokes a remote shell even when the local runner uses `shell=False`. Validate usernames and all generated names/paths, reject option-like or control-character inputs and quote values; never interpolate raw shell fragments. Use SCP's default SFTP transport; reject glob/metacharacter remote paths and do not enable legacy `-O` expansion.

**Rationale**: Shell-free local argv alone does not protect the remote interpretation boundary. Explicit redaction of known credential paths/values must apply to all command/action/failure serialization, not only error text. Use F01R durable views plus existing access failure scrubbing; do not alter shared serializers.

**Alternatives considered**: Permissive host checking, password fallback and raw remote shell strings undermine the approved identity policy. New SSH libraries/cloud SDKs are unnecessary.

## R3 — Source builds and persistent server preparation

**Decision**: Require explicit source URL/archive basename, release/reference and lowercase SHA-256 for each tool. No invented release/digest default. Download only during the future remote bootstrap boundary, verify before extraction, build/install under a deployment-scoped prefix and record actual compiler/version, binary path/version/SHA-256 and help/capability observations. Reuse only a matching verified installation; never report the requested version as an observation. Fail on malformed/missing evidence or unsupported flags.

Ubuntu bootstrap prerequisites include compiler/build essentials, autotools/pkg-config as required by the supplied sources, curl, CA certificates, archive/hash tools, iproute2, ICMP and TCP-connect utilities and narrowly scoped guest firewall tools. No package upgrade runs during measurement. Idempotent source build may be rerun after incomplete preparation; a conflicting live server or source identity fails without deleting evidence.

Use two deployment-named transient systemd services on B, ordinary tool processes rather than an agent: Sockperf `server -i B-private -p 11111`; iperf3 `-s -B B-private -p 5201 --json-stream --forceflush`. Capture output in the F01R server paths using append-safe service output descriptors. Before starting, apply an idempotent dedicated guest firewall chain for B's private IP/ports: allow A private /32 then reject other sources on those destinations; leave management rules and unrelated chains unchanged. Provider network rules are separate provider-owned evidence. Matching active units are reused without truncation; mismatches fail. Readiness only inspects units, process/binary identity, socket bindings and chain rules.

**Rationale**: Persistent servers satisfy all modes without introducing an orchestration service. Builds and capability probes remain fakeable. Required capabilities are defined in contracts; help/version fixtures establish offline logic, not real build viability or runtime measurement behavior.

**Alternatives considered**: Stock unverified package binaries, self-reported requested versions, netserver and relaunch-on-readiness do not meet F02R. Global firewall resets risk management access. Tool release selection remains ordinary caller configuration, with actual future capability checks; no mandatory software-freeze review.

## R4 — One readiness deadline and original cap

**Decision**: Caller creates one local deadline context before the first preparation SSH/cloud-init action. It contains absolute monotonic readiness and overall deadlines mapped once from the original cap, plus a positive caller cleanup allowance. Preparation and readiness use the same object; checks/polls/builds never reset it. Every pre-measurement command timeout is `min(per-command limit, readiness deadline - now, overall deadline - now)`; no command starts at nonpositive remainder. Poll waits use the same remaining budget. Cloud-init must complete before source installation, and readiness observes its completed state on both VMs.

F11/F09 activate the admitted reservation immediately BEFORE independent expiry registration and confirm expiry before first resource creation: `cap_started_at <= expiry.registered_at <= creation`. Registration/setup time counts; expiry does not exceed the reserved cap. F02R receives the existing deadline, never activates a reservation or registers expiry, and has no dependency on those unmerged implementations. Use shared `ReservationEvidence`/`ExpiryEvidence` in integration fixtures without changing them.

**Rationale**: One budget makes late SSH/cloud-init/build time visible and leaves no per-stage extension. A fake clock proves cumulative exhaustion instantly. UTC evidence remains recorded while monotonic time enforces local bounds.

**Alternatives considered**: Starting the cap after expiry registration contradicts frozen F01R ordering. Resetting readiness deadlines per VM/predicate or adding a copy timer violates the overall bounds.

## R5 — Readiness observes specific predicates

**Decision**: Require matching successful `BootstrapResult`, true authenticated SSH on A/B, cloud-init done on A/B, ICMP from A to B, bounded TCP connect from A to B:5201, B's exact private UDP:11111/TCP:5201 bindings with the expected processes/units and observed guest firewall restriction. Reject wildcard/public binds and mere textual `active` without matching listener/process. Each observation has its own action ID; failures retain the last observation.

**Rationale**: ICMP plus UDP listener and ACL observations establish the required preparation checks without sending benchmark probes in readiness. They do not prove end-to-end UDP delivery or actual provider security enforcement; real acceptance remains separate. No health command mutates a service.

**Alternatives considered**: A UDP connect success is not proof of reachability; starting servers inside readiness hides failures.

## R6 — Closed native inventory and bounded copy failures

**Decision**: F09 ends measurement, stops/flushed producers including both persistent B servers at experiment end, and supplies a deployment-bound closed inventory. This is one final export for retained calibration/day pairs, not per-trial/chunk off-VM copying. F02R may observe remote `stat`/SHA-256 for each closed path through the command runner; required remote size/hash and closure evidence must be present before a transfer can verify. Inventory may include partial-day closed files; missing required items are explicit failures, while absent optional items are reported as unavailable.

Copy into unique private staging paths under the result child root; stream local hashing in bounded chunks. Verify size/digest and publish the first valid attempt at its declared immutable native destination without overwriting existing files. A fake must physically materialize the destination bytes; SCP stdout is never file content. Zero-byte error logs may verify if observed remote size is zero; empty required measurement content is later a data-validity issue, not fabricated transfer failure/success.

On an ordinary copy failure, retain failed bytes at their attempt path and allow at most one further attempt for that artifact. Continue other eligible artifacts within the same original cap. Interruption or exhausted budget stops immediately. Each transfer/poll is bounded by `min(item timeout, overall deadline - now - cleanup allowance)`; do not start if the remainder is nonpositive. No new timer, disk retention, recovery allocation, experiment repeat or deadline renewal. Return cleanup-required on complete and incomplete results, with all independent failures; F09 attempts normal deletion by cap and records residual resources if deletion fails. Primary measurement failure stays with the caller and is never replaced by a copy error.

**Rationale**: The source deliberately delegates exact cap/copy engineering decisions and forbids an automatic retention workflow. This bounded best-effort policy preserves available local evidence and reserves existing cleanup time without promising recovery of VM disks. At cap, deletion may lose unexported remote data; record that limitation explicitly rather than pretending the export succeeded.

**Alternatives considered**: Unlimited retries, another 15-minute timer, retention promises, periodic checkpoints and deleting partial local evidence were rejected. Normal deletion remains the approved end of an experiment, not a new failed-data retention rule.

## Clarify outcome

No questions asked. The coordinator supplied cap ordering; the remaining design-consistent decisions above resolve engineering ambiguity. All clarify taxonomy categories are clear or outside this bounded feature. Numerical N/windows, overlap thresholds, provider expiry mechanism/permissions and paid price refresh are other owners' responsibilities, not unresolved F02R prerequisites. No research choice was changed.
