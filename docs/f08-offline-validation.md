# F08 Offline Validation Record

The first result table and the initial status narrative below are historical checkpoints from before the later F08 convergence fixes. Their results apply to the named commits only. The post-F018 T047 section records the current completed replay evidence.

This record covers the credential-free F08 implementation in `/private/tmp/cnb-f08`, branch `codex/f08-v2-validation`. No cloud API, SSH, Terraform apply, benchmark traffic, secrets, paid work or Thesis write was used. Sockperf and iperf3 artifacts are synthetic, source-format fixtures.

Environment: Python 3.11.13, `/private/tmp/cnb-offline-venv/bin/python`, `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`.

| Check | Result |
|---|---|
| Spec Kit prerequisites (`--require-tasks --include-tasks`) | Exit 0; feature directory resolved to `/private/tmp/cnb-f08/specs/010-v2-native-validation`; required artifacts were present. |
| F08-focused native, validation, calibration, summary, contract and replay tests | Exit 0; 82 passed against code checkpoint `26581dcd17b91719a6b8229639551343e45b1d9b`. |
| Full offline test suite | Exit 0; 709 passed against code checkpoint `26581dcd17b91719a6b8229639551343e45b1d9b`. |
| `compileall -q src/cloud_network_benchmark` | Exit 0. |
| F01R shared-contract schema drift check | Exit 0 with `python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check`. |
| Historical/shared contract preservation | `git diff --name-only origin/main -- src/cloud_network_benchmark/contracts specs/001-core-offline-contracts specs/002-vm-access-bootstrap specs/003-v2-shared-contracts` returned no paths; merged main is `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121`. |
| `git diff --check` | Exit 0 after code integration. |

The final feature branch contains code checkpoints `87177a3f2daf5e60f6628c86fc3e576c71c138bd` and `26581dcd17b91719a6b8229639551343e45b1d9b`, plus merge commit `6cf5ecf9b8cfc7765a4b7a92868c0c33817181a5` bringing in F03 from main `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121`. T039/T047 materialized replays invoke validation, summary, comparison and calibration selection against saved F01R bytes; they verify deterministic reruns and source hashes, and use 288 one-second receiver intervals for DAY. The F03 `MeasurementTiming` producer currently records `overlap_seconds` as unavailable and F01R has no source-bound active-probe acquisition-window intervals. Therefore the validator reports coverage unavailable and replayed acceptance/calibration/day bundles remain partial; frozen-N short-study peers remain ineligible. F08 does not invent interval evidence or a schema field. T047 remains unchecked pending a coordinator-owned producer/contract capability decision. This is not cloud or research evidence. Real cloud and paid acceptance remain separate authorized stages; F10 remains the separate offline system-readiness gate.


## Resume after acquisition contract merge

After the recorded checkpoint above, F08 preserved its pre-merge edits in commit `e8b600d3cd30a9c31cf3e0cb50583e2518df08d` and integrated acquisition PR #16 via F01R commit `6971ba72cf5031ad50c4658606cc1d55c10242ac`. The merged acquisition sources unblock source-bound consumer work for representable short-mode artifact identities. F08 now verifies materialized build sidecar bytes and hashes, matches command build identity, replays the public acquisition helper, derives conservative source-bound active windows, and restricts RTT sample selection to those windows. Diagnostic-day replay remains incomplete: the day layout and `ValidatorInput` cannot express the unique chunk-scoped identities required by the helper. This is a confirmed shared-contract ownership gap assigned separately to F018; F08 has not fabricated artifact IDs or edited shared contracts. No Linux acquisition behavior or cloud behavior is proven.


## T047 materialized non-DAY replay checkpoint

F08 now materializes acquisition observation bytes and four role/tool build-identity sidecars for acceptance, short-study, and calibration replay. The fixtures use the canonical unit-local observation artifact IDs, actual F01R `bind_acquisition_sources` and public replay helpers, native iperf3 session cookies/settings/stream rows, and command evidence for the persistent `systemd-run` listener. The validation consumer recognizes this recorded F02R launcher form only when its acquisition environment, installed iperf3 path, receiver mode, bind address, port, and streaming flags are present. It does not infer those settings from an arbitrary argument substring.

Observed focused result at the current uncommitted checkpoint: acceptance bundles are valid and complete; all three materialized provider calibration bundles are valid and `select_common_n` selects N=2 with plateau evidence and a source digest; three frozen-N short-study bundles with prior-calibration provenance are valid and `evaluate_comparison` marks the three peers eligible; pending-N short-study bundles remain partial and ineligible. The helper invokes validation and summaries against saved bundle bytes, repeats validation deterministically, checks recorded hashes, and confirms a native CSV mutation is invalid before restoring the bytes. These remain synthetic offline contract fixtures, not cloud, Linux runtime, or research observations.

The statements above record historical checkpoints. F018 PR #18 merged on `main` at `027c9cc483f87da89f853e94a68a4c4d9280a503`, resolving the shared DAY family identity mismatch. F08 merged that dependency in feature merge commit `7f3845067a5e2dfa5015096304335bfe862184d2` and now consumes only canonical chunk-suffixed artifact IDs and paths.

T047 DAY proof now replays one source-verified persistent receiver session with 86,400 one-second native intervals and all 288 actual 300-second Sockperf chunk source windows. The session and source sidecars agree on 5-second omit, settings, cookie, endpoints, listener identity, build identity, monotonic/UTC mapping and measured duration. Actual `validate_bundle` returns valid/complete with 86,400 observed seconds and 86,400 source-supported probe seconds (100%); `summarize_validation`, `evaluate_comparison`, repeated deterministic validation, source hash checks and the native-file mutation integrity regression run on the same materialized bundle. The fixture is synthetic F01R evidence and proves no Linux producer behavior, cloud behavior, clock accuracy or research result. The earlier status showing a partial DAY fixture was accurate at that earlier checkpoint and is superseded by this completed proof.

## Final T047 validation checkpoint

The canonical task artifacts now mark T047 complete; the older progress entries above are retained as historical checkpoints. Latest credential-free checks on the post-F018 F08 worktree:

| Check | Result |
|---|---|
| Focused native, validation, calibration, summary, contract and materialized replay suite | Exit 0; 85 passed in 53.25 seconds. |
| Full offline suite with official PowerShell on `PATH` | Exit 0; 1,162 passed in 163.55 seconds, no skips. |
| F01R contract schema drift check | Exit 0. |
| CLI `--help` | Exit 0; validate/resolve/dry-run/init commands listed. |
| `compileall -q src/cloud_network_benchmark` | Exit 0. |
| `git diff --check` | Exit 0. |
| Generated materialized configs | `find configs/tests -maxdepth 1 -name 'f08-*'` returned no paths after pytest teardown. |
| Shared-contract ownership audit | No changed paths under contracts or F01R contract/bootstrap specs relative to merged main. |

Commands used the offline Python at `/private/tmp/cnb-offline-venv/bin/python`, `PYTHONPATH=src`, the read-only Thesis source path, and for pytest `PATH=/private/tmp/cnb-tools/powershell-7.5.11:$PATH`. No cloud, SSH, Terraform apply, network benchmark, paid experiment, or Thesis write was used. These checks validate synthetic offline bundle replay only.
