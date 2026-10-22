# F02R remote access contract

Version: F02R planning 2026-10-21. Shared baseline: F01R `cef838e42fc620f90b5943f5a8d1528ddc0b0724`. This contract describes local feature interfaces; it does not amend shared F01R schemas.

## Facades and required arguments

Keep existing import locations and function names. Keyword-only additions are mandatory in V2; do not silently synthesize missing caps, closure or preparation proof.

| Interface | Input | Output |
| --- | --- | --- |
| `ConnectionOutput.from_deployment(deployment, timeouts)` | Complete `ProviderDeploymentOutput`, existing `TimeoutPolicy` | Connection preserving `IdentityContext`, deployment/provider/scenario/roles and authenticated references |
| `prepare(connection, bootstrap_config, command_runner, *, deadline, clock)` | Source config and original `RemoteDeadline`; injected monotonic clock | `BootstrapResult` including observed build/capability/listener evidence |
| `check_readiness(connection, readiness_config, command_runner, *, prepared, deadline, clock)` | Matching successful preparation and same deadline/clock | `ReadinessResult`; no server mutation |
| `retrieve(connection, retrieval_manifest, command_runner, filesystem, *, deadline, clock)` | Closed concrete native inventory, child root and original overall deadline | `RetrievalResult` with artifact records/attempts/failures and cleanup-required |

Implement the same arguments in existing `run_bootstrap`, `run_readiness`, `run_retrieval` functions. Default clock may be the real monotonic function; tests always inject it. A missing `deadline` is an input error. Models/validation are specified in [data-model.md](../data-model.md). Do not add a generic remote transport class or runtime service.

## Frozen imported contracts

Use existing `contracts.deployment.ProviderDeploymentOutput/RemoteConnectionData`; `contracts.common.IdentityContext/StrictModel/Evidence/StringEvidence`; `contracts.execution.CommandRequest/Result/Runner`; `contracts.artifacts.ArtifactInput/Record/Layout/resolve_artifact_path`; and admission/expiry evidence only for caller-context fixtures. New F02R models belong in `access.py`, `bootstrap.py`, `readiness.py` and `retrieval.py`, not shared `contracts/`. If a shared interface proves insufficient, stop that amendment and report it to the coordinator with exact evidence.

F03/F04 produce native files and F09 composes closure, caps and cleanup later; no dependency/import from their implementations is permitted. F02R fixtures synthesize those frozen inputs. `ArtifactLayout` owns names; the handoff's illustrative file tree does not supersede it. Keep dynamic diagnostic chunk IDs/unit relationships and deployment identity.

## SSH/SCP transport

Each local command is a `CommandRequest` passed to `CommandRunner`, never a local shell. Required management options are attempt `-i`, port (`ssh -p`, `scp -P`), `BatchMode=yes`, `IdentitiesOnly=yes`, `StrictHostKeyChecking=yes`, `UserKnownHostsFile=<authenticated reference>`, `PasswordAuthentication=no` and `KbdInteractiveAuthentication=no`. Do not use `StrictHostKeyChecking=no`, `accept-new`, passwords or unauthenticated key discovery. Verify required local reference files before execution. Parse/validate fields and protect remote argument interpretation; SSH command tokens must be serialized with shell quoting into one remote command argument. No raw user command string is an API. Remote SCP paths must be literal constrained paths, with no expansion/metacharacters or legacy SCP mode.

Keep real runtime references private. Durable action/request/result/failure payloads must redact supplied paths, key values, PEM blocks and credential-like text on success and failure, including nested fields. Preserve nonsecret commands, source identities, results and timestamps. The fake runner must inspect unredacted in-memory argv while exported evidence remains redacted.

## Source/build/capability preparation

Preparation observes cloud-init completion before installing/building on each VM. For each configured archive, record download/source identity; compare `sha256sum` output with the required digest before any extraction/build. Use explicit safe source/build paths and tool-specific fixed configure/build/install steps. Capture compiler `--version`, binary version/path/SHA-256 and observed help/capabilities from the installed prefix. No FLENT/netperf/netserver setup remains in the V2 path.

Required capability observations:

| Tool | Supported command surface required |
| --- | --- |
| Sockperf | `server`, `under-load`, private `-i`, `-p`, UDP operation, `-m`, `--mps`, `--reply-every`, `--full-rtt`, `--no-rdtsc`, `--full-log`, `-t` |
| iperf3 | server/client `-s/-c`, private bind `-B`, `-p`, `-P`, `-t`, `-O`, `-i`, `-l`, `-C cubic`, `-Z`, `-J`, `--get-server-output`, `--json-stream`, `--forceflush` |

Offline help fixtures/probe outcomes prove capability detection and exact command construction. They cannot establish real compiled binary behavior or compiler/package availability. Missing, malformed or contradictory observed output fails preparation. No benchmark client invocation is needed to inspect help/version.

Start persistent B services in preparation only, using absolute installed binary paths with these semantic argv:

- Sockperf: `sockperf server -i <B-private> -p 11111`.
- iperf3: `iperf3 -s -B <B-private> -p 5201 --json-stream --forceflush`.

Use deployment-owned transient systemd units and append-safe output to `ArtifactLayout.sockperf_server_log`, `.iperf3_server_log`, `.iperf3_server_error` under the deployment workspace. Scope guest ingress rules to B's private IP and these protocol/ports: A private /32 allowed, other sources rejected. Preserve management/unrelated rules; no global firewall reset. Existing matching unit/process/binary/bind/source configuration may be reused; mismatch fails without truncating logs or restarting. Neither unit restart policy nor readiness may silently restart a failed server. Servers remain until caller ends the experiment and closes/flushed producers; F02R readiness never stops them.

## Readiness and deadline predicates

The caller supplies the original `RemoteDeadline` before preparation; all preparation/readiness SSH, cloud-init, build, checks and polls share it. Compute `remaining = min(readiness_deadline, overall_deadline) - monotonic_now` before every action/wait; timeout is at most remaining and the relevant configured ceiling. At `remaining <= 0`, stop without invoking the runner. Reusing a duration to make another deadline is prohibited. Bound polling waits similarly and retain elapsed/remaining observations. No real sleep is used in fake-clock tests.

Readiness must observe all of: matching successful preparation; SSH true on A/B; cloud-init done A/B; bounded ICMP A→B; bounded TCP connect A→B:5201; B's exact UDP 11111 and TCP 5201 private bindings; expected service/process/binary identity; and narrow ingress rules. A textual active service alone is insufficient. Fail private/wildcard/wrong-protocol binds, missing rules or stale/mismatched process identity. UDP listener/ACL plus ICMP is a preparation predicate, not a claim of end-to-end UDP delivery. Health checks cannot include start/restart/install/firewall mutation.

## Closed-file inventory, transfer and failure behavior

The caller supplies UTC measurement-end and producer-closure evidence bound to this deployment after measurement, including final B server output flush/closure. No off-VM retrieval during measurement, even for completed probe chunks. The inventory retains nonempty frozen `ArtifactInput` declarations, concrete present `items` and explicit `unavailable_items`; every remote declaration resolves to one of the latter two, so an empty present-file list cannot silently satisfy required export. Resolve dynamic native declarations into concrete source-role/path/destination/unit entries. Capture required remote size/SHA-256 observations of closed files using the command boundary before transfer; remote absence is an explicit failed/unavailable item rather than fake integrity evidence.

Reject identity/role mismatches, duplicate IDs or destinations, unsafe paths, symlink/collision and open inventory before starting transfers. Copy real bytes into private uniquely named attempt staging under the child root. Never turn stdout into file content. Compare local streamed size/hash with observed remote size/hash; only then publish without overwrite at the immutable declared native destination and emit a verified `ArtifactRecord`. Zero-byte logs can verify against observed zero size; semantic data completeness belongs to F08. The final record must pass the existing materialized-file verifier.

Each file gets at most two transfer attempts. Preserve all failed attempt bytes/evidence, earlier verified files and per-item missing/failure reasons; a later verified retry does not erase earlier failure history. Continue eligible remaining items after ordinary failures, but stop on interruption/exhaustion. Transfer command budget is `min(item timeout, connection transfer ceiling, overall_deadline - now - cleanup_reserve_seconds)`; no command starts when nonpositive. Retry uses a different staging path and the same original budget. No additional final-copy timer or cap extension exists.

Return cleanup-required on every terminal path; F09 aggregates any existing measurement failure separately, attempts normal resource deletion by the original cap and records cleanup-open/residual resources. F02R does not delete resources, retain disks, register expiry, create another reservation or repeat measurement. Uncopied remote evidence may be lost during normal cleanup; retain available local evidence and report this limit honestly.

## Overall cap integration policy

F11/F09 activate admitted reservation immediately before expiry registration; provider registration must be confirmed before resource creation. Required order is `cap_started_at <= expiry.registered_at <= creation`; setup/registration consume the same cap. Independent expiry cannot exceed that cap and is not proof of billable-resource deletion. F02R simply consumes the resulting original deadlines and cleanup allowance. Provider mechanisms and lifecycle scheduling remain owned elsewhere.

## Acceptance ownership

Luna owns implementation and focused/full regression in the paths listed in plan.md. Sol convergence must review exact transport argv, help/build observations, stale-unit/firewall behavior, cumulative deadlines and physically materialized transfer attempts, not just task checkmarks. Coordinator owns shared amendments, integration and publication. Offline evidence does not validate cloud permissions, real source builds, traffic or cleanup.
